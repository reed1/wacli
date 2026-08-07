import json
from datetime import datetime
from pathlib import Path

RUNTIME_DIR = Path("/tmp/rlocal/wacli")
RUNTIME_DIR.mkdir(parents=True, exist_ok=True)

APP_LOG_FILE = RUNTIME_DIR / "wacli.log"
SUBMIT_LOG_FILE = RUNTIME_DIR / "submitted.jsonl"


def log(msg: str) -> None:
    with open(APP_LOG_FILE, "a") as f:
        f.write(f"{datetime.now().isoformat()} {msg}\n")


def log_submitted_message(chat_jid: str, text: str, action: str) -> None:
    entry = {
        "timestamp": datetime.now().isoformat(),
        "chat_jid": chat_jid,
        "action": action,
        "text": text,
    }
    with open(SUBMIT_LOG_FILE, "a") as f:
        f.write(json.dumps(entry) + "\n")
