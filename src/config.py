"""Persisted user config: target LUFS.

`watch_folder` is no longer used by the app (drag-and-drop replaced the
watch-folder workflow in 1.2.0) but the field is kept on the dataclass
so the legacy modules (src/app.py, src/watcher.py) and their tests still
import cleanly. It's preserved on round-trip but otherwise inert.
"""
from __future__ import annotations

import json
from dataclasses import dataclass, field
from pathlib import Path

APP_SUPPORT = Path.home() / "Library" / "Application Support" / "CharLUFS"
CONFIG_PATH = APP_SUPPORT / "config.json"
DEFAULT_TARGET_LUFS = -16.0
MIN_TARGET_LUFS = -23.0
MAX_TARGET_LUFS = -8.0

# Legacy: kept for the deprecated watch-folder modules and their tests.
DEFAULT_WATCH_FOLDER = Path.home() / "CharLUFS"


def clamp_lufs(value: float) -> float:
    """Snap to 0.5 increments and clamp to the supported range."""
    snapped = round(value * 2) / 2
    return max(MIN_TARGET_LUFS, min(MAX_TARGET_LUFS, snapped))


def ensure_folder(folder: Path) -> Path:
    """Legacy helper used by the deprecated watch-folder modules."""
    folder = folder.expanduser()
    folder.mkdir(parents=True, exist_ok=True)
    return folder


@dataclass
class Config:
    target_lufs: float = DEFAULT_TARGET_LUFS
    watch_folder: Path = field(default_factory=lambda: DEFAULT_WATCH_FOLDER)

    def to_dict(self) -> dict:
        return {
            "target_lufs": self.target_lufs,
            "watch_folder": str(self.watch_folder),
        }

    @classmethod
    def from_dict(cls, data: dict) -> Config:
        try:
            target = clamp_lufs(float(data.get("target_lufs", DEFAULT_TARGET_LUFS)))
        except (TypeError, ValueError):
            target = DEFAULT_TARGET_LUFS
        folder = data.get("watch_folder") or str(DEFAULT_WATCH_FOLDER)
        return cls(target_lufs=target, watch_folder=Path(folder).expanduser())


def load() -> Config:
    """Always open with the default target. The slider's last value is still
    written to disk (so we can revisit this later) but is intentionally
    ignored on read — every launch starts at DEFAULT_TARGET_LUFS so the
    producer doesn't get surprised by a stale setting from a previous
    session."""
    return Config()


def save(cfg: Config) -> None:
    APP_SUPPORT.mkdir(parents=True, exist_ok=True)
    CONFIG_PATH.write_text(json.dumps(cfg.to_dict(), indent=2))
