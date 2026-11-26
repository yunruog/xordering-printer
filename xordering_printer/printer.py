"""Sending bytes to a network receipt printer: raw TCP (usually port 9100), with status checks.

ESC/POS printers are asked for their status (DLE EOT 1, then 2 and 4 when they report being
offline); Star printers (TSP100 series) with ESC ACK SOH (their automatic status, ASB). A printer
that answers "cover open" or "paper out" fails the ticket instead of silently swallowing it.
Printers that don't answer status queries are printed to anyway. After the ticket, one more status
query (answered only once the bytes before it arrived) confirms delivery when the printer answers.

Shared with the restaurant's own POS: a printer takes one connection at a time, so while the POS
prints, port 9100 refuses us or doesn't answer. That is an ordinary failure ("busy"): the ticket
stays queued and is retried with a back-off (jobs.py). Once every byte of a ticket was accepted,
a hiccup while closing the connection no longer counts as a failure: retrying then would print the
ticket twice.
"""
import concurrent.futures
import errno
import ipaddress
import socket
import time
from typing import Dict, List, Optional

from . import star

CONNECT_TIMEOUT = 5.0
STATUS_TIMEOUT = 1.5
DLE_EOT = b"\x10\x04"


class PrinterError(Exception):
    """`code`: timeout | refused | unreachable | busy | lost | cover_open | paper_out | offline."""

    def __init__(self, message: str, code: str = "error"):
        super().__init__(message)
        self.code = code


def _connect(host: str, port: int, timeout: float = CONNECT_TIMEOUT) -> socket.socket:
    try:
        return socket.create_connection((host, int(port)), timeout=timeout)
    except socket.timeout:
        raise PrinterError(f"no answer from {host}:{port} (timed out: printer off, busy or wrong IP?)", "timeout") from None
    except ConnectionRefusedError:
        raise PrinterError(f"{host}:{port} refused the connection (printer busy with another computer, or wrong port)", "refused") from None
    except OSError as e:
        if e.errno in (errno.EHOSTUNREACH, errno.ENETUNREACH, getattr(errno, "WSAEHOSTUNREACH", -1), getattr(errno, "WSAENETUNREACH", -1)):
            raise PrinterError(f"{host} can't be reached from this computer (other network?)", "unreachable") from None
        if e.errno in (getattr(errno, "WSAECONNREFUSED", -1),):
            raise PrinterError(f"{host}:{port} refused the connection (printer busy, or wrong port)", "refused") from None
        if isinstance(e, socket.gaierror):
            raise PrinterError(f"unknown printer address {host}", "unreachable") from None
        raise PrinterError(f"can't connect to {host}:{port}: {e.strerror or e}", "unreachable") from None


# --- ESC/POS status ----------------------------------------------------------------------------

def _status(sock: socket.socket, n: int) -> Optional[int]:
    """One real-time status byte (DLE EOT n), or None when the printer doesn't answer."""
    try:
        sock.sendall(DLE_EOT + bytes([n]))
        sock.settimeout(STATUS_TIMEOUT)
        data = sock.recv(1)
    except socket.timeout:
        return None
    except OSError as e:
        raise PrinterError(f"connection lost: {e}", "lost") from None
    finally:
        sock.settimeout(CONNECT_TIMEOUT)
    if not data:
        raise PrinterError("printer closed the connection (busy?)", "busy")
    b = data[0]
    # Status bytes always have bit 1 and bit 4 set and bits 0 and 7 clear.
    return b if (b & 0x93) == 0x12 else None


def _escpos_ready(sock: socket.socket) -> Dict[str, bool]:
    s1 = _status(sock, 1)
    if s1 is None:
        return {}
    if not s1 & 0x08:  # online
        return {"ok": True}
    s2 = _status(sock, 2)
    s4 = _status(sock, 4)
    if s2 is not None and s2 & 0x04:
        raise PrinterError("printer cover open", "cover_open")
    if (s2 is not None and s2 & 0x20) or (s4 is not None and s4 & 0x60):
        raise PrinterError("printer out of paper", "paper_out")
    raise PrinterError("printer offline", "offline")


# --- Star status (ASB) ----------------------------------------------------------------------------

def _star_status(sock: socket.socket, timeout: float = STATUS_TIMEOUT) -> Optional[Dict[str, bool]]:
    try:
        sock.sendall(star.STATUS_REQUEST)
        sock.settimeout(timeout)
        data = sock.recv(2)
        if data and data[0] in star.ASB_LENGTH:
            want = star.ASB_LENGTH[data[0]]
            deadline = time.time() + timeout
            while len(data) < want and time.time() < deadline:
                more = sock.recv(want - len(data))
                if not more:
                    break
                data += more
    except socket.timeout:
        return None
    except OSError as e:
        raise PrinterError(f"connection lost: {e}", "lost") from None
    finally:
        sock.settimeout(CONNECT_TIMEOUT)
    if not data:
        raise PrinterError("printer closed the connection (busy?)", "busy")
    return star.parse_status(data)


def _star_ready(sock: socket.socket) -> Dict[str, bool]:
    st = _star_status(sock)
    if st is None:
        return {}
    if st["coverOpen"]:
        raise PrinterError("printer cover open", "cover_open")
    if st["paperEmpty"]:
        raise PrinterError("printer out of paper", "paper_out")
    if st["offline"]:
        raise PrinterError("printer offline", "offline")
    return {"ok": True, "paperLow": st["paperLow"]}


# --- Sending -------------------------------------------------------------------------------------

def send(host: str, port: int, payload: bytes, *, check_status: bool = True, kind: str = "escpos") -> Dict[str, bool]:
    """Sends one ticket (or several); raises PrinterError when it wasn't delivered.

    Returns what the printer said about itself ({} when it doesn't answer status queries).
    """
    sock = _connect(host, port)
    status: Dict[str, bool] = {}
    try:
        sock.setsockopt(socket.IPPROTO_TCP, socket.TCP_NODELAY, 1)
        if check_status:
            status = (_star_ready if kind == "star" else _escpos_ready)(sock)
        try:
            sock.sendall(payload)
        except OSError as e:
            raise PrinterError(f"sending to {host}:{port} failed: {e}", "lost") from None
        # Every byte was accepted. From here on, problems are logged, not retried (no double tickets),
        # except a reset: the printer threw the connection away with the ticket still unread.
        try:
            if check_status and status:
                if kind == "star":
                    _star_status(sock, timeout=10.0)
                else:
                    _status(sock, 1)
            sock.shutdown(socket.SHUT_WR)
            sock.settimeout(2.0)
            deadline = time.time() + 2.0
            while time.time() < deadline and sock.recv(256):
                pass
        except ConnectionResetError:
            raise PrinterError(f"{host}:{port} reset the connection before reading the ticket", "lost") from None
        except (OSError, PrinterError):
            pass
    finally:
        sock.close()
    return status


def probe(host: str, port: int = 9100, timeout: float = 3.0, kind: str = "escpos") -> Dict[str, object]:
    """Test connection: {ok, ms, status} or raises PrinterError with the reason (timeout / refused / …)."""
    started = time.time()
    sock = _connect(host, port, timeout=timeout)
    try:
        ms = int((time.time() - started) * 1000)
        try:
            st = _star_status(sock) if kind == "star" else ({"answered": _status(sock, 1) is not None})
        except PrinterError:
            st = None
        return {"ok": True, "ms": ms, "status": st}
    finally:
        sock.close()


# --- Finding printers on the local network -----------------------------------------------------

def local_networks() -> List[ipaddress.IPv4Network]:
    """The /24 networks of this computer's IPv4 addresses (not loopback)."""
    addrs = set()
    try:
        with socket.socket(socket.AF_INET, socket.SOCK_DGRAM) as s:
            s.connect(("10.255.255.255", 1))  # no packet is sent; picks the outgoing interface
            addrs.add(s.getsockname()[0])
    except OSError:
        pass
    try:
        for a in socket.gethostbyname_ex(socket.gethostname())[2]:
            addrs.add(a)
    except OSError:
        pass
    nets = []
    for a in sorted(addrs):
        try:
            ip = ipaddress.IPv4Address(a)
        except ValueError:
            continue
        if ip.is_loopback or ip.is_link_local:
            continue
        net = ipaddress.IPv4Network(f"{a}/24", strict=False)
        if net not in nets:
            nets.append(net)
    return nets


def _open(host: str, port: int, timeout: float) -> bool:
    try:
        with socket.create_connection((host, port), timeout=timeout):
            return True
    except OSError:
        return False


def scan(port: int = 9100, networks: Optional[List[ipaddress.IPv4Network]] = None, timeout: float = 0.6, workers: int = 96) -> List[str]:
    """IP addresses on the local network(s) with `port` open (printers), sorted."""
    hosts = [str(h) for net in (networks if networks is not None else local_networks()) for h in net.hosts()]
    found = []
    with concurrent.futures.ThreadPoolExecutor(max_workers=workers) as pool:
        for host, ok in zip(hosts, pool.map(lambda h: _open(h, port, timeout), hosts)):
            if ok:
                found.append(host)
    return sorted(found, key=lambda h: tuple(int(x) for x in h.split(".")))
