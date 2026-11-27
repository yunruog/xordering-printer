"""Talking to the XOrdering server (Supabase: SQL functions over HTTPS), standard library only.

Every call after setup uses only the device token: print_device_poll (the restaurant, its printers
and the tickets to print), print_device_report (printed / failed), print_device_save_printer,
print_device_status (printer states), print_device_preview (the sample tickets as PNG) and
print_device_set_paused (the "Pause printing" switch, shared with the dashboard).
Setup uses either a device key (print_device_pair, recommended) or the owner's email sign-in (the
same 6-digit email code as the dashboard: the ordering-auth function), which is used once to register
this computer and then signed out. No SMS.
"""
import json
import socket
import ssl
import urllib.error
import urllib.parse
import urllib.request
from typing import Any, Dict, List, Optional

from . import VERSION

TIMEOUT = 15


class ApiError(Exception):
    """The server answered with an error; `code` is XOrdering's error code (device_revoked, …)."""

    def __init__(self, code: str, status: int = 0, payload: Optional[Dict[str, Any]] = None):
        super().__init__(code)
        self.code = code
        self.status = status
        # e.g. {"waitSeconds": 42} or {"attemptsLeft": 3} from the sign-in function.
        self.payload = payload or {}


class NetworkError(Exception):
    """The server couldn't be reached (no internet, DNS, timeout)."""


def _context() -> ssl.SSLContext:
    return ssl.create_default_context()


def _request(method: str, url: str, body: Optional[Dict[str, Any]] = None, headers: Optional[Dict[str, str]] = None, timeout: float = TIMEOUT) -> Any:
    data = json.dumps(body).encode("utf-8") if body is not None else None
    req = urllib.request.Request(url, data=data, method=method)
    req.add_header("Accept", "application/json")
    req.add_header("User-Agent", f"XOrderingPrinter/{VERSION}")
    if data is not None:
        req.add_header("Content-Type", "application/json")
    for k, v in (headers or {}).items():
        req.add_header(k, v)
    try:
        with urllib.request.urlopen(req, timeout=timeout, context=_context() if url.startswith("https:") else None) as res:
            raw = res.read()
            return json.loads(raw.decode("utf-8")) if raw else None
    except urllib.error.HTTPError as e:
        try:
            payload = json.loads(e.read().decode("utf-8") or "{}")
        except ValueError:
            payload = {}
        code = str(payload.get("message") or payload.get("msg") or payload.get("error_description") or payload.get("error") or f"http_{e.code}")
        raise ApiError(code, e.code, payload if isinstance(payload, dict) else None) from None
    except (urllib.error.URLError, socket.timeout, ConnectionError, TimeoutError, OSError) as e:
        raise NetworkError(str(getattr(e, "reason", e))) from None


def fetch_server_config(site: str) -> Dict[str, str]:
    """The site's printer-config.json: {supabaseUrl, publishableKey} (browser-safe values)."""
    url = urllib.parse.urljoin(site if site.endswith("/") else site + "/", "printer-config.json")
    cfg = _request("GET", url)
    if not isinstance(cfg, dict) or not cfg.get("supabaseUrl") or not cfg.get("publishableKey"):
        raise ApiError("bad_server_config")
    return {"supabaseUrl": str(cfg["supabaseUrl"]).rstrip("/"), "publishableKey": str(cfg["publishableKey"])}


class Client:
    def __init__(self, supabase_url: str, publishable_key: str):
        self.url = supabase_url.rstrip("/")
        self.key = publishable_key

    def _headers(self, bearer: Optional[str] = None) -> Dict[str, str]:
        h = {"apikey": self.key}
        # Legacy anon keys are JWTs and go in Authorization too; publishable keys don't.
        if bearer:
            h["Authorization"] = f"Bearer {bearer}"
        elif self.key.startswith("eyJ"):
            h["Authorization"] = f"Bearer {self.key}"
        return h

    def rpc(self, fn: str, args: Dict[str, Any], bearer: Optional[str] = None, timeout: float = TIMEOUT) -> Any:
        return _request("POST", f"{self.url}/rest/v1/rpc/{fn}", args, self._headers(bearer), timeout)

    # --- Setup -------------------------------------------------------------------------------

    def pair(self, key: str, name: str) -> Dict[str, Any]:
        return self.rpc("print_device_pair", {"p_key": key.strip(), "p_name": name, "p_version": VERSION})

    def send_code(self, email: str, locale: str = "zh") -> Dict[str, Any]:
        """Emails the owner a 6-digit sign-in code (the dashboard's: XInventory's email sign-in)."""
        body = {"email": email.strip().lower(), "locale": "zh" if locale == "zh" else "en", "app": "printer"}
        return _request("POST", f"{self.url}/functions/v1/ordering-auth/send", body, self._headers()) or {}

    def verify_code(self, email: str, code: str) -> str:
        """The emailed code → a session (access token), used once to register this computer."""
        body = {"email": email.strip().lower(), "code": "".join(ch for ch in code if ch.isdigit())}
        res = _request("POST", f"{self.url}/functions/v1/ordering-auth/verify", body, self._headers())
        token = ((res or {}).get("session") or {}).get("access_token")
        if not token:
            raise ApiError("invalid_code")
        return token

    def my_restaurants(self, session: str) -> List[Dict[str, Any]]:
        return self.rpc("my_restaurants", {}, bearer=session) or []

    def register(self, session: str, restaurant_id: str, name: str) -> Dict[str, Any]:
        return self.rpc("register_print_device", {"p_restaurant_id": restaurant_id, "p_name": name, "p_version": VERSION}, bearer=session)

    def sign_out(self, session: str) -> None:
        try:
            _request("POST", f"{self.url}/auth/v1/logout", {}, self._headers(session))
        except (ApiError, NetworkError):
            pass

    # --- With the device token -----------------------------------------------------------

    def poll(self, token: str) -> Dict[str, Any]:
        return self.rpc("print_device_poll", {"p_token": token, "p_version": VERSION})

    def report(self, token: str, job_id: str, status: str, error: Optional[str] = None) -> Dict[str, Any]:
        return self.rpc("print_device_report", {"p_token": token, "p_job_id": job_id, "p_status": status, "p_error": error})

    def set_paused(self, token: str, paused: bool, skip_waiting: bool = False) -> Dict[str, Any]:
        """Pauses or resumes printing for the restaurant (the dashboard shows the same switch)."""
        return self.rpc("print_device_set_paused", {"p_token": token, "p_paused": paused, "p_skip_waiting": skip_waiting})

    def save_printer(self, token: str, printer: Dict[str, Any]) -> Dict[str, Any]:
        return self.rpc("print_device_save_printer", {"p_token": token, "p_printer": printer})

    def status(self, token: str, status: Dict[str, Any]) -> Any:
        """Printer states and the local queue, for the dashboard."""
        return self.rpc("print_device_status", {"p_token": token, "p_status": status})

    def preview(self, token: str, images: List[str]) -> Any:
        """The sample tickets as PNG (base64), drawn exactly as they print."""
        return self.rpc("print_device_preview", {"p_token": token, "p_images": images}, timeout=30)


def client_for(cfg: Dict[str, Any]) -> Client:
    return Client(cfg["supabaseUrl"], cfg["publishableKey"])
