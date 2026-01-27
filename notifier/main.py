#!/usr/bin/env python3

import json
import socket
import sys
import time

WACLI_SOCKET = "/tmp/rlocal/wacli/wacli.sock"
RWORKSPACES_SOCKET = "/tmp/rlocal/rworkspaces/sock"
ATTENTION_ID = "wacli"


def wait_for_socket():
    delays = [1, 2, 4, 8]
    for delay in delays:
        try:
            with socket.socket(socket.AF_UNIX, socket.SOCK_STREAM) as sock:
                sock.connect(WACLI_SOCKET)
                return True
        except (FileNotFoundError, ConnectionRefusedError):
            print(f"Socket not ready, waiting {delay}s...")
            time.sleep(delay)
    print("Socket not ready after all retries, exiting")
    sys.exit(1)


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
    wait_for_socket()
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
