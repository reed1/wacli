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
    def title(self) -> str:
        prefix = "↩ " if self.is_reply_to_me else ""
        if self.is_group:
            return f"{prefix}{self.sender_name} @ {self.chat_name}"
        return f"{prefix}{self.sender_name}"


@dataclass
class Call:
    id: int
    timestamp: int
    call_id: str
    caller_jid: str
    caller_name: str
    is_group: bool
    group_jid: str
    group_name: str

    @property
    def formatted_time(self) -> str:
        dt = datetime.fromtimestamp(self.timestamp)
        return dt.strftime("%H:%M")

    @property
    def title(self) -> str:
        if self.is_group and self.group_name:
            return f"{self.caller_name} @ {self.group_name}"
        return self.caller_name


Entry = Message | Call


class EntryWidget(Static):
    DEFAULT_CSS = """
    EntryWidget {
        height: 1;
        overflow: hidden;
    }
    EntryWidget.selected {
        background: $surface-lighten-1;
    }
    """

    def __init__(self, entry: Entry, selected: bool = False) -> None:
        self.entry = entry
        super().__init__()
        if selected:
            self.add_class("selected")

    def render(self) -> str:
        indicator = ">" if self.has_class("selected") else " "
        if isinstance(self.entry, Message):
            msg = self.entry
            text_oneline = msg.text.replace("\n", " ")
            max_text = 80
            if len(text_oneline) > max_text:
                text_preview = text_oneline[:max_text] + "..."
            else:
                text_preview = text_oneline
            return f"{indicator} [dim]{msg.formatted_time}[/][bold cyan] {msg.title}[/]: {text_preview}"
        call = self.entry
        return f"{indicator} [dim]{call.formatted_time}[/][bold yellow] 📞 {call.title}[/]: Incoming call"


class MessageList(ScrollableContainer):
    pass


class WaCLIApp(App):
    CSS = """
    MessageList {
        height: 100%;
        scrollbar-gutter: stable;
    }
    """

    BINDINGS = [
        Binding("q", "quit", "Quit"),
        Binding("j", "select_next", "Down", show=False),
        Binding("k", "select_prev", "Up", show=False),
        Binding("g", "select_first", "Top", show=False),
        Binding("G", "select_last", "Bottom", show=False),
        Binding("ctrl+d", "half_page_down", "Half Page Down", show=False),
        Binding("ctrl+u", "half_page_up", "Half Page Up", show=False),
    ]

    HALF_PAGE = 15

    def __init__(self) -> None:
        super().__init__()
        self.entries: list[Entry] = []
        self.selected_index: int = -1

    def compose(self) -> ComposeResult:
        yield Header()
        yield MessageList()
        yield Footer()

    async def on_mount(self) -> None:
        log("on_mount: start")
        self.title = "WhatsApp Messages"
        self.load_entries_from_db()
        self.render_entries()
        log("on_mount: starting worker")
        self.run_worker(self.listen_socket(), exclusive=True)

    def load_entries_from_db(self) -> None:
        if not DB_PATH.exists():
            return

        conn = sqlite3.connect(DB_PATH)
        conn.row_factory = sqlite3.Row
        cursor = conn.cursor()

        messages: list[Entry] = []
        cursor.execute("SELECT * FROM messages")
        for row in cursor.fetchall():
            messages.append(Message(
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

        calls: list[Entry] = []
        cursor.execute("SELECT * FROM calls")
        for row in cursor.fetchall():
            calls.append(Call(
                id=row["id"],
                timestamp=row["timestamp"],
                call_id=row["call_id"],
                caller_jid=row["caller_jid"],
                caller_name=row["caller_name"],
                is_group=bool(row["is_group"]),
                group_jid=row["group_jid"],
                group_name=row["group_name"],
            ))

        conn.close()
        self.entries = sorted(messages + calls, key=lambda e: e.timestamp)

    def render_entries(self) -> None:
        message_list = self.query_one(MessageList)
        message_list.remove_children()
        if self.entries:
            self.selected_index = len(self.entries) - 1
        for i, entry in enumerate(self.entries):
            message_list.mount(EntryWidget(entry, selected=(i == self.selected_index)))
        self.call_after_refresh(self.scroll_to_selected)

    def scroll_to_selected(self) -> None:
        widgets = self.query(EntryWidget)
        if widgets and 0 <= self.selected_index < len(widgets):
            widgets[self.selected_index].scroll_visible()

    def update_selection(self, new_index: int) -> None:
        if not self.entries:
            return
        new_index = max(0, min(new_index, len(self.entries) - 1))
        if new_index == self.selected_index:
            return
        widgets = list(self.query(EntryWidget))
        if 0 <= self.selected_index < len(widgets):
            widgets[self.selected_index].remove_class("selected")
            widgets[self.selected_index].refresh()
        self.selected_index = new_index
        widgets[self.selected_index].add_class("selected")
        widgets[self.selected_index].refresh()
        widgets[self.selected_index].scroll_visible()

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
            entry_type = data.get("type")
            entry: Entry
            if entry_type == "call":
                entry = Call(
                    id=data.get("id", 0),
                    timestamp=data["timestamp"],
                    call_id=data["call_id"],
                    caller_jid=data["caller_jid"],
                    caller_name=data["caller_name"],
                    is_group=data["is_group"],
                    group_jid=data["group_jid"],
                    group_name=data["group_name"],
                )
                log(f"listen_socket: parsed call from {entry.caller_name}")
            elif entry_type == "message":
                entry = Message(
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
                log(f"listen_socket: parsed message: {entry.text}")
            else:
                raise ValueError(f"Unexpected entry type: {entry_type}")
            self.entries.append(entry)
            message_list = self.query_one(MessageList)
            was_at_end = self.selected_index == len(self.entries) - 2
            message_list.mount(EntryWidget(entry, selected=was_at_end))
            if was_at_end:
                self.update_selection(len(self.entries) - 1)
            log("listen_socket: widget mounted")

    def action_select_next(self) -> None:
        self.update_selection(self.selected_index + 1)

    def action_select_prev(self) -> None:
        self.update_selection(self.selected_index - 1)

    def action_select_first(self) -> None:
        self.update_selection(0)

    def action_select_last(self) -> None:
        self.update_selection(len(self.entries) - 1)

    def action_half_page_down(self) -> None:
        self.update_selection(self.selected_index + self.HALF_PAGE)

    def action_half_page_up(self) -> None:
        self.update_selection(self.selected_index - self.HALF_PAGE)


def main() -> None:
    app = WaCLIApp()
    app.run()


if __name__ == "__main__":
    main()
