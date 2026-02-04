# wacli

WhatsApp message watcher with terminal UI.

## Structure

- `server/` - Go application built on [whatsmeow](https://github.com/tulir/whatsmeow) that connects to WhatsApp, stores messages to SQLite, and exposes a Unix socket for real-time updates
- `tui/` - Python Textual application that displays messages from the database with j/k navigation and live updates via socket
- `notifier/` - Python daemon that listens for new message events from the server and triggers desktop attention notifications via rworkspaces
