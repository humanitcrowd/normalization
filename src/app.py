"""Tkinter UI for CharLUFS."""
from __future__ import annotations

import contextlib
import queue
import subprocess
import tkinter as tk
from pathlib import Path
from tkinter import filedialog, scrolledtext, ttk

from . import config as cfg
from . import normalizer
from .log import get_store
from .watcher import FolderWatcher, WorkerCallbacks


class App:
    def __init__(self) -> None:
        self._config = cfg.load()
        self._ui_queue: queue.Queue[tuple[str, object]] = queue.Queue()

        self.root = tk.Tk()
        self.root.title("CharLUFS")
        self.root.geometry("640x460")
        self.root.minsize(560, 380)
        self.root.protocol("WM_DELETE_WINDOW", self._on_close)

        self._build_ui()
        self._wire_logging()

        self.watcher: FolderWatcher | None = None
        self._start_watching(self._config.watch_folder)

        # Pump the UI-thread queue
        self.root.after(100, self._drain_ui_queue)

    # UI ---------------------------------------------------------------

    def _build_ui(self) -> None:
        pad = {"padx": 12, "pady": 6}

        header = ttk.Frame(self.root)
        header.pack(fill="x", **pad)
        ttk.Label(header, text="Watching:",
                  font=("TkDefaultFont", 11, "bold")).pack(side="left")
        self.folder_var = tk.StringVar(value=str(self._config.watch_folder))
        ttk.Label(header, textvariable=self.folder_var,
                  font=("TkDefaultFont", 11)).pack(side="left", padx=(6, 0))

        buttons = ttk.Frame(self.root)
        buttons.pack(fill="x", **pad)
        ttk.Button(buttons, text="Open folder",
                   command=self._open_folder).pack(side="left")
        ttk.Button(buttons, text="Change folder…",
                   command=self._change_folder).pack(side="left", padx=(8, 0))

        status = ttk.Frame(self.root)
        status.pack(fill="x", **pad)
        ttk.Label(status, text="Status:",
                  font=("TkDefaultFont", 11, "bold")).pack(side="left")
        self.status_var = tk.StringVar(value="Idle")
        ttk.Label(status, textvariable=self.status_var,
                  font=("TkDefaultFont", 11)).pack(side="left", padx=(6, 0))

        counter = ttk.Frame(self.root)
        counter.pack(fill="x", **pad)
        self.counter_var = tk.StringVar(value="Files processed this session: 0")
        ttk.Label(counter, textvariable=self.counter_var).pack(side="left")

        log_frame = ttk.LabelFrame(self.root, text="Log")
        log_frame.pack(fill="both", expand=True, **pad)

        self.log_widget = scrolledtext.ScrolledText(
            log_frame, height=12, wrap="word", state="disabled",
            font=("Menlo", 10),
        )
        self.log_widget.pack(fill="both", expand=True, padx=4, pady=4)

        log_buttons = ttk.Frame(log_frame)
        log_buttons.pack(fill="x", padx=4, pady=(0, 4))
        ttk.Button(log_buttons, text="Copy log",
                   command=self._copy_log).pack(side="left")
        self._copy_feedback = ttk.Label(log_buttons, text="")
        self._copy_feedback.pack(side="left", padx=(8, 0))

    def _wire_logging(self) -> None:
        store = get_store()
        # Stream new lines into the UI via the thread-safe queue
        store.set_listener(lambda line: self._ui_queue.put(("log", line)))
        # Backfill any lines already buffered before the listener was attached
        for line in store.snapshot(50):
            self._append_log(line)

    # Watcher wiring ---------------------------------------------------

    def _start_watching(self, folder: Path) -> None:
        folder = cfg.ensure_folder(folder)
        self.folder_var.set(str(folder))
        callbacks = WorkerCallbacks(
            on_status=lambda s: self._ui_queue.put(("status", s)),
            on_done=lambda r: self._ui_queue.put(("done", r)),
            on_error=lambda p, e: self._ui_queue.put(("error", (p, e))),
        )
        self.watcher = FolderWatcher(folder, callbacks)
        self.watcher.start()

    def _stop_watching(self) -> None:
        if self.watcher is not None:
            self.watcher.stop()
            self.watcher = None

    # Button handlers --------------------------------------------------

    def _open_folder(self) -> None:
        folder = self._config.watch_folder
        cfg.ensure_folder(folder)
        with contextlib.suppress(OSError):
            subprocess.Popen(["open", str(folder)])

    def _change_folder(self) -> None:
        chosen = filedialog.askdirectory(
            initialdir=str(self._config.watch_folder),
            title="Choose a folder to watch",
        )
        if not chosen:
            return
        new_folder = Path(chosen)
        self._stop_watching()
        self._config = cfg.Config(watch_folder=new_folder)
        cfg.save(self._config)
        self._start_watching(new_folder)
        self._set_status("Idle")

    def _copy_log(self) -> None:
        text = get_store().full_buffer_text()
        self.root.clipboard_clear()
        self.root.clipboard_append(text)
        self._copy_feedback.configure(text="Copied")
        self.root.after(1500, lambda: self._copy_feedback.configure(text=""))

    # UI queue dispatch ------------------------------------------------

    def _drain_ui_queue(self) -> None:
        try:
            while True:
                kind, payload = self._ui_queue.get_nowait()
                if kind == "status":
                    self._set_status(str(payload))
                elif kind == "log":
                    self._append_log(str(payload))
                elif kind == "done":
                    assert isinstance(payload, normalizer.Result)
                    self._on_done(payload)
                elif kind == "error":
                    path, err = payload  # type: ignore[misc]
                    self._set_status(f"Error: {path.name}: {err}")
        except queue.Empty:
            pass
        self.root.after(100, self._drain_ui_queue)

    def _set_status(self, text: str) -> None:
        self.status_var.set(text)

    def _append_log(self, line: str) -> None:
        self.log_widget.configure(state="normal")
        self.log_widget.insert("end", line + "\n")
        # Keep only the last ~50 lines visible
        line_count = int(self.log_widget.index("end-1c").split(".")[0])
        if line_count > 50:
            self.log_widget.delete("1.0", f"{line_count - 50}.0")
        self.log_widget.see("end")
        self.log_widget.configure(state="disabled")

    def _on_done(self, result: normalizer.Result) -> None:
        if result.measured_out is not None:
            msg = f"Done: {result.output_path.name} ({result.measured_out:.1f} LUFS)"
        else:
            msg = f"Done: {result.output_path.name}"
        self._set_status(msg)
        if self.watcher is not None:
            self.counter_var.set(
                f"Files processed this session: {self.watcher.files_processed}"
            )

    # Lifecycle --------------------------------------------------------

    def _on_close(self) -> None:
        self._stop_watching()
        get_store().set_listener(None)
        self.root.destroy()

    def run(self) -> None:
        self.root.mainloop()
