# wacli

WhatsApp message watcher with terminal UI.

## Structure

- `server/` - Go application built on [whatsmeow](https://github.com/tulir/whatsmeow) that connects to WhatsApp, stores messages to SQLite, and exposes a TCP socket that fans real-time updates out to every connected client
- `tui/` - Python Textual application that renders the entries the server sends it, with j/k navigation and live updates over the socket
- `notifier/` - Python daemon that listens for new message events from the server and triggers desktop attention notifications via rworkspaces
- `wacli_socket.py` - server address and TCP keepalive settings, shared by the TUI and the notifier

See [docs/architecture.md](docs/architecture.md) for the process/socket map, the socket protocol, and how disconnects are handled.
