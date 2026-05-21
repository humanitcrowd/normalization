"""Top-level entry script for the py2app bundle.

py2app runs the entry script as a standalone module without package
context, so relative imports inside src/__main__.py don't work in the
bundled app. This launcher uses absolute imports instead.

For development runs (`python -m src`), src/__main__.py is still the
canonical entry point.

CHARLUFS_TK=1 forces the legacy Tkinter UI for rollback purposes.
The default UI is the pywebview build.
"""
import os
import sys


def main() -> int:
    if os.environ.get("CHARLUFS_TK") == "1":
        from src.app import App
        App().run()
        return 0
    from src.webapp import WebApp
    WebApp().run()
    return 0


if __name__ == "__main__":
    sys.exit(main())
