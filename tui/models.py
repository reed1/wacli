from dataclasses import dataclass
from datetime import datetime


@dataclass
class Message:
    id: int
    message_id: str
    timestamp: int
    chat_jid: str
    chat_name: str
    sender_jid: str
    sender_name: str
    is_group: bool
    is_muted: bool
    is_reply_to_me: bool
    mention: str
    message_type: str
    text: str
    media_file: str | None = None
    transcription: str | None = None
    is_from_me: bool = False
    original_text: str | None = None
    is_deleted: bool = False

    @property
    def display_text(self) -> str:
        return self.text or self.transcription or ""

    @property
    def is_edited(self) -> bool:
        return self.original_text is not None

    @property
    def formatted_time(self) -> str:
        dt = datetime.fromtimestamp(self.timestamp)
        return dt.strftime("%H:%M")

    @property
    def mention_marker(self) -> str:
        if self.mention == "me":
            return "❗ "
        elif self.mention == "all":
            return "📢 "
        elif self.mention == "":
            return ""
        else:
            raise ValueError(f"Unexpected mention: {self.mention}")

    @property
    def title(self) -> str:
        if self.is_from_me:
            if self.is_group:
                return f"→ 👥 {self.chat_name}"
            return f"→ {self.chat_name}"
        reply_marker = "↩ " if self.is_reply_to_me else ""
        return f"{self.mention_marker}{reply_marker}{self.sender_name}"


@dataclass
class Call:
    id: int
    timestamp: int
    call_id: str
    caller_jid: str
    caller_name: str
    is_group: bool
    group_jid: str
    group_name: str

    @property
    def formatted_time(self) -> str:
        dt = datetime.fromtimestamp(self.timestamp)
        return dt.strftime("%H:%M")

    @property
    def title(self) -> str:
        if self.is_group and self.group_name:
            return f"{self.caller_name} @ {self.group_name}"
        return self.caller_name


Entry = Message | Call
