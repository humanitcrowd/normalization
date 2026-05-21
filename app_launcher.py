"""Top-level entry script for the py2app bundle.

py2app runs the entry script as a standalone module without package
context, so relative imports inside src/__main__.py don't work in the
bundled app. This launcher uses absolute imports instead.

For development runs (`python -m src`), src/__main__.py is still the
canonical entry point.
"""
from src.app import App


def main() -> int:
    App().run()
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
