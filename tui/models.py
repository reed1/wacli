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
    text: str

    @property
    def formatted_time(self) -> str:
        dt = datetime.fromtimestamp(self.timestamp)
        return dt.strftime("%H:%M")

    @property
    def title(self) -> str:
        prefix = "↩ " if self.is_reply_to_me else ""
        return f"{prefix}{self.sender_name}"


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
