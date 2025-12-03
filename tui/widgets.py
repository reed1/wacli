from typing import TYPE_CHECKING

from textual.binding import Binding
from textual.containers import ScrollableContainer
from textual.widgets import Input, Static

from tui.models import Call, Entry, Message

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
            text_oneline = msg.text.replace("\n", " ")
            if msg.is_group:
                title = f"{msg.title} [bold magenta]👥[/] [magenta]{msg.chat_name}[/]"
            else:
                title = msg.title
            return f"{indicator} [dim]{msg.formatted_time}[/][bold cyan] {title}[/]: {text_oneline}"
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
