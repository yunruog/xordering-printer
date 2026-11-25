"""This computer's settings (config.json in the data folder), written atomically.

    site            the XOrdering site whose printer-config.json names the server
    supabaseUrl     server URL and publishable key (from printer-config.json; browser-safe values)
    publishableKey
    deviceToken     this computer's long-lived device token (never leaves this computer)
    deviceId, deviceName, restaurant {id, name}
    printers        the restaurant's printers, as last received (so tickets print while offline)
    pollSeconds     how often to ask for new tickets (3-5)
    autoUpdate      check GitHub Releases once a day
"""
import json
import os
import tempfile
from typing import Any, Dict

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
    "pollSeconds": 4,
    "autoUpdate": True,
}


def load() -> Dict[str, Any]:
    cfg = dict(DEFAULTS)
    try:
        with open(config_path(), encoding="utf-8") as f:
            stored = json.load(f)
        if isinstance(stored, dict):
            cfg.update(stored)
    except (OSError, ValueError):
        pass
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
