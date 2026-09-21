# Digging the event log

The event log is the only record of a message the server dropped. What it keeps and
how it rotates is in [architecture.md](architecture.md#the-event-log); this is the
procedure for answering "what was that message I never saw?" against it.

Everything runs over ssh, against `sgtent:app/wacli/server/events/`.

## Which file the question is in

**`sgtent` runs in UTC; you read the log in WIB (UTC+7).** The day-of-week filename
comes from the server's own clock, so anything before **07:00 WIB** belongs to the
*previous* day's file. A question about Tuesday morning is answered from `mon.jsonl`.

The same offset applies to the `time` field and to every `jq` comparison against it:
subtract 7 hours from the wall clock you are looking for.

```
date -u -d '2026-09-22 04:30 +0700' +%FT%T   # the timestamp to match on
```

When the window is unclear, grep all seven files at once — they are small enough
(`events/*.jsonl`).

## Finding the message

Start from whichever of these you have.

**A deletion.** Revokes are `decision: "revoke applied"`:

```
ssh sgtent "jq -c 'select(.kind==\"verdict\" and .data.decision==\"revoke applied\")' app/wacli/server/events/mon.jsonl"
```

The `message_id` on that record is the **revoke's own id, not the deleted message's**.
The id you want is in the raw event, under `protocolMessage.key.ID`:

```
ssh sgtent "jq -c 'select(.kind==\"event\" and .data.Info.ID==\"<revoke id>\") | .data.Message' app/wacli/server/events/mon.jsonl"
```

Then grep that id across the log. The original `*events.Message` was written before any
filtering, so its text survives the deletion:

```
ssh sgtent "grep -F '<deleted id>' app/wacli/server/events/*.jsonl | jq -c ."
```

`grep -F` first, `jq` after: it is far faster than parsing every line, and it catches the
id wherever it appears — the original event, its verdict, and the revoke that names it.

**A chat.** Everything that happened in one chat, newest fields only:

```
ssh sgtent "jq -c 'select(.kind==\"event\" and .data.Info.Chat==\"<jid>\") | {t:.time, from:.data.Info.PushName, id:.data.Info.ID, msg:(.data.Message|tostring[0:300])}' app/wacli/server/events/mon.jsonl"
```

Truncating `msg` matters — media payloads carry base64 thumbnails that will flood the
terminal otherwise.

**A time.** Bracket the window and let the verdicts show what was dropped:

```
ssh sgtent "jq -c 'select(.time>=\"2026-09-21T21:00\" and .time<\"2026-09-21T22:00\" and .kind==\"verdict\" and .data.decision!=\"stored\")' app/wacli/server/events/mon.jsonl"
```

String comparison on RFC3339 works because the times are zero-padded and all UTC.

## Naming the chat and the sender

Verdicts carry `chat_name: ""` — the name is not resolved at log time. Get it from the
message store, which keeps it on every stored row:

```
ssh sgtent 'sqlite3 app/wacli/server/messages.db "select distinct chat_jid, chat_name from messages where chat_jid=\"<jid>\";"'
```

A chat with nothing stored (fully muted, never mentioned) has no row there, and the name
has to come from WhatsApp itself.

For the sender, the raw event's `Info.PushName` is the display name the sender chose, and
`Info.SenderAlt` is their phone JID when `AddressingMode` is `lid` — the `@lid` in
`Sender` is opaque on its own.

## Why it never reached the TUI

The verdict names the rule and the values that rule saw. `dropped: muted chat, not
mentioned/reply-to-me` beside `is_muted: true` is the common one; the full list of
`decision` values is in `handleMessage` (`server/messages.go`).

Worth knowing: a muted chat's message is dropped, but a later revoke of it still logs
`revoke applied` — the revoke is handled before any mute check, so a deletion is recorded
even for a message that was never stored.

## A worked example

> Someone deleted a message in the holiday planning group this morning. What was it?

The question names no id, no text and no time beyond "this morning" — the log supplies
all of it.

1. Tuesday morning WIB → `mon.jsonl`, window from `<monday>T17:00Z`.
2. Revoke verdicts in that window → one in a group chat, say `AAAA1111…` at `21:44:44Z`.
3. Its raw event → `protocolMessage.key.ID = BBBB2222…`, the id of what was deleted.
4. `grep -F BBBB2222…` → the original at `21:44:11Z` (04:44:11 WIB), with its full
   payload and `PushName` — deleted 33 seconds later.
5. Its verdict → `dropped: muted chat, not mentioned/reply-to-me`, which is why the TUI
   never had it.
6. `messages.db` → the group's `chat_name`, if anything from it was ever stored.

Read the surrounding events in the same chat before answering: nothing posted after a
deletion reads as a wrong-chat send, a replacement moments later as a correction.
