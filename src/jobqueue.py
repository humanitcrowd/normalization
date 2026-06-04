"""Parallel job queue for the drag-and-drop UI.

Replaces the older `FolderWatcher`: files arrive via explicit user action
(dragging onto the window) rather than filesystem events. Each job carries
status (pending / processing / done / error) so the UI can render a queue
with per-item feedback.

Processing uses `normalizer.normalize_in_place`, so the pristine original
is preserved in `<dir>/CharBackup/<name>` and the file at the dragged
location is replaced with the normalized output. Per-file work happens in
disjoint directories with no shared state, so we run up to N jobs in
parallel — ffmpeg loudnorm is single-threaded per process and a modern
Apple Silicon Mac easily handles 4–8 concurrent encodes.
"""
from __future__ import annotations

import os
import threading
import time
from collections.abc import Callable
from dataclasses import dataclass
from pathlib import Path

from . import history, normalizer
from .log import get_logger


def _default_parallelism() -> int:
    """Half the cores, capped at 8, floored at 2 — leaves headroom for the
    UI and the OS while saturating most of the cores for the user."""
    cpu = os.cpu_count() or 4
    return max(2, min(8, cpu // 2))


@dataclass
class QueueCallbacks:
    on_queue: Callable[[list[dict]], None]
    on_status: Callable[[str], None]
    on_done: Callable[[normalizer.Result], None]
    on_error: Callable[[Path, Exception], None]
    on_idle: Callable[[], None]


@dataclass
class _Item:
    path: Path
    status: str = "pending"  # pending | processing | done | error
    measured_in: float | None = None
    measured_tp: float | None = None
    measured_out: float | None = None
    error: str | None = None
    output_name: str | None = None
    backup_path: str | None = None
    processed_at: str | None = None
    target_lufs_used: float | None = None
    # measure-on-drop: idle | measuring | measured. Not the same as `status`.
    measure_state: str = "idle"

    def to_dict(self) -> dict:
        return {
            "path": str(self.path),
            "name": self.path.name,
            "status": self.status,
            "measured_in": self.measured_in,
            "measured_tp": self.measured_tp,
            "measured_out": self.measured_out,
            "measure_state": self.measure_state,
            "error": self.error,
            "output_name": self.output_name,
            "backup_path": self.backup_path,
            "processed_at": self.processed_at,
            "target_lufs_used": self.target_lufs_used,
            "recoverable": (
                self.status == "done"
                and self.backup_path is not None
                and Path(self.backup_path).exists()
            ),
        }


class JobQueue:
    def __init__(self, callbacks: QueueCallbacks,
                 target_lufs: float = -16.0,
                 true_peak: float = -1.5,
                 parallelism: int | None = None) -> None:
        self.callbacks = callbacks
        self.target_lufs = target_lufs
        self.true_peak = true_peak
        self.parallelism = parallelism or _default_parallelism()
        self._items: list[_Item] = []
        # Single RLock protects: _items, _running, _active_count.
        self._state_lock = threading.RLock()
        self._running = False
        self._active_count = 0
        self._files_processed = 0
        self._stop_event = threading.Event()
        # Caps concurrent measure-on-drop ffmpeg passes so a big drop doesn't
        # spawn one ffmpeg per file all at once.
        self._measure_sem = threading.Semaphore(self.parallelism)
        # Seed from persisted history so previously processed files (with
        # working backups) show up as `done` rows with a Recover button.
        self._load_history()

    def _load_history(self) -> None:
        try:
            entries = history.load()
        except Exception:
            get_logger().exception("history load failed")
            return
        with self._state_lock:
            for e in entries:
                self._items.append(_Item(
                    path=Path(e.path),
                    status="done",
                    measured_in=e.measured_in,
                    measured_out=e.measured_out,
                    backup_path=e.backup_path,
                    processed_at=e.processed_at,
                    target_lufs_used=e.target_lufs,
                ))

    # ── public ───────────────────────────────────────────────────────

    @property
    def files_processed(self) -> int:
        return self._files_processed

    @property
    def is_running(self) -> bool:
        return self._running

    def add(self, paths: list[Path]) -> list[Path]:
        """Append paths to the queue, dedup against existing entries, drop
        unsupported formats. Returns the paths actually added.

        If a path is already in the queue as a done/error row (from history
        or earlier in this session), it gets re-armed as `pending` so the
        user can re-normalize without first clearing it manually. The
        backup is preserved either way.
        """
        added: list[Path] = []
        with self._state_lock:
            by_path = {str(it.path): it for it in self._items}
            for p in paths:
                if not p.exists() or not p.is_file():
                    continue
                if not normalizer.is_supported(p):
                    get_logger().info("Skipping unsupported file: %s", p.name)
                    continue
                key = str(p)
                existing = by_path.get(key)
                if existing is None:
                    self._items.append(_Item(path=p))
                    added.append(p)
                elif existing.status in ("done", "error"):
                    # Re-arm: keep historical fields, just flip status back
                    # to pending so workers will pick it up, and re-analyze.
                    existing.status = "pending"
                    existing.error = None
                    existing.measure_state = "idle"
                    added.append(p)
                # If already pending/processing, leave alone.
        self._push_snapshot()
        # Analyze the new drops in the background so the UI can show input
        # levels before the user clicks Start.
        for p in added:
            threading.Thread(target=self._measure_item, args=(p,),
                             daemon=True).start()
        return added

    def _measure_item(self, path: Path) -> None:
        """Background measure-on-drop: report the input loudness + true peak of
        the source (the pristine backup if one exists, else the file itself)
        using the ebur128 meter, so the UI shows accurate levels before Start."""
        key = str(path)
        with self._state_lock:
            item = next((it for it in self._items if str(it.path) == key), None)
            if item is None or item.status != "pending":
                return
            if item.measure_state == "measuring":
                return
            if item.measure_state == "measured" and item.measured_in is not None:
                return  # already measured
            item.measure_state = "measuring"
        self._push_snapshot()

        integrated = true_peak = None
        with self._measure_sem:
            # Bail if the item got picked up for processing while we waited.
            with self._state_lock:
                item = next((it for it in self._items
                             if str(it.path) == key), None)
                if item is None or item.status != "pending":
                    return
            try:
                src = normalizer.source_path_for(path)
                integrated, true_peak = normalizer.measure_loudness(src)
            except Exception:
                get_logger().exception("measure-on-drop failed for %s", path.name)

        with self._state_lock:
            item = next((it for it in self._items if str(it.path) == key), None)
            if item is None or item.status != "pending":
                return
            if integrated is not None:
                item.measured_in = integrated
                item.measured_tp = true_peak
                item.measure_state = "measured"
            else:
                item.measure_state = "idle"
        self._push_snapshot()

    def recover(self, path: str) -> bool:
        """Restore the pristine original at `path` from its backup, then
        drop the corresponding history entry. Returns True on success."""
        from shutil import copy2
        with self._state_lock:
            target = None
            for it in self._items:
                if str(it.path) == path:
                    target = it
                    break
        if target is None:
            get_logger().warning("Recover: %s not in queue", path)
            return False
        if not target.backup_path:
            get_logger().warning("Recover: no backup recorded for %s", path)
            return False
        backup = Path(target.backup_path)
        current = Path(path)
        if not backup.exists():
            get_logger().error("Recover: backup file missing at %s", backup)
            return False

        # If the post-normalize filename was a different extension than the
        # backup (lossy -> .wav), the file at `current` is the .wav. We need
        # to remove WHATEVER currently sits at the original location AND any
        # extension-swapped sibling, then restore the backup with its
        # original name.
        target_restored = current.with_name(backup.name)
        # Wipe the current normalized file (which may be a sibling with a
        # different extension than the backup).
        for candidate in {current, target_restored}:
            if candidate != backup and candidate.exists():
                try:
                    candidate.unlink()
                except OSError:
                    get_logger().exception("Recover: failed to remove %s", candidate)
                    return False
        try:
            copy2(str(backup), str(target_restored))
        except OSError:
            get_logger().exception("Recover: copy backup->target failed")
            return False

        # Drop the history entry + remove the row from the queue.
        try:
            history.remove(path)
        except Exception:
            get_logger().exception("Recover: history.remove failed")
        with self._state_lock:
            self._items = [it for it in self._items if str(it.path) != path]
        self._push_snapshot()
        get_logger().info("Recovered: %s <- %s", target_restored, backup)
        return True

    def remove(self, index: int) -> None:
        """Remove one row. Processing rows can't be removed mid-encode.
        Done rows also drop their history entry (so they won't come back on
        next launch) — the on-disk backup file itself is left intact."""
        dropped_path: str | None = None
        with self._state_lock:
            if 0 <= index < len(self._items):
                item = self._items[index]
                if item.status == "processing":
                    return
                if item.status == "done":
                    dropped_path = str(item.path)
                self._items.pop(index)
        if dropped_path is not None:
            try:
                history.remove(dropped_path)
            except Exception:
                get_logger().exception("history.remove failed")
        self._push_snapshot()

    def clear(self) -> None:
        """Clear everything that isn't actively encoding. Done rows are
        wiped from both the UI and the persisted history (the on-disk
        backup files themselves stay where they are — Recover via Finder
        still works)."""
        dropped_history_paths: list[str] = []
        with self._state_lock:
            kept: list[_Item] = []
            for it in self._items:
                if it.status == "processing":
                    kept.append(it)
                elif it.status == "done":
                    dropped_history_paths.append(str(it.path))
            self._items = kept
        for p in dropped_history_paths:
            try:
                history.remove(p)
            except Exception:
                get_logger().exception("history.remove failed for %s", p)
        self._push_snapshot()

    def start(self) -> None:
        """Begin processing pending items in parallel. No-op if running or
        nothing pending."""
        with self._state_lock:
            if self._running:
                return
            if not any(it.status == "pending" for it in self._items):
                return
            self._running = True
            self._stop_event.clear()
            self._active_count = 0
        for _ in range(self.parallelism):
            t = threading.Thread(target=self._worker_loop, daemon=True)
            t.start()

    def stop(self) -> None:
        """Ask workers to wind down. Already-running ffmpeg invocations
        finish naturally; we never kill mid-encode (avoids torn output)."""
        with self._state_lock:
            self._running = False
        self._stop_event.set()

    def snapshot(self) -> list[dict]:
        with self._state_lock:
            return [it.to_dict() for it in self._items]

    # ── internals ────────────────────────────────────────────────────

    def _push_snapshot(self) -> None:
        try:
            self.callbacks.on_queue(self.snapshot())
        except Exception:
            get_logger().exception("on_queue callback failed")

    def _claim_next_pending(self) -> _Item | None:
        """Atomically pick a pending item and mark it processing.
        Returns None if no pending items remain."""
        with self._state_lock:
            for it in self._items:
                if it.status == "pending":
                    it.status = "processing"
                    self._active_count += 1
                    return it
        return None

    def _release(self) -> None:
        with self._state_lock:
            self._active_count -= 1

    def _worker_loop(self) -> None:
        while not self._stop_event.is_set():
            item = self._claim_next_pending()
            if item is None:
                # Nothing for me right now. If no one else is working
                # either, this run is over — gracefully wind down.
                with self._state_lock:
                    if not self._running:
                        return
                    if self._active_count == 0:
                        self._running = False
                        signal_idle = True
                    else:
                        signal_idle = False
                if signal_idle:
                    try:
                        self.callbacks.on_idle()
                    except Exception:
                        get_logger().exception("on_idle callback failed")
                    return
                # Other workers still busy — wait briefly for a new pending
                # item or for them to finish.
                time.sleep(0.1)
                continue

            self._push_snapshot()
            try:
                self._process(item)
            finally:
                self._release()
            self._push_snapshot()

    def _process(self, item: _Item) -> None:
        target_lufs_used = self.target_lufs
        try:
            result = normalizer.normalize_in_place(
                item.path,
                progress=self.callbacks.on_status,
                target_lufs=target_lufs_used,
                true_peak=self.true_peak,
            )
            # Report the achieved output level with the ebur128 meter so the
            # 'Done' number matches dedicated meters. Fall back to loudnorm's
            # self-reported value if the meter pass fails.
            out_lufs = result.measured_out
            try:
                eb_out, _ = normalizer.measure_loudness(result.output_path)
                if eb_out is not None:
                    out_lufs = eb_out
            except Exception:
                get_logger().exception("output ebur128 measure failed")
            # Keep the accurate measure-on-drop input level if we have it.
            in_lufs = item.measured_in if item.measured_in is not None else result.measured_in
            backup_path = str(normalizer.backup_path_for(item.path))
            processed_at = history.now_iso()
            with self._state_lock:
                item.status = "done"
                item.measured_in = in_lufs
                item.measured_out = out_lufs
                item.output_name = result.output_path.name
                item.backup_path = backup_path
                item.processed_at = processed_at
                item.target_lufs_used = target_lufs_used
                self._files_processed += 1
            try:
                history.upsert(history.Entry(
                    path=str(item.path),
                    name=item.path.name,
                    backup_path=backup_path,
                    processed_at=processed_at,
                    target_lufs=target_lufs_used,
                    measured_in=in_lufs,
                    measured_out=out_lufs,
                ))
            except Exception:
                get_logger().exception("history.upsert failed")
            try:
                self.callbacks.on_done(result)
            except Exception:
                get_logger().exception("on_done callback failed")
        except normalizer.NormalizerError as e:
            with self._state_lock:
                item.status = "error"
                item.error = str(e)
            try:
                self.callbacks.on_error(item.path, e)
            except Exception:
                get_logger().exception("on_error callback failed")
        except Exception as e:
            get_logger().exception("Unexpected error processing %s", item.path.name)
            with self._state_lock:
                item.status = "error"
                item.error = str(e)
            try:
                self.callbacks.on_error(item.path, e)
            except Exception:
                get_logger().exception("on_error callback failed")
