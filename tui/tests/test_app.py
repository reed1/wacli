import base64
import subprocess

from tui.app import VIM_VIEW_PATH, WaCLIApp
from tui.models import Call, Message
from tui.widgets import (
    ComposeInput,
    EntryWidget,
    ImageViewer,
    MessageList,
    MessageModal,
    StatusBar,
)

from tui.tests.conftest import make_call, make_entries, make_message, png_bytes, wait_until


def loaded_stub(stub_server, texts=("one", "two", "three")):
    stub_server.entries = make_entries(
        *[
            make_message(id=i + 1, message_id=f"m{i + 1}", timestamp=1700000000 + i, text=text)
            for i, text in enumerate(texts)
        ]
    )
    return stub_server


async def test_initial_entries_rendered(stub_server):
    stub_server.entries = make_entries(
        make_message(id=1, message_id="m1", timestamp=100, text="first"),
        make_message(id=2, message_id="m2", timestamp=200, text="second"),
        make_call(timestamp=300),
    )
    app = WaCLIApp()
    async with app.run_test() as pilot:
        await wait_until(lambda: len(app.query(EntryWidget)) == 3)
        assert [type(e) for e in app.entries] == [Message, Message, Call]
        assert [e.timestamp for e in app.entries] == [100, 200, 300]
        assert app.selected_index == 2
        assert app.query(EntryWidget)[2].has_class("selected")


async def test_initial_render_lands_at_bottom(stub_server):
    stub_server.entries = make_entries(
        *[
            make_message(id=i, message_id=f"m{i}", timestamp=1700000000 + i, text=f"msg {i}")
            for i in range(60)
        ]
    )
    app = WaCLIApp()
    async with app.run_test(size=(80, 24)) as pilot:
        await wait_until(lambda: len(app.entries) == 60)
        await pilot.pause()
        message_list = app.query_one(MessageList)
        assert message_list.visible
        assert message_list.scroll_y == message_list.max_scroll_y
        assert app.selected_index == 59


async def test_live_message_appends_and_follows(stub_server):
    loaded_stub(stub_server)
    app = WaCLIApp()
    async with app.run_test() as pilot:
        await wait_until(lambda: len(app.entries) == 3)
        stub_server.send_event(
            "message", make_message(id=4, message_id="m4", timestamp=1700000100, text="fresh")
        )
        await wait_until(lambda: len(app.entries) == 4)
        assert app.entries[-1].text == "fresh"
        await wait_until(lambda: app.selected_index == 3)


async def test_live_call_appended(stub_server):
    loaded_stub(stub_server)
    app = WaCLIApp()
    async with app.run_test() as pilot:
        await wait_until(lambda: len(app.entries) == 3)
        stub_server.send_event("call", make_call(timestamp=1700000200, caller_name="Bob"))
        await wait_until(lambda: len(app.entries) == 4)
        assert isinstance(app.entries[-1], Call)


async def test_message_updated_edit_and_delete(stub_server):
    loaded_stub(stub_server)
    app = WaCLIApp()
    async with app.run_test() as pilot:
        await wait_until(lambda: len(app.query(EntryWidget)) == 3)

        stub_server.send_event(
            "message_updated",
            make_message(
                id=2, message_id="m2", timestamp=1700000001, text="edited", original_text="two"
            ),
        )
        await wait_until(lambda: app.entries[1].is_edited)
        assert app.entries[1].text == "edited"
        assert "✎" in app.query(EntryWidget)[1].render()

        stub_server.send_event(
            "message_updated",
            make_message(
                id=3, message_id="m3", timestamp=1700000002, text="three", is_deleted=True
            ),
        )
        await wait_until(lambda: app.entries[2].is_deleted)
        assert "🗑" in app.query(EntryWidget)[2].render()


async def test_message_updated_unknown_id_ignored(stub_server):
    loaded_stub(stub_server)
    app = WaCLIApp()
    async with app.run_test() as pilot:
        await wait_until(lambda: len(app.entries) == 3)
        stub_server.send_event("message_updated", make_message(message_id="nope", text="ghost"))
        stub_server.send_event(
            "message", make_message(id=4, message_id="m4", timestamp=1700000100, text="anchor")
        )
        await wait_until(lambda: len(app.entries) == 4)
        assert all(e.text != "ghost" for e in app.entries)


async def test_keyboard_navigation(stub_server):
    loaded_stub(stub_server, texts=("a", "b", "c", "d"))
    app = WaCLIApp()
    async with app.run_test() as pilot:
        await wait_until(lambda: len(app.query(EntryWidget)) == 4)
        assert app.selected_index == 3

        await pilot.press("k")
        assert app.selected_index == 2
        await pilot.press("j")
        assert app.selected_index == 3
        await pilot.press("j")
        assert app.selected_index == 3

        await pilot.press("g", "g")
        assert app.selected_index == 0
        await pilot.press("k")
        assert app.selected_index == 0

        await pilot.press("G")
        assert app.selected_index == 3
        assert app.query(EntryWidget)[3].has_class("selected")
        assert not app.query(EntryWidget)[2].has_class("selected")


async def test_compose_send_payload(stub_server):
    loaded_stub(stub_server)
    app = WaCLIApp()
    async with app.run_test() as pilot:
        await wait_until(lambda: len(app.entries) == 3)

        await pilot.press("enter")
        compose = app.query_one(ComposeInput)
        assert compose.has_class("visible")

        await pilot.press("h", "i")
        await pilot.press("enter")

        cmd = await stub_server.wait_for_command("send")
        assert cmd["chat_jid"] == "111@s.whatsapp.net"
        assert cmd["text"] == "hi"
        await wait_until(lambda: not compose.has_class("visible"))
        assert app.compose_mode is None


async def test_compose_reply_payload(stub_server):
    loaded_stub(stub_server)
    app = WaCLIApp()
    async with app.run_test() as pilot:
        await wait_until(lambda: len(app.entries) == 3)

        await pilot.press("r")
        await pilot.press("o", "k")
        await pilot.press("enter")

        cmd = await stub_server.wait_for_command("reply")
        assert cmd["message_id"] == "m3"
        assert cmd["sender_jid"] == "111@s.whatsapp.net"
        assert cmd["text"] == "ok"


async def test_compose_escape_cancels(stub_server):
    loaded_stub(stub_server)
    app = WaCLIApp()
    async with app.run_test() as pilot:
        await wait_until(lambda: len(app.entries) == 3)

        await pilot.press("enter")
        compose = app.query_one(ComposeInput)
        assert compose.has_class("visible")

        await pilot.press("escape")
        assert not compose.has_class("visible")
        assert app.compose_mode is None
        assert not any(cmd["action"] != "get_entries" for cmd in stub_server.commands)


async def test_open_in_vim_launches_kitty_overlay(stub_server, monkeypatch):
    loaded_stub(stub_server, texts=("one", "two", 'say <mention jid="9@s" name="Bob"/> hi'))
    monkeypatch.setenv("KITTY_WINDOW_ID", "7")
    launched: list[list[str]] = []

    def fake_run(command, **kwargs):
        launched.append(command)
        return subprocess.CompletedProcess(args=command, returncode=0, stdout=b"", stderr=b"")

    monkeypatch.setattr(subprocess, "run", fake_run)
    app = WaCLIApp()
    async with app.run_test() as pilot:
        await wait_until(lambda: len(app.entries) == 3)

        await pilot.press("v")

        assert launched == [
            [
                "kitty",
                "@",
                "launch",
                "--type=overlay",
                "--next-to",
                "id:7",
                "--title",
                "wacli: Alice",
                "vim",
                "-M",
                "-n",
                str(VIM_VIEW_PATH),
            ]
        ]
        assert VIM_VIEW_PATH.read_text() == "say @Bob hi\n"


async def test_open_in_vim_ignores_calls(stub_server, monkeypatch):
    stub_server.entries = make_entries(make_call(timestamp=300))
    monkeypatch.setenv("KITTY_WINDOW_ID", "7")
    launched = []
    monkeypatch.setattr(subprocess, "run", lambda command, **kwargs: launched.append(command))
    app = WaCLIApp()
    async with app.run_test() as pilot:
        await wait_until(lambda: len(app.entries) == 1)

        await pilot.press("v")
        assert launched == []


def stub_clipboard(monkeypatch, image: bytes):
    completed = subprocess.CompletedProcess(args=[], returncode=0, stdout=image, stderr=b"")
    monkeypatch.setattr(subprocess, "run", lambda *args, **kwargs: completed)


async def test_send_image_confirms_without_dismissing_preview(stub_server, monkeypatch):
    loaded_stub(stub_server)
    image = png_bytes()
    stub_clipboard(monkeypatch, image)
    app = WaCLIApp()
    async with app.run_test() as pilot:
        await wait_until(lambda: len(app.entries) == 3)

        await pilot.press("I")
        await wait_until(lambda: bool(app.query(ImageViewer)))
        await pilot.pause()
        viewer = app.query_one(ImageViewer)
        assert "Send this image to Alice?" in viewer.prompt

        await pilot.press("y")
        cmd = await stub_server.wait_for_command("send_image")
        assert base64.b64decode(cmd["image_data"]) == image
        await wait_until(lambda: not app.query(ImageViewer))


async def test_send_image_cancelled(stub_server, monkeypatch):
    loaded_stub(stub_server)
    stub_clipboard(monkeypatch, png_bytes())
    app = WaCLIApp()
    async with app.run_test() as pilot:
        await wait_until(lambda: len(app.entries) == 3)

        await pilot.press("I")
        await wait_until(lambda: bool(app.query(ImageViewer)))
        await pilot.pause()

        await pilot.press("n")
        await wait_until(lambda: not app.query(ImageViewer))
        assert app.pending_image_chat is None
        assert all(cmd["action"] != "send_image" for cmd in stub_server.commands)


async def test_view_image_message_opens_inline_viewer(stub_server):
    loaded_stub(stub_server)
    stub_server.entries["entries"][2]["message"]["media_file"] = "pic.png"
    stub_server.entries["entries"][2]["message"]["text"] = "a caption"
    stub_server.media["pic.png"] = png_bytes()
    app = WaCLIApp()
    async with app.run_test() as pilot:
        await wait_until(lambda: len(app.entries) == 3)

        await pilot.press("H")
        await wait_until(lambda: bool(app.query(ImageViewer)))
        viewer = app.query_one(ImageViewer)
        assert viewer.caption == "a caption"
        assert viewer.prompt is None
        assert not app.query_one(MessageModal).has_class("visible")

        await pilot.pause()
        await pilot.press("escape")
        await wait_until(lambda: not app.query(ImageViewer))


async def test_search(stub_server):
    loaded_stub(stub_server, texts=("apple pie", "banana", "apple juice"))
    app = WaCLIApp()
    async with app.run_test() as pilot:
        await wait_until(lambda: len(app.entries) == 3)

        await pilot.press("slash")
        await pilot.press("a", "p", "p", "l", "e")
        await pilot.press("enter")

        assert app.search_matches == [0, 2]
        assert app.selected_index == 2
        assert "2/2 for 'apple'" in str(app.query_one(StatusBar).render())

        await pilot.press("n")
        assert app.selected_index == 0
        await pilot.press("N")
        assert app.selected_index == 2

        await pilot.press("escape")
        assert app.search_matches == []
        assert str(app.query_one(StatusBar).render()).strip() == ""
