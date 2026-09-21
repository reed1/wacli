# wacli

WhatsApp message watcher with terminal UI.

## Structure

- `server/` - Go application built on [whatsmeow](https://github.com/tulir/whatsmeow) that connects to WhatsApp, stores messages to SQLite, and exposes a TCP socket that fans real-time updates out to every connected client
- `tui/` - Python Textual application that renders the entries the server sends it, with j/k navigation and live updates over the socket
- `notifier/` - Python daemon that listens for new message events from the server and raises an rworkspaces attention flag, clearing it again when a message of mine shows I have been in WhatsApp
- `wacli-send` - one-shot CLI that sends a single message to a chat JID, so scripts can fire a message without knowing the socket protocol
- `wacli_socket.py` - server address and TCP keepalive settings, shared by the TUI and the notifier

## See also

- [docs/architecture.md](docs/architecture.md) - the process/socket map, the socket protocol, and how disconnects are handled
- [docs/digging-the-event-log.md](docs/digging-the-event-log.md) - tracing a message the server dropped or a deletion took away
