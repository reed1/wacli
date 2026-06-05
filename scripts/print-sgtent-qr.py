#!/usr/bin/env python3
"""SSH to the prod server and re-pair WhatsApp from this terminal.

We log out first to clear any stale session (a session that WhatsApp
invalidated server-side still looks "logged in" locally and blocks a fresh
login), then `wacli login` renders the pairing QR as half-block ANSI to
stdout, which we stream over an SSH TTY. Scan it from WhatsApp -> Linked
Devices. Once pairing succeeds we restart the systemd service.
"""
import argparse
import subprocess
import sys

HOST = "sgtent"
SERVER_DIR = "/home/reed/app/wacli/server"
SERVICE = "wacli-server"


def main() -> int:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--host", default=HOST, help=f"ssh host (default: {HOST})")
    args = parser.parse_args()

    login = subprocess.run(
        ["ssh", "-t", args.host,
         f"cd {SERVER_DIR} && ./wacli logout && exec ./wacli login"]
    )
    if login.returncode != 0:
        print(f"login command exited with {login.returncode}", file=sys.stderr)
        return login.returncode

    print(f"\nLogin complete. Restarting {SERVICE}...", file=sys.stderr)
    return subprocess.run(
        ["ssh", args.host, f"systemctl --user restart {SERVICE}"]
    ).returncode


if __name__ == "__main__":
    sys.exit(main())
