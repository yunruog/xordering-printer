"""Getting tickets to one printer, whichever way this computer reaches it.

    connection "tcp"       straight to IP:port (9100), raw bytes, status checked (printer.py)
    connection "windows"   through the Windows printer of that name (winprint.py): RAW bytes, or
                           the picture through the driver when the driver refuses RAW jobs

Failures say why in words a restaurant can act on (PrinterError.code):
    in_use        another program (the POS: qMenu …) is connected to the printer right now: the
                  ticket waits in the queue and is retried (a network printer takes one connection)
    wrong_subnet  the printer's address isn't on any of this computer's networks
    timeout / refused / unreachable / offline / paper_out / cover_open / busy / lost
    not_found / driver / paused / spooler    (Windows printers)
"""
import ipaddress
from typing import Any, Callable, Dict, List, Optional

from . import escpos, render
from .printer import PrinterError, send
from .ticket import Line

Sender = Callable[..., Dict[str, Any]]
CONNECT_CODES = ("refused", "timeout", "busy", "lost", "unreachable")


def connection(printer: Dict[str, Any]) -> str:
    return "windows" if printer.get("connection") == "windows" else "tcp"


def describe(printer: Dict[str, Any]) -> str:
    if connection(printer) == "windows":
        return f"Windows 打印机 “{printer.get('windowsPrinter', '')}”"
    return f"IP {printer.get('host', '')}:{printer.get('port') or 9100}"


def on_local_network(host: str) -> Optional[bool]:
    """Whether an IPv4 address is on one of this computer's networks (None: can't tell)."""
    try:
        ip = ipaddress.IPv4Address(host)
    except ValueError:
        return None
    if ip.is_loopback:
        return True
    try:
        from . import discover

        nets = [ipaddress.IPv4Network(i["network"], strict=False) for i in discover.interfaces() if i["skipped"] in ("", "down")]
    except Exception:
        return None
    if not nets:
        return None
    return any(ip in n for n in nets)


def explain_tcp_failure(host: str, err: PrinterError, who_uses: Optional[Callable[[str], str]] = None) -> PrinterError:
    """A connection failure, explained: a program holding the printer, or the wrong network."""
    if err.code not in CONNECT_CODES:
        return err
    if who_uses is None:
        from .discover import who_uses as who_uses_fn

        who_uses = who_uses_fn
    try:
        user = who_uses(host)
    except Exception:
        user = ""
    if user:
        return PrinterError(f"打印机正被 {user} 使用，订单在排队，会自动重试 The printer is in use by {user}: the ticket waits and is retried", "in_use")
    if err.code in ("timeout", "unreachable") and on_local_network(host) is False:
        return PrinterError(f"网段不对：{host} 不在这台电脑的网络里 {host} isn't on this computer's network (wrong IP or another network)", "wrong_subnet")
    return err


def pictures(tickets: List[List[Line]], printer: Dict[str, Any]) -> List[Any]:
    return [render.render(t, escpos.paper(printer)) for t in tickets]


def deliver(printer: Dict[str, Any], tickets: List[List[Line]], *, sender: Sender = send, who_uses: Optional[Callable[[str], str]] = None,
            title: str = "XOrdering ticket") -> Dict[str, Any]:
    """Prints the tickets on the printer; returns what the printer said ({} when nothing), with
    `method` (tcp | raw | driver). Raises PrinterError (explained) when they didn't get there."""
    if connection(printer) == "windows":
        from . import winprint

        name = printer.get("windowsPrinter") or ""
        if not name:
            raise PrinterError("没有选择 Windows 打印机 No Windows printer chosen", "not_found")
        if printer.get("windowsMethod") == "driver":
            return winprint.send_picture(name, pictures(tickets, printer), title)
        try:
            return winprint.send_raw(name, escpos.tickets_bytes(tickets, printer), title)
        except PrinterError as e:
            if e.code != "driver":
                raise
            # The driver won't take raw bytes: draw the ticket through it instead.
            st = winprint.send_picture(name, pictures(tickets, printer), title)
            st["fallback"] = str(e)
            return st
    host, port = (printer.get("host") or "").strip(), int(printer.get("port") or 9100)
    if not host:
        raise PrinterError("没有填 IP 地址 No IP address", "unreachable")
    kind = "star" if escpos.kind(printer) == "star" else "escpos"
    try:
        st = sender(host, port, escpos.tickets_bytes(tickets, printer), kind=kind) or {}
    except PrinterError as e:
        raise explain_tcp_failure(host, e, who_uses) from None
    return dict(st, method="tcp")
