#!/usr/bin/env python3
import sys
from pathlib import Path

sys.path.insert(0, str(Path(__file__).parent.parent))

from tui.app import WaCLIApp
from tui.utils import LOCK_FILE, focus_existing_instance, write_lock_file


def main() -> None:
    if focus_existing_instance():
        sys.exit(0)

    write_lock_file()
    app = WaCLIApp()
    try:
        app.run()
    finally:
        LOCK_FILE.unlink(missing_ok=True)


if __name__ == "__main__":
    main()
