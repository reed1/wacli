import argparse

import pytest

from commands.changes import Painter, parse_window, render

NOW = 1790000000


def change(**overrides) -> dict:
    base = {
        "message_id": "m1",
        "source": "log",
        "timestamp": NOW - 3600,
        "chat_name": "Holiday Planning",
        "is_group": True,
        "sender_jid": "1@lid",
        "sender_name": "Bob",
        "is_from_me": False,
        "message_type": "",
        "text": "original",
        "decision": "stored",
        "edits": [],
        "deletion": None,
    }
    return base | overrides


def plain(changes: list[dict], since: int = NOW - 3600 * 5, log_starts: int = NOW - 86400) -> str:
    return render({"log_starts": log_starts, "changes": changes}, since, "5h", Painter(color=False))


@pytest.mark.parametrize(
    "text, seconds",
    [("30m", 1800), ("5h", 18000), ("2d", 172800), ("1d12h", 129600), ("7d", 604800)],
)
def test_parse_window(text, seconds):
    assert parse_window(text).seconds == seconds


@pytest.mark.parametrize("text", ["", "5", "h", "8d", "0h", "5h2d", "1w"])
def test_parse_window_rejects(text):
    with pytest.raises(argparse.ArgumentTypeError):
        parse_window(text)


def test_deletion_shows_who_and_the_text_taken_away():
    out = plain(
        [
            change(
                deletion={
                    "timestamp": NOW - 3500,
                    "by_jid": "9@lid",
                    "by_name": "Admin",
                    "by_me": False,
                }
            )
        ]
    )
    assert "Holiday Planning › Bob" in out
    assert "    original" in out
    assert "deleted by Admin" in out
    assert "0 edited" in out and "1 deleted" in out


def test_edits_follow_the_original_in_order():
    out = plain(
        [
            change(
                edits=[
                    {"timestamp": NOW - 60, "text": "second"},
                    {"timestamp": NOW, "text": "third"},
                ]
            )
        ]
    )
    assert out.index("original") < out.index("second") < out.index("third")


def test_mentions_render_as_names():
    out = plain(
        [
            change(
                text='hi <mention jid="5@lid" name="Ann"/>', edits=[{"timestamp": NOW, "text": "x"}]
            )
        ]
    )
    assert "hi @Ann" in out


def test_dropped_message_says_why_the_tui_never_had_it():
    out = plain(
        [
            change(
                decision="dropped: muted chat, not mentioned/reply-to-me",
                edits=[{"timestamp": NOW, "text": "x"}],
            )
        ]
    )
    assert "(not in TUI: muted chat, not mentioned/reply-to-me)" in out


def test_window_older_than_the_log_is_flagged():
    assert "the log starts" in plain([], since=NOW - 7 * 86400, log_starts=NOW - 6 * 86400)
    assert "the log starts" not in plain([], since=NOW - 3600, log_starts=NOW - 6 * 86400)


def test_missing_original_is_said_plainly():
    out = plain(
        [
            change(
                source="missing",
                timestamp=0,
                text="",
                deletion={"timestamp": NOW, "by_jid": "1@lid", "by_name": "Bob", "by_me": False},
            )
        ]
    )
    assert "sent ?" in out
    assert "older than the log" in out


def test_deleting_my_own_message_reads_as_me():
    out = plain(
        [
            change(
                is_from_me=True,
                is_group=False,
                chat_name="Alice",
                deletion={"timestamp": NOW, "by_jid": "me", "by_name": "Ridwan", "by_me": True},
            )
        ]
    )
    assert "me → Alice" in out
    assert "deleted by me" in out


def test_color_styles_every_struck_line_on_its_own():
    out = render(
        {
            "log_starts": 0,
            "changes": [
                change(
                    text="one\ntwo",
                    deletion={
                        "timestamp": NOW,
                        "by_jid": "1@lid",
                        "by_name": "Bob",
                        "by_me": False,
                    },
                )
            ],
        },
        NOW - 3600,
        "1h",
        Painter(color=True),
    )
    assert "    \033[31m\033[9mone\033[0m\n    \033[31m\033[9mtwo\033[0m" in out


def test_unexpected_source_is_an_error():
    with pytest.raises(ValueError, match="Unexpected source"):
        plain([change(source="elsewhere")])
