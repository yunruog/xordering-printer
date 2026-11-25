"""A small local log (rotating, ~5 MB at most) for troubleshooting: logs/printer.log."""
import logging
import logging.handlers
import re

from .paths import log_dir

_SECRET = re.compile(r"(xpd_|xpk_)[0-9a-fA-F]{8,}|eyJ[\w-]{10,}\.[\w-]+\.[\w-]+")


class _Redact(logging.Filter):
    """Never write a device token, device key or session token to the log."""

    def filter(self, record: logging.LogRecord) -> bool:
        msg = record.getMessage()
        clean = _SECRET.sub(lambda m: m.group(0)[:4] + "…", msg)
        if clean != msg:
            record.msg, record.args = clean, ()
        return True


def setup(role: str) -> logging.Logger:
    log = logging.getLogger("xordering_printer")
    if log.handlers:
        return log
    log.setLevel(logging.INFO)
    handler = logging.handlers.RotatingFileHandler(log_dir() / "printer.log", maxBytes=1_000_000, backupCount=4, encoding="utf-8")
    handler.setFormatter(logging.Formatter(f"%(asctime)s [{role}] %(levelname)s %(message)s"))
    handler.addFilter(_Redact())
    log.addHandler(handler)
    return log


def get() -> logging.Logger:
    return logging.getLogger("xordering_printer")
