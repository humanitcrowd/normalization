"""py2app entry point. Build with: python setup.py py2app"""
from pathlib import Path

from setuptools import setup

ROOT = Path(__file__).parent
RESOURCES = ROOT / "resources"

APP = ["app_launcher.py"]
WEB_DIR = ROOT / "web"

DATA_FILES = []
ffmpeg_bin = RESOURCES / "ffmpeg"
if ffmpeg_bin.exists():
    DATA_FILES.append(("", [str(ffmpeg_bin)]))

# Bundle the web/ directory wholesale so the pywebview UI ships with the
# .app. py2app preserves the relative directory layout when data_files
# entries use a target subdir.
if WEB_DIR.exists():
    for path in sorted(WEB_DIR.rglob("*")):
        if not path.is_file():
            continue
        rel = path.relative_to(ROOT).parent  # e.g. "web" or "web/vendor"
        DATA_FILES.append((str(rel), [str(path)]))

icon_path = RESOURCES / "icon.icns"

OPTIONS = {
    "argv_emulation": False,
    "packages": ["watchdog", "src", "webview"],
    "includes": ["tkinter"],
    "iconfile": str(icon_path) if icon_path.exists() else None,
    "plist": {
        "CFBundleName": "CharLUFS",
        "CFBundleDisplayName": "CharLUFS",
        "CFBundleIdentifier": "com.charlufs.app",
        "CFBundleShortVersionString": "1.2.0",
        "CFBundleVersion": "1.2.0",
        "LSMinimumSystemVersion": "11.0",
        "NSHighResolutionCapable": True,
        "LSUIElement": False,
        "NSHumanReadableCopyright": "",
    },
}

OPTIONS = {k: v for k, v in OPTIONS.items() if v is not None}

setup(
    app=APP,
    name="CharLUFS",
    data_files=DATA_FILES,
    options={"py2app": OPTIONS},
    setup_requires=["py2app"],
)
