"""Installing on Windows, done by the program itself the first time it is opened (no admin rights):

1. copies itself to %LOCALAPPDATA%\\XOrderingPrinter\\bin\\XOrderingPrinter.exe (a newer download
   opened later replaces an older installed copy);
2. starts the service at sign-in: a shortcut in the Startup folder (falls back to the registry
   Run key if the shortcut can't be made);
3. a scheduled task starts the service every 5 minutes if it isn't running (crash recovery);
4. starts the service now.
"""
import os
import shutil
import subprocess
import time
from pathlib import Path

from . import APP_NAME, VERSION
from .logs import get as get_log
from .paths import FROZEN, IS_WINDOWS, current_exe, install_dir, installed_exe
from .service import NO_WINDOW, ask, running, start_detached

TASK = f"{APP_NAME} Watchdog"


def startup_folder() -> Path:
    return Path(os.environ.get("APPDATA", "")) / "Microsoft" / "Windows" / "Start Menu" / "Programs" / "Startup"


def _run(args: list, timeout: float = 30) -> bool:
    try:
        return subprocess.run(args, stdin=subprocess.DEVNULL, stdout=subprocess.DEVNULL, stderr=subprocess.DEVNULL, timeout=timeout, creationflags=NO_WINDOW).returncode == 0
    except (OSError, subprocess.TimeoutExpired):
        return False


def _ps_quote(s: str) -> str:
    return "'" + s.replace("'", "''") + "'"


def make_shortcut(link: Path, target: Path, arguments: str, description: str) -> bool:
    script = (
        f"$s=(New-Object -ComObject WScript.Shell).CreateShortcut({_ps_quote(str(link))});"
        f"$s.TargetPath={_ps_quote(str(target))};$s.Arguments={_ps_quote(arguments)};"
        f"$s.WorkingDirectory={_ps_quote(str(target.parent))};$s.Description={_ps_quote(description)};$s.Save()"
    )
    return _run(["powershell", "-NoProfile", "-NonInteractive", "-ExecutionPolicy", "Bypass", "-Command", script])


def set_autostart(exe: Path) -> str:
    link = startup_folder() / f"{APP_NAME}.lnk"
    if make_shortcut(link, exe, "--service", "XOrdering kitchen printing"):
        return "startup-folder"
    import winreg  # noqa: PLC0415 (Windows only)

    with winreg.OpenKey(winreg.HKEY_CURRENT_USER, r"Software\Microsoft\Windows\CurrentVersion\Run", 0, winreg.KEY_SET_VALUE) as key:
        winreg.SetValueEx(key, APP_NAME, 0, winreg.REG_SZ, f'"{exe}" --service')
    return "registry"


def set_watchdog(exe: Path) -> bool:
    return _run(["schtasks", "/Create", "/F", "/SC", "MINUTE", "/MO", "5", "/TN", TASK, "/TR", f'"{exe}" --service'])


def ensure_installed() -> Path:
    """Installs or upgrades the copy that runs at sign-in; returns the exe that should run."""
    log = get_log()
    if not (FROZEN and IS_WINDOWS):
        return current_exe()
    target = installed_exe()
    me = current_exe()
    if me != target:
        from .updater import parse_version

        info = running()
        installed_version = info.get("version") if info else None
        installed_version = installed_version or installed_version_file()
        if not target.exists() or parse_version(VERSION) > parse_version(installed_version or ""):
            if info:
                ask("quit")
                for _ in range(20):
                    if not running():
                        break
                    time.sleep(0.5)
            install_dir().mkdir(parents=True, exist_ok=True)
            for attempt in range(10):
                try:
                    tmp = target.with_suffix(".new")
                    shutil.copy2(me, tmp)
                    os.replace(tmp, target)
                    break
                except OSError:
                    if attempt == 9:
                        log.exception("couldn't install to %s; running from %s", target, me)
                        return me
                    time.sleep(1)
            write_installed_version(VERSION)
            log.info("installed %s to %s", VERSION, target)
    how = set_autostart(target)
    watchdog = set_watchdog(target)
    log.info("starts at sign-in (%s); watchdog task %s", how, "set" if watchdog else "not set")
    return target


def installed_version_file() -> str:
    try:
        return (install_dir() / "version.txt").read_text("utf-8").strip()
    except OSError:
        return ""


def write_installed_version(version: str) -> None:
    try:
        (install_dir() / "version.txt").write_text(version, "utf-8")
    except OSError:
        pass


def ensure_running() -> None:
    if not running():
        start_detached()


def uninstall() -> None:
    ask("quit")
    if IS_WINDOWS:
        try:
            (startup_folder() / f"{APP_NAME}.lnk").unlink()
        except OSError:
            pass
        _run(["schtasks", "/Delete", "/F", "/TN", TASK])
        try:
            import winreg  # noqa: PLC0415

            with winreg.OpenKey(winreg.HKEY_CURRENT_USER, r"Software\Microsoft\Windows\CurrentVersion\Run", 0, winreg.KEY_SET_VALUE) as key:
                winreg.DeleteValue(key, APP_NAME)
        except OSError:
            pass
