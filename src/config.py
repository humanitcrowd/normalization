"""Persisted user config: which folder to watch."""
from __future__ import annotations

import json
from dataclasses import dataclass
from pathlib import Path

APP_SUPPORT = Path.home() / "Library" / "Application Support" / "CharLUFS"
CONFIG_PATH = APP_SUPPORT / "config.json"
DEFAULT_WATCH_FOLDER = Path.home() / "CharLUFS"


@dataclass
class Config:
    watch_folder: Path

    def to_dict(self) -> dict:
        return {"watch_folder": str(self.watch_folder)}

    @classmethod
    def from_dict(cls, data: dict) -> Config:
        folder = data.get("watch_folder") or str(DEFAULT_WATCH_FOLDER)
        return cls(watch_folder=Path(folder).expanduser())


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
