"""Live log console shown under the viewport."""
from __future__ import annotations

import html
import logging

from PySide6.QtGui import QFont
from PySide6.QtWidgets import QPlainTextEdit

from ui.theme import C

_COLORS = {logging.DEBUG: C["faint"], logging.INFO: C["muted"], logging.WARNING: C["warn"]}


class Console(QPlainTextEdit):
    def __init__(self, bus, parent=None) -> None:
        super().__init__(parent, readOnly=True)
        self.setMaximumBlockCount(2000)
        self.setFrameShape(QPlainTextEdit.Shape.NoFrame)
        font = QFont("Cascadia Mono")
        font.setStyleHint(QFont.StyleHint.Monospace)
        font.setPointSizeF(8.5)
        self.setFont(font)
        bus.subscribe("log.record", self._append)

    def _append(self, record: dict) -> None:
        color = _COLORS.get(record["level"], C["err"])
        self.appendHtml(f'<span style="color:{color}; white-space:pre">{html.escape(record["text"])}</span>')

    def contextMenuEvent(self, e) -> None:
        menu = self.createStandardContextMenu()
        menu.addSeparator()
        menu.addAction("Clear").triggered.connect(self.clear)
        menu.exec(e.globalPos())
