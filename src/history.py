"""Persistent record of normalized files, for the in-app Recover button.

Each time a file completes successfully, we append an entry to
`~/Library/Application Support/CharLUFS/processed.json`. The UI shows
these entries (alongside the in-session queue) so the user can revert
any past normalization by clicking Recover, which copies the pristine
backup back over the current file.

Schema (a single JSON list):
[
  {
    "path":         "/abs/path/to/file.wav",
    "name":         "file.wav",
    "backup_path":  "/abs/path/to/CharBackup/file.wav",
    "processed_at": "2026-05-21T12:34:56",
    "target_lufs":  -16.0,
    "measured_in":  -22.3,
    "measured_out": -16.0
  },
  ...
]

Deduplicated by `path` — re-processing the same file replaces its entry
rather than stacking duplicates.
"""
from __future__ import annotations

import datetime as _dt
import json
import threading
from dataclasses import asdict, dataclass
from pathlib import Path

from .config import APP_SUPPORT

HISTORY_PATH = APP_SUPPORT / "processed.json"

_lock = threading.Lock()


@dataclass
class Entry:
    path: str
    name: str
    backup_path: str
    processed_at: str
    target_lufs: float
    measured_in: float | None = None
    measured_out: float | None = None

    def to_dict(self) -> dict:
        return asdict(self)

    @classmethod
    def from_dict(cls, data: dict) -> Entry:
        return cls(
            path=str(data.get("path", "")),
            name=str(data.get("name", "")),
            backup_path=str(data.get("backup_path", "")),
            processed_at=str(data.get("processed_at", "")),
            target_lufs=float(data.get("target_lufs", -16.0)),
            measured_in=data.get("measured_in"),
            measured_out=data.get("measured_out"),
        )


def now_iso() -> str:
    return _dt.datetime.now().replace(microsecond=0).isoformat()


def load() -> list[Entry]:
    with _lock:
        if not HISTORY_PATH.exists():
            return []
        try:
            raw = json.loads(HISTORY_PATH.read_text())
        except (json.JSONDecodeError, OSError):
            return []
        if not isinstance(raw, list):
            return []
        return [Entry.from_dict(d) for d in raw if isinstance(d, dict)]


def save(entries: list[Entry]) -> None:
    with _lock:
        APP_SUPPORT.mkdir(parents=True, exist_ok=True)
        HISTORY_PATH.write_text(
            json.dumps([e.to_dict() for e in entries], indent=2)
        )


def upsert(entry: Entry) -> list[Entry]:
    """Insert `entry`, replacing any existing entry with the same `path`.
    The new entry goes to the front (most-recent-first ordering)."""
    entries = load()
    entries = [e for e in entries if e.path != entry.path]
    entries.insert(0, entry)
    save(entries)
    return entries


def remove(path: str) -> list[Entry]:
    entries = [e for e in load() if e.path != path]
    save(entries)
    return entries
