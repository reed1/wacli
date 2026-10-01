"""Run the TUI, offering a restart whenever it quits because the socket died.

The TUI does not reconnect in place: its view is one `get_entries` snapshot plus
the live stream, so a fresh process is the cheapest way to resync. Each run is a
separate process for that reason — nothing from the dead session survives.
"""

import subprocess
import sys
import termios
import tty
from pathlib import Path

from tui.utils import EXIT_DISCONNECTED

TUI_MAIN = Path(__file__).parent.parent / "tui" / "main.py"


def add_parser(subparsers) -> None:
    parser = subparsers.add_parser("tui", help="open the terminal UI")
    parser.add_argument("-v", "--verbose", action="store_true")
    parser.set_defaults(run=run)


def wait_for_key() -> None:
    print("Press any key to reconnect...", end="", flush=True)
    fd = sys.stdin.fileno()
    saved = termios.tcgetattr(fd)
    try:
        tty.setcbreak(fd)
        sys.stdin.read(1)
    finally:
        termios.tcsetattr(fd, termios.TCSADRAIN, saved)
    print()


def run(args) -> int:
    command = [sys.executable, str(TUI_MAIN)] + (["--verbose"] if args.verbose else [])
    while True:
        code = subprocess.run(command, check=False).returncode
        if code != EXIT_DISCONNECTED:
            return code
        wait_for_key()
