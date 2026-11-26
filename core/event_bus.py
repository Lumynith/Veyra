"""Thread-safe publish/subscribe bus. Callbacks always run on the GUI thread."""
from __future__ import annotations

import logging
import threading
from collections import defaultdict
from typing import Any, Callable

from PySide6.QtCore import QObject, Signal

log = logging.getLogger(__name__)
Callback = Callable[[Any], None]


class EventBus(QObject):
    # Qt queues this signal when emitted from a worker thread, so delivery lands on the GUI thread.
    _dispatch = Signal(str, object)

    def __init__(self) -> None:
        super().__init__()
        self._subs: dict[str, list[Callback]] = defaultdict(list)
        self._lock = threading.Lock()
        self._dispatch.connect(self._deliver)

    def subscribe(self, topic: str, callback: Callback) -> None:
        with self._lock:
            self._subs[topic].append(callback)

    def unsubscribe(self, topic: str, callback: Callback) -> None:
        with self._lock:
            if callback in self._subs[topic]:
                self._subs[topic].remove(callback)

    def publish(self, topic: str, payload: Any = None) -> None:
        self._dispatch.emit(topic, payload)

    def _deliver(self, topic: str, payload: Any) -> None:
        with self._lock:
            callbacks = list(self._subs[topic]) + list(self._subs["*"])
        for cb in callbacks:
            try:
                cb(payload)
            except Exception:
                log.exception("Subscriber for %r failed", topic)
