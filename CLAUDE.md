# wacli

WhatsApp message watcher with terminal UI.

## Structure

- `cli/` - Go application built on [whatsmeow](https://github.com/tulir/whatsmeow) that connects to WhatsApp, stores messages to SQLite, and exposes a Unix socket for real-time updates
- `tui/` - Python Textual application that displays messages from the database with j/k navigation and live updates via socket

## Configuration

Copy `cli/.env.example` to `cli/.env`:

- `INCLUDE_STATUS_MESSAGES` - Include status/story updates (default: false)
- `INCLUDE_MUTED_MESSAGES` - Include messages from muted chats (default: false)

## Behavior

Messages from muted chats are excluded unless:
- You are mentioned (@you)
- Someone replies to your message

These bypass the mute filter.
