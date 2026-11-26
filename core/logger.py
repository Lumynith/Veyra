"""Logging to a rotating file, the console, and the in-app Logs panel (via the event bus)."""
from __future__ import annotations

import logging
import sys
import threading
from logging.handlers import RotatingFileHandler
from pathlib import Path

FORMAT = "%(asctime)s [%(levelname)s] %(name)s: %(message)s"


class BusHandler(logging.Handler):
    def __init__(self, bus) -> None:
        super().__init__()
        self._bus = bus
        self._busy = threading.local()

    def emit(self, record: logging.LogRecord) -> None:
        if getattr(self._busy, "on", False):  # a failing log subscriber must not recurse forever
            return
        self._busy.on = True
        try:
            self._bus.publish("log.record", {"level": record.levelno, "text": self.format(record)})
        finally:
            self._busy.on = False


_installed: list[logging.Handler] = []


def setup_logging(log_dir: Path, bus) -> Path:
    log_dir.mkdir(parents=True, exist_ok=True)
    log_file = log_dir / "veyra.log"
    fmt = logging.Formatter(FORMAT)
    root = logging.getLogger()
    root.setLevel(logging.INFO)
    for old in _installed:  # set up again (e.g. a second Application in tests): replace, don't stack
        root.removeHandler(old)
        old.close()
    _installed.clear()
    for handler in (
        RotatingFileHandler(log_file, maxBytes=1_000_000, backupCount=3, encoding="utf-8"),
        logging.StreamHandler(sys.stderr),
        BusHandler(bus),
    ):
        handler.setFormatter(fmt)
        root.addHandler(handler)
        _installed.append(handler)
    sys.excepthook = lambda t, v, tb: root.critical("Unhandled exception", exc_info=(t, v, tb))
    threading.excepthook = lambda a: root.critical("Unhandled exception in thread %s", a.thread.name if a.thread
                                                   else "?", exc_info=(a.exc_type, a.exc_value, a.exc_traceback))
    return log_file


def detach_ui() -> None:
    """Stop sending log records to the UI (on shutdown: the windows are about to be destroyed)."""
    root = logging.getLogger()
    for h in [h for h in _installed if isinstance(h, BusHandler)]:
        root.removeHandler(h)
        _installed.remove(h)


def set_level(name: str) -> None:
    logging.getLogger().setLevel(getattr(logging, str(name).upper(), logging.INFO))
