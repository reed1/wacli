import asyncio
import json
import re
import subprocess
import uuid

from collections import namedtuple

import pyperclip
from textual.app import App, ComposeResult
from textual.binding import Binding
from textual.widgets import Footer, Header

ChordBinding = namedtuple("ChordBinding", ["keys", "action", "description"])

MAX_ENTRIES = 200

from tui.models import Call, Entry, Message
from tui.utils import RUNTIME_DIR, SERVER_ADDR, log, log_submitted_message
from tui.widgets import (
    ComposeInput,
    ComposeQuote,
    EntryWidget,
    MessageList,
    MessageModal,
    format_entry_plain,
    render_mentions,
    strip_mentions,
)


class WaCLIApp(App):
    CSS = """
    Screen {
        layers: default above;
        align: center middle;
    }
    MessageList {
        height: 1fr;
        width: 100%;
        scrollbar-gutter: stable;
    }
    ComposeQuote {
        layer: above;
        width: 80%;
    }
    ComposeInput {
        layer: above;
        width: 80%;
    }
    MessageModal {
        layer: above;
    }
    """

    BINDINGS = [
        Binding("q", "quit", "Quit"),
        Binding("j", "select_next", "Down", show=False),
        Binding("k", "select_prev", "Up", show=False),
        Binding("G", "select_last", "Bottom", show=False),
        Binding("ctrl+d", "half_page_down", "Half Page Down", show=False),
        Binding("ctrl+u", "half_page_up", "Half Page Up", show=False),
        Binding("enter", "compose_send", "Send", show=False),
        Binding("r", "compose_reply", "Reply"),
        Binding("y", "copy_message", "Copy"),
        Binding("H", "show_message", "View"),
        Binding("slash", "search_nvim", "Search"),
    ]

    HALF_PAGE = 15
    CHORD_TIMEOUT = 1.0
    CHORD_BINDINGS = [
        ChordBinding("gg", "select_first", "Top"),
        ChordBinding("gx", "open_url", "Open URL"),
    ]

    def __init__(self) -> None:
        super().__init__()
        self.entries: list[Entry] = []
        self.selected_index: int = -1
        self.socket_writer: asyncio.StreamWriter | None = None
        self.compose_mode: str | None = None
        self.pending_request_id: str | None = None
        self._chord_buf: str = ""
        self._chord_timer: asyncio.TimerHandle | None = None
        self._chord_prefixes: set[str] = set()
        self._chord_map: dict[str, str] = {}
        for cb in self.CHORD_BINDINGS:
            self._chord_map[cb.keys] = cb.action
            for i in range(1, len(cb.keys)):
                self._chord_prefixes.add(cb.keys[:i])

    def compose(self) -> ComposeResult:
        yield Header()
        yield MessageList()
        yield ComposeQuote()
        yield ComposeInput()
        yield MessageModal()
        yield Footer()

    async def on_mount(self) -> None:
        log("on_mount: start")
        self.title = "WhatsApp Messages"
        log("on_mount: starting worker")
        self.run_worker(self.listen_socket(), exclusive=True)

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
        reader, writer = await asyncio.open_connection(*SERVER_ADDR, limit=1024 * 1024)
        self.socket_writer = writer
        log("listen_socket: connected, requesting entries")
        writer.write(b'{"action":"get_entries"}\n')
        await writer.drain()
        while True:
            line = await reader.readline()
            log(f"listen_socket: got line: {line}")
            if not line:
                raise ConnectionError("Socket connection closed")
            event = json.loads(line.decode())
            entry_type = event["type"]

            if entry_type == "response":
                request_id = event["request_id"]
                if request_id == self.pending_request_id:
                    self.pending_request_id = None
                    if event["success"]:
                        self.hide_compose()
                    else:
                        self.exit(return_code=1, message=f"Send failed: {event.get('error', 'unknown error')}")
                continue

            data = event["data"]
            if entry_type == "entries":
                self.load_entries_from_data(data)
                self.render_entries()
                continue
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
                    message_type=data.get("message_type", ""),
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

    def load_entries_from_data(self, data: dict) -> None:
        messages: list[Entry] = []
        for msg in data.get("messages") or []:
            messages.append(
                Message(
                    id=msg.get("id", 0),
                    message_id=msg.get("message_id", ""),
                    timestamp=msg["timestamp"],
                    chat_jid=msg["chat_jid"],
                    chat_name=msg["chat_name"],
                    sender_jid=msg["sender_jid"],
                    sender_name=msg["sender_name"],
                    is_group=msg["is_group"],
                    is_muted=msg["is_muted"],
                    is_reply_to_me=msg["is_reply_to_me"],
                    message_type=msg.get("message_type", ""),
                    text=msg["text"],
                )
            )
        calls: list[Entry] = []
        for call in data.get("calls") or []:
            calls.append(
                Call(
                    id=call.get("id", 0),
                    timestamp=call["timestamp"],
                    call_id=call["call_id"],
                    caller_jid=call["caller_jid"],
                    caller_name=call["caller_name"],
                    is_group=call["is_group"],
                    group_jid=call["group_jid"],
                    group_name=call["group_name"],
                )
            )
        merged = sorted(messages + calls, key=lambda e: e.timestamp)
        self.entries = merged[-MAX_ENTRIES:]
        log(f"load_entries_from_data: loaded {len(self.entries)} entries")

    def action_select_next(self) -> None:
        self.update_selection(self.selected_index + 1)

    def action_select_prev(self) -> None:
        self.update_selection(self.selected_index - 1)

    def _reset_chord(self) -> None:
        if self._chord_timer is not None:
            self._chord_timer.cancel()
            self._chord_timer = None
        self._chord_buf = ""

    def on_key(self, event) -> None:
        candidate = self._chord_buf + event.key
        if candidate in self._chord_map:
            action = self._chord_map[candidate]
            self._reset_chord()
            event.prevent_default()
            event.stop()
            getattr(self, f"action_{action}")()
            return
        if candidate in self._chord_prefixes:
            self._reset_chord()
            event.prevent_default()
            event.stop()
            self._chord_buf = candidate
            loop = asyncio.get_event_loop()
            self._chord_timer = loop.call_later(self.CHORD_TIMEOUT, self._reset_chord)
            return
        if self._chord_buf:
            self._reset_chord()

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
        pyperclip.copy(strip_mentions(entry.text))
        self.notify("Copied to clipboard")

    def action_open_url(self) -> None:
        entry = self.get_selected_entry()
        if not entry or isinstance(entry, Call):
            return
        urls = re.findall(r"https?://[^\s<>\[\]]+", strip_mentions(entry.text))
        if not urls:
            self.notify("No URL found")
            return
        for url in urls:
            subprocess.Popen(["xdg-open", url], stdout=subprocess.DEVNULL, stderr=subprocess.DEVNULL)
        self.notify(f"Opened {len(urls)} URL(s)")

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
        compose_input.border_title = f"Message to {entry.chat_name}"
        compose_input.add_class("visible")
        compose_input.focus()

    def action_compose_reply(self) -> None:
        entry = self.get_selected_entry()
        if not entry:
            return
        if isinstance(entry, Call):
            return
        self.compose_mode = "reply"
        quote = self.query_one(ComposeQuote)
        quote.border_title = "Quote"
        quote_text = strip_mentions(entry.text).replace("\n", " ").replace("[", "\\[")
        quote.update(quote_text)
        quote.add_class("visible")
        compose_input = self.query_one(ComposeInput)
        compose_input.border_title = f"Reply to {entry.sender_name}"
        compose_input.add_class("visible")
        compose_input.focus()

    def hide_compose(self) -> None:
        compose_input = self.query_one(ComposeInput)
        compose_input.clear()
        compose_input.remove_class("visible")
        self.query_one(ComposeQuote).remove_class("visible")
        self.compose_mode = None

    async def submit_compose(self) -> None:
        compose_input = self.query_one(ComposeInput)
        text = compose_input.text.strip()
        if not text:
            self.hide_compose()
            return

        entry = self.get_selected_entry()
        if not entry or isinstance(entry, Call):
            self.hide_compose()
            return

        if not self.socket_writer:
            self.exit(return_code=1, message="Not connected to socket")
            return

        request_id = str(uuid.uuid4())
        self.pending_request_id = request_id

        if self.compose_mode == "send":
            payload = {
                "action": "send",
                "request_id": request_id,
                "chat_jid": entry.chat_jid,
                "text": text,
            }
        elif self.compose_mode == "reply":
            payload = {
                "action": "reply",
                "request_id": request_id,
                "chat_jid": entry.chat_jid,
                "message_id": entry.message_id,
                "sender_jid": entry.sender_jid,
                "text": text,
            }
        else:
            self.hide_compose()
            return

        log(f"Sending: {payload}")
        log_submitted_message(payload["chat_jid"], text, payload["action"])
        self.socket_writer.write((json.dumps(payload) + "\n").encode())
        await self.socket_writer.drain()

    def action_search_nvim(self) -> None:
        if not self.entries:
            return
        tmp = RUNTIME_DIR / "messages.txt"
        with open(tmp, "w") as f:
            for entry in self.entries:
                f.write(format_entry_plain(entry) + "\n")
        with self.suspend():
            subprocess.run(["nvim", "+$", "+call feedkeys('?')", str(tmp)])

    def action_show_message(self) -> None:
        entry = self.get_selected_entry()
        if not entry or isinstance(entry, Call):
            return
        modal = self.query_one(MessageModal)
        modal.update(render_mentions(entry.text))
        modal.add_class("visible")
        modal.focus()

    def hide_message_modal(self) -> None:
        modal = self.query_one(MessageModal)
        modal.remove_class("visible")
