#!/usr/bin/env python3
import sys
from pathlib import Path

sys.path.insert(0, str(Path(__file__).parent.parent))

from tui.app import WaCLIApp


def main() -> None:
    app = WaCLIApp()
    app.run()


if __name__ == "__main__":
    main()
