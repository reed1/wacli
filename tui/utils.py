from datetime import datetime
from pathlib import Path

RUNTIME_DIR = Path("/tmp/rlocal/wacli")
RUNTIME_DIR.mkdir(parents=True, exist_ok=True)

LOG_FILE = RUNTIME_DIR / "wacli.log"
SOCKET_PATH = str(RUNTIME_DIR / "wacli.sock")

DB_PATH = Path(__file__).parent.parent / "cli" / "messages.db"


def log(msg: str) -> None:
    with open(LOG_FILE, "a") as f:
        f.write(f"{datetime.now().isoformat()} {msg}\n")
