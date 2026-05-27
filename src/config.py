"""Persisted user config: target LUFS + true-peak ceiling.

`watch_folder` is no longer used by the app (drag-and-drop replaced the
watch-folder workflow in 1.2.0) but the field is kept on the dataclass
so the legacy modules (src/app.py, src/watcher.py) and their tests still
import cleanly. It's preserved on round-trip but otherwise inert.

Persistence note: `target_lufs` is intentionally NOT restored on launch
(every session starts at the default so the producer isn't surprised by
a stale creative setting). `true_peak` IS restored — it's a delivery-spec
ceiling you set once, not a per-session choice.
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

DEFAULT_TRUE_PEAK = -1.5
MIN_TRUE_PEAK = -6.0
MAX_TRUE_PEAK = -0.5

# Legacy: kept for the deprecated watch-folder modules and their tests.
DEFAULT_WATCH_FOLDER = Path.home() / "CharLUFS"


def clamp_lufs(value: float) -> float:
    """Snap to 0.5 increments and clamp to the supported range."""
    snapped = round(value * 2) / 2
    return max(MIN_TARGET_LUFS, min(MAX_TARGET_LUFS, snapped))


def clamp_true_peak(value: float) -> float:
    """Snap to 0.5 increments and clamp to the supported range."""
    snapped = round(value * 2) / 2
    return max(MIN_TRUE_PEAK, min(MAX_TRUE_PEAK, snapped))


def ensure_folder(folder: Path) -> Path:
    """Legacy helper used by the deprecated watch-folder modules."""
    folder = folder.expanduser()
    folder.mkdir(parents=True, exist_ok=True)
    return folder


@dataclass
class Config:
    target_lufs: float = DEFAULT_TARGET_LUFS
    true_peak: float = DEFAULT_TRUE_PEAK
    watch_folder: Path = field(default_factory=lambda: DEFAULT_WATCH_FOLDER)

    def to_dict(self) -> dict:
        return {
            "target_lufs": self.target_lufs,
            "true_peak": self.true_peak,
            "watch_folder": str(self.watch_folder),
        }

    @classmethod
    def from_dict(cls, data: dict) -> Config:
        try:
            target = clamp_lufs(float(data.get("target_lufs", DEFAULT_TARGET_LUFS)))
        except (TypeError, ValueError):
            target = DEFAULT_TARGET_LUFS
        try:
            tp = clamp_true_peak(float(data.get("true_peak", DEFAULT_TRUE_PEAK)))
        except (TypeError, ValueError):
            tp = DEFAULT_TRUE_PEAK
        folder = data.get("watch_folder") or str(DEFAULT_WATCH_FOLDER)
        return cls(target_lufs=target, true_peak=tp,
                   watch_folder=Path(folder).expanduser())


def load() -> Config:
    """Open with the default loudness target every launch (the slider's last
    value is written to disk but intentionally ignored on read), while
    restoring the persisted true-peak ceiling."""
    cfg = Config()
    if CONFIG_PATH.exists():
        try:
            data = json.loads(CONFIG_PATH.read_text())
            cfg.true_peak = clamp_true_peak(
                float(data.get("true_peak", DEFAULT_TRUE_PEAK))
            )
        except (json.JSONDecodeError, OSError, TypeError, ValueError):
            pass
    return cfg


def save(cfg: Config) -> None:
    APP_SUPPORT.mkdir(parents=True, exist_ok=True)
    CONFIG_PATH.write_text(json.dumps(cfg.to_dict(), indent=2))
