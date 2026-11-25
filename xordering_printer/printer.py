"""Sending bytes to a network receipt printer: raw TCP (usually port 9100), with status checks.

Before a ticket, the printer is asked for its status (DLE EOT 1, then 2 and 4 when it reports
being offline): a printer that answers "cover open" or "paper out" fails the job instead of
silently swallowing it. Printers that don't answer status queries are printed to anyway. After the
ticket, one more status query (answered only once the bytes before it arrived) confirms delivery.
"""
import socket
import time
from typing import Optional

CONNECT_TIMEOUT = 5.0
STATUS_TIMEOUT = 1.5
DLE_EOT = b"\x10\x04"


class PrinterError(Exception):
    pass


def _status(sock: socket.socket, n: int) -> Optional[int]:
    """One real-time status byte (DLE EOT n), or None when the printer doesn't answer."""
    try:
        sock.sendall(DLE_EOT + bytes([n]))
        sock.settimeout(STATUS_TIMEOUT)
        data = sock.recv(1)
    except socket.timeout:
        return None
    except OSError as e:
        raise PrinterError(f"connection lost: {e}") from None
    finally:
        sock.settimeout(CONNECT_TIMEOUT)
    if not data:
        raise PrinterError("printer closed the connection")
    b = data[0]
    # Status bytes always have bit 1 and bit 4 set and bits 0 and 7 clear.
    return b if (b & 0x93) == 0x12 else None


def _check_ready(sock: socket.socket) -> None:
    s1 = _status(sock, 1)
    if s1 is None or not s1 & 0x08:  # no answer, or online
        return
    s2 = _status(sock, 2)
    s4 = _status(sock, 4)
    if s2 is not None and s2 & 0x04:
        raise PrinterError("printer cover open")
    if (s2 is not None and s2 & 0x20) or (s4 is not None and s4 & 0x60):
        raise PrinterError("printer out of paper")
    raise PrinterError("printer offline")


def send(host: str, port: int, payload: bytes, *, check_status: bool = True) -> None:
    """Sends one ticket; raises PrinterError when it can't be delivered."""
    try:
        sock = socket.create_connection((host, int(port)), timeout=CONNECT_TIMEOUT)
    except socket.timeout:
        raise PrinterError(f"no answer from {host}:{port} (printer off or wrong IP?)") from None
    except OSError as e:
        raise PrinterError(f"can't connect to {host}:{port}: {e.strerror or e}") from None
    try:
        sock.setsockopt(socket.IPPROTO_TCP, socket.TCP_NODELAY, 1)
        if check_status:
            _check_ready(sock)
        try:
            sock.sendall(payload)
        except OSError as e:
            raise PrinterError(f"sending to {host}:{port} failed: {e}") from None
        if check_status:
            _status(sock, 1)
        try:
            sock.shutdown(socket.SHUT_WR)
            sock.settimeout(2.0)
            deadline = time.time() + 2.0
            while time.time() < deadline and sock.recv(256):
                pass
        except OSError:
            pass
    finally:
        sock.close()
