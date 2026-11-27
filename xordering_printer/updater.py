"""Self-update from this repository's GitHub Releases (public, no sign-in), checked every 3 hours.

The worker downloads a newer XOrderingPrinter.exe next to the data folder and checks it against the
release's XOrderingPrinter.exe.sha256, then exits with UPDATE_EXIT_CODE. The service (which runs
the installed exe) swaps the files (Windows lets a running exe be renamed), starts the new version
and, if it doesn't come up within a minute, puts the old one back. Config, device token and the
ticket queue live in the data folder, so nothing is lost.
"""
import hashlib
import json
import os
import re
import shutil
import time
import urllib.request
from pathlib import Path
from typing import Optional, Tuple

from . import APP_NAME, GITHUB_REPO, VERSION
from .logs import get as get_log
from .paths import FROZEN, IS_WINDOWS, updates_dir

ASSET = f"{APP_NAME}.exe"
CHECK_EVERY = 3 * 3600  # fixes reach the restaurants within hours (GitHub allows 60 checks an hour)


def parse_version(v: str) -> Tuple[int, ...]:
    m = re.match(r"^v?(\d+)\.(\d+)\.(\d+)$", (v or "").strip())
    return tuple(int(x) for x in m.groups()) if m else (0, 0, 0)


def _get(url: str, timeout: float = 30) -> bytes:
    req = urllib.request.Request(url, headers={"User-Agent": f"{APP_NAME}/{VERSION}", "Accept": "application/vnd.github+json"})
    with urllib.request.urlopen(req, timeout=timeout) as res:
        return res.read()


def latest_release() -> Optional[dict]:
    data = json.loads(_get(f"https://api.github.com/repos/{GITHUB_REPO}/releases/latest").decode("utf-8"))
    assets = {a.get("name"): a.get("browser_download_url") for a in data.get("assets") or []}
    if ASSET not in assets or f"{ASSET}.sha256" not in assets:
        return None
    return {"version": data.get("tag_name", ""), "exe": assets[ASSET], "sha256": assets[f"{ASSET}.sha256"]}


def _last_check_file() -> Path:
    return updates_dir() / "last_check.json"


def pending_file() -> Path:
    return updates_dir() / "pending.json"


def check_and_download(force: bool = False) -> bool:
    """True when a newer version was downloaded and verified (pending.json written)."""
    if not (FROZEN and IS_WINDOWS):
        return False
    log = get_log()
    try:
        last = json.loads(_last_check_file().read_text("utf-8")).get("at", 0)
    except (OSError, ValueError):
        last = 0
    if not force and time.time() - last < CHECK_EVERY:
        return False
    _last_check_file().write_text(json.dumps({"at": time.time()}), "utf-8")
    try:
        rel = latest_release()
    except Exception as e:  # no internet, GitHub down, rate limit: try again tomorrow
        log.info("update check skipped: %s", e)
        return False
    if not rel or parse_version(rel["version"]) <= parse_version(VERSION):
        log.info("up to date (%s; latest %s)", VERSION, rel["version"] if rel else "none")
        return False
    log.info("downloading update %s", rel["version"])
    expected = _get(rel["sha256"]).decode("ascii", "replace").split()[0].lower()
    data = _get(rel["exe"], timeout=300)
    if hashlib.sha256(data).hexdigest() != expected or not data.startswith(b"MZ"):
        log.warning("update %s failed verification; not installed", rel["version"])
        return False
    target = updates_dir() / f"{APP_NAME}-{rel['version']}.exe"
    target.write_bytes(data)
    pending_file().write_text(json.dumps({"version": rel["version"], "path": str(target)}), "utf-8")
    return True


def take_pending() -> Optional[dict]:
    try:
        p = json.loads(pending_file().read_text("utf-8"))
    except (OSError, ValueError):
        return None
    try:
        pending_file().unlink()
    except OSError:
        pass
    return p if p.get("path") and os.path.exists(p["path"]) else None


def swap_in(exe: Path, new_file: str) -> Path:
    """Puts the new exe in place of the running one; returns where the old one went."""
    old = exe.with_name(exe.stem + ".old.exe")
    try:
        if old.exists():
            old.unlink()
    except OSError:
        old = exe.with_name(f"{exe.stem}.old-{int(time.time())}.exe")
    os.replace(exe, old)
    shutil.copy2(new_file, exe)
    return old


def restore(exe: Path, old: Path) -> None:
    try:
        if exe.exists():
            exe.unlink()
    except OSError:
        exe.rename(exe.with_name(f"{exe.stem}.failed-{int(time.time())}.exe"))
    os.replace(old, exe)


def cleanup_old(exe: Path) -> None:
    for f in exe.parent.glob(f"{exe.stem}.old*.exe"):
        try:
            f.unlink()
        except OSError:
            pass
    for f in updates_dir().glob(f"{APP_NAME}-*.exe"):
        try:
            if time.time() - f.stat().st_mtime > 3600:
                f.unlink()
        except OSError:
            pass
