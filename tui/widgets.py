import re
from typing import TYPE_CHECKING

from textual.binding import Binding
from textual.containers import ScrollableContainer
from textual.widgets import Input, Static

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


class ComposeInput(Input):
    DEFAULT_CSS = """
    ComposeInput {
        display: none;
        width: 60%;
        height: auto;
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
