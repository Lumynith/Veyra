"""Bottom status bar: automation state, current block, elapsed time, Roblox link, CPU, memory, errors."""
from __future__ import annotations

import logging
import os
import time

import win32api
import win32process
from PySide6.QtCore import QSize, Qt, QTimer, Signal
from PySide6.QtWidgets import QHBoxLayout, QLabel, QStatusBar, QToolButton, QWidget

from ui.theme import C, icon
from ui.widgets import Spinner, StatusDot

_RESULT = {"completed": "Completed", "stopped": "Stopped", "error": "Failed"}


class AppStatusBar(QStatusBar):
    errors_clicked = Signal()

    def __init__(self, bus, parent=None) -> None:
        super().__init__(parent)
        self.setSizeGripEnabled(False)
        self.spinner = Spinner(12)
        self.state = QLabel("Idle")
        self.block = QLabel("")
        self.elapsed = QLabel("00:00.0")
        self.elapsed.setToolTip("Execution time")
        self.roblox_dot = StatusDot()
        self.roblox = QLabel("Roblox disconnected")
        self.cpu = QLabel("CPU –")
        self.mem = QLabel("RAM –")
        self.errors = QToolButton(objectName="errorIndicator", toolButtonStyle=Qt.ToolButtonStyle.ToolButtonTextBesideIcon)
        self.errors.setIcon(icon("alert"))
        self.errors.setIconSize(QSize(13, 13))
        self.errors.setToolTip("Errors since last cleared (click to clear)")
        self.errors.clicked.connect(self._clear_errors)
        self._error_count = 0
        self._clear_errors()

        left = QWidget()
        row = QHBoxLayout(left)
        row.setContentsMargins(6, 0, 0, 0)
        row.setSpacing(0)
        for w in (self.spinner, self.state, self.block, self.elapsed):
            row.addWidget(w)
        row.addStretch(1)
        self.addWidget(left, 1)
        for w in (self.roblox_dot, self.roblox, self.cpu, self.mem, self.errors):
            self.addPermanentWidget(w)

        self._started: float | None = None
        self._tick = QTimer(self, interval=100, timeout=self._update_elapsed)
        self._res = QTimer(self, interval=1000, timeout=self._update_resources)
        self._res.start()
        self._last_cpu = (time.perf_counter(), sum(os.times()[:2]))
        self._proc = win32api.GetCurrentProcess()

        bus.subscribe("engine.state", self._on_state)
        bus.subscribe("engine.block", lambda p: p["title"] and self.block.setText(f"›  {p['title']}"))
        bus.subscribe("engine.finished", self._on_finished)
        bus.subscribe("roblox.attached", lambda p: self._roblox(f"Roblox {p['mode']}", C["ok"]))
        bus.subscribe("roblox.detached", lambda _: self._roblox("Roblox disconnected", C["faint"]))
        bus.subscribe("roblox.closed", lambda _: self._roblox("Roblox closed", C["err"]))
        bus.subscribe("roblox.hung", lambda hung: self._roblox("Roblox not responding", C["warn"]) if hung
                      else self._roblox("Roblox connected", C["ok"]))
        bus.subscribe("log.record", self._on_log)

    def _on_state(self, state: str) -> None:
        if state == "running" and self._started is None:
            self._started = time.perf_counter()
            self.block.setText("")
            self._tick.start()
        if state == "idle":
            return  # engine.finished reports the result
        self.state.setText(state.capitalize())
        (self.spinner.start if state == "running" else self.spinner.stop)()

    def _on_finished(self, p: dict) -> None:
        self._tick.stop()
        self._started = None
        self.spinner.stop()
        self.state.setText(_RESULT.get(p["result"], p["result"]))
        self._show_time(p["elapsed"])

    def _update_elapsed(self) -> None:
        if self._started is not None:
            self._show_time(time.perf_counter() - self._started)

    def _show_time(self, seconds: float) -> None:
        self.elapsed.setText(f"{int(seconds // 60):02d}:{seconds % 60:04.1f}")

    def _update_resources(self) -> None:
        now, cpu = time.perf_counter(), sum(os.times()[:2])
        last_now, last_cpu = self._last_cpu
        self._last_cpu = (now, cpu)
        pct = (cpu - last_cpu) / max(now - last_now, 1e-6) / (os.cpu_count() or 1) * 100
        self.cpu.setText(f"CPU {pct:4.1f}%")
        mem = win32process.GetProcessMemoryInfo(self._proc)["WorkingSetSize"] / 1048576
        self.mem.setText(f"RAM {mem:.0f} MB")

    def _roblox(self, text: str, color: str) -> None:
        self.roblox.setText(text)
        self.roblox_dot.set_color(color)

    def _on_log(self, record: dict) -> None:
        if record["level"] >= logging.ERROR:
            self._error_count += 1
            self._render_errors()

    def _clear_errors(self) -> None:
        self._error_count = 0
        self._render_errors()
        self.errors_clicked.emit()

    def _render_errors(self) -> None:
        has = self._error_count > 0
        self.errors.setText(str(self._error_count))
        self.errors.setIcon(icon("alert", C["err"] if has else C["faint"]))
        self.errors.setProperty("hasErrors", has)
        self.errors.style().unpolish(self.errors)
        self.errors.style().polish(self.errors)
