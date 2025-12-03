import json
import os
import subprocess
from datetime import datetime
from pathlib import Path

RUNTIME_DIR = Path("/tmp/rlocal/wacli")
RUNTIME_DIR.mkdir(parents=True, exist_ok=True)

LOG_FILE = RUNTIME_DIR / "wacli.log"
LOCK_FILE = RUNTIME_DIR / "tui.json"
SOCKET_PATH = str(RUNTIME_DIR / "wacli.sock")

DB_PATH = Path(__file__).parent.parent / "cli" / "messages.db"


def log(msg: str) -> None:
    with open(LOG_FILE, "a") as f:
        f.write(f"{datetime.now().isoformat()} {msg}\n")


def is_process_running(pid: int) -> bool:
    try:
        os.kill(pid, 0)
        return True
    except OSError:
        return False


def focus_existing_instance() -> bool:
    """Check for existing instance and focus it if valid. Returns True if focused."""
    if not LOCK_FILE.exists():
        return False

    try:
        data = json.loads(LOCK_FILE.read_text())
        pid = data.get("pid")
        window_id = data.get("window_id")

        if not pid or not window_id:
            return False

        if not is_process_running(pid):
            LOCK_FILE.unlink(missing_ok=True)
            return False

        subprocess.run(
            ["i3-msg", f"[id={window_id}] focus"],
            check=True,
            capture_output=True,
        )
        return True
    except (json.JSONDecodeError, subprocess.CalledProcessError, KeyError):
        LOCK_FILE.unlink(missing_ok=True)
        return False


def write_lock_file() -> None:
    pid = os.getpid()
    window_id = os.environ.get("WINDOWID", "")
    LOCK_FILE.write_text(json.dumps({"pid": pid, "window_id": window_id}))
