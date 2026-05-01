"""File log + in-memory buffer for the in-app log view."""
from __future__ import annotations

import contextlib
import logging
from collections import deque
from collections.abc import Callable
from logging.handlers import RotatingFileHandler
from pathlib import Path
from threading import Lock

LOG_DIR = Path.home() / "Library" / "Logs" / "PodcastNormalizer"
LOG_FILE = LOG_DIR / "normalizer.log"
MAX_BUFFER_LINES = 200


class _BufferHandler(logging.Handler):
    def __init__(self, buffer: deque[str], buffer_lock: Lock,
                 listener: Callable[[str], None] | None = None) -> None:
        super().__init__()
        self._buffer = buffer
        self._buffer_lock = buffer_lock
        self._listener = listener

    def emit(self, record: logging.LogRecord) -> None:
        try:
            line = self.format(record)
        except Exception:
            return
        with self._buffer_lock:
            self._buffer.append(line)
        if self._listener is not None:
            with contextlib.suppress(Exception):
                self._listener(line)


class LogStore:
    """Holds the rotating file handler and an in-memory ring buffer."""

    def __init__(self) -> None:
        self._buffer: deque[str] = deque(maxlen=MAX_BUFFER_LINES)
        self._lock = Lock()
        self._listener: Callable[[str], None] | None = None

        LOG_DIR.mkdir(parents=True, exist_ok=True)
        self.logger = logging.getLogger("podcast_normalizer")
        self.logger.setLevel(logging.INFO)
        self.logger.propagate = False
        if not self.logger.handlers:
            fmt = logging.Formatter("%(asctime)s %(levelname)s %(message)s",
                                    datefmt="%Y-%m-%d %H:%M:%S")
            file_handler = RotatingFileHandler(
                LOG_FILE, maxBytes=1_000_000, backupCount=3, encoding="utf-8"
            )
            file_handler.setFormatter(fmt)
            self.logger.addHandler(file_handler)

            self._buf_handler = _BufferHandler(self._buffer, self._lock,
                                               listener=self._dispatch)
            self._buf_handler.setFormatter(fmt)
            self.logger.addHandler(self._buf_handler)

    def set_listener(self, listener: Callable[[str], None] | None) -> None:
        self._listener = listener

    def _dispatch(self, line: str) -> None:
        if self._listener is not None:
            self._listener(line)

    def snapshot(self, n: int = 50) -> list[str]:
        with self._lock:
            return list(self._buffer)[-n:]

    def full_buffer_text(self) -> str:
        with self._lock:
            return "\n".join(self._buffer)


_store: LogStore | None = None


def get_store() -> LogStore:
    global _store
    if _store is None:
        _store = LogStore()
    return _store


def get_logger() -> logging.Logger:
    return get_store().logger
