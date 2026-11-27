"""This computer's settings (config.json in the data folder), written atomically.

    site            the XOrdering site whose printer-config.json names the server
    supabaseUrl     server URL and publishable key (from printer-config.json; browser-safe values)
    publishableKey
    deviceToken     this computer's long-lived device token (never leaves this computer)
    deviceId, deviceName, restaurant {id, name}
    printers        the restaurant's printers, as last received (so tickets print while offline)
    printerLocal    how THIS computer reaches each printer, by printer id (the server keeps name,
                    address, type and paper; these stay here, so a poll never overwrites them):
                    {connection: tcp | windows, windowsPrinter, windowsMethod: raw | driver}
    pollSeconds     how often to ask for new tickets (3-5)
    autoUpdate      check GitHub Releases for updates
    paused          printing paused (this window's switch or the dashboard's): tickets wait
    pausePending    a pause / resume made in this window, not yet sent to the server:
                    {paused, skip, at} (skip: resume without printing what waited since `at`)
"""
import json
import os
import shutil
import tempfile
from typing import Any, Dict, List, Optional

from . import DEFAULT_SITE
from .paths import config_path

DEFAULTS: Dict[str, Any] = {
    "site": DEFAULT_SITE,
    "supabaseUrl": "",
    "publishableKey": "",
    "deviceToken": "",
    "deviceId": "",
    "deviceName": "",
    "restaurant": None,
    "printers": [],
    "printerLocal": {},
    "pollSeconds": 4,
    "autoUpdate": True,
    "paused": False,
    "pausePending": None,
}


def _read(path) -> Optional[Dict[str, Any]]:
    try:
        with open(path, encoding="utf-8") as f:
            stored = json.load(f)
        return stored if isinstance(stored, dict) else None
    except (OSError, ValueError):
        return None


def backup_path():
    return config_path().with_name("config.backup.json")


def load() -> Dict[str, Any]:
    """The settings; when config.json is missing or damaged (a crash mid-write on an old version, a
    disk hiccup) the last good copy (config.backup.json) is used, so the device token survives."""
    cfg = dict(DEFAULTS)
    stored = _read(config_path())
    if stored is None:
        stored = _read(backup_path())
    if stored:
        cfg.update(stored)
    if not isinstance(cfg.get("printerLocal"), dict):
        cfg["printerLocal"] = {}
    cfg["pollSeconds"] = min(5, max(3, int(cfg.get("pollSeconds") or 4)))
    return cfg


def save(cfg: Dict[str, Any]) -> None:
    path = config_path()
    fd, tmp = tempfile.mkstemp(prefix="config-", suffix=".tmp", dir=str(path.parent))
    try:
        with os.fdopen(fd, "w", encoding="utf-8") as f:
            json.dump(cfg, f, ensure_ascii=False, indent=2)
            f.flush()
            os.fsync(f.fileno())
        old = _read(path)
        if old and old.get("deviceToken"):
            # Keep the last good copy that had a token next to it before replacing it.
            shutil.copy2(path, backup_path())
        os.replace(tmp, path)
    finally:
        if os.path.exists(tmp):
            os.unlink(tmp)


def update(**changes: Any) -> Dict[str, Any]:
    cfg = load()
    cfg.update(changes)
    save(cfg)
    return cfg


def mtime() -> float:
    try:
        return config_path().stat().st_mtime
    except OSError:
        return 0.0


def signed_in(cfg: Dict[str, Any]) -> bool:
    return bool(cfg.get("deviceToken") and cfg.get("supabaseUrl") and cfg.get("publishableKey"))


LOCAL_KEYS = ("connection", "windowsPrinter", "windowsMethod")


def effective_printers(cfg: Dict[str, Any]) -> List[Dict[str, Any]]:
    """The server's printers with this computer's way of reaching each (printerLocal) applied."""
    local = cfg.get("printerLocal") or {}
    out = []
    for p in cfg.get("printers") or []:
        mine = {k: v for k, v in (local.get(p.get("id")) or {}).items() if k in LOCAL_KEYS}
        out.append(dict(p, connection="tcp", **mine) if "connection" not in mine else dict(p, **mine))
    return out


def set_local(printer_id: str, **values: Any) -> Dict[str, Any]:
    """Saves how this computer reaches one printer (None removes a value)."""
    cfg = load()
    local = dict(cfg.get("printerLocal") or {})
    entry = dict(local.get(printer_id) or {})
    for k, v in values.items():
        if k not in LOCAL_KEYS:
            continue
        if v is None or v == "":
            entry.pop(k, None)
        else:
            entry[k] = v
    local[printer_id] = entry
    cfg["printerLocal"] = local
    save(cfg)
    return cfg
