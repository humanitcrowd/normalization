"""Launch the CharLUFS app (pywebview UI)."""
from __future__ import annotations

import sys

from .webapp import WebApp


def main() -> int:
    WebApp().run()
    return 0


if __name__ == "__main__":
    sys.exit(main())
