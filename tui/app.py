import asyncio
import json
import sqlite3

import pyperclip
from textual.app import App, ComposeResult
from textual.binding import Binding
from textual.widgets import Footer, Header, Input

from tui.models import Call, Entry, Message
from tui.utils import DB_PATH, SOCKET_PATH, log
from tui.widgets import ComposeInput, EntryWidget, MessageList


class WaCLIApp(App):
    CSS = """
    Screen {
        layers: default above;
    }
    MessageList {
        height: 1fr;
        scrollbar-gutter: stable;
    }
    ComposeInput {
        layer: above;
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
        Binding("enter", "compose_send", "Send", show=False),
        Binding("r", "compose_reply", "Reply"),
        Binding("y", "copy_message", "Copy"),
    ]

    HALF_PAGE = 15

    def __init__(self) -> None:
        super().__init__()
        self.entries: list[Entry] = []
        self.selected_index: int = -1
        self.socket_writer: asyncio.StreamWriter | None = None
        self.compose_mode: str | None = None

    def compose(self) -> ComposeResult:
        yield Header()
        yield MessageList()
        yield ComposeInput(placeholder="Type your message...")
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
            messages.append(
                Message(
                    id=row["id"],
                    message_id=row["message_id"],
                    timestamp=row["timestamp"],
                    chat_jid=row["chat_jid"],
                    chat_name=row["chat_name"],
                    sender_jid=row["sender_jid"],
                    sender_name=row["sender_name"],
                    is_group=bool(row["is_group"]),
                    is_muted=bool(row["is_muted"]),
                    is_reply_to_me=bool(row["is_reply_to_me"]),
                    text=row["text"],
                )
            )

        calls: list[Entry] = []
        cursor.execute("SELECT * FROM calls")
        for row in cursor.fetchall():
            calls.append(
                Call(
                    id=row["id"],
                    timestamp=row["timestamp"],
                    call_id=row["call_id"],
                    caller_jid=row["caller_jid"],
                    caller_name=row["caller_name"],
                    is_group=bool(row["is_group"]),
                    group_jid=row["group_jid"],
                    group_name=row["group_name"],
                )
            )

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
        reader, writer = await asyncio.open_unix_connection(SOCKET_PATH)
        self.socket_writer = writer
        log("listen_socket: connected")
        while True:
            line = await reader.readline()
            log(f"listen_socket: got line: {line}")
            if not line:
                raise ConnectionError("Socket connection closed")
            event = json.loads(line.decode())
            entry_type = event["type"]
            data = event["data"]
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
                    message_id=data.get("message_id", ""),
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

    def action_copy_message(self) -> None:
        entry = self.get_selected_entry()
        if not entry or isinstance(entry, Call):
            return
        pyperclip.copy(entry.text)
        self.notify("Copied to clipboard")

    def get_selected_entry(self) -> Entry | None:
        if 0 <= self.selected_index < len(self.entries):
            return self.entries[self.selected_index]
        return None

    def action_compose_send(self) -> None:
        entry = self.get_selected_entry()
        if not entry:
            return
        if isinstance(entry, Call):
            return
        self.compose_mode = "send"
        compose_input = self.query_one(ComposeInput)
        compose_input.placeholder = f"Message to {entry.chat_name}..."
        compose_input.add_class("visible")
        compose_input.focus()

    def action_compose_reply(self) -> None:
        entry = self.get_selected_entry()
        if not entry:
            return
        if isinstance(entry, Call):
            return
        self.compose_mode = "reply"
        compose_input = self.query_one(ComposeInput)
        compose_input.placeholder = f"Reply to {entry.sender_name}..."
        compose_input.add_class("visible")
        compose_input.focus()

    def hide_compose(self) -> None:
        compose_input = self.query_one(ComposeInput)
        compose_input.value = ""
        compose_input.remove_class("visible")
        self.compose_mode = None

    async def on_input_submitted(self, event: Input.Submitted) -> None:
        text = event.value.strip()
        if not text:
            self.hide_compose()
            return

        entry = self.get_selected_entry()
        if not entry or isinstance(entry, Call):
            self.hide_compose()
            return

        if not self.socket_writer:
            self.notify("Not connected to socket", severity="error")
            self.hide_compose()
            return

        if self.compose_mode == "send":
            payload = {
                "action": "send",
                "chat_jid": entry.chat_jid,
                "text": text,
            }
        elif self.compose_mode == "reply":
            payload = {
                "action": "reply",
                "chat_jid": entry.chat_jid,
                "message_id": entry.message_id,
                "sender_jid": entry.sender_jid,
                "text": text,
            }
        else:
            self.hide_compose()
            return

        log(f"Sending: {payload}")
        self.socket_writer.write((json.dumps(payload) + "\n").encode())
        await self.socket_writer.drain()
        self.hide_compose()
