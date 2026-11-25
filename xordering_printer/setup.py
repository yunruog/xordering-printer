"""First-run setup, shared by the window and the tests: connect this computer, set printers, test.

Connecting gives the computer a device token, by either
  * a device key made on the dashboard (Settings → Kitchen printers → Create device key), or
  * the owner's phone sign-in (the dashboard's SMS code): the session is used once to register
    this computer for the chosen restaurant, then signed out. Only the device token is kept.
"""
import socket
from typing import Any, Dict, List, Optional

from . import api, config, escpos, ticket
from .logs import get as get_log
from .printer import send


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


def pair_with_key(key: str, name: Optional[str] = None) -> Dict[str, Any]:
    return _store_device(_client().pair(key, name or computer_name()))


def normalize_phone(country: str, number: str) -> str:
    digits = "".join(ch for ch in number if ch.isdigit())
    cc = "".join(ch for ch in country if ch.isdigit()) or "1"
    if number.strip().startswith("+"):
        return "+" + digits
    return f"+{cc}{digits}"


def send_code(phone: str) -> None:
    _client().send_code(phone)


def sign_in(phone: str, code: str) -> str:
    return _client().verify_code(phone, code)


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


def test_print(printer: Dict[str, Any]) -> None:
    """Prints the test ticket straight to the printer (raises PrinterError when it can't)."""
    cfg = config.load()
    lines = ticket.test_lines(printer, cfg.get("restaurant"))
    send(printer["host"], int(printer.get("port") or 9100), escpos.ticket_bytes(lines, printer))
    get_log().info("test print sent to %s (%s)", printer.get("name"), printer.get("host"))
