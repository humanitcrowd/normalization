"""py2app entry point. Build with: python setup.py py2app"""
from pathlib import Path

from setuptools import setup

ROOT = Path(__file__).parent
RESOURCES = ROOT / "resources"

APP = ["src/__main__.py"]

DATA_FILES = []
ffmpeg_bin = RESOURCES / "ffmpeg"
if ffmpeg_bin.exists():
    DATA_FILES.append(("", [str(ffmpeg_bin)]))

icon_path = RESOURCES / "icon.icns"

OPTIONS = {
    "argv_emulation": False,
    "packages": ["watchdog"],
    "includes": ["tkinter"],
    "iconfile": str(icon_path) if icon_path.exists() else None,
    "plist": {
        "CFBundleName": "Podcast Normalizer",
        "CFBundleDisplayName": "Podcast Normalizer",
        "CFBundleIdentifier": "com.podcastnormalizer.app",
        "CFBundleShortVersionString": "1.0.0",
        "CFBundleVersion": "1.0.0",
        "LSMinimumSystemVersion": "11.0",
        "NSHighResolutionCapable": True,
        "LSUIElement": False,
        "NSHumanReadableCopyright": "",
    },
}

OPTIONS = {k: v for k, v in OPTIONS.items() if v is not None}

setup(
    app=APP,
    name="Podcast Normalizer",
    data_files=DATA_FILES,
    options={"py2app": OPTIONS},
    setup_requires=["py2app"],
)
