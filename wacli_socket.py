"""How the TUI and the notifier reach the wacli server.

Both are long-lived readers of the same fan-out socket, so both need the same
address and the same keepalive settings. Keeping that here is what stops one
client from silently outliving a connection the other already noticed was dead.
"""

import os
import socket
from pathlib import Path

from dotenv import load_dotenv

load_dotenv(Path(__file__).parent / ".env")

SERVER_ADDR = (os.environ["SERVER_HOST"], int(os.environ["SERVER_PORT"]))


def enable_keepalive(sock: socket.socket) -> None:
    """Make the kernel probe the idle connection so a peer that went away without
    sending a FIN — server restart, dropped VPN route — surfaces as a read error
    instead of a socket that stays ESTABLISHED and silently never delivers again.
    Dead within roughly two minutes."""
    sock.setsockopt(socket.SOL_SOCKET, socket.SO_KEEPALIVE, 1)
    sock.setsockopt(socket.IPPROTO_TCP, socket.TCP_KEEPIDLE, 60)
    sock.setsockopt(socket.IPPROTO_TCP, socket.TCP_KEEPINTVL, 15)
    sock.setsockopt(socket.IPPROTO_TCP, socket.TCP_KEEPCNT, 4)
