"""Finding the restaurant's receipt printers without anyone knowing the network.

Six looks, each on its own and none of them fatal when it fails (run() goes on and reports it):

    a. Windows printers      installed printers: name, driver, port (USB001, IP_192.168.1.50, BTH…);
                             Standard TCP/IP ports give their IP and port; PDF / XPS / OneNote / Fax
                             and other virtual printers are left out (winprint.py)
    b. connections           TCP connections this computer has open to printer ports (9100-9102,
                             515, 631, Epson ePOS 8008/8043) and which program holds them: the POS
                             (qMenu …) printing to its printer. Also remembered between looks
                             (seen-connections.json, sampled by the worker), because a POS connects
                             only while it prints.
    c. ARP table             devices this computer talked to recently, on the same networks; the
                             MAC's maker (Star Micronics, Seiko Epson) is a hint
    d. network scan          every active network card's network (not only the first; virtual
                             cards, loopback and 169.254 skipped), printer ports tried in parallel
    e. identify              each candidate's web page title (HTTP 80) and SNMP sysDescr / sysName
                             tell the make and model (Star Micronics TSP143IIILAN, EPSON TM-T88VI …)
    f. USB                   Windows printers on USB ports, and Star / Epson USB devices in the
                             device manager (also ones whose driver isn't installed)

merge() joins what was seen of one printer (one IP address, one Windows printer, one USB device)
into one candidate, guesses its type and paper width (guess()) and ranks the ones most like a
receipt printer first. report() turns it all into text for remote troubleshooting.
"""
import concurrent.futures
import ipaddress
import json
import os
import re
import socket
import subprocess
import time
import urllib.error
import urllib.request
from typing import Any, Callable, Dict, Iterable, List, Optional, Tuple

from . import VERSION
from .paths import IS_WINDOWS, data_dir

PRINT_PORTS = (9100, 9101, 9102, 515, 631, 8008, 8043)
SCAN_PORTS = (9100, 9101, 9102, 515)
PORT_NAMES = {9100: "RAW 9100", 9101: "RAW 9101", 9102: "RAW 9102", 515: "LPD 515", 631: "IPP 631", 8008: "Epson ePOS 8008", 8043: "Epson ePOS 8043"}

# A source of a candidate (shown in the list).
SOURCE_LABELS = {
    "windows": "Windows 打印机 Windows printer",
    "in_use": "被其他程序占用中 In use by",
    "network": "网络 IP Network",
    "usb": "USB",
    "arp": "ARP",
}

STAR = re.compile(r"\bstar\b|star\s*micronics|\btsp\s?\d|tsp1\d\d|futureprnt|mc-print|mcp\d|\bsm-[lst]\d|starprnt|\bmpop\b|\bsp7\d\d|\btup\d", re.I)
EPSON = re.compile(r"epson|\btm-|\btm[tum]\d", re.I)
ESCPOS = re.compile(r"\bpos\b|pos-?\d\d|thermal|receipt|xprinter|\bxp-|rongta|bixolon|\bsrp-|citizen|\bct-s|sewoo|zjiang|hprt|munbyn|gprinter|\bgp-|\b80\s?mm\b|\b58\s?mm\b|ticket|kitchen|后厨|小票|热敏", re.I)
OFFICE = re.compile(r"laserjet|officejet|deskjet|envy|\bhp\b|hewlett|canon|pixma|imageclass|brother|\bmfc-|\bhl-|\bdcp-|xerox|ricoh|kyocera|ecosys|lexmark|konica|bizhub|sharp|samsung|\bdell\b|pantum|oki", re.I)
VIRTUAL = re.compile(r"microsoft print to pdf|xps|onenote|\bfax\b|send to|\bpdf\b|snagit|evernote|anydesk|teamviewer|microsoft shared fax", re.I)
VIRTUAL_PORTS = re.compile(r"^(portprompt:|nul:?|shrfax:|file:|xpsport:|onenote|pdf|microsoft\.)", re.I)
VIRTUAL_NIC = re.compile(r"vethernet|hyper-v|virtualbox|vbox|vmware|vmnet|loopback|pseudo|docker|wsl|bluetooth|tap-|tun\b|utun|tailscale|zerotier|wireguard|vpn|awdl|llw|npcap|anpi|^ap\d", re.I)

# Makers' MAC prefixes (the first three bytes): a hint only, the scan decides.
OUI = {
    "00:11:62": "Star Micronics",
    "00:00:48": "Seiko Epson", "00:26:ab": "Seiko Epson", "64:eb:8c": "Seiko Epson", "ac:18:26": "Seiko Epson",
    "9c:ae:d3": "Seiko Epson", "b0:e8:92": "Seiko Epson", "38:1a:52": "Seiko Epson", "44:d2:44": "Seiko Epson",
    "e0:bb:9e": "Seiko Epson", "a4:ee:57": "Seiko Epson", "64:c6:d2": "Seiko Epson",
}
USB_VENDORS = {"VID_0519": "Star Micronics", "VID_04B8": "Seiko Epson"}

Progress = Callable[[int, int, str], None]


def _run(args: List[str], timeout: float = 15) -> str:
    """A system command's output ('' when it isn't there or fails); no console window on Windows."""
    kwargs: Dict[str, Any] = {"capture_output": True, "timeout": timeout}
    if IS_WINDOWS:
        kwargs["creationflags"] = 0x08000000  # CREATE_NO_WINDOW
    try:
        out = subprocess.run(args, **kwargs).stdout
    except (OSError, subprocess.SubprocessError):
        return ""
    for enc in ("utf-8", "gbk", "latin-1"):
        try:
            return out.decode(enc)
        except UnicodeDecodeError:
            continue
    return ""


def powershell_json(script: str, timeout: float = 20) -> List[Dict[str, Any]]:
    out = _run(["powershell", "-NoProfile", "-NonInteractive", "-ExecutionPolicy", "Bypass", "-Command",
                "[Console]::OutputEncoding=[Text.Encoding]::UTF8; " + script + " | ConvertTo-Json -Depth 3 -Compress"], timeout)
    try:
        data = json.loads(out.strip() or "[]")
    except ValueError:
        return []
    return data if isinstance(data, list) else [data] if isinstance(data, dict) else []


# --- Classifying ----------------------------------------------------------------------------

def brand(text: str) -> str:
    if STAR.search(text or ""):
        return "Star"
    if EPSON.search(text or ""):
        return "Epson"
    return ""


def guess(text: str) -> Dict[str, Any]:
    """{render, paperWidth, receipt, office} from a name, driver, model or web page title.

    Star / TSP → Star raster (80 mm); Epson, TM-, POS, Thermal … → ESC/POS picture (prints Chinese
    on every ESC/POS printer; text mode needs a GBK font); 58 mm when the name says so.
    """
    t = text or ""
    star = bool(STAR.search(t))
    escpos = bool(EPSON.search(t) or ESCPOS.search(t))
    office = bool(OFFICE.search(t)) and not (star or escpos)
    paper = 58 if re.search(r"\b58\s?mm|pos-?58|xp-58|\b58\b", t, re.I) else 80
    if star:
        return {"render": "star", "paperWidth": 80, "receipt": True, "office": False}
    if escpos:
        return {"render": "image", "paperWidth": paper, "receipt": True, "office": False}
    return {"render": "", "paperWidth": paper, "receipt": False, "office": office}


def is_virtual_printer(name: str, driver: str = "", port: str = "") -> bool:
    return bool(VIRTUAL.search(name or "") or VIRTUAL.search(driver or "") or VIRTUAL_PORTS.match(port or ""))


def port_kind(port: str) -> str:
    p = (port or "").strip()
    if re.match(r"^usb\d*", p, re.I) or "usb" in p.lower():
        return "usb"
    if re.match(r"^(bth|bt:|bluetooth)", p, re.I):
        return "bluetooth"
    if re.match(r"^com\d+:?$", p, re.I):
        return "serial"
    if re.match(r"^lpt\d+:?$", p, re.I):
        return "parallel"
    if re.match(r"^wsd", p, re.I):
        return "wsd"
    if re.match(r"^ts\d+", p, re.I):
        return "remote"
    if ip_in(p) or re.match(r"^(ip_|tcp|lan|net)", p, re.I):
        return "network"
    return "other"


IP_RE = re.compile(r"(?<![\d.])((?:25[0-5]|2[0-4]\d|1\d\d|[1-9]?\d)(?:\.(?:25[0-5]|2[0-4]\d|1\d\d|[1-9]?\d)){3})(?![\d.])")


def ip_in(text: str) -> str:
    """The first IPv4 address in a text (a port name like IP_192.168.1.50 or TCP:192.168.1.50:9100)."""
    m = IP_RE.search((text or "").replace("_", " "))
    return m.group(1) if m else ""


def port_in(text: str) -> Optional[int]:
    m = re.search(r"[:_](\d{2,5})$", (text or "").strip())
    return int(m.group(1)) if m and 1 <= int(m.group(1)) <= 65535 else None


def normalize_mac(mac: str) -> str:
    parts = re.split(r"[:-]", (mac or "").strip().lower())
    if len(parts) != 6:
        return ""
    try:
        return ":".join(f"{int(p, 16):02x}" for p in parts)
    except ValueError:
        return ""


def mac_vendor(mac: str) -> str:
    return OUI.get(normalize_mac(mac)[:8], "")


# --- a. Windows printers ---------------------------------------------------------------------

def windows_printers() -> List[Dict[str, Any]]:
    """Installed printers (virtual ones flagged): {name, driver, port, portKind, ip, tcpPort, virtual, status}."""
    if not IS_WINDOWS:
        return []
    from . import winprint

    return winprint.list_printers()


# --- b. Connections to printer ports ---------------------------------------------------------

NETSTAT_LINE = re.compile(r"^\s*TCP\s+(\S+):(\d+)\s+(\S+):(\d+)\s+(\S+)\s+(\d+)\s*$", re.I)


def parse_netstat(text: str) -> List[Dict[str, Any]]:
    """`netstat -ano` (Windows) → [{local, localPort, ip, port, state, pid}] (IPv4 TCP only)."""
    out = []
    for line in (text or "").splitlines():
        m = NETSTAT_LINE.match(line)
        if not m or m.group(3).startswith("["):
            continue
        out.append({"local": m.group(1), "localPort": int(m.group(2)), "ip": m.group(3), "port": int(m.group(4)),
                    "state": m.group(5).upper().replace("_", ""), "pid": int(m.group(6))})
    return out


def parse_tasklist(text: str) -> Dict[int, str]:
    """`tasklist /FO CSV /NH` → {pid: image name}."""
    names = {}
    for line in (text or "").splitlines():
        cells = re.findall(r'"([^"]*)"', line)
        if len(cells) >= 2 and cells[1].isdigit():
            names[int(cells[1])] = cells[0]
    return names


def _psutil_connections() -> Optional[List[Dict[str, Any]]]:
    try:
        import psutil
    except ImportError:
        return None
    conns = []
    try:
        raw = [(c, c.pid) for c in psutil.net_connections(kind="tcp4")]
    except (psutil.AccessDenied, PermissionError, OSError):
        raw = []  # macOS without root: each of this user's programs instead
        for proc in psutil.process_iter(["pid"]):
            try:
                getter = getattr(proc, "net_connections", None) or proc.connections
                raw.extend((c, proc.pid) for c in getter(kind="tcp4"))
            except (psutil.Error, OSError):
                continue
    names: Dict[int, str] = {}
    for c, pid in raw:
        if not c.raddr:
            continue
        if pid and pid not in names:
            try:
                names[pid] = psutil.Process(pid).name()
            except (psutil.Error, OSError):
                names[pid] = ""
        conns.append({"local": c.laddr.ip, "localPort": c.laddr.port, "ip": c.raddr.ip, "port": c.raddr.port,
                      "state": str(c.status).upper().replace("_", ""), "pid": pid or 0, "process": names.get(pid or 0, "")})
    return conns


def tcp_connections() -> List[Dict[str, Any]]:
    conns = _psutil_connections()
    if conns is not None:
        return conns
    if not IS_WINDOWS:
        return []
    conns = parse_netstat(_run(["netstat", "-ano", "-p", "TCP"]))
    names = parse_tasklist(_run(["tasklist", "/FO", "CSV", "/NH"]))
    for c in conns:
        c["process"] = names.get(c["pid"], "")
    return conns


def _own_pids() -> set:
    """This program's processes: this one, and (in the worker) the service that started it. Other
    copies of XOrderingPrinter are recognised by their name."""
    pids = {os.getpid()}
    try:
        import psutil

        parent = psutil.Process().parent()
        if parent is not None and "xorderingprinter" in parent.name().lower().replace(" ", ""):
            pids.add(parent.pid)
    except Exception:
        pass
    return pids


def printer_connections(ports: Iterable[int] = PRINT_PORTS, include_loopback: bool = False) -> List[Dict[str, Any]]:
    """Other programs' TCP connections to printer ports: [{ip, port, state, pid, process}].

    TIME_WAIT entries (a print that just finished; the program is no longer named) count too.
    This program's own connections are left out.
    """
    wanted = set(ports)
    own = _own_pids()
    out = []
    for c in tcp_connections():
        if c["port"] not in wanted or c["state"] in ("LISTEN", "LISTENING", "NONE"):
            continue
        try:
            ip = ipaddress.IPv4Address(c["ip"])
        except ValueError:
            continue
        if (ip.is_loopback and not include_loopback) or ip.is_unspecified or ip.is_multicast:
            continue
        if c.get("pid") in own or "xorderingprinter" in (c.get("process") or "").lower():
            continue
        out.append({"ip": c["ip"], "port": c["port"], "state": c["state"], "pid": c.get("pid") or 0, "process": c.get("process") or ""})
    return out


def seen_path():
    return data_dir() / "seen-connections.json"


def remember_connections(conns: List[Dict[str, Any]], now: Optional[float] = None, keep: float = 7 * 86400) -> Dict[str, Any]:
    """Adds connections to seen-connections.json ({"ip:port": {ip, port, process, first, last}})."""
    now = now or time.time()
    seen = load_seen()
    for c in conns:
        key = f"{c['ip']}:{c['port']}"
        old = seen.get(key) or {"first": now}
        process = c.get("process") or old.get("process") or ""
        seen[key] = {"ip": c["ip"], "port": c["port"], "process": process, "first": old.get("first", now), "last": now}
    seen = {k: v for k, v in seen.items() if now - float(v.get("last") or 0) < keep}
    if conns:
        tmp = str(seen_path()) + ".tmp"
        try:
            with open(tmp, "w", encoding="utf-8") as f:
                json.dump(seen, f)
            os.replace(tmp, seen_path())
        except OSError:
            pass
    return seen


def load_seen() -> Dict[str, Any]:
    try:
        data = json.loads(seen_path().read_text("utf-8"))
        return data if isinstance(data, dict) else {}
    except (OSError, ValueError):
        return {}


def who_uses(host: str, within: float = 180, ports: Iterable[int] = PRINT_PORTS, include_loopback: bool = False) -> str:
    """The program connected to this printer now (or within `within` seconds), '' when none."""
    try:
        now = [c for c in printer_connections(ports, include_loopback=include_loopback) if c["ip"] == host]
    except Exception:
        now = []
    named = [c["process"] for c in now if c["process"]]
    if named:
        return named[0]
    cutoff = time.time() - within
    for v in load_seen().values():
        if v.get("ip") == host and float(v.get("last") or 0) >= cutoff:
            return v.get("process") or "另一个程序 another program"
    if now:
        return "另一个程序 another program"
    return ""


# --- c. ARP table -----------------------------------------------------------------------------

def parse_arp(text: str) -> List[Dict[str, str]]:
    """`arp -a` (Windows or macOS / Linux) → [{ip, mac, interface}] without broadcast / multicast."""
    out = []
    iface = ""
    for line in (text or "").splitlines():
        m = re.match(r"^\s*Interface:\s*([\d.]+)", line, re.I) or re.match(r"^\s*接口:\s*([\d.]+)", line)
        if m:
            iface = m.group(1)
            continue
        m = re.match(r"^\s*([\d.]+)\s+([0-9a-fA-F]{1,2}(?:[-:][0-9a-fA-F]{1,2}){5})\s+(\S+)", line)
        if not m:
            m2 = re.search(r"\(([\d.]+)\) at ([0-9a-fA-F]{1,2}(?::[0-9a-fA-F]{1,2}){5})(?:.* on (\S+))?", line)
            if not m2:
                continue
            ip, mac, nic = m2.group(1), m2.group(2), m2.group(3) or ""
        else:
            ip, mac, nic = m.group(1), m.group(2), iface
        mac = normalize_mac(mac)
        try:
            addr = ipaddress.IPv4Address(ip)
        except ValueError:
            continue
        if not mac or mac == "ff:ff:ff:ff:ff:ff" or mac.startswith("01:00:5e") or addr.is_multicast or ip.endswith(".255"):
            continue
        out.append({"ip": ip, "mac": mac, "interface": nic, "vendor": mac_vendor(mac)})
    return out


def arp_table() -> List[Dict[str, str]]:
    return parse_arp(_run(["arp", "-a"], 10))


# --- d. Networks and the scan ---------------------------------------------------------------

def interfaces() -> List[Dict[str, Any]]:
    """Active, real network cards with IPv4: [{name, ip, prefix, network, scan, skipped}].

    `scan` is what gets scanned: the card's own network, or the /24 around its address when the
    network is bigger (a /16 would be 65 000 addresses). Virtual cards, loopback and 169.254 are
    listed with `skipped` (the report shows them) and not scanned.
    """
    found: List[Dict[str, Any]] = []
    try:
        import psutil

        stats = psutil.net_if_stats()
        for name, addrs in psutil.net_if_addrs().items():
            st = stats.get(name)
            for a in addrs:
                if a.family != socket.AF_INET or not a.address:
                    continue
                found.append({"name": name, "ip": a.address, "netmask": a.netmask or "255.255.255.0", "up": bool(st and st.isup)})
    except Exception:
        from .printer import local_networks

        for net in local_networks():
            found.append({"name": "", "ip": str(next(net.hosts())), "netmask": "255.255.255.0", "up": True, "guessed": True})
    out = []
    for f in found:
        try:
            ip = ipaddress.IPv4Address(f["ip"])
            net = ipaddress.IPv4Network(f"{f['ip']}/{f['netmask']}", strict=False)
        except ValueError:
            continue
        skipped = ""
        if ip.is_loopback:
            skipped = "loopback"
        elif ip.is_link_local:
            skipped = "169.254 (no DHCP)"
        elif not f["up"]:
            skipped = "down"
        elif VIRTUAL_NIC.search(f["name"] or ""):
            skipped = "virtual"
        scan = net if net.prefixlen >= 24 else ipaddress.IPv4Network(f"{f['ip']}/24", strict=False)
        out.append({"name": f["name"], "ip": f["ip"], "prefix": net.prefixlen, "network": str(net), "scan": str(scan), "skipped": skipped})
    return out


def probe_port(host: str, port: int, timeout: float) -> str:
    """'open' | 'refused' (the host answered: it's there, the port is closed or busy) | 'none'."""
    try:
        with socket.create_connection((host, port), timeout=timeout):
            return "open"
    except ConnectionRefusedError:
        return "refused"
    except OSError as e:
        if getattr(e, "winerror", None) == 10061 or getattr(e, "errno", None) in (61, 111, 10061):
            return "refused"
        return "none"


def scan(networks: List[str], ports: Iterable[int] = SCAN_PORTS, timeout: float = 0.5, workers: int = 128,
         extra_hosts: Iterable[str] = (), progress: Optional[Callable[[int, int], None]] = None) -> Dict[str, Dict[int, str]]:
    """{ip: {port: open|refused}} for hosts that answered on any of the ports."""
    hosts: List[str] = []
    for n in networks:
        for h in ipaddress.IPv4Network(n, strict=False).hosts():
            if str(h) not in hosts:
                hosts.append(str(h))
    for h in extra_hosts:
        if h not in hosts:
            hosts.append(h)
    tasks = [(h, p) for h in hosts for p in ports]
    result: Dict[str, Dict[int, str]] = {}
    done = 0
    with concurrent.futures.ThreadPoolExecutor(max_workers=workers) as pool:
        futures = {pool.submit(probe_port, h, p, timeout): (h, p) for h, p in tasks}
        for fut in concurrent.futures.as_completed(futures):
            h, p = futures[fut]
            state = fut.result()
            if state != "none":
                result.setdefault(h, {})[p] = state
            done += 1
            if progress and done % 64 == 0:
                progress(done, len(tasks))
    return result


# --- e. Identifying a device ------------------------------------------------------------------

# Devices on the shop's network are asked directly, never through a proxy set up on the computer.
NO_PROXY = urllib.request.build_opener(urllib.request.ProxyHandler({}))


def http_title(host: str, port: int = 80, timeout: float = 1.5) -> str:
    """The device's web page title (and Server header): many printers name their model there."""
    try:
        req = urllib.request.Request(f"http://{host}:{port}/", headers={"User-Agent": f"XOrderingPrinter/{VERSION}"})
        with NO_PROXY.open(req, timeout=timeout) as res:
            server = (res.headers.get("Server") or "").strip()
            body = res.read(65536).decode("utf-8", "replace")
    except Exception as e:
        body = ""
        server = ""
        try:  # a 401 page (login) still names the device
            if isinstance(e, urllib.error.HTTPError):
                server = (e.headers.get("Server") or "").strip()
                realm = e.headers.get("WWW-Authenticate") or ""
                body = e.read(65536).decode("utf-8", "replace") + " " + realm
        except Exception:
            pass
    m = re.search(r"<title[^>]*>(.*?)</title>", body, re.I | re.S)
    title = re.sub(r"\s+", " ", m.group(1)).strip() if m else ""
    if not title:
        m = re.search(r'realm="([^"]+)"', body)
        title = m.group(1) if m else ""
    return " · ".join(x for x in (title[:120], server[:60]) if x)


# SNMP v1 GET (community "public"), BER by hand: sysDescr, sysName, hrDeviceDescr.1
SNMP_OIDS = ("1.3.6.1.2.1.1.1.0", "1.3.6.1.2.1.1.5.0", "1.3.6.1.2.1.25.3.2.1.3.1")


def _ber_len(n: int) -> bytes:
    if n < 0x80:
        return bytes([n])
    b = n.to_bytes((n.bit_length() + 7) // 8, "big")
    return bytes([0x80 | len(b)]) + b


def _tlv(tag: int, content: bytes) -> bytes:
    return bytes([tag]) + _ber_len(len(content)) + content


def _ber_int(n: int) -> bytes:
    return _tlv(0x02, n.to_bytes(max(1, (n.bit_length() + 8) // 8), "big", signed=True))


def _ber_oid(oid: str) -> bytes:
    parts = [int(x) for x in oid.split(".")]
    out = bytearray([parts[0] * 40 + parts[1]])
    for p in parts[2:]:
        chunk = [p & 0x7F]
        p >>= 7
        while p:
            chunk.insert(0, 0x80 | (p & 0x7F))
            p >>= 7
        out += bytes(chunk)
    return _tlv(0x06, bytes(out))


def snmp_get_request(oids: Iterable[str] = SNMP_OIDS, community: str = "public", request_id: int = 0x5850) -> bytes:
    binds = b"".join(_tlv(0x30, _ber_oid(o) + b"\x05\x00") for o in oids)
    pdu = _tlv(0xA0, _ber_int(request_id) + _ber_int(0) + _ber_int(0) + _tlv(0x30, binds))
    return _tlv(0x30, _ber_int(0) + _tlv(0x04, community.encode()) + pdu)


def _ber_items(data: bytes) -> List[Tuple[int, bytes]]:
    items, i = [], 0
    while i + 2 <= len(data):
        tag = data[i]
        n = data[i + 1]
        i += 2
        if n & 0x80:
            k = n & 0x7F
            n = int.from_bytes(data[i:i + k], "big")
            i += k
        items.append((tag, data[i:i + n]))
        i += n
    return items


def _decode_oid(b: bytes) -> str:
    if not b:
        return ""
    parts = [b[0] // 40, b[0] % 40]
    n = 0
    for x in b[1:]:
        n = (n << 7) | (x & 0x7F)
        if not x & 0x80:
            parts.append(n)
            n = 0
    return ".".join(str(p) for p in parts)


def parse_snmp_response(data: bytes) -> Dict[str, str]:
    """{oid: text} from a GetResponse (only the string values)."""
    out: Dict[str, str] = {}
    try:
        (_, msg), = _ber_items(data)[:1]
        parts = _ber_items(msg)
        pdu = parts[2][1]
        binds = _ber_items(pdu)[3][1]
        for _, vb in _ber_items(binds):
            (t1, oid), (t2, val) = _ber_items(vb)[:2]
            if t1 == 0x06 and t2 == 0x04:
                out[_decode_oid(oid)] = val.decode("utf-8", "replace").strip("\x00 ").strip()
    except (ValueError, IndexError):
        pass
    return out


def snmp_describe(host: str, timeout: float = 1.2, port: int = 161) -> str:
    try:
        with socket.socket(socket.AF_INET, socket.SOCK_DGRAM) as s:
            s.settimeout(timeout)
            s.sendto(snmp_get_request(), (host, port))
            data, _ = s.recvfrom(4096)
    except OSError:
        return ""
    vals = parse_snmp_response(data)
    seen: List[str] = []
    for oid in SNMP_OIDS:
        v = vals.get(oid, "")
        if v and v not in seen:
            seen.append(v[:120])
    return " · ".join(seen)


def identify(hosts: Iterable[str], timeout: float = 1.5) -> Dict[str, Dict[str, str]]:
    """{ip: {http, snmp}} (empty strings when the device doesn't say)."""
    hosts = list(dict.fromkeys(hosts))
    out: Dict[str, Dict[str, str]] = {h: {"http": "", "snmp": ""} for h in hosts}
    if not hosts:
        return out
    with concurrent.futures.ThreadPoolExecutor(max_workers=min(64, 2 * len(hosts))) as pool:
        fh = {pool.submit(http_title, h, 80, timeout): h for h in hosts}
        fs = {pool.submit(snmp_describe, h, timeout): h for h in hosts}
        for f, h in fh.items():
            out[h]["http"] = f.result()
        for f, h in fs.items():
            out[h]["snmp"] = f.result()
    return out


# --- f. USB devices ---------------------------------------------------------------------------

def usb_devices() -> List[Dict[str, str]]:
    """Star / Epson / receipt printer devices Windows sees (driver installed or not): [{name, id, status, vendor}]."""
    if not IS_WINDOWS:
        return []
    rows = powershell_json(
        "Get-CimInstance Win32_PnPEntity | Where-Object { $_.PNPDeviceID -match 'VID_0519|VID_04B8' -or $_.Name -match 'Star|Epson|TSP|TM-|Receipt|Thermal|POS' }"
        " | Select-Object Name, PNPDeviceID, Status, PNPClass")
    out = []
    for r in rows:
        dev_id = str(r.get("PNPDeviceID") or "")
        name = str(r.get("Name") or "")
        if not dev_id.upper().startswith(("USB", "USBPRINT", "SWD\\PRINTENUM")) and not re.search(r"VID_0519|VID_04B8", dev_id, re.I):
            continue
        vendor = next((v for k, v in USB_VENDORS.items() if k in dev_id.upper()), brand(name))
        out.append({"name": name, "id": dev_id, "status": str(r.get("Status") or ""), "class": str(r.get("PNPClass") or ""), "vendor": vendor})
    return out


# --- Putting it together ----------------------------------------------------------------------

STEPS = (
    "读取 Windows 打印机 Windows printers",
    "查看其他程序的打印连接 Printer connections of other programs",
    "读取 ARP 表 ARP table",
    "扫描网络 Scanning the network",
    "识别品牌型号 Identifying make and model",
    "检查 USB 打印机 USB printers",
)


def run(progress: Optional[Progress] = None, scan_timeout: float = 0.5, ports: Iterable[int] = SCAN_PORTS, networks: Optional[List[str]] = None,
        include_loopback: bool = False) -> Dict[str, Any]:
    """Every look, in order (a failing look is recorded in `errors` and skipped): the raw findings
    plus `candidates` (merge()). Takes about 5-15 s; `progress(step, steps, text)` follows it."""
    started = time.time()
    total = len(STEPS)
    res: Dict[str, Any] = {"version": VERSION, "at": started, "computer": socket.gethostname(), "errors": {}}

    def step(i: int, extra: str = "") -> None:
        if progress:
            try:
                progress(i, total, STEPS[i - 1] + (f" · {extra}" if extra else ""))
            except Exception:
                pass

    def attempt(key: str, fn: Callable[[], Any], default: Any) -> Any:
        try:
            return fn()
        except Exception as e:
            res["errors"][key] = f"{type(e).__name__}: {e}"
            return default

    res["interfaces"] = attempt("interfaces", interfaces, [])
    step(1)
    res["windowsPrinters"] = attempt("windowsPrinters", windows_printers, [])
    step(2)
    conns = attempt("connections", lambda: printer_connections(include_loopback=include_loopback), [])
    if conns:
        attempt("seen", lambda: remember_connections(conns), None)
    res["connections"] = conns
    res["seen"] = list(load_seen().values())
    step(3)
    nets = networks if networks is not None else [i["scan"] for i in res["interfaces"] if not i["skipped"]]
    res["networks"] = nets
    in_nets = [ipaddress.IPv4Network(n, strict=False) for n in nets]
    arp = attempt("arp", arp_table, [])
    res["arp"] = [a for a in arp if any(ipaddress.IPv4Address(a["ip"]) in n for n in in_nets)] if in_nets else arp
    step(4, ", ".join(nets) or "—")
    extra = {c["ip"] for c in conns} | {s["ip"] for s in res["seen"]} | {p["ip"] for p in res["windowsPrinters"] if p.get("ip")} | {a["ip"] for a in res["arp"]}
    if include_loopback:
        extra |= {"127.0.0.1"}

    def scan_progress(done: int, all_: int) -> None:
        step(4, f"{done}/{all_}")

    res["scan"] = attempt("scan", lambda: {h: {str(p): s for p, s in v.items()} for h, v in scan(nets, ports, scan_timeout, extra_hosts=sorted(extra), progress=scan_progress).items()}, {})
    step(5)
    to_identify = set(h for h, v in res["scan"].items() if "open" in v.values()) | {c["ip"] for c in conns} | {s["ip"] for s in res["seen"]} \
        | {p["ip"] for p in res["windowsPrinters"] if p.get("ip")} | {a["ip"] for a in res["arp"] if a.get("vendor")}
    res["identify"] = attempt("identify", lambda: identify(sorted(to_identify)), {})
    step(6)
    res["usb"] = attempt("usb", usb_devices, [])
    res["candidates"] = merge(res)
    res["seconds"] = round(time.time() - started, 1)
    return res


def _cand(key: str) -> Dict[str, Any]:
    return {"key": key, "sources": [], "name": "", "model": "", "brand": "", "ip": "", "port": 9100, "ports": {}, "windowsPrinter": "",
            "driver": "", "portName": "", "portKind": "", "process": "", "usb": "", "mac": "", "vendor": "", "notes": []}


def merge(res: Dict[str, Any]) -> List[Dict[str, Any]]:
    """One candidate per printer, most like a receipt printer first, each with its guessed settings."""
    cands: Dict[str, Dict[str, Any]] = {}

    def by_ip(ip: str) -> Dict[str, Any]:
        return cands.setdefault(f"ip:{ip}", dict(_cand(f"ip:{ip}"), ip=ip))

    def add_source(c: Dict[str, Any], s: str) -> None:
        if s not in c["sources"]:
            c["sources"].append(s)

    for p in res.get("windowsPrinters") or []:
        if p.get("virtual"):
            continue
        c = by_ip(p["ip"]) if p.get("ip") else cands.setdefault(f"win:{p['name']}", _cand(f"win:{p['name']}"))
        if c["windowsPrinter"]:  # two Windows printers on one IP: keep both
            c = cands.setdefault(f"win:{p['name']}", dict(_cand(f"win:{p['name']}"), ip=p.get("ip", "")))
        add_source(c, "windows")
        c.update(windowsPrinter=p["name"], driver=p.get("driver", ""), portName=p.get("port", ""), portKind=p.get("portKind", ""), winStatus=p.get("status", ""))
        if p.get("tcpPort"):
            c["port"] = int(p["tcpPort"])
        if p.get("portKind") == "usb":
            add_source(c, "usb")
    for conn in list(res.get("connections") or []) + [dict(s, state="SEEN") for s in res.get("seen") or []]:
        c = by_ip(conn["ip"])
        add_source(c, "in_use")
        if conn.get("process") and not c["process"]:
            c["process"] = conn["process"]
        c["ports"].setdefault(str(conn["port"]), "in use")
        if conn["port"] in (9100, 9101, 9102):
            c["port"] = conn["port"]
    for ip, ports in (res.get("scan") or {}).items():
        opened = [int(p) for p, s in ports.items() if s == "open"]
        if not opened and f"ip:{ip}" not in cands:
            continue
        c = by_ip(ip)
        c["ports"].update({str(p): s for p, s in ports.items()})
        if opened:
            add_source(c, "network")
            raw = [p for p in opened if p in (9100, 9101, 9102)]
            if raw and not c.get("windowsPrinter"):
                c["port"] = raw[0]
    for a in res.get("arp") or []:
        c = cands.get(f"ip:{a['ip']}")
        if c is None and a.get("vendor"):
            c = by_ip(a["ip"])
        if c is not None:
            c["mac"], c["vendor"] = a["mac"], a.get("vendor", "")
            add_source(c, "arp")
    for ip, info in (res.get("identify") or {}).items():
        c = cands.get(f"ip:{ip}")
        if c is not None:
            c["model"] = info.get("snmp") or info.get("http") or ""
            c["web"], c["snmp"] = info.get("http", ""), info.get("snmp", "")
    for u in res.get("usb") or []:
        # The same printer as a Windows printer on a USB port (same make): one entry.
        make = brand(u["name"] + " " + u.get("vendor", ""))
        twin = next((c for c in cands.values() if c["portKind"] == "usb" and brand(c["windowsPrinter"] + " " + c["driver"]) == make), None)
        if twin is not None and not twin["usb"]:
            twin["usb"] = u["name"]
            continue
        c = cands.setdefault(f"usb:{u['id']}", _cand(f"usb:{u['id']}"))
        add_source(c, "usb")
        c.update(usb=u["name"], vendor=u.get("vendor", ""))
        c["notes"].append("USB 设备没有 Windows 打印机：请安装打印机驱动 USB device without a Windows printer: install its driver")

    out = []
    for c in cands.values():
        text = " ".join(x for x in (c["windowsPrinter"], c["driver"], c["model"], c.get("web", ""), c.get("snmp", ""), c["usb"], c["vendor"], c["process"]) if x)
        g = guess(text)
        c["brand"] = brand(text) or ("Star" if "Star" in c["vendor"] else "Epson" if "Epson" in c["vendor"] else "")
        if not g["render"] and c["brand"]:
            g = guess(c["brand"])
        c["render"] = g["render"] or ("star" if c["brand"] == "Star" else "image")
        c["paperWidth"] = g["paperWidth"]
        c["receipt"] = g["receipt"] or bool(c["brand"])
        c["office"] = g["office"]
        c["name"] = c["windowsPrinter"] or c["model"].split(" · ")[0] or c["usb"] or (f"{c['brand']} {c['ip']}" if c["brand"] else c["ip"])
        c["connection"] = "windows" if c["windowsPrinter"] else "tcp" if c["ip"] else ""
        c["score"] = score(c)
        out.append(c)
    out.sort(key=lambda c: (-c["score"], c["name"].lower()))
    return out


def score(c: Dict[str, Any]) -> int:
    s = 0
    if "in_use" in c["sources"]:
        s += 50
    if c["windowsPrinter"]:
        s += 30
    if c["receipt"]:
        s += 40
    if any(v == "open" for p, v in c["ports"].items() if p in ("9100", "9101", "9102")):
        s += 25
    if c["vendor"]:
        s += 20
    if c["usb"]:
        s += 10
    if c["office"]:
        s -= 60
    if c["connection"] == "":
        s -= 30  # a USB device with no driver can't be printed to yet
    return s


def receipt_printers(cands: List[Dict[str, Any]]) -> List[Dict[str, Any]]:
    """The candidates that are real receipt printers we can print to."""
    return [c for c in cands if c["connection"] and not c["office"] and (c["receipt"] or "in_use" in c["sources"]
            or any(v == "open" for p, v in c["ports"].items() if p in ("9100", "9101", "9102")))]


def settings_for(c: Dict[str, Any], name: str = "") -> Dict[str, Any]:
    """A printer's settings from a candidate (for the dialog to fill in)."""
    return {"name": name, "connection": c["connection"], "host": c["ip"], "port": int(c.get("port") or 9100),
            "windowsPrinter": c["windowsPrinter"], "render": c["render"], "paperWidth": 80 if c["render"] == "star" else c["paperWidth"]}


def source_text(c: Dict[str, Any]) -> str:
    parts = []
    for s in c["sources"]:
        if s == "in_use":
            parts.append(f"被 {c['process'] or '其他程序'} 占用中 In use by {c['process'] or 'another program'}")
        elif s != "arp":
            parts.append(SOURCE_LABELS[s])
    return " / ".join(parts) or "ARP"


def address_text(c: Dict[str, Any]) -> str:
    if c["ip"]:
        return f"{c['ip']}:{c['port']}"
    if c["portName"]:
        return c["portName"]
    return "USB"


# --- The report -------------------------------------------------------------------------------

def report(res: Dict[str, Any], cfg: Optional[Dict[str, Any]] = None, status: Optional[Dict[str, Any]] = None) -> str:
    """Everything found, as plain text to paste in a message (no keys or tokens in it)."""
    t = time.strftime("%Y-%m-%d %H:%M:%S", time.localtime(res.get("at") or time.time()))
    lines = [f"XOrdering Printer {res.get('version', VERSION)} · 诊断报告 Diagnostics · {t}", f"电脑 Computer: {res.get('computer', '')}  ({res.get('seconds', '?')} s)", ""]
    if cfg is not None:
        r = cfg.get("restaurant") or {}
        name = r.get("name")
        if isinstance(name, dict):
            name = name.get("en") or name.get("zh")
        lines.append(f"餐厅 Restaurant: {name or '—'}   连接 Connected: {'yes' if cfg.get('deviceToken') else 'no'}")
        lines.append("已配置打印机 Configured printers:")
        pstate = (status or {}).get("printers") or {}
        for p in cfg.get("printers") or []:
            st = pstate.get(p.get("id")) or {}
            where = f"Windows \"{p.get('windowsPrinter')}\"" if p.get("connection") == "windows" else f"{p.get('host')}:{p.get('port', 9100)}"
            lines.append(f"  - {p.get('name')}: {where}, {p.get('render')}, {p.get('paperWidth', 80)} mm, {'on' if p.get('enabled', True) else 'off'}"
                         + (f"  · {('OK' if st.get('ok') else st.get('error'))}" if st else ""))
        if not cfg.get("printers"):
            lines.append("  (none)")
        q = (status or {}).get("queue")
        if q:
            lines.append(f"队列 Queue: waiting {q.get('queued', 0)}, printed {q.get('printed', 0)}")
        lines.append("")
    lines.append("网卡 Network cards:")
    for i in res.get("interfaces") or []:
        lines.append(f"  - {i['name'] or '?'}: {i['ip']}/{i['prefix']}  scan {i['scan']}" + (f"  (skipped: {i['skipped']})" if i["skipped"] else ""))
    lines.append(f"扫描的网段 Scanned: {', '.join(res.get('networks') or []) or '—'}")
    lines.append("")
    lines.append("Windows 打印机 Windows printers:")
    for p in res.get("windowsPrinters") or []:
        lines.append(f"  - {p['name']} | driver {p.get('driver', '')} | port {p.get('port', '')} ({p.get('portKind', '')})"
                     + (f" -> {p['ip']}:{p.get('tcpPort') or 9100}" if p.get("ip") else "") + (" [virtual]" if p.get("virtual") else "")
                     + (f" | status {p['status']}" if p.get("status") else ""))
    if not res.get("windowsPrinters"):
        lines.append("  (none)" if IS_WINDOWS else "  (not Windows)")
    lines.append("")
    lines.append("打印端口连接 Connections to printer ports (now):")
    for c in res.get("connections") or []:
        lines.append(f"  - {c['ip']}:{c['port']} {c['state']} by {c['process'] or '?'} (pid {c['pid']})")
    if not res.get("connections"):
        lines.append("  (none)")
    seen = res.get("seen") or []
    if seen:
        lines.append("最近见过 Seen recently:")
        for s in sorted(seen, key=lambda s: -float(s.get("last") or 0)):
            lines.append(f"  - {s['ip']}:{s['port']} by {s.get('process') or '?'}, last {time.strftime('%m-%d %H:%M', time.localtime(float(s.get('last') or 0)))}")
    lines.append("")
    lines.append("ARP 同网段设备 ARP (same networks):")
    for a in res.get("arp") or []:
        lines.append(f"  - {a['ip']}  {a['mac']}" + (f"  {a['vendor']}" if a.get("vendor") else ""))
    if not res.get("arp"):
        lines.append("  (none)")
    lines.append("")
    lines.append("扫描结果 Scan (port: open / refused):")
    for ip in sorted(res.get("scan") or {}, key=lambda h: tuple(int(x) for x in h.split("."))):
        ports = res["scan"][ip]
        lines.append(f"  - {ip}: " + ", ".join(f"{p} {s}" for p, s in sorted(ports.items())))
    if not res.get("scan"):
        lines.append("  (nothing answered on " + ", ".join(str(p) for p in SCAN_PORTS) + ")")
    ident = {h: v for h, v in (res.get("identify") or {}).items() if v.get("http") or v.get("snmp")}
    if ident:
        lines.append("识别 Identified:")
        for h, v in sorted(ident.items()):
            lines.append(f"  - {h}: " + " | ".join(x for x in (f"web: {v['http']}" if v.get("http") else "", f"snmp: {v['snmp']}" if v.get("snmp") else "") if x))
    lines.append("")
    lines.append("USB 设备 USB devices:")
    for u in res.get("usb") or []:
        lines.append(f"  - {u['name']} ({u['vendor'] or '?'}) {u['status']} {u['id']}")
    if not res.get("usb"):
        lines.append("  (none)" if IS_WINDOWS else "  (not Windows)")
    lines.append("")
    lines.append("候选打印机 Candidates (best first):")
    for c in res.get("candidates") or []:
        lines.append(f"  - [{c['score']}] {c['name']} | {address_text(c)} | {source_text(c)} | {c['brand'] or '?'} | type {c['render']} {c['paperWidth']} mm"
                     + (" | office printer" if c["office"] else ""))
    if not res.get("candidates"):
        lines.append("  (none)")
    if res.get("errors"):
        lines.append("")
        lines.append("出错的步骤 Steps that failed:")
        for k, v in res["errors"].items():
            lines.append(f"  - {k}: {v}")
    return "\n".join(lines)
