import asyncio

import pytest

from tui.app import MediaAssembler, WaCLIApp, message_from_data
from tui.models import Call, Message
from tui.widgets import format_entry_plain, render_mentions, strip_mentions

from tui.tests.conftest import make_call, make_entries, make_message

import base64


def test_message_from_data_full():
    msg = message_from_data(make_message(text="hi", original_text="ho", is_deleted=True))
    assert msg.text == "hi"
    assert msg.original_text == "ho"
    assert msg.is_edited
    assert msg.is_deleted


def test_message_from_data_defaults():
    data = make_message()
    for optional in (
        "id",
        "message_id",
        "message_type",
        "media_file",
        "transcription",
        "original_text",
        "is_deleted",
        "is_from_me",
    ):
        del data[optional]
    msg = message_from_data(data)
    assert msg.id == 0
    assert msg.message_id == ""
    assert msg.media_file is None
    assert msg.transcription is None
    assert not msg.is_edited
    assert not msg.is_deleted
    assert not msg.is_from_me


def test_voice_transcription_is_displayed_as_text():
    voice = message_from_data(make_message(message_type="Voice", text="", transcription="halo mas"))
    assert voice.display_text == "halo mas"
    assert "[Voice] halo mas" in format_entry_plain(voice)

    captioned = message_from_data(make_message(text="caption", transcription="halo mas"))
    assert captioned.display_text == "caption"


def test_message_title():
    base = message_from_data(make_message(sender_name="Alice", chat_name="Chat"))
    assert base.title == "Alice"

    reply = message_from_data(make_message(is_reply_to_me=True, sender_name="Alice"))
    assert reply.title == "↩ Alice"

    mine = message_from_data(make_message(is_from_me=True, chat_name="Chat"))
    assert mine.title == "→ Chat"

    mine_group = message_from_data(make_message(is_from_me=True, is_group=True, chat_name="Chat"))
    assert mine_group.title == "→ 👥 Chat"


def test_strip_and_render_mentions():
    text = 'hey <mention jid="1@s.whatsapp.net" name="Alice"/> hi'
    assert strip_mentions(text) == "hey @Alice hi"
    rendered = render_mentions(text)
    assert "[bold green]@Alice[/]" in rendered
    assert "<mention" not in rendered


def test_render_mentions_escapes_and_highlights():
    assert render_mentions("a [RUN] b") == "a \\[RUN] b"
    assert render_mentions("abc", search_query="b") == "a[reverse]b[/reverse]c"


def test_format_entry_plain():
    dm = message_from_data(make_message(text="hi", sender_name="Alice"))
    assert format_entry_plain(dm).endswith("Alice: hi")

    group = message_from_data(
        make_message(text="hi", sender_name="Alice", chat_name="Team", is_group=True)
    )
    assert "Alice | Team: hi" in format_entry_plain(group)

    typed = message_from_data(make_message(text="cap", message_type="Image"))
    assert "[Image] cap" in format_entry_plain(typed)

    call = Call(**make_call(caller_name="Bob"))
    assert format_entry_plain(call).endswith("Bob: Incoming call")


def test_load_entries_from_data_preserves_server_order():
    app = WaCLIApp()
    app.load_entries_from_data(
        make_entries(
            make_message(id=1, timestamp=100, text="early"),
            make_call(timestamp=200),
            make_message(id=2, timestamp=300, text="late"),
        )
    )
    assert [type(e) for e in app.entries] == [Message, Call, Message]
    assert [e.timestamp for e in app.entries] == [100, 200, 300]


def test_load_entries_from_data_rejects_unknown_kind():
    app = WaCLIApp()
    with pytest.raises(ValueError):
        app.load_entries_from_data({"entries": [{"kind": "sticker"}]})


def test_load_entries_from_data_handles_null_list():
    app = WaCLIApp()
    app.load_entries_from_data({"entries": None})
    assert app.entries == []


async def test_media_assembler_in_order(tmp_path):
    path = tmp_path / "img.jpg"
    future = asyncio.get_running_loop().create_future()
    assembler = MediaAssembler(future, path)

    assembler.handle_event({"seq": 0, "data": base64.b64encode(b"hello ").decode()})
    assembler.handle_event({"seq": 1, "data": base64.b64encode(b"world").decode(), "done": True})

    await future
    assert path.read_bytes() == b"hello world"


async def test_media_assembler_empty_file(tmp_path):
    path = tmp_path / "empty.jpg"
    future = asyncio.get_running_loop().create_future()
    MediaAssembler(future, path).handle_event({"seq": 0, "done": True})

    await future
    assert path.read_bytes() == b""


async def test_media_assembler_out_of_order(tmp_path):
    path = tmp_path / "img.jpg"
    future = asyncio.get_running_loop().create_future()
    assembler = MediaAssembler(future, path)

    assembler.handle_event({"seq": 0, "data": base64.b64encode(b"part").decode()})
    assembler.handle_event({"seq": 2, "data": base64.b64encode(b"skip").decode()})

    with pytest.raises(RuntimeError, match="out-of-order"):
        await future
    assert not path.exists()


async def test_media_assembler_error_event(tmp_path):
    path = tmp_path / "img.jpg"
    future = asyncio.get_running_loop().create_future()
    MediaAssembler(future, path).handle_event({"error": "file not found"})

    with pytest.raises(RuntimeError, match="file not found"):
        await future
