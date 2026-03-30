#!/usr/bin/env python3
import argparse
import logging
import sys
from pathlib import Path

sys.path.insert(0, str(Path(__file__).parent.parent))

from tui.app import WaCLIApp
from tui.utils import RUNTIME_DIR


def main() -> int:
    parser = argparse.ArgumentParser(description="WhatsApp TUI")
    parser.add_argument("-v", "--verbose", action="store_true")
    args = parser.parse_args()

    log_file = RUNTIME_DIR / "tui.log"
    if args.verbose:
        logging.basicConfig(
            level=logging.INFO,
            format="%(asctime)s %(name)s %(levelname)s %(message)s",
            filename=str(log_file),
        )

    app = WaCLIApp()
    try:
        app.run()
    except Exception as e:
        print(f"Error: {e}", file=sys.stderr)
        return 1
    finally:
        if args.verbose:
            print(f"Log file: {log_file}", file=sys.stderr)
    return 0


if __name__ == "__main__":
    sys.exit(main())
