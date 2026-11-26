"""First-run setup, shared by the window and the tests: connect this computer, set printers, test.

Connecting gives the computer a device token, by either
  * a device key made on the dashboard (Settings → Kitchen printers → Create device key): the
    recommended way, or
  * the owner's email sign-in (the dashboard's 6-digit email code): the session is used once to
    register this computer for the chosen restaurant, then signed out. Only the device token is kept.
"""
import re
import socket
from typing import Any, Dict, List, Optional

from . import api, config, escpos, render, ticket
from .logs import get as get_log
from .printer import probe, scan, send


def computer_name() -> str:
    return (socket.gethostname() or "Kitchen PC").split(".")[0][:60]


def connect_server(site: Optional[str] = None) -> Dict[str, Any]:
    """Reads the site's printer-config.json (server URL and publishable key) into the config."""
    cfg = config.load()
    site = (site or cfg.get("site") or "").strip()
    server = api.fetch_server_config(site)
    return config.update(site=site, **server)


def _client() -> api.Client:
    cfg = config.load()
    if not cfg.get("supabaseUrl"):
        cfg = connect_server()
    return api.client_for(cfg)


def _store_device(result: Dict[str, Any]) -> Dict[str, Any]:
    device = result.get("device") or {}
    restaurant = result.get("restaurant") or {}
    cfg = config.update(deviceToken=result["token"], deviceId=device.get("id", ""), deviceName=device.get("name", ""), restaurant=restaurant, printers=[])
    get_log().info("connected as device %s for restaurant %s", device.get("id"), restaurant.get("id"))
    return cfg


def clean_key(text: str) -> str:
    """The device key out of whatever was pasted (a whole message, a link, extra spaces)."""
    m = re.search(r"xpk_[0-9a-fA-F]{32}", text or "")
    return m.group(0) if m else (text or "").strip()


def pair_with_key(key: str, name: Optional[str] = None) -> Dict[str, Any]:
    return _store_device(_client().pair(clean_key(key), name or computer_name()))


EMAIL = re.compile(r"^[^@\s]+@[^@\s]+\.[^@\s]+$")


def normalize_email(text: str) -> str:
    """The email as typed (trimmed, lower case), or ApiError("invalid_email")."""
    email = (text or "").strip().lower()
    if not EMAIL.match(email) or len(email) > 254:
        raise api.ApiError("invalid_email")
    return email


def send_code(email: str, locale: str = "zh") -> Dict[str, Any]:
    return _client().send_code(normalize_email(email), locale)


def sign_in(email: str, code: str) -> str:
    return _client().verify_code(normalize_email(email), code)


def owned_restaurants(session: str) -> List[Dict[str, Any]]:
    return [r for r in _client().my_restaurants(session) if r.get("role") == "owner"]


def register(session: str, restaurant_id: str, name: Optional[str] = None) -> Dict[str, Any]:
    client = _client()
    try:
        return _store_device(client.register(session, restaurant_id, name or computer_name()))
    finally:
        client.sign_out(session)


def disconnect() -> Dict[str, Any]:
    """Forgets this computer's device token (the dashboard can also sign it out)."""
    return config.update(deviceToken="", deviceId="", restaurant=None, printers=[])


def refresh() -> Dict[str, Any]:
    """Polls once for the restaurant and its printers (tickets it lists are this device's anyway: the worker prints them)."""
    cfg = config.load()
    res = api.client_for(cfg).rpc("print_device_poll", {"p_token": cfg["deviceToken"], "p_version": None})
    return config.update(restaurant=res.get("restaurant"), printers=res.get("printers") or [])


def save_printer(printer: Dict[str, Any]) -> Dict[str, Any]:
    cfg = config.load()
    saved = api.client_for(cfg).save_printer(cfg["deviceToken"], printer)
    printers = [p for p in cfg.get("printers") or [] if p.get("id") != saved["id"]] + [saved]
    printers.sort(key=lambda p: (p.get("position", 0), p.get("name", "")))
    config.update(printers=printers)
    return saved


def _kind(printer: Dict[str, Any]) -> str:
    return "star" if escpos.kind(printer) == "star" else "escpos"


def test_print(printer: Dict[str, Any]) -> None:
    """Prints the test page (the sample order, ASAP and scheduled) straight to the printer."""
    cfg = config.load()
    payload = escpos.tickets_bytes(ticket.test_tickets(cfg.get("restaurant")), printer)
    send(printer["host"], int(printer.get("port") or 9100), payload, kind=_kind(printer))
    get_log().info("test page sent to %s (%s)", printer.get("name"), printer.get("host"))


def test_connection(host: str, port: int = 9100, render_kind: str = "text") -> Dict[str, Any]:
    """Connects to the printer without printing: {ok, ms, status} or raises PrinterError (timeout, refused, …)."""
    return probe(host.strip(), int(port or 9100), kind="star" if render_kind == "star" else "escpos")


def find_printers(port: int = 9100) -> List[str]:
    """Devices on this computer's network(s) with the printer port open."""
    found = scan(port)
    get_log().info("printer scan found %s", ", ".join(found) or "nothing")
    return found


def preview_file() -> str:
    """The sample tickets as one PNG next to the logs (what the printer gets), for the window's preview."""
    from PIL import Image

    from .paths import log_dir

    imgs = render.render_many(ticket.test_tickets(config.load().get("restaurant")), 80)
    gap = 24
    sheet = Image.new("1", (sum(i.size[0] for i in imgs) + gap * (len(imgs) - 1), max(i.size[1] for i in imgs)), 1)
    x = 0
    for img in imgs:
        sheet.paste(img, (x, 0))
        x += img.size[0] + gap
    path = log_dir().parent / "preview.png"
    sheet.save(path)
    return str(path)
