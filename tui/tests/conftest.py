import os

os.environ.setdefault("SERVER_HOST", "127.0.0.1")
os.environ.setdefault("SERVER_PORT", "0")

import asyncio
import base64
import io
import json

import pytest
from PIL import Image

from tui import app as app_module


def png_bytes(width: int = 16, height: int = 9) -> bytes:
    buffer = io.BytesIO()
    Image.new("RGB", (width, height), (200, 30, 90)).save(buffer, format="PNG")
    return buffer.getvalue()


def make_message(**overrides) -> dict:
    data = {
        "id": 1,
        "message_id": "m1",
        "timestamp": 1700000000,
        "chat_jid": "111@s.whatsapp.net",
        "chat_name": "Alice",
        "sender_jid": "111@s.whatsapp.net",
        "sender_name": "Alice",
        "is_group": False,
        "is_muted": False,
        "is_reply_to_me": False,
        "is_from_me": False,
        "message_type": "",
        "text": "hello",
        "media_file": None,
        "transcription": None,
        "original_text": None,
        "is_deleted": False,
    }
    data.update(overrides)
    return data


def make_entries(*items: dict) -> dict:
    """Wraps message/call dicts in the server's ordered entries payload."""
    entries = []
    for item in items:
        if "message_id" in item:
            entries.append({"kind": "message", "message": item})
        else:
            entries.append({"kind": "call", "call": item})
    return {"entries": entries}


def make_call(**overrides) -> dict:
    data = {
        "id": 1,
        "timestamp": 1700000000,
        "call_id": "c1",
        "caller_jid": "222@s.whatsapp.net",
        "caller_name": "Bob",
        "is_group": False,
        "group_jid": "",
        "group_name": "",
    }
    data.update(overrides)
    return data


class StubServer:
    """Speaks the wacli server's newline-delimited JSON socket protocol."""

    def __init__(self) -> None:
        self.entries: dict = {"entries": []}
        self.media: dict[str, bytes] = {}
        self.commands: list[dict] = []
        self.writers: list[asyncio.StreamWriter] = []
        self.auto_respond = True
        self._command_received = asyncio.Event()

    async def start(self) -> None:
        self._server = await asyncio.start_server(self._handle, "127.0.0.1", 0)
        self.port = self._server.sockets[0].getsockname()[1]

    async def stop(self) -> None:
        for writer in self.writers:
            writer.close()
        self._server.close()
        await self._server.wait_closed()

    async def _handle(self, reader: asyncio.StreamReader, writer: asyncio.StreamWriter) -> None:
        self.writers.append(writer)
        self._write(writer, {"type": "connection_state", "data": {"connected": True}})
        while True:
            line = await reader.readline()
            if not line:
                return
            cmd = json.loads(line)
            self.commands.append(cmd)
            self._command_received.set()
            if cmd["action"] == "get_entries":
                self._write(writer, {"type": "entries", "data": self.entries})
            elif cmd["action"] == "get_media":
                self._write(
                    writer,
                    {
                        "type": "media",
                        "request_id": cmd["request_id"],
                        "seq": 0,
                        "data": base64.b64encode(self.media[cmd["filename"]]).decode(),
                        "done": True,
                    },
                )
            elif self.auto_respond and cmd.get("request_id"):
                self._write(
                    writer,
                    {"type": "response", "request_id": cmd["request_id"], "success": True},
                )

    def _write(self, writer: asyncio.StreamWriter, obj: dict) -> None:
        writer.write((json.dumps(obj) + "\n").encode())

    def send_event(self, event_type: str, data: dict) -> None:
        for writer in self.writers:
            self._write(writer, {"type": event_type, "data": data})

    async def wait_for_command(self, action: str, timeout: float = 3.0) -> dict:
        async def _wait() -> dict:
            while True:
                for cmd in self.commands:
                    if cmd["action"] == action:
                        return cmd
                self._command_received.clear()
                await self._command_received.wait()

        return await asyncio.wait_for(_wait(), timeout)


@pytest.fixture
async def stub_server(monkeypatch):
    server = StubServer()
    await server.start()
    monkeypatch.setattr(app_module, "SERVER_ADDR", ("127.0.0.1", server.port))
    yield server
    await server.stop()


async def wait_until(condition, timeout: float = 3.0) -> None:
    loop = asyncio.get_running_loop()
    deadline = loop.time() + timeout
    while not condition():
        if loop.time() > deadline:
            raise TimeoutError("condition not met within timeout")
        await asyncio.sleep(0.02)
