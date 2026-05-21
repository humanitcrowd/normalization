"""pywebview-based UI for CharLUFS.

Renders the web/ design bundle in a native WKWebView window. Python keeps
the job queue + normalizer + config; JS calls into Python via the
`pywebview.api.*` bridge and Python pushes status/log/queue events back
into JS via dispatched CustomEvents.

Drag-and-drop note: WKWebView delivers drop events to JS but strips the
local path from the File objects (sandbox/privacy). To make `CharBackup`
work we need real filesystem paths, so we hook the WKWebView's native
`performDragOperation:` at startup and route NSURL paths into the queue.
JS handles only the visual drag-over feedback.
"""
from __future__ import annotations

import json
import logging
import os
import subprocess
import sys
import threading
from pathlib import Path

import webview

from . import config as cfg
from . import normalizer
from .jobqueue import JobQueue, QueueCallbacks
from .log import get_store


log = logging.getLogger("charlufs.webapp")


def _find_web_root() -> Path:
    """Find the bundled web/ directory.

    py2app drops it next to the binary under Contents/Resources/.
    During dev, it sits at the repo root.
    """
    bundle_resources = os.environ.get("RESOURCEPATH")
    if bundle_resources:
        candidate = Path(bundle_resources) / "web"
        if candidate.exists():
            return candidate
    if getattr(sys, "frozen", False):
        bundle_root = Path(sys.executable).resolve().parent.parent
        candidate = bundle_root / "Resources" / "web"
        if candidate.exists():
            return candidate
    return Path(__file__).resolve().parent.parent / "web"


class Api:
    """JS-callable surface. Methods are wired by pywebview as
    `window.pywebview.api.<method>(...)` and return JSON-serialisable
    values."""

    def __init__(self, app: "WebApp") -> None:
        self._app = app

    def get_initial_state(self) -> dict:
        snapshot = get_store().snapshot(80)
        return {
            "target_lufs": self._app.config.target_lufs,
            "log": list(snapshot),
            "queue": self._app.queue.snapshot() if self._app.queue else [],
            "files_processed": (
                self._app.queue.files_processed if self._app.queue else 0
            ),
            "running": self._app.queue.is_running if self._app.queue else False,
            "min_lufs": cfg.MIN_TARGET_LUFS,
            "max_lufs": cfg.MAX_TARGET_LUFS,
        }

    def set_target_lufs(self, value: float) -> float:
        snapped = cfg.clamp_lufs(float(value))
        self._app.config.target_lufs = snapped
        self._app.schedule_save()
        if self._app.queue is not None:
            self._app.queue.target_lufs = snapped
        return snapped

    def start_processing(self) -> bool:
        if self._app.queue is None:
            return False
        self._app.queue.start()
        return self._app.queue.is_running

    def clear_queue(self) -> None:
        if self._app.queue is not None:
            self._app.queue.clear()

    def remove_from_queue(self, index: int) -> None:
        if self._app.queue is not None:
            self._app.queue.remove(int(index))

    def recover_file(self, path: str) -> bool:
        if self._app.queue is None:
            return False
        return self._app.queue.recover(str(path))

    def reveal_in_finder(self, path: str) -> None:
        try:
            subprocess.Popen(["open", "-R", path])
        except OSError:
            pass

    def copy_log(self, text: str) -> bool:
        """Clipboard fallback for when the JS clipboard API is blocked."""
        try:
            proc = subprocess.Popen(["pbcopy"], stdin=subprocess.PIPE)
            proc.communicate(input=(text or "").encode("utf-8"))
            return proc.returncode == 0
        except OSError:
            return False


class WebApp:
    def __init__(self) -> None:
        self.config = cfg.load()
        self.window: webview.Window | None = None
        self.queue: JobQueue | None = None
        self._save_timer: threading.Timer | None = None
        self._save_lock = threading.Lock()
        self._drag_handler_installed = False

    # ── Python -> JS push ────────────────────────────────────────────

    def push(self, event: str, detail) -> None:
        if self.window is None:
            return
        payload = json.dumps(detail, default=str)
        try:
            self.window.evaluate_js(
                f"window.dispatchEvent(new CustomEvent('charlufs:{event}',"
                f" {{detail: {payload}}}))"
            )
        except Exception:
            log.exception("evaluate_js failed for charlufs:%s", event)

    # ── queue wiring ─────────────────────────────────────────────────

    def _make_callbacks(self) -> QueueCallbacks:
        return QueueCallbacks(
            on_queue=lambda items: self.push("queue", items),
            on_status=lambda s: self.push("status", {
                "kind": "working", "text": s,
            }),
            on_done=self._on_done,
            on_error=lambda p, e: self.push("status", {
                "kind": "error",
                "file": p.name,
                "text": f"Couldn't process {p.name}: {e}",
            }),
            on_idle=lambda: self.push("status", {"kind": "idle"}),
        )

    def _on_done(self, result: normalizer.Result) -> None:
        self.push("status", {
            "kind": "done",
            "file": result.output_path.name,
            "out_lufs": result.measured_out,
            "in_lufs": result.measured_in,
        })
        if self.queue is not None:
            self.push("counter", self.queue.files_processed)

    def _start_queue(self) -> None:
        self.queue = JobQueue(
            self._make_callbacks(),
            target_lufs=self.config.target_lufs,
        )

    # ── files dropped from native handler ────────────────────────────

    def on_files_dropped(self, paths: list[str]) -> None:
        if self.queue is None:
            return
        added = self.queue.add([Path(p) for p in paths])
        if added:
            log.info("Queued %d file(s) via drop", len(added))
        # Native handler swallows the WebKit drop event, so JS never sees
        # `drop` or `dragleave` and would otherwise leave its drag-over
        # overlay stuck on. Tell it to clear.
        self.push("drag_reset", None)

    # ── debounced config save ────────────────────────────────────────

    def schedule_save(self) -> None:
        with self._save_lock:
            if self._save_timer is not None:
                self._save_timer.cancel()
            self._save_timer = threading.Timer(0.4, self._save_now)
            self._save_timer.daemon = True
            self._save_timer.start()

    def _save_now(self) -> None:
        try:
            cfg.save(self.config)
        except Exception:
            log.exception("config save failed")

    # ── native drag-and-drop hook ────────────────────────────────────

    def _install_native_drag(self) -> None:
        """Hook macOS-native drop on the WKWebView to capture file paths.

        Without this, JS receives the `drop` event but `e.dataTransfer.files`
        gives no usable paths (WKWebView strips them for sandbox reasons).
        We monkey-patch WKWebView.performDragOperation_ to read NSURL paths
        off the pasteboard and forward them to the queue.

        Safe to call multiple times — we install only once.
        """
        if self._drag_handler_installed:
            return
        if sys.platform != "darwin":
            return
        try:
            from WebKit import WKWebView  # type: ignore
            from Foundation import NSURL  # type: ignore
        except ImportError:
            log.warning("PyObjC/WebKit not available — drag-and-drop disabled")
            return

        original = WKWebView.performDragOperation_
        app = self

        def perform_drag(self_, sender):  # noqa: N802 (ObjC naming)
            try:
                pb = sender.draggingPasteboard()
                urls = pb.readObjectsForClasses_options_([NSURL.class__()], None)
                if urls:
                    paths: list[str] = []
                    for u in urls:
                        p = u.path()
                        if p and not str(p).startswith(("http:", "https:")):
                            paths.append(str(p))
                    if paths:
                        threading.Thread(
                            target=app.on_files_dropped,
                            args=(paths,),
                            daemon=True,
                        ).start()
                        return True
            except Exception:
                log.exception("native performDragOperation handler failed")
            return original(self_, sender)

        try:
            WKWebView.performDragOperation_ = perform_drag
            self._drag_handler_installed = True
        except Exception:
            log.exception("Failed to install native drag handler")

    # ── lifecycle ────────────────────────────────────────────────────

    def _on_closed(self) -> None:
        get_store().set_listener(None)
        if self.queue is not None:
            self.queue.stop()
        with self._save_lock:
            if self._save_timer is not None:
                self._save_timer.cancel()
                self._save_timer = None
        # Make sure the last config value lands on disk
        self._save_now()

    def run(self) -> None:
        web_root = _find_web_root()
        index = web_root / "index.html"
        if not index.exists():
            raise RuntimeError(f"web bundle missing: {index}")

        # Wire log -> JS push. We attach the listener BEFORE creating the
        # window so backlogged messages don't get lost.
        get_store().set_listener(lambda line: self.push("log", line))

        self.window = webview.create_window(
            "CharLUFS",
            url=str(index),
            js_api=Api(self),
            width=640, height=560,
            min_size=(520, 440),
            background_color="#1C1D20",
            resizable=True,
        )
        self.window.events.closed += self._on_closed

        def _on_loaded():
            try:
                self._start_queue()
                self._install_native_drag()
            except Exception:
                log.exception("startup failed")
        self.window.events.loaded += _on_loaded

        webview.start()
