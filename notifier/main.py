#!/usr/bin/env python3

import json
import socket

WACLI_SOCKET = "/tmp/rlocal/wacli/wacli.sock"
RWORKSPACES_SOCKET = "/tmp/rlocal/rworkspaces/sock"
ATTENTION_ID = "wacli"


def send_attention():
    with socket.socket(socket.AF_UNIX, socket.SOCK_STREAM) as sock:
        sock.connect(RWORKSPACES_SOCKET)
        payload = json.dumps(
            {
                "id": ATTENTION_ID,
                "command": ["toggle-window", "show", "wacli-tui"],
                "dismiss_on_window_class": "wacli-tui",
            }
        )
        sock.send(f"add_attention_by_cmd {payload}".encode())
        sock.recv(256)


def main():
    with socket.socket(socket.AF_UNIX, socket.SOCK_STREAM) as sock:
        sock.connect(WACLI_SOCKET)
        print("Connected to wacli socket, listening for events...")

        buffer = ""
        while True:
            data = sock.recv(4096).decode()
            if not data:
                break

            buffer += data
            while "\n" in buffer:
                line, buffer = buffer.split("\n", 1)
                event = json.loads(line)
                if event["type"] == "message":
                    send_attention()


if __name__ == "__main__":
    main()
