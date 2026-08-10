import re
from pathlib import Path
from typing import TYPE_CHECKING

from textual.app import ComposeResult
from textual.binding import Binding
from textual.containers import ScrollableContainer, Vertical
from textual.screen import ModalScreen
from textual.widgets import Static, TextArea

from tui.kitty_image import KittyImage
from tui.models import Call, Entry, Message

MENTION_RE = re.compile(r'<mention jid="[^"]*" name="([^"]*)"/>')


def _highlight(escaped: str, query: str) -> str:
    if not query:
        return escaped
    return re.compile(re.escape(query), re.IGNORECASE).sub(
        lambda m: f"[reverse]{m.group(0)}[/reverse]", escaped
    )


def render_mentions(text: str, search_query: str = "") -> str:
    parts = MENTION_RE.split(text)
    result = []
    for i, part in enumerate(parts):
        if i % 2 == 0:
            result.append(_highlight(part.replace("[", "\\["), search_query))
        else:
            name = part.replace("[", "\\[")
            result.append(f"[bold green]@{_highlight(name, search_query)}[/]")
    return "".join(result)


def highlight_plain(text: str, query: str) -> str:
    return _highlight(text.replace("[", "\\["), query)


def strip_mentions(text: str) -> str:
    return MENTION_RE.sub(r"@\1", text)


def format_entry_title(entry: "Entry", search_query: str = "") -> str:
    sender = highlight_plain(entry.title, search_query)
    if isinstance(entry, Message) and entry.is_group and not entry.is_from_me:
        chat = highlight_plain(entry.chat_name, search_query)
        return f"{sender} [bold magenta]👥[/] [magenta]{chat}[/]"
    return sender


def format_entry_plain(entry: "Entry") -> str:
    if isinstance(entry, Message):
        text = strip_mentions(entry.display_text).replace("\n", " ")
        type_prefix = f"[{entry.message_type}] " if entry.message_type else ""
        if entry.is_group and not entry.is_from_me:
            return (
                f"[{entry.formatted_time}] {entry.title} | {entry.chat_name}: {type_prefix}{text}"
            )
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
        query = getattr(self.app, "search_query", "")
        if isinstance(self.entry, Message):
            msg = self.entry
            text_oneline = render_mentions(msg.display_text.replace("\n", " "), query)
            type_prefix = f"[dim]\\[{msg.message_type}][/] " if msg.message_type else ""
            if msg.is_deleted:
                content = f"{type_prefix}[dim]🗑 [strike]{text_oneline}[/strike][/dim]"
            elif msg.is_edited:
                content = f"{type_prefix}[dim]✎[/dim] {text_oneline}"
            else:
                content = f"{type_prefix}{text_oneline}"
            # Don't use rich.markup.escape() — it skips uppercase tags like [RUN]
            title = format_entry_title(msg, query)
            return f"{indicator} [dim]{msg.formatted_time}[/][bold cyan] {title}[/]: {content}"
        call = self.entry
        title = highlight_plain(call.title, query)
        return (
            f"{indicator} [dim]{call.formatted_time}[/][bold yellow] 📞 {title}[/]: Incoming call"
        )


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
        Binding("alt+left", "cursor_word_left", "Word left", show=False),
        Binding("alt+right", "cursor_word_right", "Word right", show=False),
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
        border-title-align: left;
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


class ImageViewer(Vertical):
    """Shows an image inline, optionally with a yes/no prompt below it.

    The prompt shares the modal with the image so a send can be confirmed without
    dismissing the preview first.
    """

    DEFAULT_CSS = """
    ImageViewer {
        layer: above;
        width: 80%;
        height: 80%;
        padding: 0 1;
        border: tall $primary;
        background: $surface;
    }
    ImageViewer.confirming {
        border: tall $warning;
    }
    ImageViewer > #image-caption {
        height: 1;
        text-align: center;
        color: $text;
    }
    """

    BINDINGS = [
        Binding("y", "confirm", "Yes", show=False),
        Binding("enter", "confirm", "Yes", show=False),
        Binding("n", "cancel", "No", show=False),
        Binding("escape", "cancel", "No", show=False),
        Binding("q", "cancel", "No", show=False),
    ]

    app: "WaCLIApp"

    def __init__(self, path: Path, prompt: str | None = None, caption: str = "") -> None:
        super().__init__()
        self.path = path
        self.prompt = prompt
        self.caption = caption
        self.can_focus = True
        self.border_title = "Send image" if prompt else path.name
        if prompt:
            self.add_class("confirming")

    def compose(self) -> ComposeResult:
        yield KittyImage(self.path)
        yield Static(self.prompt or self.caption or "[dim]esc to close[/]", id="image-caption")

    def action_confirm(self) -> None:
        if self.prompt is None:
            self.app.close_image_viewer()
            return
        self.app.confirm_send_image()

    def action_cancel(self) -> None:
        if self.prompt is None:
            self.app.close_image_viewer()
            return
        self.app.cancel_send_image()


class HelpScreen(ModalScreen):
    DEFAULT_CSS = """
    HelpScreen {
        align: center middle;
        background: $background 60%;
    }
    HelpScreen > #help-body {
        width: auto;
        max-width: 80%;
        height: auto;
        max-height: 80%;
        padding: 1 2;
        border: tall $primary;
        background: $surface;
        overflow-y: auto;
    }
    """

    def __init__(self, shortcuts: list[tuple[str, str]]) -> None:
        super().__init__()
        self.shortcuts = shortcuts

    def compose(self) -> ComposeResult:
        width = max(len(key) for key, _ in self.shortcuts)
        lines = "\n".join(
            f"[bold cyan]{key:>{width}}[/]  {description}" for key, description in self.shortcuts
        )
        body = Static(lines, id="help-body")
        body.border_title = "Keyboard shortcuts"
        body.border_subtitle = "esc to close"
        yield body

    # Swallow every key so the app bindings underneath stay inert while the
    # modal is up; they act on widgets that only exist on the main screen.
    async def _on_key(self, event) -> None:
        event.stop()
        event.prevent_default()
        if event.key in ("escape", "q", "question_mark"):
            self.dismiss()
        elif event.key in ("j", "down"):
            self.query_one("#help-body").scroll_down()
        elif event.key in ("k", "up"):
            self.query_one("#help-body").scroll_up()


class StatusBar(Static):
    DEFAULT_CSS = """
    StatusBar {
        height: 1;
        padding: 0 1;
        background: $surface;
        color: $text;
    }
    StatusBar.hidden {
        display: none;
    }
    """


class SearchInput(Static):
    DEFAULT_CSS = """
    SearchInput {
        display: none;
        height: 1;
        padding: 0 1;
        background: $surface;
        color: $text;
    }
    SearchInput.visible {
        display: block;
    }
    """

    app: "WaCLIApp"

    def __init__(self) -> None:
        super().__init__()
        self.can_focus = True
        self.value: str = ""

    def render(self) -> str:
        return f"/{self.value}\u2588"

    def reset(self) -> None:
        self.value = ""
        self.refresh()

    async def _on_key(self, event) -> None:
        event.stop()
        event.prevent_default()
        if event.key == "escape":
            self.app.hide_search()
            return
        if event.key == "enter":
            self.app.run_search(self.value)
            return
        if event.key == "backspace":
            self.value = self.value[:-1]
            self.refresh()
            return
        char = event.character
        if char and char.isprintable():
            self.value += char
            self.refresh()
