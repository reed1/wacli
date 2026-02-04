import os
from datetime import datetime
from pathlib import Path

from dotenv import load_dotenv

load_dotenv(Path(__file__).parent.parent / ".env")

RUNTIME_DIR = Path("/tmp/rlocal/wacli")
RUNTIME_DIR.mkdir(parents=True, exist_ok=True)

LOG_FILE = RUNTIME_DIR / "wacli.log"

SERVER_ADDR = (os.environ["SERVER_HOST"], int(os.environ["SERVER_PORT"]))


def log(msg: str) -> None:
    with open(LOG_FILE, "a") as f:
        f.write(f"{datetime.now().isoformat()} {msg}\n")
