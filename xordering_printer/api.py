"""Talking to the XOrdering server (Supabase: SQL functions over HTTPS), standard library only.

Every call after setup uses only the device token: print_device_poll (the restaurant, its printers
and the tickets to print), print_device_report (printed / failed), print_device_save_printer,
print_device_status (printer states) and print_device_preview (the sample tickets as PNG).
Setup uses either a device key (print_device_pair) or the owner's phone sign-in (the same SMS code
as the dashboard), which is used once to register this computer and then signed out.
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

    def __init__(self, code: str, status: int = 0):
        super().__init__(code)
        self.code = code
        self.status = status


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
        raise ApiError(code, e.code) from None
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

    def send_code(self, phone: str) -> None:
        _request("POST", f"{self.url}/auth/v1/otp", {"phone": phone, "create_user": False}, self._headers())

    def verify_code(self, phone: str, code: str) -> str:
        res = _request("POST", f"{self.url}/auth/v1/verify", {"type": "sms", "phone": phone, "token": code.strip()}, self._headers())
        token = (res or {}).get("access_token")
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
