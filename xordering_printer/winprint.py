"""Printers installed in Windows: listing them and printing through the Windows print spooler.

    list_printers()     name, driver, port (USB001, IP_192.168.1.50, BTH…, WSD…) and, for TCP/IP
                        ports, the printer's IP and port (from the port monitor's settings)
    send_raw()          the ticket's printer bytes (Star raster, ESC/POS picture or text) as a RAW
                        job: the driver passes them to the printer untouched, over whatever port
                        Windows uses (USB, network, Bluetooth). This is how the POS's own app prints.
    send_picture()      the ticket picture drawn through the driver (GDI), for drivers that refuse
                        RAW jobs (some newer "v4" drivers)

Either way the job is watched in the Windows queue until it has gone to the printer. A job that is
stuck (printer offline, out of paper, paused, busy with another program) is taken out of the queue
again and reported as a failure, so the ticket stays in our own queue and is retried: never lost,
and never printed twice by Windows later on.

Uses pywin32 (bundled in the exe); listing falls back to PowerShell's Get-Printer without it.
"""
import re
import time
from typing import Any, Dict, List, Optional

from .printer import PrinterError

PRINTER_ENUM_LOCAL = 0x2
PRINTER_ENUM_CONNECTIONS = 0x4

# PRINTER_INFO_2.Status
P_PAUSED, P_ERROR, P_PAPER_JAM, P_PAPER_OUT, P_OFFLINE = 0x1, 0x2, 0x8, 0x10, 0x80
P_NOT_AVAILABLE, P_USER_INTERVENTION, P_DOOR_OPEN = 0x1000, 0x100000, 0x400000
PRINTER_ATTRIBUTE_WORK_OFFLINE = 0x400
# JOB_INFO_1.Status
J_PAUSED, J_ERROR, J_DELETING, J_SPOOLING, J_PRINTING, J_OFFLINE, J_PAPEROUT = 0x1, 0x2, 0x4, 0x8, 0x10, 0x20, 0x40
J_PRINTED, J_DELETED, J_BLOCKED, J_USER_INTERVENTION, J_COMPLETE = 0x80, 0x100, 0x200, 0x400, 0x1000
JOB_CONTROL_DELETE = 5

STATUS_TEXT = [
    (P_PAUSED, "paused 已暂停"), (P_ERROR, "error 错误"), (P_PAPER_JAM, "paper jam 卡纸"), (P_PAPER_OUT, "out of paper 缺纸"),
    (P_OFFLINE, "offline 脱机"), (P_NOT_AVAILABLE, "not available 不可用"), (P_USER_INTERVENTION, "needs attention 需要处理"),
    (P_DOOR_OPEN, "cover open 盖子开着"),
]

# Windows error codes we explain.
ERROR_FILE_NOT_FOUND = 2
ERROR_ACCESS_DENIED = 5
ERROR_INVALID_PRINTER_NAME = 1801
ERROR_INVALID_DATATYPE = 1804
ERROR_UNKNOWN_PRINTER_DRIVER = 1797
RPC_UNAVAILABLE = 1722
ERROR_PRINTER_DRIVER_BLOCKED = 3014


def available() -> bool:
    try:
        import win32print  # noqa: F401
        return True
    except ImportError:
        return False


def status_text(status: int, attributes: int = 0) -> str:
    parts = [t for bit, t in STATUS_TEXT if status & bit]
    if attributes & PRINTER_ATTRIBUTE_WORK_OFFLINE:
        parts.append("set to 'Use printer offline' 已设为脱机使用")
    return ", ".join(parts)


# --- Listing -----------------------------------------------------------------------------------

def _port_settings(port: str) -> Dict[str, Any]:
    """{ip, tcpPort} of a TCP/IP port from any port monitor's registry settings (Standard TCP/IP
    Port, Star's and Epson's monitors …), or from the port name (IP_192.168.1.50, TCP:…:9100)."""
    from . import discover

    out: Dict[str, Any] = {}
    try:
        import winreg

        base = r"SYSTEM\CurrentControlSet\Control\Print\Monitors"
        with winreg.OpenKey(winreg.HKEY_LOCAL_MACHINE, base) as monitors:
            i = 0
            while True:
                try:
                    monitor = winreg.EnumKey(monitors, i)
                except OSError:
                    break
                i += 1
                try:
                    with winreg.OpenKey(monitors, rf"{monitor}\Ports\{port}") as k:
                        vals = {}
                        j = 0
                        while True:
                            try:
                                name, value, _ = winreg.EnumValue(k, j)
                            except OSError:
                                break
                            vals[name.lower()] = value
                            j += 1
                except OSError:
                    continue
                host = str(vals.get("ipaddress") or vals.get("hostname") or vals.get("address") or vals.get("host") or "").strip()
                ip = discover.ip_in(host) or host
                if ip:
                    out["ip"] = ip
                    out["monitor"] = monitor
                    num = vals.get("portnumber") or vals.get("port")
                    try:
                        out["tcpPort"] = int(num) if num else 9100
                    except (TypeError, ValueError):
                        out["tcpPort"] = 9100
                    out["protocol"] = "lpr" if vals.get("protocol") == 2 else "raw"
                    return out
    except (ImportError, OSError):
        pass
    ip = discover.ip_in(port)
    if ip:
        out["ip"] = ip
        out["tcpPort"] = discover.port_in(port) or 9100
    return out


def describe(name: str, driver: str, port: str, status: int = 0, attributes: int = 0, location: str = "", comment: str = "") -> Dict[str, Any]:
    from . import discover

    # A printer can be on several ports ("USB001,USB002"): the first one names it.
    first = (port or "").split(",")[0].strip()
    p: Dict[str, Any] = {"name": name, "driver": driver or "", "port": port or "", "portKind": discover.port_kind(first),
                         "virtual": discover.is_virtual_printer(name, driver, first), "status": status_text(status, attributes),
                         "location": location or "", "comment": comment or "", "ip": "", "tcpPort": None}
    settings = _port_settings(first) if first else {}
    if settings.get("ip"):
        p.update(ip=settings["ip"], tcpPort=settings.get("tcpPort") or 9100, portKind="network", protocol=settings.get("protocol", "raw"))
    return p


def list_printers() -> List[Dict[str, Any]]:
    try:
        import win32print
    except ImportError:
        return _list_powershell()
    out = []
    seen = set()
    for info in win32print.EnumPrinters(PRINTER_ENUM_LOCAL | PRINTER_ENUM_CONNECTIONS, None, 2):
        name = info.get("pPrinterName") or ""
        if not name or name in seen:
            continue
        seen.add(name)
        out.append(describe(name, info.get("pDriverName") or "", info.get("pPortName") or "", int(info.get("Status") or 0),
                            int(info.get("Attributes") or 0), info.get("pLocation") or "", info.get("pComment") or ""))
    return out


def _list_powershell() -> List[Dict[str, Any]]:
    from . import discover

    rows = discover.powershell_json("Get-Printer | Select-Object Name, DriverName, PortName, PrinterStatus")
    ports = {str(r.get("Name")): r for r in discover.powershell_json("Get-PrinterPort | Select-Object Name, PrinterHostAddress, PortNumber")}
    out = []
    for r in rows:
        p = describe(str(r.get("Name") or ""), str(r.get("DriverName") or ""), str(r.get("PortName") or ""))
        port = ports.get(p["port"]) or {}
        if not p["ip"] and port.get("PrinterHostAddress"):
            p.update(ip=discover.ip_in(str(port["PrinterHostAddress"])) or str(port["PrinterHostAddress"]), tcpPort=int(port.get("PortNumber") or 9100), portKind="network")
        out.append(p)
    return out


def default_printer() -> str:
    try:
        import win32print

        return win32print.GetDefaultPrinter() or ""
    except Exception:
        return ""


# --- Printing ------------------------------------------------------------------------------------

def _explain(e: Exception, name: str) -> PrinterError:
    code = getattr(e, "winerror", None) or (e.args[0] if getattr(e, "args", None) and isinstance(e.args[0], int) else None)
    text = getattr(e, "strerror", None) or str(e)
    if code in (ERROR_INVALID_PRINTER_NAME, ERROR_FILE_NOT_FOUND):
        return PrinterError(f"Windows 里找不到打印机 “{name}” (removed or renamed?)", "not_found")
    if code in (ERROR_INVALID_DATATYPE, ERROR_UNKNOWN_PRINTER_DRIVER, ERROR_PRINTER_DRIVER_BLOCKED):
        return PrinterError(f"驱动错误 Driver error on “{name}”: {text}", "driver")
    if code == RPC_UNAVAILABLE:
        return PrinterError("Windows 打印服务（Print Spooler）没有运行 The Windows print spooler isn't running", "spooler")
    if code == ERROR_ACCESS_DENIED:
        return PrinterError(f"没有权限使用打印机 “{name}” Access denied", "driver")
    return PrinterError(f"Windows 打印出错 Windows print error on “{name}”: {text} ({code})", "driver")


def _check_printer(win32print: Any, handle: Any, name: str) -> Dict[str, Any]:
    info = win32print.GetPrinter(handle, 2)
    status, attrs = int(info.get("Status") or 0), int(info.get("Attributes") or 0)
    if attrs & PRINTER_ATTRIBUTE_WORK_OFFLINE:
        raise PrinterError(f"“{name}” 在 Windows 里设成了“脱机使用打印机” is set to Use Printer Offline", "offline")
    if status & P_PAUSED:
        raise PrinterError(f"“{name}” 的 Windows 打印队列已暂停 The Windows queue is paused", "paused")
    return {"status": status, "attributes": attrs, "jobs": int(info.get("cJobs") or 0)}


def _job_reason(job_status: int, printer_status: int, text: str) -> str:
    bits = []
    if job_status & J_PAPEROUT or printer_status & P_PAPER_OUT:
        bits.append("缺纸 out of paper")
    if printer_status & P_DOOR_OPEN:
        bits.append("盖子开着 cover open")
    if job_status & J_OFFLINE or printer_status & P_OFFLINE:
        bits.append("打印机离线 printer offline")
    if job_status & J_BLOCKED:
        bits.append("被前面的任务卡住 blocked by an earlier job")
    if job_status & (J_ERROR | J_USER_INTERVENTION) or printer_status & (P_ERROR | P_USER_INTERVENTION):
        bits.append("打印出错 error")
    if text:
        bits.append(text)
    return ", ".join(bits) or "没有送到打印机 didn't reach the printer"


def wait_for_job(win32print: Any, handle: Any, name: str, job_id: int, timeout: float = 30.0, poll: float = 0.3) -> None:
    """Until the job has gone to the printer; a stuck job is deleted and PrinterError raised.

    Done: the job left the queue, or says printed / complete (a queue that keeps printed jobs).
    Stuck: still there after `timeout`, or in error / offline / paper out for a few seconds.
    """
    started = time.time()
    bad_since: Optional[float] = None
    last: Dict[str, Any] = {}
    while True:
        try:
            job = win32print.GetJob(handle, job_id, 1)
        except Exception:
            return  # no longer in the queue: sent
        st = int(job.get("Status") or 0)
        if st & (J_PRINTED | J_COMPLETE | J_DELETED):
            return
        try:
            pst = int(win32print.GetPrinter(handle, 2).get("Status") or 0)
        except Exception:
            pst = 0
        last = {"job": st, "printer": pst, "text": str(job.get("pStatus") or "")}
        bad = st & (J_ERROR | J_OFFLINE | J_PAPEROUT | J_BLOCKED | J_USER_INTERVENTION | J_PAUSED) or pst & (P_PAPER_OUT | P_DOOR_OPEN | P_PAPER_JAM | P_PAUSED)
        if bad:
            bad_since = bad_since or time.time()
        else:
            bad_since = None
        now = time.time()
        if (bad_since and now - bad_since > 5) or now - started > timeout:
            try:
                win32print.SetJob(handle, job_id, 0, None, JOB_CONTROL_DELETE)
            except Exception:
                pass
            reason = _job_reason(last.get("job", 0), last.get("printer", 0), last.get("text", ""))
            code = "paper_out" if "paper" in reason else "cover_open" if "cover" in reason else "offline"
            raise PrinterError(f"“{name}”: {reason}（已从 Windows 队列撤回，稍后重试 taken back from the Windows queue, will retry）", code)
        time.sleep(poll)


def send_raw(name: str, data: bytes, title: str = "XOrdering ticket", timeout: float = 30.0) -> Dict[str, Any]:
    """The printer bytes as one RAW job; returns {job, method: raw} once it reached the printer."""
    try:
        import win32print
    except ImportError:
        raise PrinterError("Windows 打印机模式只能在 Windows 上使用 Windows printers work on Windows only", "driver") from None
    try:
        handle = win32print.OpenPrinter(name)
    except Exception as e:
        raise _explain(e, name) from None
    try:
        _check_printer(win32print, handle, name)
        try:
            job_id = win32print.StartDocPrinter(handle, 1, (title, None, "RAW"))
        except Exception as e:
            raise _explain(e, name) from None
        try:
            win32print.StartPagePrinter(handle)
            written = win32print.WritePrinter(handle, data)
            win32print.EndPagePrinter(handle)
        except Exception as e:
            try:
                win32print.SetJob(handle, job_id, 0, None, JOB_CONTROL_DELETE)
            except Exception:
                pass
            win32print.EndDocPrinter(handle)
            raise _explain(e, name) from None
        win32print.EndDocPrinter(handle)
        if written is not None and int(written) < len(data):
            raise PrinterError(f"“{name}”: only {written} of {len(data)} bytes accepted", "driver")
        wait_for_job(win32print, handle, name, job_id, timeout)
        return {"job": job_id, "method": "raw"}
    finally:
        win32print.ClosePrinter(handle)


def send_picture(name: str, images: List[Any], title: str = "XOrdering ticket", timeout: float = 30.0) -> Dict[str, Any]:
    """The ticket pictures drawn through the printer's driver (GDI), one page per ticket, scaled to
    the printable width; a ticket longer than the driver's page goes on several pages."""
    try:
        import win32print
        import win32ui
        from PIL import ImageWin
    except ImportError:
        raise PrinterError("Windows 打印机模式只能在 Windows 上使用 Windows printers work on Windows only", "driver") from None
    try:
        handle = win32print.OpenPrinter(name)
    except Exception as e:
        raise _explain(e, name) from None
    try:
        _check_printer(win32print, handle, name)
        dc = win32ui.CreateDC()
        try:
            dc.CreatePrinterDC(name)
            width, height = dc.GetDeviceCaps(8), dc.GetDeviceCaps(10)  # HORZRES, VERTRES
            job_id = dc.StartDoc(title)
            for img in images:
                rgb = img.convert("RGB")
                scale = width / rgb.size[0]
                page_rows = max(1, int(height / scale)) if height > 0 else rgb.size[1]
                for top in range(0, rgb.size[1], page_rows):
                    part = rgb.crop((0, top, rgb.size[0], min(rgb.size[1], top + page_rows)))
                    dc.StartPage()
                    ImageWin.Dib(part).draw(dc.GetHandleOutput(), (0, 0, width, int(part.size[1] * scale)))
                    dc.EndPage()
            dc.EndDoc()
        except PrinterError:
            raise
        except Exception as e:
            raise _explain(e, name) from None
        finally:
            try:
                dc.DeleteDC()
            except Exception:
                pass
        if isinstance(job_id, int) and job_id > 0:
            wait_for_job(win32print, handle, name, job_id, timeout)
        return {"job": job_id, "method": "driver"}
    finally:
        win32print.ClosePrinter(handle)


def tcp_target(printer_name: str) -> Dict[str, Any]:
    """The IP and port behind a Windows printer on a TCP/IP port ({} for USB and the like)."""
    for p in list_printers():
        if p["name"] == printer_name and p.get("ip"):
            return {"host": p["ip"], "port": int(p.get("tcpPort") or 9100)}
    return {}


def clean_name(name: str) -> str:
    return re.sub(r"\s+", " ", name or "").strip()
