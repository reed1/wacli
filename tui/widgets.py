import re
from typing import TYPE_CHECKING

from textual.binding import Binding
from textual.containers import ScrollableContainer
from textual.widgets import Static, TextArea

from tui.models import Call, Entry, Message

MENTION_RE = re.compile(r'<mention jid="[^"]*" name="([^"]*)"/>')


def render_mentions(text: str) -> str:
    parts = MENTION_RE.split(text)
    result = []
    for i, part in enumerate(parts):
        if i % 2 == 0:
            result.append(part.replace("[", "\\["))
        else:
            result.append(f"[bold green]@{part.replace("[", "\\[")}[/]")
    return "".join(result)


def strip_mentions(text: str) -> str:
    return MENTION_RE.sub(r"@\1", text)


def format_entry_plain(entry: "Entry") -> str:
    if isinstance(entry, Message):
        text = strip_mentions(entry.text).replace("\n", " ")
        type_prefix = f"[{entry.message_type}] " if entry.message_type else ""
        if entry.is_group:
            return f"[{entry.formatted_time}] {entry.title} | {entry.chat_name}: {type_prefix}{text}"
        return f"[{entry.formatted_time}] {entry.title}: {type_prefix}{text}"
    return f"[{entry.formatted_time}] {entry.title}: Incoming call"


if TYPE_CHECKING:
    from tui.app import WaCLIApp


class EntryWidget(Static):
    DEFAULT_CSS = """
    EntryWidget {
        height: 1;
        overflow: hidden;
        text-overflow: ellipsis;
        text-wrap: nowrap;
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
            text_oneline = render_mentions(msg.text.replace("\n", " "))
            if msg.message_type:
                content = f"[dim]\\[{msg.message_type}][/] {text_oneline}"
            else:
                content = text_oneline
            # Don't use rich.markup.escape() — it skips uppercase tags like [RUN]
            sender = msg.title.replace("[", "\\[")
            if msg.is_group:
                chat = msg.chat_name.replace("[", "\\[")
                title = f"{sender} [bold magenta]👥[/] [magenta]{chat}[/]"
            else:
                title = sender
            return f"{indicator} [dim]{msg.formatted_time}[/][bold cyan] {title}[/]: {content}"
        call = self.entry
        return f"{indicator} [dim]{call.formatted_time}[/][bold yellow] 📞 {call.title}[/]: Incoming call"


class MessageList(ScrollableContainer):
    pass


class ComposeQuote(Static):
    DEFAULT_CSS = """
    ComposeQuote {
        display: none;
        width: 60%;
        height: auto;
        padding: 0 1;
        color: $text;
        background: $surface;
        border: tall $accent;
        overflow: hidden;
        text-overflow: ellipsis;
        text-wrap: nowrap;
    }
    ComposeQuote.visible {
        display: block;
    }
    """


class ComposeInput(TextArea):
    DEFAULT_CSS = """
    ComposeInput {
        display: none;
        width: 60%;
        height: auto;
        max-height: 12;
        border: tall $primary;
        background: $surface;
    }
    ComposeInput.visible {
        display: block;
    }
    """

    BINDINGS = [
        Binding("escape", "cancel", "Cancel", show=False),
    ]

    app: "WaCLIApp"

    async def _on_key(self, event) -> None:
        if event.key == "escape":
            event.stop()
            event.prevent_default()
            self.action_cancel()
            return
        if event.key == "enter":
            event.stop()
            event.prevent_default()
            await self.app.submit_compose()
            return
        if event.key == "ctrl+j":
            event.stop()
            event.prevent_default()
            start, end = self.selection
            self._replace_via_keyboard("\n", start, end)
            return
        await super()._on_key(event)

    def action_cancel(self) -> None:
        self.app.hide_compose()


class MessageModal(Static):
    DEFAULT_CSS = """
    MessageModal {
        display: none;
        width: 80%;
        max-height: 80%;
        padding: 1 2;
        border: tall $primary;
        background: $surface;
        overflow-y: auto;
    }
    MessageModal.visible {
        display: block;
    }
    """

    BINDINGS = [
        Binding("escape", "dismiss", "Close", show=False),
        Binding("q", "dismiss", "Close", show=False),
    ]

    app: "WaCLIApp"

    def __init__(self) -> None:
        super().__init__()
        self.can_focus = True

    def action_dismiss(self) -> None:
        self.app.hide_message_modal()
