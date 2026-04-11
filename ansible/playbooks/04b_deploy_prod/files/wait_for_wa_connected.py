#!/usr/bin/env python3
import json
import socket
import sys
import time

DEADLINE_SECONDS = 60


def main() -> int:
    if len(sys.argv) != 2:
        print(f"usage: {sys.argv[0]} host:port", file=sys.stderr)
        return 2

    host, port_str = sys.argv[1].rsplit(":", 1)
    port = int(port_str)
    deadline = time.monotonic() + DEADLINE_SECONDS

    while time.monotonic() < deadline:
        try:
            with socket.create_connection((host, port), timeout=5) as s:
                s.settimeout(2)
                buf = b""
                while time.monotonic() < deadline:
                    try:
                        chunk = s.recv(4096)
                    except socket.timeout:
                        continue
                    if not chunk:
                        print("server closed connection, reconnecting", file=sys.stderr)
                        break
                    buf += chunk
                    while b"\n" in buf:
                        line, buf = buf.split(b"\n", 1)
                        if not line:
                            continue
                        event = json.loads(line)
                        if event.get("type") != "connection_state":
                            continue
                        data = event.get("data") or {}
                        if data.get("connected"):
                            print("wacli-server reports WhatsApp connected")
                            return 0
                        reason = data.get("reason") or "starting"
                        print(f"wacli-server reports not connected: {reason}")
        except OSError as e:
            print(f"connect retry: {e}", file=sys.stderr)
            time.sleep(2)

    print(
        f"timeout after {DEADLINE_SECONDS}s waiting for wacli-server WhatsApp connected state",
        file=sys.stderr,
    )
    return 1


if __name__ == "__main__":
    sys.exit(main())
