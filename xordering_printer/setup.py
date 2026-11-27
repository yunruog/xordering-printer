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

import time

from . import api, config, deliver, discover, escpos, pipeline, render, ticket
from .logs import get as get_log
from .printer import PrinterError, probe


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


SERVER_KEYS = ("id", "name", "host", "port", "paperWidth", "render", "enabled", "assignment")


def host_label() -> str:
    """What the dashboard shows as the address of a printer reached through this computer's Windows
    printer (USB, Bluetooth): the computer's name, as a valid host name."""
    name = re.sub(r"[^A-Za-z0-9-]+", "-", computer_name()).strip("-")[:60]
    return name if name and name[0].isalnum() else "windows-printer"


def save_printer(printer: Dict[str, Any]) -> Dict[str, Any]:
    """Saves a printer: name, address, type and paper on the server (the dashboard shows them), and
    how this computer reaches it (IP or Windows printer) here, in config.json's printerLocal."""
    cfg = config.load()
    windows = printer.get("connection") == "windows"
    server = {k: v for k, v in printer.items() if k in SERVER_KEYS}
    server.setdefault("assignment", "full")
    if windows and not (server.get("host") or "").strip():
        server["host"] = host_label()
    saved = api.client_for(cfg).save_printer(cfg["deviceToken"], server)
    cfg = config.load()
    printers = [p for p in cfg.get("printers") or [] if p.get("id") != saved["id"]] + [saved]
    printers.sort(key=lambda p: (p.get("position", 0), p.get("name", "")))
    config.update(printers=printers)
    if windows:
        old = (cfg.get("printerLocal") or {}).get(saved["id"]) or {}
        same = old.get("windowsPrinter") == printer.get("windowsPrinter")
        config.set_local(saved["id"], connection="windows", windowsPrinter=printer.get("windowsPrinter") or "",
                         windowsMethod=(printer.get("windowsMethod") or (old.get("windowsMethod") if same else None)))
    else:
        config.set_local(saved["id"], connection="tcp", windowsPrinter=None, windowsMethod=None)
    get_log().info("printer saved: %s via %s", saved.get("name"), deliver.describe(dict(saved, **{k: printer.get(k) for k in ("connection", "windowsPrinter")})))
    return next((p for p in config.effective_printers(config.load()) if p["id"] == saved["id"]), saved)


def _kind(printer: Dict[str, Any]) -> str:
    return "star" if escpos.kind(printer) == "star" else "escpos"


def test_print(printer: Dict[str, Any]) -> Dict[str, Any]:
    """Prints the test page (the sample order, ASAP and scheduled) the way this computer reaches the printer."""
    cfg = config.load()
    st = pipeline.print_tickets(printer, ticket.test_tickets(cfg.get("restaurant")), source="test page", title="XOrdering test page")
    _remember_fallback(printer, st)
    get_log().info("test page sent to %s (%s)", printer.get("name"), deliver.describe(printer))
    return st


def _remember_fallback(printer: Dict[str, Any], st: Dict[str, Any]) -> None:
    if st.get("fallback") and printer.get("id"):
        config.set_local(printer["id"], windowsMethod="driver")


def test_connection(printer: Dict[str, Any]) -> Dict[str, Any]:
    """The connection test: really prints one short ticket (store, time, connection, printer).

    {ok, ms, method, status} or raises PrinterError explaining why (wrong network, in use by
    another program, offline, driver error …).
    """
    cfg = config.load()
    started = time.time()
    st = pipeline.print_tickets(printer, [ticket.test_lines(printer, cfg.get("restaurant"))], source="connection test", title="XOrdering connection test")
    _remember_fallback(printer, st)
    get_log().info("connection test printed on %s (%s, %s)", printer.get("name"), deliver.describe(printer), st.get("method"))
    return {"ok": True, "ms": int((time.time() - started) * 1000), "method": st.get("method"), "status": st, "fallback": st.get("fallback")}


def reprint_last(printer: Optional[Dict[str, Any]] = None) -> Dict[str, Any]:
    """"Reprint last order": the newest order ticket this computer received, printed again (marked
    REPRINT) on `printer`, or on the printer it was for. Same pipeline as everything else; nothing
    is ordered or sent to the server. Raises LookupError when no order has come in yet."""
    from .jobs import JobStore
    from .paths import jobs_db_path

    store = JobStore(str(jobs_db_path()))
    try:
        job = store.last_order()
    finally:
        store.close()
    if not job:
        raise LookupError("no order yet")
    cfg = config.load()
    printers = {p["id"]: p for p in config.effective_printers(cfg)}
    target = printer or printers.get(job.get("printerId"))
    if not target:
        raise LookupError("printer gone")
    order = job["order"]
    st = pipeline.print_tickets(target, [ticket.build(order, cfg.get("restaurant") or {}, reason="reprint")], source="reprint (window)", job_id=job.get("id") or "",
                                order=order, title=f"XOrdering #{order.get('number')} reprint")
    _remember_fallback(target, st)
    get_log().info("order #%s reprinted on %s from the window", order.get("number"), target.get("name"))
    return dict(st, order=order.get("number"), printer=target.get("name"))


def set_paused(paused: bool, skip_waiting: bool = False) -> Dict[str, Any]:
    """The window's "Pause printing" switch: takes effect here at once (the worker reads config.json),
    and reaches the server (and the dashboard) with the worker's next poll."""
    get_log().info("printing %s in the window%s", "paused" if paused else "resumed", " (waiting tickets skipped)" if skip_waiting else "")
    return config.update(paused=paused, pausePending={"paused": paused, "skip": bool(skip_waiting and not paused), "at": time.time()})


def check_connection(host: str, port: int = 9100, render_kind: str = "text") -> Dict[str, Any]:
    """Connects to a network printer without printing: {ok, ms, status} or raises PrinterError."""
    try:
        return probe(host.strip(), int(port or 9100), kind="star" if render_kind == "star" else "escpos")
    except PrinterError as e:
        raise deliver.explain_tcp_failure(host.strip(), e) from None


def find_printers(progress: Optional[discover.Progress] = None) -> Dict[str, Any]:
    """Looks for printers every way there is (discover.run); the result is kept in discovery.json."""
    res = discover.run(progress)
    try:
        from .worker import discovery_path
        import json as _json

        discovery_path().write_text(_json.dumps(res, ensure_ascii=False), "utf-8")
    except Exception:
        pass
    top = discover.receipt_printers(res["candidates"])
    get_log().info("printer search (%.1f s): %s", res.get("seconds", 0), "; ".join(f"{c['name']} {discover.address_text(c)} [{discover.source_text(c)}]" for c in top) or "no receipt printer")
    return res


def last_discovery() -> Optional[Dict[str, Any]]:
    try:
        import json as _json
        from .worker import discovery_path

        return _json.loads(discovery_path().read_text("utf-8"))
    except Exception:
        return None


def diagnostics(res: Dict[str, Any]) -> str:
    """The diagnostics report (no tokens or keys), also saved next to the logs."""
    import json as _json

    from .paths import log_dir, status_path

    try:
        status = _json.loads(status_path().read_text("utf-8"))
    except (OSError, ValueError):
        status = None
    cfg = config.load()
    text = discover.report(res, dict(cfg, printers=config.effective_printers(cfg)), status)
    try:
        (log_dir() / "diagnostics.txt").write_text(text, "utf-8")
    except OSError:
        pass
    return text


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
