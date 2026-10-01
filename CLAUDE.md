# wacli

WhatsApp message watcher with terminal UI.

## Structure

- `server/` - Go application built on [whatsmeow](https://github.com/tulir/whatsmeow) that connects to WhatsApp, stores messages to SQLite, and exposes a TCP socket that fans real-time updates out to every connected client
- `tui/` - Python Textual application that renders the entries the server sends it, with j/k navigation and live updates over the socket
- `notifier/` - Python daemon that listens for new message events from the server and raises an rworkspaces attention flag, clearing it again when a message of mine shows I have been in WhatsApp
- `wacli` - the one command-line entry point: `wacli tui` opens the TUI and restarts it after a disconnect, `wacli send` sends a single message to a chat JID so scripts can fire one without knowing the socket protocol. `wacli changes 5h` pages through every message edited or deleted in that window, read from the server's event log. Each subcommand lives in `commands/`
- `shared/connection.py` - server address and TCP keepalive settings, shared by every client

## See also

- [docs/architecture.md](docs/architecture.md) - the process/socket map, the socket protocol, and how disconnects are handled
- [docs/digging-the-event-log.md](docs/digging-the-event-log.md) - tracing a message the server dropped or a deletion took away
