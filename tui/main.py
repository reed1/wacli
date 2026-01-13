#!/usr/bin/env python3
import sys
from pathlib import Path

sys.path.insert(0, str(Path(__file__).parent.parent))

from tui.app import WaCLIApp


def main() -> int:
    app = WaCLIApp()
    try:
        app.run()
        return 0
    except Exception as e:
        print(f"Error: {e}", file=sys.stderr)
        return 1


if __name__ == "__main__":
    sys.exit(main())
