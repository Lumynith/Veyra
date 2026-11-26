"""Run slow work (capture, detection, OCR) off the GUI thread and get the result back on it."""
from __future__ import annotations

import logging
from typing import Any, Callable

from PySide6.QtCore import QObject, QRunnable, QThreadPool, Signal

log = logging.getLogger(__name__)
_pending: set = set()  # keeps signal objects alive until their task reports back


class _Signals(QObject):
    done = Signal(object)
    failed = Signal(str)


class _Task(QRunnable):
    def __init__(self, fn: Callable[[], Any], signals: _Signals) -> None:
        super().__init__()
        self._fn, self._signals = fn, signals

    def run(self) -> None:
        try:
            result = self._fn()
        except Exception as e:  # reported to the caller on the GUI thread
            log.debug("Background task failed", exc_info=True)
            self._signals.failed.emit(str(e))
        else:
            self._signals.done.emit(result)


def run_async(fn: Callable[[], Any], on_done: Callable[[Any], None],
              on_error: Callable[[str], None] | None = None) -> None:
    signals = _Signals()  # created on the GUI thread, so its slots run there
    _pending.add(signals)
    signals.done.connect(on_done)
    signals.failed.connect(on_error or (lambda msg: log.error("%s", msg)))
    signals.done.connect(lambda _: _pending.discard(signals))
    signals.failed.connect(lambda _: _pending.discard(signals))
    QThreadPool.globalInstance().start(_Task(fn, signals))
