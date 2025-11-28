#!/usr/bin/env python3
import asyncio
import json
import sqlite3
from dataclasses import dataclass
from datetime import datetime
from pathlib import Path

from textual.app import App, ComposeResult
from textual.binding import Binding
from textual.containers import ScrollableContainer
from textual.widgets import Footer, Header, Static


SOCKET_PATH = "/tmp/wacli.sock"
DB_PATH = Path(__file__).parent.parent / "cli" / "messages.db"


@dataclass
class Message:
    id: int
    chat_jid: str
    chat_name: str
    sender_jid: str
    sender_name: str
    text: str
    timestamp: int
    is_group: bool

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
        if self.is_group:
            return f"{self.sender_name} @ {self.chat_name}"
        return self.sender_name


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
        self.title = "WhatsApp Messages"
        self.load_messages_from_db()
        self.render_messages()
        asyncio.create_task(self.listen_socket())

    def load_messages_from_db(self) -> None:
        if not DB_PATH.exists():
            return

        conn = sqlite3.connect(DB_PATH)
        cursor = conn.cursor()
        cursor.execute(
            "SELECT id, chat_jid, chat_name, sender_jid, sender_name, text, timestamp, is_group "
            "FROM messages ORDER BY timestamp ASC"
        )
        for row in cursor.fetchall():
            self.messages.append(Message(
                id=row[0],
                chat_jid=row[1],
                chat_name=row[2],
                sender_jid=row[3],
                sender_name=row[4],
                text=row[5],
                timestamp=row[6],
                is_group=bool(row[7]),
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
        while True:
            try:
                reader, _ = await asyncio.open_unix_connection(SOCKET_PATH)
                while True:
                    line = await reader.readline()
                    if not line:
                        break
                    data = json.loads(line.decode())
                    msg = Message(
                        id=data.get("id", 0),
                        chat_jid=data["chat_jid"],
                        chat_name=data["chat_name"],
                        sender_jid=data["sender_jid"],
                        sender_name=data["sender_name"],
                        text=data["text"],
                        timestamp=data["timestamp"],
                        is_group=data["is_group"],
                    )
                    self.messages.append(msg)
                    message_list = self.query_one(MessageList)
                    message_list.mount(MessageWidget(msg))
                    self.call_after_refresh(self.scroll_to_bottom)
            except (FileNotFoundError, ConnectionRefusedError):
                await asyncio.sleep(2)
            except Exception:
                await asyncio.sleep(2)

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
