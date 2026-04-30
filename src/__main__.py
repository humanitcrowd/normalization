"""Launch the Podcast Normalizer app."""
from __future__ import annotations

import sys

from .app import App


def main() -> int:
    app = App()
    app.run()
    return 0


if __name__ == "__main__":
    sys.exit(main())
