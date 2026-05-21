"""pywebview-based UI for CharLUFS.

Renders the web/ design bundle in a native WKWebView window. Python keeps
the watcher + normalizer + config; JS calls into Python via the
`pywebview.api.*` bridge and Python pushes status/log/folder events back
into JS via dispatched CustomEvents.
"""
from __future__ import annotations

import contextlib
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
from .log import get_store
from .watcher import FolderWatcher, WorkerCallbacks


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
            "watch_folder": str(self._app.config.watch_folder),
            "target_lufs": self._app.config.target_lufs,
            "log": list(snapshot),
            "files_processed": (
                self._app.watcher.files_processed if self._app.watcher else 0
            ),
            "min_lufs": cfg.MIN_TARGET_LUFS,
            "max_lufs": cfg.MAX_TARGET_LUFS,
        }

    def set_target_lufs(self, value: float) -> float:
        snapped = cfg.clamp_lufs(float(value))
        self._app.config.target_lufs = snapped
        self._app.schedule_save()
        if self._app.watcher is not None:
            self._app.watcher.target_lufs = snapped
        return snapped

    def open_folder(self) -> None:
        path = str(self._app.config.watch_folder)
        cfg.ensure_folder(self._app.config.watch_folder)
        with contextlib.suppress(OSError):
            subprocess.Popen(["open", path])

    def change_folder(self) -> str | None:
        win = self._app.window
        if win is None:
            return None
        result = win.create_file_dialog(
            webview.FOLDER_DIALOG,
            directory=str(self._app.config.watch_folder),
        )
        if not result:
            return None
        new_folder = Path(result[0])
        self._app.set_watch_folder(new_folder)
        return str(new_folder)

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
        self.watcher: FolderWatcher | None = None
        self._save_timer: threading.Timer | None = None
        self._save_lock = threading.Lock()

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

    # ── watcher wiring ───────────────────────────────────────────────

    def _make_callbacks(self) -> WorkerCallbacks:
        return WorkerCallbacks(
            on_status=lambda s: self.push("status", {
                "kind": "working", "text": s,
            }),
            on_done=self._on_done,
            on_error=lambda p, e: self.push("status", {
                "kind": "error",
                "file": p.name,
                "text": f"Couldn't process {p.name}: {e}",
            }),
        )

    def _on_done(self, result: normalizer.Result) -> None:
        self.push("status", {
            "kind": "done",
            "file": result.output_path.name,
            "out_lufs": result.measured_out,
            "in_lufs": result.measured_in,
        })
        if self.watcher is not None:
            self.push("counter", self.watcher.files_processed)

    def _start_watcher(self) -> None:
        self.watcher = FolderWatcher(
            self.config.watch_folder, self._make_callbacks(),
            target_lufs=self.config.target_lufs,
        )
        self.watcher.start()
        self.push("folder", str(self.config.watch_folder))

    def _stop_watcher(self) -> None:
        if self.watcher is not None:
            self.watcher.stop()
            self.watcher = None

    def set_watch_folder(self, folder: Path) -> None:
        self._stop_watcher()
        folder = cfg.ensure_folder(folder)
        self.config = cfg.Config(
            watch_folder=folder,
            target_lufs=self.config.target_lufs,
        )
        cfg.save(self.config)
        self._start_watcher()

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

    # ── lifecycle ────────────────────────────────────────────────────

    def _on_closed(self) -> None:
        get_store().set_listener(None)
        self._stop_watcher()
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
            width=640, height=520,
            min_size=(540, 440),
            background_color="#1C1D20",
            resizable=True,
        )
        self.window.events.closed += self._on_closed

        # Start the watcher only after the window is ready, so the first
        # status push has a sink to land in.
        def _on_loaded():
            try:
                self._start_watcher()
            except Exception:
                log.exception("watcher failed to start")
        self.window.events.loaded += _on_loaded

        webview.start()
