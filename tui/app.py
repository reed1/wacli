import asyncio
import base64
import json
import os
import re
import subprocess
import uuid

from collections import namedtuple
from functools import partial
from pathlib import Path

import pyperclip
from textual.app import App, ComposeResult
from textual.binding import Binding
from textual.command import DiscoveryHit, Hit, Provider
from textual.containers import Vertical
from textual.notifications import Notification, Notify
from textual.widgets import Footer, Header

from tui.models import Call, Entry, Message
from tui.utils import RUNTIME_DIR, log, log_submitted_message
from wacli_socket import SERVER_ADDR, enable_keepalive

CLIPBOARD_IMAGE_PATH = RUNTIME_DIR / "clipboard_send.png"
VIM_VIEW_PATH = RUNTIME_DIR / "message.txt"
from tui.widgets import (
    ComposeInput,
    ComposeQuote,
    EntryWidget,
    HelpScreen,
    ImageViewer,
    MessageList,
    MessageModal,
    SearchInput,
    StatusBar,
    format_entry_plain,
    format_entry_title,
    render_mentions,
    strip_mentions,
)

ChordBinding = namedtuple("ChordBinding", ["keys", "action", "description"])

SOCKET_READ_LIMIT = 1024 * 1024
IMAGE_EXTS = {".jpg", ".jpeg", ".png", ".webp", ".gif"}
VIDEO_EXTS = {".mp4", ".3gp", ".mov", ".webm", ".mkv"}
CLIPBOARD_TIMEOUT = 5

# Tells the wacli-tui wrapper to offer a restart rather than treat this as a crash.
EXIT_DISCONNECTED = 75

REMOVE_REACTION_ID = "🚫"
REACTION_EMOJIS = json.loads((Path(__file__).parent / "reaction_emojis.json").read_text())
REACTION_EMOJIS.append({"id": REMOVE_REACTION_ID, "label": "remove clear reaction"})


class MediaAssembler:
    # Chunks land in a .part file that is renamed into place only once the transfer
    # completes, so an interrupted download can never be mistaken for a cached one.
    def __init__(self, future: asyncio.Future, path: Path) -> None:
        self.future = future
        self.path = path
        self.partial = path.with_name(path.name + ".part")
        self.file = None
        self.next_seq = 0

    def handle_event(self, event: dict) -> None:
        if event.get("error"):
            self._fail(event["error"])
            return

        seq = event.get("seq", 0)
        if seq != self.next_seq:
            self._fail(f"out-of-order chunk: expected {self.next_seq}, got {seq}")
            return
        self.next_seq += 1

        chunk_b64 = event.get("data", "")
        if chunk_b64:
            if self.file is None:
                self.file = open(self.partial, "wb")
            self.file.write(base64.b64decode(chunk_b64))

        if event.get("done"):
            if self.file is not None:
                self.file.close()
                self.file = None
            else:
                self.partial.write_bytes(b"")
            self.partial.replace(self.path)
            if not self.future.done():
                self.future.set_result(None)

    def _fail(self, msg: str) -> None:
        if self.file is not None:
            self.file.close()
            self.file = None
        self.partial.unlink(missing_ok=True)
        if not self.future.done():
            self.future.set_exception(RuntimeError(msg))


def is_image_message(entry: Entry) -> bool:
    return (
        isinstance(entry, Message)
        and bool(entry.media_file)
        and Path(entry.media_file).suffix.lower() in IMAGE_EXTS
    )


def message_from_data(data: dict) -> Message:
    return Message(
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
        media_file=data.get("media_file"),
        transcription=data.get("transcription"),
        is_from_me=data.get("is_from_me", False),
        original_text=data.get("original_text"),
        is_deleted=data.get("is_deleted", False),
    )


def call_from_data(data: dict) -> Call:
    return Call(
        id=data.get("id", 0),
        timestamp=data["timestamp"],
        call_id=data["call_id"],
        caller_jid=data["caller_jid"],
        caller_name=data["caller_name"],
        is_group=data["is_group"],
        group_jid=data["group_jid"],
        group_name=data["group_name"],
    )


PaletteCommand = namedtuple("PaletteCommand", ["title", "action", "help"])


class WaCLICommands(Provider):
    @property
    def _commands(self) -> list[PaletteCommand]:
        return self.app.PALETTE_COMMANDS

    async def discover(self):
        for cmd in self._commands:
            yield DiscoveryHit(cmd.title, partial(self.app.run_action, cmd.action), help=cmd.help)

    async def search(self, query: str):
        matcher = self.matcher(query)
        for cmd in self._commands:
            score = matcher.match(cmd.title)
            if score > 0:
                yield Hit(
                    score,
                    matcher.highlight(cmd.title),
                    partial(self.app.run_action, cmd.action),
                    help=cmd.help,
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
        scrollbar-size-vertical: 1;
        /* Rest the rows on the bottom edge while they are too few to fill the pane.
           Textual writes an anchored container's scroll offset unclamped, so a
           top-aligned short list is left at a negative offset that pushes later rows
           below the fold, behind the footer. Bottom alignment keeps the offset at 0. */
        align-vertical: bottom;
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
    #bottom-bars {
        dock: bottom;
        height: auto;
    }
    #bottom-bars Footer {
        dock: none;
    }
    """

    COMMANDS = {WaCLICommands}
    PALETTE_COMMANDS = [
        PaletteCommand("Send message", "compose_send", "Compose a message to the selected chat"),
        PaletteCommand("Reply", "compose_reply", "Reply to the selected message"),
        PaletteCommand("React", "react", "Send an emoji reaction to the selected message"),
        PaletteCommand("Send image", "send_image", "Send the clipboard image to the selected chat"),
        PaletteCommand("Copy", "copy_message", "Copy the selected message text or image"),
        PaletteCommand("Open in Vim", "open_in_vim", "View the selected message text in Vim"),
        PaletteCommand("View", "show_message", "Open the selected message or its media"),
        PaletteCommand("Open URL", "open_url", "Open links found in the selected message"),
        PaletteCommand("Search", "open_search", "Search messages"),
        PaletteCommand("Top", "select_first", "Jump to the first entry"),
        PaletteCommand("Bottom", "select_last", "Jump to the last entry"),
        PaletteCommand("Half page down", "half_page_down", "Move the selection down half a page"),
        PaletteCommand("Half page up", "half_page_up", "Move the selection up half a page"),
        PaletteCommand("Quit", "quit", "Exit wacli"),
    ]

    BINDINGS = [
        Binding("q", "quit", "Quit"),
        Binding("space", "command_palette", "Commands"),
        Binding("j", "select_next", "Down", show=False),
        Binding("k", "select_prev", "Up", show=False),
        Binding("G", "select_last", "Bottom", show=False),
        Binding("ctrl+d", "half_page_down", "Half Page Down", show=False),
        Binding("ctrl+u", "half_page_up", "Half Page Up", show=False),
        Binding("enter", "compose_send", "Send", show=False),
        Binding("r", "compose_reply", "Reply"),
        Binding("m", "react", "React"),
        Binding("I", "send_image", "Send image"),
        Binding("y", "copy_message", "Copy"),
        Binding("v", "open_in_vim", "Vim"),
        Binding("H", "show_message", "View"),
        Binding("slash", "open_search", "Search"),
        Binding("n", "search_next", "Next match", show=False),
        Binding("N", "search_prev", "Prev match", show=False),
        Binding("escape", "clear_search", "Clear search", show=False),
        Binding("question_mark", "show_help", "Keys"),
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
        self.pending_image_chat: tuple[str, str] | None = None
        self.pending_image_toast: Notification | None = None
        self.media_assemblers: dict[str, MediaAssembler] = {}
        self._chord_buf: str = ""
        self._chord_timer: asyncio.TimerHandle | None = None
        self._chord_prefixes: set[str] = set()
        self._chord_map: dict[str, str] = {}
        self.search_query: str = ""
        self.search_matches: list[int] = []
        self.search_index: int = -1
        self.status_text: str = ""
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
        # Docking each bar separately would stack them on the same row, so the
        # footer would paint over the status/search line.
        yield Vertical(SearchInput(), StatusBar(""), Footer(), id="bottom-bars")

    async def on_mount(self) -> None:
        log("on_mount: start")
        self.title = "WhatsApp Messages"
        self.set_status("")
        log("on_mount: starting worker")
        self.run_worker(self.listen_socket(), exclusive=True)

    async def render_entries(self) -> None:
        message_list = self.query_one(MessageList)
        await message_list.remove_children()
        if self.entries:
            self.selected_index = len(self.entries) - 1
        # Keep the list hidden until it is scrolled into place so the initial
        # top-anchored layout and the jump to the bottom never get painted.
        message_list.visible = False
        # Anchoring pins the scroll to the bottom on every layout pass, including the
        # last one. A scroll_end here instead would read max_scroll_y before the rows
        # have been arranged and strand the list at the top.
        message_list.anchor()
        await message_list.mount_all(
            [
                EntryWidget(entry, selected=(i == self.selected_index))
                for i, entry in enumerate(self.entries)
            ]
        )
        self.call_after_refresh(self.reveal_entries)

    def reveal_entries(self) -> None:
        # The anchor is left in place; Textual releases it as soon as the selection
        # scrolls the list, so it only ever governs the untouched startup view.
        self.query_one(MessageList).visible = True

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
        # The socket is the only source of entries, so once it is gone the view is a
        # stale snapshot. Quit and let the wacli-tui wrapper offer a restart.
        try:
            await self.stream_events()
        except OSError as error:
            log(f"stream_events: disconnected: {error}")
            self.exit(return_code=EXIT_DISCONNECTED, message=f"Disconnected: {error}")

    async def stream_events(self) -> None:
        log("stream_events: connecting...")
        reader, writer = await asyncio.open_connection(*SERVER_ADDR, limit=SOCKET_READ_LIMIT)
        # Without keepalive a peer that vanished without a FIN — server restart,
        # dropped VPN route — leaves this socket ESTABLISHED and silent forever.
        enable_keepalive(writer.get_extra_info("socket"))
        self.socket_writer = writer
        log("stream_events: connected, requesting entries")
        writer.write(b'{"action":"get_entries"}\n')
        await writer.drain()
        while True:
            line = await reader.readline()
            log(f"stream_events: got line: {line}")
            if not line:
                raise ConnectionError("server closed the connection")
            event = json.loads(line.decode())
            entry_type = event["type"]

            if entry_type == "response":
                request_id = event["request_id"]
                if request_id == self.pending_request_id:
                    self.pending_request_id = None
                    if event["success"]:
                        self.hide_compose()
                        if self.pending_image_toast is not None:
                            self.dismiss_image_toast()
                            self.notify("Image sent")
                    else:
                        self.dismiss_image_toast()
                        self.exit(
                            return_code=1,
                            message=f"Send failed: {event.get('error', 'unknown error')}",
                        )
                continue

            if entry_type == "media":
                request_id = event.get("request_id", "")
                assembler = self.media_assemblers.get(request_id)
                if assembler is None:
                    continue
                assembler.handle_event(event)
                if assembler.future.done():
                    self.media_assemblers.pop(request_id, None)
                continue

            if entry_type == "connection_state":
                state = event.get("data") or {}
                log(
                    f"stream_events: connection_state connected={state.get('connected')} "
                    f"reason={state.get('reason', '')}"
                )
                continue

            data = event["data"]
            if entry_type == "entries":
                self.load_entries_from_data(data)
                await self.render_entries()
                continue

            if entry_type == "message_updated":
                self.apply_message_update(message_from_data(data))
                continue

            entry: Entry
            if entry_type == "call":
                entry = call_from_data(data)
                log(f"stream_events: parsed call from {entry.caller_name}")
            elif entry_type == "message":
                entry = message_from_data(data)
                log(f"stream_events: parsed message: {entry.text}")
            else:
                raise ValueError(f"Unexpected entry type: {entry_type}")
            self.entries.append(entry)
            message_list = self.query_one(MessageList)
            force_follow = isinstance(entry, Message) and entry.is_from_me
            should_follow = force_follow or self.selected_index == len(self.entries) - 2
            message_list.mount(EntryWidget(entry, selected=should_follow))
            if should_follow:
                self.call_after_refresh(lambda: self.update_selection(len(self.entries) - 1))
            log("stream_events: widget mounted")

    def apply_message_update(self, message: Message) -> None:
        index = next(
            (
                i
                for i, entry in enumerate(self.entries)
                if isinstance(entry, Message) and entry.message_id == message.message_id
            ),
            None,
        )
        if index is None:
            log(f"apply_message_update: {message.message_id} not in view, ignoring")
            return
        self.entries[index] = message
        widgets = list(self.query(EntryWidget))
        if index < len(widgets):
            widgets[index].entry = message
            widgets[index].refresh()
        log(f"apply_message_update: updated {message.message_id} deleted={message.is_deleted}")

    def load_entries_from_data(self, data: dict) -> None:
        entries: list[Entry] = []
        for item in data.get("entries") or []:
            kind = item["kind"]
            if kind == "message":
                entries.append(message_from_data(item["message"]))
            elif kind == "call":
                entries.append(call_from_data(item["call"]))
            else:
                raise ValueError(f"Unexpected entry kind: {kind}")
        self.entries = entries
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
        if is_image_message(entry):
            self.run_worker(self.copy_image(entry))
            return
        pyperclip.copy(strip_mentions(entry.display_text))
        self.notify("Copied to clipboard")

    async def copy_image(self, entry: Message) -> None:
        local_path = await self.fetch_media(entry.media_file)
        if local_path is None:
            return
        # The clipboard carries a file:// pointer, not the pixels: a photo runs to megabytes
        # and every paste target worth having reads text/uri-list. copyq is the only local
        # tool that can advertise several targets at once, so text/plain rides along and a
        # captioned image still pastes its caption into a text field.
        caption = strip_mentions(entry.display_text).strip()
        command = [
            "copyq",
            "copy",
            "text/uri-list",
            local_path.as_uri(),
            "text/plain",
            caption or str(local_path),
        ]
        try:
            subprocess.run(command, capture_output=True, check=True, timeout=CLIPBOARD_TIMEOUT)
        except FileNotFoundError:
            self.notify("copyq not found", severity="error")
            return
        except (subprocess.CalledProcessError, subprocess.TimeoutExpired):
            self.notify("Could not set clipboard", severity="error")
            return
        self.notify("Copied image and caption" if caption else "Copied image")

    def action_open_in_vim(self) -> None:
        entry = self.get_selected_entry()
        if not entry or isinstance(entry, Call):
            return
        text = strip_mentions(entry.display_text)
        if not text.strip():
            self.notify("No text to open")
            return

        window_id = os.environ.get("KITTY_WINDOW_ID")
        if not window_id:
            self.notify("Not running inside kitty", severity="error")
            return

        VIM_VIEW_PATH.write_text(text.rstrip("\n") + "\n")
        # -M leaves the buffer unmodifiable and unwritable so the view stays read-only;
        # -n skips the swap file, which would otherwise prompt for recovery whenever this
        # reused path is opened twice.
        command = [
            "kitty",
            "@",
            "launch",
            "--type=overlay",
            "--next-to",
            f"id:{window_id}",
            "--title",
            f"wacli: {entry.title}",
            "vim",
            "-M",
            "-n",
            str(VIM_VIEW_PATH),
        ]
        try:
            result = subprocess.run(command, capture_output=True, timeout=5)
        except FileNotFoundError:
            self.notify("kitty not found", severity="error")
            return
        except subprocess.TimeoutExpired:
            self.notify("kitty overlay timed out", severity="error")
            return

        if result.returncode != 0:
            self.notify(f"Vim overlay failed: {result.stderr.decode().strip()}", severity="error")

    def action_open_url(self) -> None:
        entry = self.get_selected_entry()
        if not entry or isinstance(entry, Call):
            return
        urls = re.findall(r"https?://[^\s<>\[\]]+", strip_mentions(entry.display_text))
        if not urls:
            self.notify("No URL found")
            return
        for url in urls:
            subprocess.Popen(
                ["xdg-open", url], stdout=subprocess.DEVNULL, stderr=subprocess.DEVNULL
            )
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
        quote_text = strip_mentions(entry.display_text).replace("\n", " ").replace("[", "\\[")
        quote.update(quote_text)
        quote.add_class("visible")
        compose_input = self.query_one(ComposeInput)
        compose_input.border_title = f"Reply to {entry.sender_name}"
        compose_input.add_class("visible")
        compose_input.focus()

    def action_react(self) -> None:
        entry = self.get_selected_entry()
        if not entry or isinstance(entry, Call):
            return
        if not self.socket_writer:
            self.exit(return_code=1, message="Not connected to socket")
            return
        self.run_worker(self.pick_and_send_reaction(entry))

    async def pick_and_send_reaction(self, entry: Message) -> None:
        menu = "\n".join(f"{e['id']} {e['label']}" for e in REACTION_EMOJIS)
        try:
            proc = await asyncio.create_subprocess_exec(
                "rofi",
                "-dmenu",
                "-i",
                "-p",
                "React",
                stdin=asyncio.subprocess.PIPE,
                stdout=asyncio.subprocess.PIPE,
                stderr=asyncio.subprocess.DEVNULL,
            )
        except FileNotFoundError:
            self.notify("rofi not found", severity="error")
            return

        stdout, _ = await proc.communicate(menu.encode())
        selection = stdout.decode().strip()
        if not selection:
            return

        emoji = selection.split(maxsplit=1)[0]
        if emoji == REMOVE_REACTION_ID:
            emoji = ""

        request_id = str(uuid.uuid4())
        self.pending_request_id = request_id
        payload = {
            "action": "react",
            "request_id": request_id,
            "chat_jid": entry.chat_jid,
            "message_id": entry.message_id,
            "sender_jid": entry.sender_jid,
            "text": emoji,
        }
        log(f"Reacting to {entry.message_id} in {entry.chat_jid} with {emoji!r}")
        self.socket_writer.write((json.dumps(payload) + "\n").encode())
        await self.socket_writer.drain()
        self.notify("Reaction removed" if emoji == "" else f"Reacted {emoji}")

    def action_send_image(self) -> None:
        entry = self.get_selected_entry()
        if not entry or isinstance(entry, Call):
            return
        if not self.socket_writer:
            self.exit(return_code=1, message="Not connected to socket")
            return

        try:
            result = subprocess.run(
                ["xclip", "-selection", "clipboard", "-t", "image/png", "-o"],
                capture_output=True,
                timeout=CLIPBOARD_TIMEOUT,
            )
        except FileNotFoundError:
            self.notify("xclip not found", severity="error")
            return
        except subprocess.TimeoutExpired:
            self.notify("Reading clipboard timed out", severity="error")
            return

        if result.returncode != 0 or not result.stdout:
            self.notify("No image in clipboard", severity="error")
            return

        CLIPBOARD_IMAGE_PATH.write_bytes(result.stdout)
        self.pending_image_chat = (entry.chat_jid, entry.chat_name)
        self.open_image_viewer(
            CLIPBOARD_IMAGE_PATH, prompt=f"Send this image to {entry.chat_name}? \\[Y/n]"
        )

    def open_image_viewer(self, path: Path, prompt: str | None = None, caption: str = "") -> None:
        self.close_image_viewer()
        viewer = ImageViewer(path, prompt, caption)
        self.mount(viewer)
        self.call_after_refresh(viewer.focus)

    def close_image_viewer(self) -> None:
        for viewer in self.query(ImageViewer):
            viewer.remove()
        self.set_focus(None)

    def confirm_send_image(self) -> None:
        self.close_image_viewer()
        pending = self.pending_image_chat
        self.pending_image_chat = None
        if not pending or not self.socket_writer:
            return

        chat_jid, chat_name = pending
        image_b64 = base64.b64encode(CLIPBOARD_IMAGE_PATH.read_bytes()).decode()
        request_id = str(uuid.uuid4())
        self.pending_request_id = request_id
        payload = {
            "action": "send_image",
            "request_id": request_id,
            "chat_jid": chat_jid,
            "image_data": image_b64,
        }
        log(f"Sending image to {chat_jid} ({len(image_b64)} b64 chars)")
        log_submitted_message(chat_jid, "[image]", "send_image")
        self.socket_writer.write((json.dumps(payload) + "\n").encode())
        self.run_worker(self.socket_writer.drain())
        self.pending_image_toast = Notification(f"Sending image to {chat_name}…", timeout=60)
        self.post_message(Notify(self.pending_image_toast))

    def dismiss_image_toast(self) -> None:
        if self.pending_image_toast is not None:
            self._unnotify(self.pending_image_toast)
            self.pending_image_toast = None

    def cancel_send_image(self) -> None:
        self.close_image_viewer()
        self.pending_image_chat = None
        self.notify("Image send cancelled")

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

    def action_show_message(self) -> None:
        entry = self.get_selected_entry()
        if not entry or isinstance(entry, Call):
            return
        # An image carries its text as a caption inside the viewer, so it replaces the
        # text modal rather than stacking on top of it.
        if is_image_message(entry):
            caption = render_mentions(entry.display_text.replace("\n", " "))
            self.run_worker(self.open_media(entry.media_file, caption))
            return
        if entry.display_text.strip():
            body = render_mentions(entry.display_text)
            if entry.is_deleted:
                body = f"[dim italic]🗑 deleted[/]\n\n{body}"
            if entry.is_edited:
                body = (
                    f"{body}\n\n[dim italic]✎ original:[/]\n{render_mentions(entry.original_text)}"
                )
            modal = self.query_one(MessageModal)
            modal.border_title = f"[bold cyan]{format_entry_title(entry)}[/]"
            modal.border_subtitle = f"[dim]{entry.formatted_time}[/]"
            modal.update(body)
            modal.add_class("visible")
            modal.focus()
        if entry.media_file:
            self.run_worker(self.open_media(entry.media_file))

    async def fetch_media(self, media_file: str) -> Path | None:
        """Downloads media into RUNTIME_DIR once. Server filenames are content-stable
        UUIDs, so an existing file is always the right bytes and needs no refetch."""
        local_path = RUNTIME_DIR / media_file
        if local_path.exists():
            return local_path

        request_id = str(uuid.uuid4())
        future: asyncio.Future = asyncio.get_event_loop().create_future()
        self.media_assemblers[request_id] = MediaAssembler(future, local_path)

        try:
            payload = json.dumps(
                {"action": "get_media", "request_id": request_id, "filename": media_file}
            )
            self.socket_writer.write((payload + "\n").encode())
            await self.socket_writer.drain()
            await future
        except Exception:
            self.media_assemblers.pop(request_id, None)
            self.notify("Media could not be fetched", severity="error")
            return None

        return local_path

    async def open_media(self, media_file: str, caption: str = "") -> None:
        local_path = RUNTIME_DIR / media_file
        ext = local_path.suffix.lower()
        if ext in IMAGE_EXTS:
            kind = "image"
        elif ext in VIDEO_EXTS:
            kind = "video"
        else:
            self.notify(f"No viewer for {ext or 'unknown type'}", severity="error")
            return

        if await self.fetch_media(media_file) is None:
            return

        if kind == "image":
            self.open_image_viewer(local_path, caption=caption)
        elif kind == "video":
            subprocess.Popen(
                ["mpv", str(local_path)], stdout=subprocess.DEVNULL, stderr=subprocess.DEVNULL
            )
        else:
            raise ValueError(f"Unexpected media kind: {kind}")

    def hide_message_modal(self) -> None:
        modal = self.query_one(MessageModal)
        modal.remove_class("visible")

    def set_status(self, text: str) -> None:
        self.status_text = text
        status = self.query_one(StatusBar)
        status.update(text)
        status.set_class(not text, "hidden")

    def action_open_search(self) -> None:
        if not self.entries:
            return
        search = self.query_one(SearchInput)
        search.reset()
        self.query_one(StatusBar).add_class("hidden")
        search.add_class("visible")
        search.focus()

    def hide_search(self) -> None:
        search = self.query_one(SearchInput)
        search.remove_class("visible")
        self.query_one(StatusBar).set_class(not self.status_text, "hidden")
        self.set_focus(None)

    def run_search(self, query: str) -> None:
        query = query.strip()
        self.hide_search()
        if not query:
            self.clear_search()
            return
        needle = query.lower()
        matches = [
            i for i, entry in enumerate(self.entries) if needle in format_entry_plain(entry).lower()
        ]
        self.search_query = query
        self.search_matches = matches
        for widget in self.query(EntryWidget):
            widget.refresh()
        if not matches:
            self.search_index = -1
            self.set_status(f"0/0 for '{query}'")
            return
        cur = self.selected_index
        idx = next(
            (k for k in range(len(matches) - 1, -1, -1) if matches[k] <= cur), len(matches) - 1
        )
        self.search_index = idx
        self.update_selection(matches[idx])
        self.set_status(f"{idx + 1}/{len(matches)} for '{query}'")

    def clear_search(self) -> None:
        if not self.search_query and not self.search_matches:
            return
        self.search_query = ""
        self.search_matches = []
        self.search_index = -1
        self.set_status("")
        for widget in self.query(EntryWidget):
            widget.refresh()

    def _step_search(self, delta: int) -> None:
        if not self.search_matches:
            self.set_status("No active search")
            return
        n = len(self.search_matches)
        self.search_index = (self.search_index + delta) % n
        self.update_selection(self.search_matches[self.search_index])
        self.set_status(f"{self.search_index + 1}/{n} for '{self.search_query}'")

    def action_show_help(self) -> None:
        if isinstance(self.screen, HelpScreen):
            return
        shortcuts = [
            (self.get_key_display(binding), binding.description) for binding in self.BINDINGS
        ]
        shortcuts += [(cb.keys, cb.description) for cb in self.CHORD_BINDINGS]
        self.push_screen(HelpScreen(shortcuts))

    def action_clear_search(self) -> None:
        self.clear_search()

    def action_search_next(self) -> None:
        self._step_search(-1)

    def action_search_prev(self) -> None:
        self._step_search(1)
