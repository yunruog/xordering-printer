"""The one way every ticket is printed: orders, the dashboard's test page, this window's test page and
connection test, and "Reprint last order" all go through print_tickets():

    build      escpos.tickets_bytes(): the ticket drawn at the printer's paper width (render.py),
               made black and white, as the printer type's commands (ESC/POS picture GS v 0, Star
               raster, or ESC/POS text). Nothing else ever makes printer bytes.
    check      validate(): the bytes are well formed for that type (they start with the type's
               init command, every picture row is exactly the paper's width in dots), the ticket
               isn't longer than 3x what its content needs, and the type agrees with the Windows
               driver (Star raster sent to an ESC/POS printer prints metres of garbage). A ticket
               that fails is NOT sent (BadTicket): the window and the dashboard say why.
    send       deliver(): RAW bytes to the Windows printer (winspool WritePrinter, datatype RAW),
               straight to IP:9100, or the same picture through the driver (GDI) when the driver
               refuses RAW jobs.
    log        one PRINTJOB line per attempt in printer.log and print-jobs.jsonl: where it came from
               (order / test / reprint), order and job id, attempt, type, paper, connection, API and
               datatype, byte count, the first 64 and last 32 bytes in hex, and the result.
               "Copy log" in the window copies them (copy_text()).

Why it exists: on 2026-11-27 an ESC/POS printer was saved as "Star raster" after its test print, so
the next order went out as Star raster commands and printed as a long strip of text garbage (each
raster row's header 62 48 00 printed as "bH"). check() now refuses that combination and the log
shows the mode and bytes of every ticket.
"""
import json
import re
import time
from typing import Any, Callable, Dict, List, Optional

from . import escpos, render, star
from .logs import get as get_log
from .paths import log_dir
from .printer import PrinterError
from .ticket import Line, layout

LENGTH_FACTOR = 3  # a ticket longer than 3x its content's normal height is refused
MIN_ROWS = 600  # never refuse a short ticket for length (a little over 7 cm)
JOURNAL_KEEP = 300


class BadTicket(PrinterError):
    """The bytes failed the check: they were not sent. Retrying won't help (same input, same bytes)."""

    def __init__(self, message: str):
        super().__init__(message, "bad_ticket")


# --- Build ---------------------------------------------------------------------------------------

def build(printer: Dict[str, Any], tickets: List[List[Line]]) -> bytes:
    return escpos.tickets_bytes(tickets, printer)


# --- Check ---------------------------------------------------------------------------------------

def nominal_rows(lines: List[Line], paper_width: int) -> int:
    """About how many dot rows the ticket's content needs (render.py's line heights)."""
    scale = render.dots_for(paper_width) / 576
    rows = 0
    for line in lines:
        if line.kind == "feed":
            rows += 16
        elif line.kind == "rule":
            rows += 18
        elif line.kind == "double":
            rows += 22
        else:
            n = max(1, len(layout(line, paper_width)))
            rows += int(render.SIZES.get(line.size, render.SIZES[1]) * 1.32) * n + (24 if line.invert else 0)
    return int(rows * scale) + 12


def row_limit(tickets: List[List[Line]], paper_width: int) -> int:
    return max(MIN_ROWS, LENGTH_FACTOR * sum(nominal_rows(t, paper_width) for t in tickets))


def _picture_rows(data: bytes, dots: int) -> int:
    """Walks ESC/POS picture bytes exactly as escpos.picture_bytes() writes them; the rows printed."""
    xbytes = dots // 8
    i, rows, n = 0, 0, len(data)
    tail = escpos.ESC + b"d\x03" + escpos.CUT
    while i < n:
        if data[i:i + 5] != escpos.INIT + escpos.ESC + b"a\x00":
            raise BadTicket(f"ESC/POS 图片数据开头不对 picture data doesn't start with ESC @ at byte {i}: {data[i:i + 8].hex(' ')}")
        i += 5
        bands = 0
        while data[i:i + 4] == escpos.GS + b"v0\x00":
            xb = data[i + 4] | data[i + 5] << 8
            h = data[i + 6] | data[i + 7] << 8
            if xb != xbytes:
                raise BadTicket(f"图片宽度 {xb * 8} 点，纸宽应为 {dots} 点 picture is {xb * 8} dots wide, the paper takes {dots}")
            if h == 0 or h > 128:
                raise BadTicket(f"图片分段高度不对 bad band height {h}")
            i += 8 + xb * h
            if i > n:
                raise BadTicket("图片数据被截断 picture data cut short")
            rows += h
            bands += 1
        if not bands or data[i:i + len(tail)] != tail:
            raise BadTicket(f"图片数据结尾不对 picture data doesn't end with feed + cut at byte {i}")
        i += len(tail)
    return rows


def _star_rows(data: bytes, dots: int) -> int:
    """Walks Star raster bytes exactly as star.raster_bytes() writes them; the rows printed."""
    head = star.INIT + star.ENTER + star.PAGE_CONTINUOUS + star.FF_CUT
    end = star.FORM_FEED + star.QUIT
    i, rows, n = 0, 0, len(data)
    while i < n:
        if data[i:i + len(head)] != head:
            raise BadTicket(f"Star 光栅数据开头不对 Star raster doesn't start with ESC * r R at byte {i}: {data[i:i + 8].hex(' ')}")
        i += len(head)
        while i < n and data[i:i + len(end)] != end:
            if data[i] == 0x62:  # b n1 n2 data
                k = data[i + 1] | data[i + 2] << 8
                if k == 0 or k > dots // 8:
                    raise BadTicket(f"Star 光栅行宽 {k} 字节超过纸宽 {dots // 8} raster row of {k} bytes, paper takes {dots // 8}")
                i += 3 + k
                rows += 1
            elif data[i:i + 4] == star.ESC + b"*rY":
                j = data.index(b"\x00", i + 4)
                rows += int(data[i + 4:j] or b"0")
                i = j + 1
            else:
                raise BadTicket(f"Star 光栅数据不对 unexpected Star raster byte {data[i]:02x} at {i}")
        if data[i:i + len(end)] != end:
            raise BadTicket("Star 光栅数据被截断 Star raster cut short")
        i += len(end)
    return rows


# ESC/POS makes and models. "Generic / Text Only" is not one: Star printers are installed with it too.
ESCPOS_DRIVER = re.compile(r"\bpos\b|pos-?\d\d|thermal|receipt|xprinter|\bxp-|rongta|bixolon|\bsrp-|"
                           r"citizen|\bct-s|sewoo|zjiang|hprt|munbyn|gprinter|\bgp-|epson|\btm-|80\s?mm|58\s?mm", re.I)


def type_conflict(printer: Dict[str, Any], driver: str) -> str:
    """Why the printer type can't be right for this Windows driver ('' when it may be)."""
    from .discover import STAR

    if not driver or escpos.kind(printer) != "star":
        return ""
    # The Windows printer's own name counts too ("Star TSP143" on the Generic / Text Only driver).
    if ESCPOS_DRIVER.search(driver) and not STAR.search(driver) and not STAR.search(printer.get("windowsPrinter") or ""):
        return (f"打印机类型设成了“Star 光栅”，但 Windows 驱动是 “{driver}”（ESC/POS 打印机）：会打出一长串乱码，已拦截。请在打印机设置里把类型改成“ESC/POS 图片”。"
                f" The type is Star raster but the Windows driver “{driver}” is an ESC/POS printer: it would print metres of garbage, so it was stopped. Set the type to ESC/POS picture.")
    return ""


def windows_driver(printer: Dict[str, Any]) -> str:
    if printer.get("connection") != "windows":
        return ""
    try:
        from . import winprint

        return winprint.driver_name(printer.get("windowsPrinter") or "")
    except Exception:
        return ""


def validate(printer: Dict[str, Any], data: bytes, tickets: List[List[Line]], driver: str = "") -> Dict[str, Any]:
    """Raises BadTicket when the bytes must not go to this printer; else {rows, limit}."""
    k = escpos.kind(printer)
    paper = escpos.paper(printer)
    dots = render.dots_for(paper)
    conflict = type_conflict(printer, driver)
    if conflict:
        raise BadTicket(conflict)
    if not data:
        raise BadTicket("没有打印数据 nothing to print")
    limit = row_limit(tickets, paper)
    if k == "image":
        rows = _picture_rows(data, dots)
    elif k == "star":
        rows = _star_rows(data, dots)
    else:
        if not data.startswith(escpos.INIT + escpos.CHINESE_ON) or not data.endswith(escpos.CUT):
            raise BadTicket("ESC/POS 文字数据格式不对 ESC/POS text doesn't start with ESC @ or end with a cut")
        cols = escpos.columns_for(printer)
        text_rows = sum(len(layout(line, paper)) or 1 for t in tickets for line in t)
        byte_limit = LENGTH_FACTOR * (text_rows * (cols * 2 + 16) + 64 * len(tickets))
        if len(data) > byte_limit:
            raise BadTicket(f"文字数据过长 {len(data)} 字节（上限 {byte_limit}）text is {len(data)} bytes, at most {byte_limit}")
        return {"rows": text_rows, "limit": byte_limit}
    if rows > limit:
        raise BadTicket(f"小票过长：{rows} 行点（{rows / 8:.0f} mm），正常不超过 {limit} 行点，已拦截。ticket is {rows} dot rows ({rows / 8:.0f} mm), at most {limit}: not sent")
    return {"rows": rows, "limit": limit}


# --- Log -----------------------------------------------------------------------------------------

API = {"raw": ("winspool WritePrinter", "RAW"), "driver": ("GDI StartDoc (driver)", "EMF"), "tcp": ("TCP socket", "RAW")}


def journal_path():
    return log_dir() / "print-jobs.jsonl"


def record(entry: Dict[str, Any]) -> None:
    """One print attempt: in printer.log (PRINTJOB …) and print-jobs.jsonl (last JOURNAL_KEEP)."""
    get_log().info("PRINTJOB %s", json.dumps(entry, ensure_ascii=False, sort_keys=True))
    path = journal_path()
    try:
        try:
            lines = path.read_text("utf-8").splitlines()[-(JOURNAL_KEEP - 1):]
        except OSError:
            lines = []
        lines.append(json.dumps(entry, ensure_ascii=False, sort_keys=True))
        tmp = path.with_suffix(".tmp")
        tmp.write_text("\n".join(lines) + "\n", "utf-8")
        tmp.replace(path)
    except OSError:
        pass


def journal(limit: int = 50) -> List[Dict[str, Any]]:
    try:
        rows = journal_path().read_text("utf-8").splitlines()[-limit:]
    except OSError:
        return []
    out = []
    for r in rows:
        try:
            out.append(json.loads(r))
        except ValueError:
            pass
    return out


# --- Print ---------------------------------------------------------------------------------------

def print_tickets(printer: Dict[str, Any], tickets: List[List[Line]], *, source: str, title: str, job_id: str = "", order: Optional[Dict[str, Any]] = None,
                  attempt: int = 1, sender: Optional[Callable[..., Dict[str, Any]]] = None, who_uses: Optional[Callable[[str], str]] = None,
                  driver: Optional[str] = None) -> Dict[str, Any]:
    """Builds, checks, sends and logs. Returns the printer's status ({method, …}); raises
    BadTicket (not sent) or PrinterError (sending failed)."""
    from .deliver import connection, deliver
    from .printer import send

    order = order or {}
    data = build(printer, tickets)
    entry: Dict[str, Any] = {
        "at": time.strftime("%Y-%m-%d %H:%M:%S"), "source": source, "job": job_id, "orderId": order.get("id") or "",
        "order": order.get("number"), "attempt": attempt, "printer": printer.get("name"), "printerId": printer.get("id") or "",
        "mode": escpos.kind(printer), "paper": escpos.paper(printer), "dots": render.dots_for(escpos.paper(printer)),
        "connection": connection(printer), "target": printer.get("windowsPrinter") if connection(printer) == "windows" else f"{printer.get('host')}:{printer.get('port') or 9100}",
        "tickets": len(tickets), "bytes": len(data), "head": data[:64].hex(" "), "tail": data[-32:].hex(" "),
    }
    try:
        drv = windows_driver(printer) if driver is None else driver
        if drv:
            entry["driver"] = drv
        entry.update(validate(printer, data, tickets, drv))
    except BadTicket as e:
        entry.update(result="refused", error=str(e), api="-", datatype="-")
        record(entry)
        raise
    planned = "tcp" if connection(printer) == "tcp" else ("driver" if printer.get("windowsMethod") == "driver" else "raw")
    try:
        st = deliver(printer, tickets, data=data, sender=sender or send, who_uses=who_uses, title=title)
    except PrinterError as e:
        api, datatype = API[planned]
        entry.update(result="error", code=e.code, error=str(e), api=api, datatype=datatype)
        record(entry)
        raise
    method = (st or {}).get("method") or planned
    api, datatype = API.get(method, (method, "?"))
    entry.update(result="sent", api=api, datatype=datatype, method=method)
    if st.get("fallback"):
        entry["fallback"] = st["fallback"]
    record(entry)
    return st


# --- Copy log ------------------------------------------------------------------------------------

def copy_text(cfg: Dict[str, Any], status: Dict[str, Any], log_lines: int = 150) -> str:
    """What "Copy log" puts on the clipboard: version, printers, queue, the last print jobs, the log's
    tail. No device token or key (the log redacts them; the config's token isn't included)."""
    from . import VERSION, config

    out = [f"XOrdering Printer v{VERSION} · {time.strftime('%Y-%m-%d %H:%M:%S')} · state {status.get('state')} · paused {bool(cfg.get('paused'))}"]
    out.append(f"queue {json.dumps(status.get('queue') or {})}")
    for p in config.effective_printers(cfg):
        out.append(f"printer {p.get('name')} id={p.get('id')} type={p.get('render')} paper={escpos.paper(p)}mm connection={p.get('connection')} "
                   f"target={p.get('windowsPrinter') or p.get('host')} method={p.get('windowsMethod') or '-'} enabled={p.get('enabled', True)}")
    out.append("")
    out.append("--- print jobs (newest last) ---")
    for e in journal(30):
        out.append(json.dumps(e, ensure_ascii=False, sort_keys=True))
    out.append("")
    out.append("--- printer.log ---")
    try:
        text = (log_dir() / "printer.log").read_text("utf-8", errors="replace").splitlines()[-log_lines:]
    except OSError:
        text = []
    out.extend(text)
    return "\n".join(out)
