"""Folder watcher with size-stable debounce.

A worker thread services a queue. The watchdog handler enqueues new file paths;
the worker waits for the file size to be stable for STABLE_SECONDS before
handing it off to the normalizer. This handles slow Finder copies, AirDrop
drops, and DAW bounces that grow over time.

We use watchdog's PollingObserver (1s interval) rather than FSEvents because
FSEvents has been observed to drop events in hardened-runtime py2app bundles.
A 1-second poll is plenty for a podcast producer's workflow and ~0% CPU on
the watched folder size we expect.

The worker also runs a periodic re-scan of the directory as a safety net,
so a file that somehow bypasses the observer still gets picked up.
"""
from __future__ import annotations

import queue
import threading
import time
from collections.abc import Callable
from dataclasses import dataclass
from pathlib import Path

from watchdog.events import FileSystemEvent, FileSystemEventHandler
from watchdog.observers.polling import PollingObserver

from . import normalizer
from .log import get_logger

STABLE_SECONDS = 2.0
POLL_INTERVAL = 0.5
MAX_STABLE_WAIT = 60 * 30  # 30 min ceiling for very slow copies
OBSERVER_INTERVAL = 1.0
RESCAN_INTERVAL = 5.0  # safety-net rescan of the watched folder


@dataclass
class WorkerCallbacks:
    on_status: Callable[[str], None]
    on_done: Callable[[normalizer.Result], None]
    on_error: Callable[[Path, Exception], None]


class _Handler(FileSystemEventHandler):
    def __init__(self, q: queue.Queue[Path], watch_root: Path) -> None:
        self.queue = q
        self.watch_root = watch_root

    def _maybe_enqueue(self, raw_path: str) -> None:
        try:
            p = Path(raw_path)
        except (TypeError, ValueError):
            return
        # Only direct children of the watched folder
        try:
            if p.parent.resolve() != self.watch_root.resolve():
                return
        except OSError:
            return
        if not normalizer.should_process(p):
            return
        self.queue.put(p)

    def on_created(self, event: FileSystemEvent) -> None:
        if event.is_directory:
            return
        self._maybe_enqueue(event.src_path)

    def on_moved(self, event: FileSystemEvent) -> None:
        if event.is_directory:
            return
        # Treat the destination as a fresh arrival
        dest = getattr(event, "dest_path", None)
        if dest:
            self._maybe_enqueue(dest)


class FolderWatcher:
    """Watches a folder, debounces size, queues work, runs normalization."""

    def __init__(self, folder: Path, callbacks: WorkerCallbacks,
                 target_lufs: float = -16.0) -> None:
        self.folder = folder
        self.callbacks = callbacks
        self.target_lufs = target_lufs
        self._queue: queue.Queue[Path] = queue.Queue()
        self._observer: PollingObserver | None = None
        self._worker: threading.Thread | None = None
        self._rescanner: threading.Thread | None = None
        self._stop_event = threading.Event()
        self._files_processed = 0
        self._seen: set[Path] = set()
        self._seen_lock = threading.Lock()

    @property
    def files_processed(self) -> int:
        return self._files_processed

    def start(self) -> None:
        self.folder.mkdir(parents=True, exist_ok=True)
        self._stop_event.clear()
        self._observer = PollingObserver(timeout=OBSERVER_INTERVAL)
        handler = _Handler(self._queue, self.folder)
        self._observer.schedule(handler, str(self.folder), recursive=False)
        self._observer.start()
        self._worker = threading.Thread(target=self._worker_loop, daemon=True)
        self._worker.start()
        self._rescanner = threading.Thread(target=self._rescanner_loop,
                                           daemon=True)
        self._rescanner.start()
        self._scan_existing()
        get_logger().info("Watching %s (poll %.1fs, rescan %.1fs)",
                          self.folder, OBSERVER_INTERVAL, RESCAN_INTERVAL)

    def stop(self) -> None:
        self._stop_event.set()
        if self._observer is not None:
            self._observer.stop()
            self._observer.join(timeout=2.0)
            self._observer = None
        self._worker = None
        self._rescanner = None

    def _scan_existing(self) -> None:
        """Queue any files in the folder we haven't already seen.

        Called once at startup and periodically by the rescanner as a
        belt-and-suspenders backup to the polling observer.
        """
        try:
            for entry in self.folder.iterdir():
                if entry.is_file() and normalizer.should_process(entry):
                    key = entry.resolve() if entry.exists() else entry
                    with self._seen_lock:
                        if key in self._seen:
                            continue
                    self._queue.put(entry)
        except OSError:
            pass

    def _rescanner_loop(self) -> None:
        """Periodic safety-net rescan in case the observer drops an event."""
        while not self._stop_event.wait(RESCAN_INTERVAL):
            self._scan_existing()

    def _wait_stable(self, path: Path) -> bool:
        """Wait for file size to stop changing. Returns True when stable.

        A 0-byte file is considered stable after STABLE_SECONDS — ffmpeg will
        reject it downstream, but the worker won't get stuck on it.
        """
        last_size = -1
        stable_for = 0.0
        elapsed = 0.0
        while not self._stop_event.is_set():
            try:
                size = path.stat().st_size
            except FileNotFoundError:
                return False
            except OSError:
                return False
            if size == last_size:
                stable_for += POLL_INTERVAL
                if stable_for >= STABLE_SECONDS:
                    return True
            else:
                stable_for = 0.0
                last_size = size
            time.sleep(POLL_INTERVAL)
            elapsed += POLL_INTERVAL
            if elapsed > MAX_STABLE_WAIT:
                get_logger().warning("Gave up waiting for %s to stabilize", path.name)
                return False
        return False

    def _claim(self, path: Path) -> bool:
        """Idempotency: don't process the same path twice in this session."""
        key = path.resolve() if path.exists() else path
        with self._seen_lock:
            if key in self._seen:
                return False
            self._seen.add(key)
            return True

    def _worker_loop(self) -> None:
        while not self._stop_event.is_set():
            try:
                path = self._queue.get(timeout=0.5)
            except queue.Empty:
                continue
            self._process(path)

    def _process(self, path: Path) -> None:
        if not path.exists():
            return
        if not normalizer.should_process(path):
            return
        if not self._claim(path):
            return

        self.callbacks.on_status(f"Waiting for {path.name} to finish copying…")
        if not self._wait_stable(path):
            # Allow re-processing later if file appears again
            with self._seen_lock:
                self._seen.discard(path.resolve() if path.exists() else path)
            return

        # Re-check after the wait; the file may have moved or been renamed
        if not path.exists() or not normalizer.should_process(path):
            return

        try:
            result = normalizer.normalize(
                path, progress=self.callbacks.on_status,
                target_lufs=self.target_lufs,
            )
        except normalizer.NormalizerError as e:
            self.callbacks.on_error(path, e)
            return
        except Exception as e:  # don't take down the worker on unexpected errors
            get_logger().exception("Unexpected error processing %s", path.name)
            self.callbacks.on_error(path, e)
            return

        self._files_processed += 1
        self.callbacks.on_done(result)
