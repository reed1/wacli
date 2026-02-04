from datetime import datetime
from pathlib import Path

RUNTIME_DIR = Path("/tmp/rlocal/wacli")
RUNTIME_DIR.mkdir(parents=True, exist_ok=True)

LOG_FILE = RUNTIME_DIR / "wacli.log"

SERVER_ADDR = ("100.97.165.105", 3010)
DB_PATH = Path(__file__).parent.parent / "server" / "messages.db"


def log(msg: str) -> None:
    with open(LOG_FILE, "a") as f:
        f.write(f"{datetime.now().isoformat()} {msg}\n")
