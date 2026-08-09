# wacli architecture

Three processes on two machines, joined by one TCP socket.

```
                              sgtent  (100.97.165.105, reached over Tailscale)
                     ┌───────────────────────────────────────────────┐
    WhatsApp  <──────┤  wacli-server        (Go, systemd --user)     │
    (whatsmeow)      │    messages.db  wacli.db  media/  transcribe  │
                     │    listens on TCP 100.97.165.105:3010         │
                     └────────────────────┬──────────────────────────┘
                                          │  newline-delimited JSON
                                          │  fan-out: every connected
                                          │  client gets every event
      this machine  ┌───────────────────┬─┴───────────────────┐
                    │                   │                     │
             ┌──────┴───────┐   ┌───────┴────────┐   ┌────────┴─────────┐
             │  wacli-tui   │   │ wacli-notifier │   │ (ansible deploy  │
             │  (Textual)   │   │ systemd --user │   │  health check)   │
             └──────┬───────┘   └───────┬────────┘   └──────────────────┘
                    │                   │ AF_UNIX
kitty / rofi / mpv  │                   ▼
xclip / copyq       ▼          /tmp/rlocal/rworkspaces/sock
```

## Who is who

| Process | Where | Started by | Talks to |
|---|---|---|---|
| `wacli-server` | `sgtent:/home/reed/app/wacli/server` | `wacli-server.service` (systemd user, lingering enabled) | WhatsApp via whatsmeow; listens on `LISTEN_ADDR` |
| `wacli-notifier` | this machine, `notifier/main.py` | `wacli-notifier.service` (systemd user) | server socket; rworkspaces Unix socket |
| `wacli-tui` | this machine, `tui/main.py` | you, in a kitty window via the `wacli-tui` wrapper | server socket; kitty/rofi/xclip/copyq/mpv/xdg-open |
| `wacli-send` | this machine, repo root | any script wanting to send one message | server socket, for one request/response |

`scripts/print-sgtent-qr.py` SSHes to sgtent to re-pair WhatsApp. `ansible/playbooks/04b_deploy_prod/files/wait_for_wa_connected.py` is copied to the prod host at deploy time and opens the same socket just long enough to see one `connection_state` — it takes `host:port` on argv and deliberately shares no code with the clients here.

## Socket map

| From | To | Type | Address |
|---|---|---|---|
| TUI, notifier, `wacli-send`, deploy check | server | TCP | `SERVER_HOST:SERVER_PORT` (`.env`) = `LISTEN_ADDR` on the server |
| notifier | rworkspaces | AF_UNIX | `/tmp/rlocal/rworkspaces/sock` |
| TUI | kitty | kitty remote control | `kitty @ launch`, for the Vim overlay |

The TUI's `.env` and the server's `.env` are different files with different keys: clients read `SERVER_HOST`/`SERVER_PORT`, the server reads `LISTEN_ADDR` (rendered by `ansible/playbooks/04b_deploy_prod/templates/env.j2`).

## The server socket protocol

Newline-delimited JSON in both directions on one long-lived connection. Inbound commands are capped at 32 MiB per line (`maxSocketLine`) so a base64 image fits.

**Server → client.** Most events are `{"type": ..., "data": ...}`, but `response` and `media` are flat — their fields sit at the top level, not under `data`. Clients have to know that.

| `type` | Payload | When |
|---|---|---|
| `connection_state` | `data: {connected, reason?}` | once on accept, then on every WhatsApp link change |
| `entries` | `data: {entries: [{kind, message\|call}]}` | reply to `get_entries` — last 50, oldest first |
| `message` | `data: Message` | a message was stored |
| `message_updated` | `data: Message` | an edit or delete rewrote a stored message |
| `call` | `data: Call` | incoming call offer |
| `response` | `request_id, success, error?` (flat) | ack for `send`/`reply`/`react`/`send_image` |
| `media` | `request_id, seq, data, done, error?` (flat) | 256 KiB base64 chunks answering `get_media` |

**Client → server.** Every action but `get_entries` carries a `request_id` that comes back on the matching `response`.

| `action` | Fields |
|---|---|
| `get_entries` | — |
| `get_media` | `request_id`, `filename` |
| `send` | `request_id`, `chat_jid`, `text` |
| `reply` | `request_id`, `chat_jid`, `message_id`, `sender_jid`, `text` |
| `react` | `request_id`, `chat_jid`, `message_id`, `sender_jid`, `text` (empty removes the reaction) |
| `send_image` | `request_id`, `chat_jid`, `image_data` (base64 PNG) |

**Fan-out.** `entries`, `response` and `media` go only to the connection that asked. `message`, `message_updated`, `call` and `connection_state` are broadcast to every entry in `socketConns`. So the TUI and the notifier receive byte-identical event streams — there is no per-client subscription or filtering.

Filtering happens once, server-side, before the row is inserted and broadcast (`server/messages.go`):

- status broadcasts (`@broadcast` JIDs) are dropped unless `INCLUDE_STATUS_MESSAGES=true`
- muted chats are dropped unless the message mentions you, replies to you, or is from you — unless `INCLUDE_MUTED_MESSAGES=true`

## How each client uses the stream

|  | TUI | notifier |
|---|---|---|
| Framing | `asyncio.StreamReader.readline` | blocking `recv` + manual split on `\n` |
| Events consumed | all of them | `message` (skipping `is_from_me`) and `connection_state` |
| Sends commands | yes, all six | never — read-only |
| On disconnect | exits `75` | exits `1` |
| Restarted by | the `wacli-tui` wrapper loop, after a keypress | systemd, after 10s |

Both import `SERVER_ADDR` and `enable_keepalive` from `wacli_socket.py` at the repo root. `wacli-send` takes only `SERVER_ADDR`: it opens a connection, sends one command, waits for the ack under a timeout and exits, so it is never idle long enough for keepalive to have anything to say. That module exists because the two clients must agree on where the server is and how aggressively to probe a quiet connection; when they disagreed, one of them silently went stale (below). The notifier reaches it with a `sys.path` insert, the same trick `tui/main.py` already uses.

## Disconnect handling

A TCP connection whose peer disappears without sending a FIN — server restart, dropped Tailscale route, laptop suspend — stays `ESTABLISHED` on this side forever. Nothing arrives, no error is raised, and a blocking read never returns. `ss -tn` will happily show the socket as healthy on one end while the other end has no record of it at all.

`enable_keepalive` is the only thing that turns that into an error: after 60s idle the kernel probes, and 4 failed probes 15s apart (or an immediate RST from a peer that no longer knows the connection) surfaces as a read error. Detection takes roughly one to two minutes.

Recovery is per-client, and deliberately different:

- **notifier** — exits 1, systemd restarts it 10s later, and it reconnects. Self-healing, no interaction.
- **TUI** — exits `EXIT_DISCONNECTED` (75). The `wacli-tui` wrapper loop treats only that code as "offer a restart", prints `Press any key to reconnect...`, and re-runs. Any other exit code falls through to `pause-if-error`, which holds the window open so the failure can be read. Restarting re-runs `get_entries`, so messages missed while the socket was dead come back.

The TUI does not reconnect in place. Its whole view is built from one `get_entries` snapshot plus the live stream, so a reconnect would have to reconcile the two; a restart gets the same result for free.

> This is what a stale TUI looked like: the notifier kept flagging new messages while the TUI sat on a message list hours out of date. Both were connected to the same broadcast — but the notifier had keepalive and a supervisor, and the TUI had neither.

## Server-side storage

Everything lives in the server's working directory, `/home/reed/app/wacli/server`:

- `wacli.db` — whatsmeow's device/session store. Deleting it means re-pairing. Also opened read-only to answer "is this chat muted?"
- `messages.db` — `messages` and `calls`. Both are capped at a maximum entry count and trimmed back down once they pass it (`maxEntries`/`trimToCount` in `server/main.go`), so this is a rolling window, not an archive.
- `media/` — images and video, named by content hash and extension. Files belonging to trimmed messages are deleted with them. The TUI never reads this directory; it asks for `get_media` and writes chunks into `/tmp/rlocal/wacli/`, via a `.part` file renamed on completion. Server filenames are stable UUIDs, so `fetch_media` treats an existing local file as a finished download and skips the transfer.

## Yanking an image

`y` on an image message copies the image; on anything else it copies text. The clipboard gets a **pointer, not the pixels**: X11 selections carry whatever the owner offers, and a clipboard manager pulls every advertised target the moment ownership changes, so offering `image/png` would push megabytes into CopyQ's on-disk history for every yank. Instead `copy_image` shells out to `copyq copy text/uri-list file:///tmp/rlocal/wacli/<uuid>.jpg text/plain <caption or path>`.

`copyq` rather than `xclip` because only it can advertise several targets in one call, and because it marks its own copies with `application/x-copyq-owner` and keeps them out of the history — so the yank stores nothing. Video is deliberately excluded: `mpv` already plays it and no paste target wants a video file URI.
- voice notes — `$TMPDIR/wacli-voice`, not configurable: they are scratch files, deleted after 5 days. `TRANSCRIPTION_SCRIPT` transcribes them and the text is written back onto the message row, which then goes out as `message_updated`.

## Operations

```
ansible-playbook -i ansible/inventory.yaml ansible/playbooks/04b_deploy_prod/main.yaml --tags push-server
ansible-playbook -i ansible/inventory.yaml ansible/playbooks/04c_deploy_local/main.yaml --tags push-notifier
python scripts/print-sgtent-qr.py          # re-pair WhatsApp
wacli-send <chat_jid> 'text'               # send one message; "-" reads stdin
ssh sgtent journalctl --user -u wacli-server -f
journalctl --user -u wacli-notifier -f
tail -f /tmp/rlocal/wacli/wacli.log        # TUI event log
```

The prod playbook ends by running `wait_for_wa_connected.py` against `LISTEN_ADDR`, so a deploy fails loudly if the server comes back up without a working WhatsApp link. `wacli-server.service` sets `RestartPreventExitStatus=2`, which is the exit code the server uses for a permanent WhatsApp failure — the kind that needs re-pairing rather than a restart.
