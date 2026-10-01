"""Send one WhatsApp message through the wacli server and exit.

The socket protocol lives here so callers — chord scripts, cron jobs, anything
that wants to fire a message — only need a chat JID and some text.
"""

import json
import socket
import sys
import uuid

from shared.connection import SERVER_ADDR

TIMEOUT = 20


def add_parser(subparsers) -> None:
    parser = subparsers.add_parser("send", help="send one message to a chat")
    parser.add_argument("chat_jid", help="target chat, e.g. 223454632669317@lid")
    parser.add_argument("text", help='message body, or "-" to read it from stdin')
    parser.set_defaults(run=run)


def read_response(sock: socket.socket, request_id: str) -> None:
    """Consume the fan-out stream until the ack for our request arrives."""
    buffer = b""
    while True:
        chunk = sock.recv(65536)
        if not chunk:
            raise SystemExit("wacli send: server closed the connection before responding")
        buffer += chunk

        while b"\n" in buffer:
            line, buffer = buffer.split(b"\n", 1)
            if not line.strip():
                continue
            event = json.loads(line)
            # connection_state and broadcast messages share this stream
            if event.get("request_id") != request_id:
                continue
            if event.get("success"):
                return
            raise SystemExit(f"wacli send: {event.get('error') or 'server reported failure'}")


def send(chat_jid: str, text: str) -> None:
    request_id = str(uuid.uuid4())
    command = {
        "action": "send",
        "request_id": request_id,
        "chat_jid": chat_jid,
        "text": text,
    }

    try:
        with socket.create_connection(SERVER_ADDR, timeout=TIMEOUT) as sock:
            sock.settimeout(TIMEOUT)
            sock.sendall((json.dumps(command) + "\n").encode())
            read_response(sock, request_id)
    except OSError as exc:
        host, port = SERVER_ADDR
        raise SystemExit(f"wacli send: cannot reach the server at {host}:{port}: {exc}")


def run(args) -> None:
    text = sys.stdin.read() if args.text == "-" else args.text
    if not text.strip():
        raise SystemExit("wacli send: refusing to send an empty message")

    send(args.chat_jid, text)
