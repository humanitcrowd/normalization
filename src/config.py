"""Persisted user config: which folder to watch, target LUFS."""
from __future__ import annotations

import json
from dataclasses import dataclass
from pathlib import Path

APP_SUPPORT = Path.home() / "Library" / "Application Support" / "CharLUFS"
CONFIG_PATH = APP_SUPPORT / "config.json"
DEFAULT_WATCH_FOLDER = Path.home() / "CharLUFS"
DEFAULT_TARGET_LUFS = -16.0
MIN_TARGET_LUFS = -23.0
MAX_TARGET_LUFS = -8.0


def clamp_lufs(value: float) -> float:
    """Snap to 0.5 increments and clamp to the supported range."""
    snapped = round(value * 2) / 2
    return max(MIN_TARGET_LUFS, min(MAX_TARGET_LUFS, snapped))


@dataclass
class Config:
    watch_folder: Path
    target_lufs: float = DEFAULT_TARGET_LUFS

    def to_dict(self) -> dict:
        return {
            "watch_folder": str(self.watch_folder),
            "target_lufs": self.target_lufs,
        }

    @classmethod
    def from_dict(cls, data: dict) -> Config:
        folder = data.get("watch_folder") or str(DEFAULT_WATCH_FOLDER)
        try:
            target = clamp_lufs(float(data.get("target_lufs", DEFAULT_TARGET_LUFS)))
        except (TypeError, ValueError):
            target = DEFAULT_TARGET_LUFS
        return cls(watch_folder=Path(folder).expanduser(), target_lufs=target)


def load() -> Config:
    if CONFIG_PATH.exists():
        try:
            return Config.from_dict(json.loads(CONFIG_PATH.read_text()))
        except (json.JSONDecodeError, OSError):
            pass
    return Config(watch_folder=DEFAULT_WATCH_FOLDER)


def save(cfg: Config) -> None:
    APP_SUPPORT.mkdir(parents=True, exist_ok=True)
    CONFIG_PATH.write_text(json.dumps(cfg.to_dict(), indent=2))


def ensure_folder(folder: Path) -> Path:
    folder = folder.expanduser()
    folder.mkdir(parents=True, exist_ok=True)
    return folder
