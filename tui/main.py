#!/usr/bin/env python3
import asyncio
import json
import sqlite3
from dataclasses import dataclass
from datetime import datetime
from pathlib import Path

LOG_FILE = Path(__file__).parent / "wacli.log"

def log(msg: str) -> None:
    with open(LOG_FILE, "a") as f:
        f.write(f"{datetime.now().isoformat()} {msg}\n")

from textual.app import App, ComposeResult
from textual.binding import Binding
from textual.containers import ScrollableContainer
from textual.widgets import Footer, Header, Static


SOCKET_PATH = "/tmp/wacli.sock"
DB_PATH = Path(__file__).parent.parent / "cli" / "messages.db"


@dataclass
class Message:
    id: int
    timestamp: int
    chat_jid: str
    chat_name: str
    sender_jid: str
    sender_name: str
    is_group: bool
    is_muted: bool
    is_reply_to_me: bool
    text: str

    @property
    def formatted_time(self) -> str:
        dt = datetime.fromtimestamp(self.timestamp)
        return dt.strftime("%H:%M")

    @property
    def formatted_date(self) -> str:
        dt = datetime.fromtimestamp(self.timestamp)
        return dt.strftime("%Y-%m-%d")

    @property
    def title(self) -> str:
        prefix = "[Reply] " if self.is_reply_to_me else ""
        if self.is_group:
            return f"{prefix}{self.sender_name} @ {self.chat_name}"
        return f"{prefix}{self.sender_name}"


class MessageWidget(Static):
    def __init__(self, message: Message) -> None:
        self.message = message
        super().__init__()

    def compose(self) -> ComposeResult:
        msg = self.message
        content = f"[bold cyan]{msg.title}[/] [dim]{msg.formatted_time}[/]\n{msg.text}"
        yield Static(content, markup=True)


class MessageList(ScrollableContainer):
    pass


class WaCLIApp(App):
    CSS = """
    MessageList {
        height: 100%;
        scrollbar-gutter: stable;
    }

    MessageWidget {
        padding: 0 1;
        margin-bottom: 1;
        border-bottom: solid $surface-lighten-2;
    }

    MessageWidget:focus {
        background: $surface-lighten-1;
    }
    """

    BINDINGS = [
        Binding("q", "quit", "Quit"),
        Binding("j", "scroll_down", "Down", show=False),
        Binding("k", "scroll_up", "Up", show=False),
        Binding("g", "scroll_home", "Top", show=False),
        Binding("G", "scroll_end", "Bottom", show=False),
    ]

    def __init__(self) -> None:
        super().__init__()
        self.messages: list[Message] = []

    def compose(self) -> ComposeResult:
        yield Header()
        yield MessageList()
        yield Footer()

    async def on_mount(self) -> None:
        log("on_mount: start")
        self.title = "WhatsApp Messages"
        self.load_messages_from_db()
        self.render_messages()
        log("on_mount: starting worker")
        self.run_worker(self.listen_socket(), exclusive=True)

    def load_messages_from_db(self) -> None:
        if not DB_PATH.exists():
            return

        conn = sqlite3.connect(DB_PATH)
        conn.row_factory = sqlite3.Row
        cursor = conn.cursor()
        cursor.execute("SELECT * FROM messages ORDER BY timestamp ASC")
        for row in cursor.fetchall():
            self.messages.append(Message(
                id=row["id"],
                timestamp=row["timestamp"],
                chat_jid=row["chat_jid"],
                chat_name=row["chat_name"],
                sender_jid=row["sender_jid"],
                sender_name=row["sender_name"],
                is_group=bool(row["is_group"]),
                is_muted=bool(row["is_muted"]),
                is_reply_to_me=bool(row["is_reply_to_me"]),
                text=row["text"],
            ))
        conn.close()

    def render_messages(self) -> None:
        message_list = self.query_one(MessageList)
        message_list.remove_children()
        for msg in self.messages:
            message_list.mount(MessageWidget(msg))
        self.call_after_refresh(self.scroll_to_bottom)

    def scroll_to_bottom(self) -> None:
        message_list = self.query_one(MessageList)
        message_list.scroll_end(animate=False)

    async def listen_socket(self) -> None:
        log("listen_socket: connecting...")
        reader, _ = await asyncio.open_unix_connection(SOCKET_PATH)
        log("listen_socket: connected")
        while True:
            line = await reader.readline()
            log(f"listen_socket: got line: {line}")
            if not line:
                raise ConnectionError("Socket connection closed")
            data = json.loads(line.decode())
            msg = Message(
                id=data.get("id", 0),
                timestamp=data["timestamp"],
                chat_jid=data["chat_jid"],
                chat_name=data["chat_name"],
                sender_jid=data["sender_jid"],
                sender_name=data["sender_name"],
                is_group=data["is_group"],
                is_muted=data["is_muted"],
                is_reply_to_me=data["is_reply_to_me"],
                text=data["text"],
            )
            log(f"listen_socket: parsed message: {msg.text}")
            self.messages.append(msg)
            message_list = self.query_one(MessageList)
            message_list.mount(MessageWidget(msg))
            self.call_after_refresh(self.scroll_to_bottom)
            log("listen_socket: widget mounted")

    def action_scroll_down(self) -> None:
        self.query_one(MessageList).scroll_relative(y=3)

    def action_scroll_up(self) -> None:
        self.query_one(MessageList).scroll_relative(y=-3)

    def action_scroll_home(self) -> None:
        self.query_one(MessageList).scroll_home()

    def action_scroll_end(self) -> None:
        self.query_one(MessageList).scroll_end()


def main() -> None:
    app = WaCLIApp()
    app.run()


if __name__ == "__main__":
    main()
