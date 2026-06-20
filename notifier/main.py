#!/usr/bin/env python3

import json
import os
import socket
import sys
import time
from pathlib import Path

from dotenv import load_dotenv

load_dotenv(Path(__file__).parent.parent / ".env")

SERVER_ADDR = (os.environ["SERVER_HOST"], int(os.environ["SERVER_PORT"]))
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


def enable_keepalive(sock):
    sock.setsockopt(socket.SOL_SOCKET, socket.SO_KEEPALIVE, 1)
    sock.setsockopt(socket.IPPROTO_TCP, socket.TCP_KEEPIDLE, 60)
    sock.setsockopt(socket.IPPROTO_TCP, socket.TCP_KEEPINTVL, 15)
    sock.setsockopt(socket.IPPROTO_TCP, socket.TCP_KEEPCNT, 4)


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
