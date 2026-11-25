"""Where things live on this computer.

Data (kept across updates and reinstalls, never inside the program's folder):
    Windows: %APPDATA%\\XOrderingPrinter\\  config.json, jobs.db, status.json, logs\\
    elsewhere (development): ~/.xordering-printer/
The program itself is installed to %LOCALAPPDATA%\\XOrderingPrinter\\bin\\XOrderingPrinter.exe.
XORDERING_PRINTER_HOME overrides the data folder (tests run several copies side by side).
"""
import os
import sys
from pathlib import Path

from . import APP_NAME

IS_WINDOWS = sys.platform == "win32"
FROZEN = bool(getattr(sys, "frozen", False))


def data_dir() -> Path:
    override = os.environ.get("XORDERING_PRINTER_HOME")
    if override:
        base = Path(override)
    elif IS_WINDOWS and os.environ.get("APPDATA"):
        base = Path(os.environ["APPDATA"]) / APP_NAME
    else:
        base = Path.home() / ".xordering-printer"
    base.mkdir(parents=True, exist_ok=True)
    return base


def config_path() -> Path:
    return data_dir() / "config.json"


def jobs_db_path() -> Path:
    return data_dir() / "jobs.db"


def status_path() -> Path:
    return data_dir() / "status.json"


def log_dir() -> Path:
    d = data_dir() / "logs"
    d.mkdir(parents=True, exist_ok=True)
    return d


def updates_dir() -> Path:
    d = data_dir() / "updates"
    d.mkdir(parents=True, exist_ok=True)
    return d


def install_dir() -> Path:
    local = os.environ.get("LOCALAPPDATA") or str(Path.home() / "AppData" / "Local")
    return Path(local) / APP_NAME / "bin"


def installed_exe() -> Path:
    return install_dir() / f"{APP_NAME}.exe"


def current_exe() -> Path:
    return Path(sys.executable).resolve()


def self_command(*args: str) -> list:
    """The command line that runs this program again with other arguments."""
    if FROZEN:
        return [sys.executable, *args]
    return [sys.executable, "-m", "xordering_printer", *args]
