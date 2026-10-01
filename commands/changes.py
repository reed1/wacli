"""List the messages that were edited or deleted within a recent window.

The server reads them out of its event log, which holds every message it saw
whether or not it was stored — so a deletion in a muted chat still shows up,
with the text that was taken away.
"""

import argparse
import json
import os
import re
import socket
import subprocess
import sys
import time
from datetime import datetime
from typing import NamedTuple

from shared.connection import SERVER_ADDR

TIMEOUT = 60

# The server keeps seven day-of-week files, so its log never reaches further back.
MAX_WINDOW = 7 * 24 * 3600

DURATION_RE = re.compile(r"^(?:(\d+)d)?(?:(\d+)h)?(?:(\d+)m)?$")
MENTION_RE = re.compile(r'<mention jid="[^"]*" name="([^"]*)"/>')

RESET = "\033[0m"
BOLD = "\033[1m"
DIM = "\033[2m"
STRIKE = "\033[9m"
RED = "\033[31m"
GREEN = "\033[32m"
YELLOW = "\033[33m"
MAGENTA = "\033[35m"
CYAN = "\033[36m"


def add_parser(subparsers) -> None:
    parser = subparsers.add_parser("changes", help="list messages edited or deleted recently")
    parser.add_argument(
        "window",
        type=parse_window,
        help="how far back to look, e.g. 30m, 5h, 2d, 1d12h (at most 7d)",
    )
    parser.set_defaults(run=run)


class Window(NamedTuple):
    text: str
    seconds: int


def parse_window(text: str) -> Window:
    match = DURATION_RE.match(text)
    if not text or not match:
        raise argparse.ArgumentTypeError(f"not a duration: {text}")
    days, hours, minutes = (int(part or 0) for part in match.groups())
    seconds = days * 86400 + hours * 3600 + minutes * 60
    if seconds == 0:
        raise argparse.ArgumentTypeError("the window must be longer than zero")
    if seconds > MAX_WINDOW:
        raise argparse.ArgumentTypeError(
            "the server keeps a week of events; the window can be at most 7d"
        )
    return Window(text, seconds)


def fetch_changes(since: int) -> dict:
    command = {"action": "get_changes", "since": since}
    try:
        with socket.create_connection(SERVER_ADDR, timeout=TIMEOUT) as sock:
            sock.settimeout(TIMEOUT)
            sock.sendall((json.dumps(command) + "\n").encode())
            return read_changes(sock)
    except OSError as exc:
        host, port = SERVER_ADDR
        raise SystemExit(f"wacli changes: cannot reach the server at {host}:{port}: {exc}")


def read_changes(sock: socket.socket) -> dict:
    """Consume the fan-out stream until the changes reply arrives."""
    buffer = b""
    while True:
        chunk = sock.recv(65536)
        if not chunk:
            raise SystemExit("wacli changes: server closed the connection before responding")
        buffer += chunk

        while b"\n" in buffer:
            line, buffer = buffer.split(b"\n", 1)
            if not line.strip():
                continue
            event = json.loads(line)
            if event.get("type") != "changes":
                continue
            data = event["data"]
            if data.get("error"):
                raise SystemExit(f"wacli changes: {data['error']}")
            return data


class Painter:
    def __init__(self, color: bool):
        self.color = color

    def __call__(self, text: str, *styles: str) -> str:
        if not self.color or not styles:
            return text
        return "".join(styles) + text + RESET


def format_time(timestamp: int, reference: int | None = None) -> str:
    moment = datetime.fromtimestamp(timestamp)
    if reference is not None and moment.date() == datetime.fromtimestamp(reference).date():
        return moment.strftime("%H:%M")
    return moment.strftime("%a %d %b %H:%M")


def plain(text: str) -> str:
    return MENTION_RE.sub(r"@\1", text)


def body(change_type: str, text: str) -> str:
    text = plain(text)
    if change_type and text:
        return f"[{change_type}] {text}"
    if change_type:
        return f"[{change_type}]"
    return text


def block(text: str, paint: Painter, *styles: str) -> str:
    """Indent text under its header, styling line by line so a pager that
    resets at each newline keeps the style."""
    return "\n".join("    " + paint(line, *styles) for line in text.splitlines() or [""])


def not_in_tui_note(decision: str) -> str | None:
    if decision in ("", "stored"):
        return None
    if decision.startswith("dropped: "):
        return "not in TUI: " + decision.removeprefix("dropped: ")
    raise ValueError(f"Unexpected decision: {decision}")


def who(change: dict, paint: Painter) -> str:
    sender = (
        paint("me", GREEN, BOLD) if change["is_from_me"] else paint(change["sender_name"], YELLOW)
    )
    chat = paint(change["chat_name"], CYAN, BOLD)
    if change["is_group"]:
        return f"{chat} › {sender}"
    if change["is_from_me"]:
        return f"{sender} → {chat}"
    return chat


def render_change(change: dict, paint: Painter) -> str:
    sent = change["timestamp"]
    header = [paint(format_time(sent) if sent else "sent ?", DIM), who(change, paint)]
    note = not_in_tui_note(change["decision"])
    if note:
        header.append(paint(f"({note})", DIM))
    lines = ["  ".join(header)]

    deletion = change["deletion"]
    edits = change["edits"]
    # What a deletion took away is the last version anyone saw.
    gone = (RED, STRIKE)

    source = change["source"]
    if source in ("log", "store"):
        styles = gone if deletion and not edits else ()
        lines.append(block(body(change["message_type"], change["text"]), paint, *styles))
    elif source == "missing":
        lines.append(paint("    (the original is older than the log and gone from the store)", DIM))
    else:
        raise ValueError(f"Unexpected source: {source}")

    for i, edit in enumerate(edits):
        mark = paint(f"  ✎ {format_time(edit['timestamp'], sent or None)}", MAGENTA)
        styles = gone if deletion and i == len(edits) - 1 else ()
        lines.append(mark)
        lines.append(block(plain(edit["text"]), paint, *styles))

    if deletion:
        by = "me" if deletion["by_me"] else deletion["by_name"]
        lines.append(
            paint(
                f"  ✗ {format_time(deletion['timestamp'], sent or None)} deleted by {by}", RED, BOLD
            )
        )

    return "\n".join(lines)


def render(data: dict, since: int, window: str, paint: Painter) -> str:
    changes = data["changes"]
    deleted = sum(1 for change in changes if change["deletion"])
    edited = sum(1 for change in changes if change["edits"])
    blocks = [paint(f"{deleted} deleted, {edited} edited in the last {window}", BOLD)]

    log_starts = data["log_starts"]
    if log_starts and since < log_starts:
        blocks.append(
            paint(
                f"the log starts {format_time(log_starts)}; nothing from {format_time(since)} until then is covered",
                YELLOW,
            )
        )

    blocks.extend(render_change(change, paint) for change in changes)
    return "\n\n".join(blocks) + "\n"


def page(text: str) -> None:
    env = dict(os.environ)
    less_flags = env.get("LESS", "FRX")
    env["LESS"] = less_flags if "R" in less_flags else less_flags + "R"
    pager = subprocess.Popen(
        env.get("PAGER") or "less", shell=True, stdin=subprocess.PIPE, env=env, text=True
    )
    try:
        pager.stdin.write(text)
        pager.stdin.close()
    except BrokenPipeError:
        # The pager was quit before reading everything.
        pass
    pager.wait()


def run(args) -> None:
    since = int(time.time()) - args.window.seconds
    data = fetch_changes(since)

    if sys.stdout.isatty():
        page(render(data, since, args.window.text, Painter(color=True)))
    else:
        sys.stdout.write(render(data, since, args.window.text, Painter(color=False)))
