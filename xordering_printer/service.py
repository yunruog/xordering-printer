"""The background service: one per computer, started at sign-in, keeps the worker running.

    XOrderingPrinter.exe --service    (what Windows starts at sign-in; also started by the window)

It holds a local control port (127.0.0.1 only) as its single-instance lock: a second copy sees the
port taken and exits. It runs the worker as a child process and starts it again whenever it stops
(a crash, a hang the worker can't recover from, Windows killing it), with a short back-off. The
worker's exit code 42 means "update downloaded": the service swaps the exe and restarts into it.
A scheduled task (install.py) starts the service again every few minutes in case the service
itself is ever killed.
"""
import json
import os
import socket
import subprocess
import sys
import threading
import time
from typing import Optional

from . import VERSION, updater
from .logs import get as get_log
from .paths import FROZEN, IS_WINDOWS, current_exe, self_command
from .worker import UPDATE_EXIT_CODE

CONTROL_PORT = int(os.environ.get("XORDERING_PRINTER_PORT") or 47291)
NO_WINDOW = 0x08000000 if IS_WINDOWS else 0
DETACHED = (0x00000008 | 0x00000200) if IS_WINDOWS else 0  # DETACHED_PROCESS | CREATE_NEW_PROCESS_GROUP


def popen(args: list, detached: bool = False) -> subprocess.Popen:
    kwargs = {"stdin": subprocess.DEVNULL, "stdout": subprocess.DEVNULL, "stderr": subprocess.DEVNULL, "close_fds": True}
    if IS_WINDOWS:
        kwargs["creationflags"] = NO_WINDOW | (DETACHED if detached else 0)
    elif detached:
        kwargs["start_new_session"] = True
    return subprocess.Popen(args, **kwargs)


def ask(command: str, timeout: float = 2.0) -> Optional[dict]:
    """Sends a command to the running service; None when none is running."""
    try:
        with socket.create_connection(("127.0.0.1", CONTROL_PORT), timeout=timeout) as s:
            s.sendall(command.encode() + b"\n")
            s.settimeout(timeout)
            data = b""
            while not data.endswith(b"\n"):
                chunk = s.recv(4096)
                if not chunk:
                    break
                data += chunk
        return json.loads(data.decode() or "{}")
    except (OSError, ValueError):
        return None


def running() -> Optional[dict]:
    return ask("status")


def start_detached() -> None:
    from .paths import installed_exe

    exe = installed_exe()
    args = [str(exe), "--service"] if FROZEN and IS_WINDOWS and exe.exists() else self_command("--service")
    popen(args, detached=True)


class Service:
    def __init__(self) -> None:
        self.log = get_log()
        self.lock: Optional[socket.socket] = None
        self.worker: Optional[subprocess.Popen] = None
        self.stopping = threading.Event()

    def acquire(self, wait: float = 0) -> bool:
        deadline = time.time() + wait
        while True:
            s = socket.socket(socket.AF_INET, socket.SOCK_STREAM)
            if IS_WINDOWS:
                s.setsockopt(socket.SOL_SOCKET, getattr(socket, "SO_EXCLUSIVEADDRUSE", -5), 1)
            else:
                # Rebinding right after the last copy quit (TIME_WAIT); a live listener still blocks it.
                s.setsockopt(socket.SOL_SOCKET, socket.SO_REUSEADDR, 1)
            try:
                s.bind(("127.0.0.1", CONTROL_PORT))
                s.listen(4)
                self.lock = s
                return True
            except OSError:
                s.close()
                if time.time() >= deadline:
                    return False
                time.sleep(1)

    def release(self) -> None:
        if self.lock:
            try:
                self.lock.close()
            except OSError:
                pass
            self.lock = None

    def _serve(self) -> None:
        while not self.stopping.is_set() and self.lock:
            try:
                conn, _ = self.lock.accept()
            except OSError:
                return
            with conn:
                try:
                    conn.settimeout(2)
                    cmd = conn.recv(64).decode(errors="replace").strip()
                    if cmd == "quit":
                        conn.sendall(b'{"ok": true}\n')
                        self.stopping.set()
                    else:
                        info = {"version": VERSION, "pid": os.getpid(), "workerPid": self.worker.pid if self.worker else None, "exe": str(current_exe())}
                        conn.sendall(json.dumps(info).encode() + b"\n")
                except OSError:
                    pass

    def run(self, after_update: bool = False) -> int:
        if not self.acquire(wait=60 if after_update else 3):
            self.log.info("service already running; this copy exits")
            return 0
        self.log.info("service %s started (pid %s)", VERSION, os.getpid())
        if FROZEN:
            updater.cleanup_old(current_exe())
            from .install import write_installed_version
            from .paths import installed_exe

            if current_exe() == installed_exe():
                write_installed_version(VERSION)
        threading.Thread(target=self._serve, daemon=True).start()
        backoff = 1.0
        while not self.stopping.is_set():
            started = time.time()
            self.worker = popen(self_command("--worker"))
            while self.worker.poll() is None and not self.stopping.is_set():
                time.sleep(0.5)
            if self.stopping.is_set():
                break
            code = self.worker.returncode
            if code == UPDATE_EXIT_CODE and self.apply_update():
                return 0
            if code != 0:
                self.log.warning("worker stopped (exit %s); restarting", code)
            backoff = 1.0 if time.time() - started > 60 else min(30.0, backoff * 2)
            self.stopping.wait(backoff)
        self.stop_worker()
        self.log.info("service stopped")
        self.release()
        return 0

    def stop_worker(self) -> None:
        if self.worker and self.worker.poll() is None:
            self.worker.terminate()
            try:
                self.worker.wait(10)
            except subprocess.TimeoutExpired:
                self.worker.kill()

    def apply_update(self) -> bool:
        pending = updater.take_pending()
        if not (pending and FROZEN and IS_WINDOWS):
            return False
        exe = current_exe()
        self.log.info("installing update %s", pending["version"])
        try:
            old = updater.swap_in(exe, pending["path"])
        except OSError:
            self.log.exception("update could not be installed")
            return False
        self.release()
        new = popen([str(exe), "--service", "--after-update"], detached=True)
        deadline = time.time() + 60
        while time.time() < deadline:
            info = running()
            if info and updater.parse_version(info.get("version", "")) == updater.parse_version(pending["version"]):
                self.log.info("now running %s", pending["version"])
                return True
            if new.poll() is not None:
                break
            time.sleep(2)
        self.log.warning("update %s didn't start; going back to %s", pending["version"], VERSION)
        try:
            ask("quit")
            new.kill()
        except OSError:
            pass
        time.sleep(2)
        try:
            updater.restore(exe, old)
        except OSError:
            self.log.exception("restoring the previous version failed")
        popen([str(exe), "--service", "--after-update"], detached=True)
        return True


def run_service(after_update: bool = False) -> int:
    return Service().run(after_update=after_update)


def run_worker() -> int:
    from .worker import Worker

    update = None
    if FROZEN and IS_WINDOWS:
        from . import config

        def update() -> bool:
            return bool(config.load().get("autoUpdate", True)) and updater.check_and_download()

    return Worker().run_forever(update_check=update)


if __name__ == "__main__":  # pragma: no cover
    sys.exit(run_service())
