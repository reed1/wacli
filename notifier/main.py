#!/usr/bin/env python3
"""Turns incoming WhatsApp messages into an rworkspaces attention flag.

Reads the same fan-out socket the TUI reads, but only cares about `message`
events. Exits on any disconnect; the wacli-notifier systemd unit restarts it.
"""

import json
import socket
import sys
from pathlib import Path

sys.path.insert(0, str(Path(__file__).parent.parent))

from wacli_socket import SERVER_ADDR, enable_keepalive

RWORKSPACES_SOCKET = "/tmp/rlocal/rworkspaces/sock"
ATTENTION_ID = "wacli"


def send_attention():
    with socket.socket(socket.AF_UNIX, socket.SOCK_STREAM) as sock:
        sock.connect(RWORKSPACES_SOCKET)
        payload = json.dumps(
            {
                "id": ATTENTION_ID,
                "command": ["toggle-window", "show", "wacli-tui"],
                "dismiss_on_window_classes": ["wacli-tui", "elecwhat"],
            }
        )
        sock.send(f"add_attention_by_cmd {payload}".encode())
        sock.recv(256)


def main():
    try:
        sock = socket.create_connection(SERVER_ADDR)
    except OSError as error:
        # systemd retries on a 10s timer, so a server that is not up yet just
        # means waiting for the next start.
        print(f"Cannot reach wacli server: {error}", file=sys.stderr)
        sys.exit(1)

    with sock:
        enable_keepalive(sock)
        print("Connected to wacli server, listening for events...")

        buffer = ""
        while True:
            data = sock.recv(4096).decode()
            if not data:
                print("Server closed connection, exiting", file=sys.stderr)
                sys.exit(1)

            buffer += data
            while "\n" in buffer:
                line, buffer = buffer.split("\n", 1)
                event = json.loads(line)
                event_type = event["type"]
                if event_type == "message":
                    if event["data"].get("is_from_me"):
                        continue
                    send_attention()
                elif event_type == "connection_state":
                    connected = event["data"]["connected"]
                    reason = event["data"].get("reason", "")
                    if not connected:
                        print(
                            f"WhatsApp disconnected: {reason or 'unknown'}",
                            file=sys.stderr,
                        )
                        sys.exit(1)
                    print("WhatsApp connected")


if __name__ == "__main__":
    main()
