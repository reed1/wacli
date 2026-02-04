#!/usr/bin/env python3

import json
import socket
import sys
import time

SERVER_ADDR = ("100.97.165.105", 3010)
RWORKSPACES_SOCKET = "/tmp/rlocal/rworkspaces/sock"
ATTENTION_ID = "wacli"


def wait_for_server():
    delays = [1, 2, 4, 8]
    for delay in delays:
        try:
            with socket.socket(socket.AF_INET, socket.SOCK_STREAM) as sock:
                sock.connect(SERVER_ADDR)
                return True
        except (ConnectionRefusedError, OSError):
            print(f"Server not ready, waiting {delay}s...")
            time.sleep(delay)
    print("Server not ready after all retries, exiting")
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
    wait_for_server()
    with socket.socket(socket.AF_INET, socket.SOCK_STREAM) as sock:
        sock.connect(SERVER_ADDR)
        print("Connected to wacli server, listening for events...")

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
