"""Debugging panels for the editor: workflow variables (manager + live values) and the run log."""
from __future__ import annotations

import logging

from PySide6.QtCore import Qt, Signal
from PySide6.QtGui import QColor
from PySide6.QtWidgets import (QAbstractItemView, QComboBox, QHBoxLayout, QHeaderView, QInputDialog, QLabel,
                               QLineEdit, QMessageBox, QTableWidget, QTableWidgetItem, QVBoxLayout, QWidget)

from automation.engine import IDLE
from automation.expressions import coerce, evaluate, fmt, interpolate
from automation.workflow import valid_var_name
from ui.theme import C
from ui.widgets import IconButton

log = logging.getLogger(__name__)
MAX_ROWS = 2000
PROBLEMS = {"error", "timeout", "retry", "skipped", "not found"}
COLORS = {"error": "err", "timeout": "err", "retry": "warn", "skipped": "warn", "not found": "warn",
          "found": "ok", "run": "accent_hi"}


def _item(text, editable=False, color: str | None = None) -> QTableWidgetItem:
    item = QTableWidgetItem(text)
    if not editable:
        item.setFlags(item.flags() & ~Qt.ItemFlag.ItemIsEditable)
    if color:
        item.setForeground(QColor(C[color]))
    return item


class VariablesPanel(QWidget):
    """Declare workflow variables with initial values; see (and while running, change) live values."""

    def __init__(self, scene, engine, bus, parent=None) -> None:
        super().__init__(parent)
        self._scene, self._engine = scene, engine
        self._live: dict = {}
        self._filling = False
        self.table = QTableWidget(0, 3)
        self.table.setHorizontalHeaderLabels(["Variable", "Initial value", "Current value"])
        self.table.verticalHeader().hide()
        self.table.horizontalHeader().setSectionResizeMode(QHeaderView.ResizeMode.Stretch)
        self.table.setSelectionBehavior(QAbstractItemView.SelectionBehavior.SelectRows)
        self.table.setToolTip("Initial values are saved with the workflow and set before the first block runs.\n"
                              "Current values can be changed while the workflow is running or paused.")
        self.table.itemChanged.connect(self._edited)
        add = IconButton("new", "Add variable")
        add.clicked.connect(self.add)
        remove = IconButton("trash", "Remove the selected variable's initial value")
        remove.clicked.connect(self.remove)
        self.search = QLineEdit(placeholderText="Filter variables", clearButtonEnabled=True)
        self.search.textChanged.connect(lambda _: self.refresh())
        top = QHBoxLayout()
        top.addWidget(self.search, 1)
        top.addWidget(add)
        top.addWidget(remove)
        layout = QVBoxLayout(self)
        layout.setContentsMargins(6, 4, 6, 6)
        layout.addLayout(top)
        layout.addWidget(self.table, 1)
        scene.changed_workflow.connect(self.refresh)
        bus.subscribe("engine.vars", self._set_live)
        bus.subscribe("engine.finished", lambda p: self._set_live(p.get("variables", {})))
        self.refresh()

    def _set_live(self, values: dict) -> None:
        self._live = dict(values)
        self.refresh()

    def refresh(self) -> None:
        if self.table.state() == QAbstractItemView.State.EditingState:
            return  # don't yank the editor away mid-typing
        declared = self._scene.wf.variables
        needle = self.search.text().strip().lower()
        names = [n for n in sorted(set(declared) | set(self._live), key=str.lower) if needle in n.lower()]
        self._filling = True
        self.table.setRowCount(len(names))
        running = self._engine.state != IDLE
        for row, name in enumerate(names):
            self.table.setItem(row, 0, _item(name, color=None if name in declared else "muted"))
            self.table.setItem(row, 1, _item(fmt(declared[name]) if name in declared else "", editable=True))
            live = fmt(self._live[name]) if name in self._live else ""
            self.table.setItem(row, 2, _item(live, editable=running))
        self._filling = False

    def _edited(self, item: QTableWidgetItem) -> None:
        if self._filling:
            return
        name = self.table.item(item.row(), 0).text()
        value = coerce(item.text())
        if item.column() == 1:
            self._scene.wf.variables[name] = value
            self._scene.changed_workflow.emit()
        elif item.column() == 2:
            try:
                self._engine.set_variable(name, value)
                log.info("Debug: %s set to %s", name, fmt(value))
            except RuntimeError as e:
                QMessageBox.information(self, "Variables", str(e))
                self.refresh()

    def add(self) -> None:
        name, ok = QInputDialog.getText(self, "Add variable", "Name (letters, digits, _):")
        name = name.strip()
        if not ok or not name:
            return
        if not valid_var_name(name):
            QMessageBox.information(self, "Add variable", f"{name!r} is not a valid variable name.")
            return
        self._scene.wf.variables.setdefault(name, 0)
        self._scene.changed_workflow.emit()

    def remove(self) -> None:
        row = self.table.currentRow()
        if row < 0:
            return
        name = self.table.item(row, 0).text()
        if name in self._scene.wf.variables:
            del self._scene.wf.variables[name]
            self._scene.changed_workflow.emit()


class RunLogPanel(QWidget):
    """Execution history with timestamps, errors and detection results, plus a debug console."""
    go_to = Signal(str)  # node id

    def __init__(self, engine, bus, parent=None) -> None:
        super().__init__(parent)
        self._engine = engine
        self.entries: list[dict] = []
        self._vars: dict = {}
        self.filter = QComboBox()
        for text, key in (("Everything", "all"), ("Problems", "problems"), ("Detections", "detections")):
            self.filter.addItem(text, key)
        self.filter.currentIndexChanged.connect(lambda _: self._render())
        clear = IconButton("trash", "Clear the run log")
        clear.clicked.connect(self.clear)
        self.table = QTableWidget(0, 5)
        self.table.setHorizontalHeaderLabels(["Time", "Block", "Result", "ms", "Details"])
        self.table.verticalHeader().hide()
        header = self.table.horizontalHeader()
        for col in range(4):
            header.setSectionResizeMode(col, QHeaderView.ResizeMode.ResizeToContents)
        header.setSectionResizeMode(4, QHeaderView.ResizeMode.Stretch)
        self.table.setSelectionBehavior(QAbstractItemView.SelectionBehavior.SelectRows)
        self.table.setWordWrap(False)
        self.table.setToolTip("Double-click a row to show its block")
        self.table.cellDoubleClicked.connect(self._activate)
        self.console = QLineEdit(placeholderText="Debug console: {variable}, ={hp} * 2, or name = value")
        self.console.setToolTip("Evaluate text with {variables} or an =expression against the current values.\n"
                                "name = value changes a variable of the running (or paused) workflow.")
        self.console.returnPressed.connect(self._command)
        self.answer = QLabel("", objectName="muted")
        self.answer.setTextInteractionFlags(Qt.TextInteractionFlag.TextSelectableByMouse)

        top = QHBoxLayout()
        top.addWidget(QLabel("Show:", objectName="muted"))
        top.addWidget(self.filter)
        top.addStretch(1)
        top.addWidget(clear)
        layout = QVBoxLayout(self)
        layout.setContentsMargins(6, 4, 6, 6)
        layout.addLayout(top)
        layout.addWidget(self.table, 1)
        layout.addWidget(self.console)
        layout.addWidget(self.answer)

        bus.subscribe("engine.trace", self.add)
        bus.subscribe("engine.vars", lambda v: setattr(self, "_vars", dict(v)))
        bus.subscribe("engine.state", self._state)
        bus.subscribe("engine.finished", self._finished)
        self._was_idle = True

    # -- entries -------------------------------------------------------------------------------

    def _state(self, state: str) -> None:
        if state != IDLE and self._was_idle:
            from time import strftime
            self.add([{"time": strftime("%H:%M:%S"), "id": None, "title": "Run started", "status": "run",
                       "detail": "", "ms": None}])
        self._was_idle = state == IDLE

    def _finished(self, p: dict) -> None:
        from time import strftime
        self._vars = dict(p.get("variables", {}))
        detail = p.get("error") or ""
        if p.get("restarts"):
            detail = f"{p['restarts']} restart(s). {detail}".strip()
        self.add([{"time": strftime("%H:%M:%S"), "id": None, "title": "Run " + p["result"],
                   "status": "error" if p["result"] == "error" else "run", "detail": detail,
                   "ms": round(p.get("elapsed", 0) * 1000)}])

    def _visible(self, e: dict) -> bool:
        key = self.filter.currentData()
        if key == "problems":
            return e["status"] in PROBLEMS or e["status"] == "error"
        if key == "detections":
            return e["status"] in ("found", "not found")
        return True

    def add(self, batch: list[dict]) -> None:
        batch = batch[-MAX_ROWS:]
        self.entries.extend(batch)
        at_bottom = self.table.verticalScrollBar().value() >= self.table.verticalScrollBar().maximum() - 2
        self.table.setUpdatesEnabled(False)
        try:
            overflow = len(self.entries) - MAX_ROWS
            if overflow > 0:  # drop the oldest rows in one go instead of re-rendering the table
                gone = sum(1 for e in self.entries[:overflow] if self._visible(e))
                del self.entries[:overflow]
                self.table.model().removeRows(0, min(gone, self.table.rowCount()))
            for e in batch:
                if self._visible(e):
                    self._append(e)
        finally:
            self.table.setUpdatesEnabled(True)
        if at_bottom:
            self.table.scrollToBottom()

    def _append(self, e: dict) -> None:
        row = self.table.rowCount()
        self.table.insertRow(row)
        color = COLORS.get(e["status"])
        first = _item(e["time"])
        first.setData(Qt.ItemDataRole.UserRole, e.get("id"))
        self.table.setItem(row, 0, first)
        self.table.setItem(row, 1, _item(e["title"]))
        self.table.setItem(row, 2, _item(e["status"], color=color))
        self.table.setItem(row, 3, _item("" if e["ms"] is None else f"{e['ms']:g}"))
        detail = _item(e["detail"], color="err" if color == "err" else None)
        detail.setToolTip(e["detail"])
        self.table.setItem(row, 4, detail)

    def _render(self) -> None:
        self.table.setRowCount(0)
        for e in self.entries:
            if self._visible(e):
                self._append(e)
        self.table.scrollToBottom()

    def clear(self) -> None:
        self.entries.clear()
        self.table.setRowCount(0)

    def _activate(self, row: int, _col: int) -> None:
        node_id = self.table.item(row, 0).data(Qt.ItemDataRole.UserRole)
        if node_id:
            self.go_to.emit(node_id)

    # -- debug console -------------------------------------------------------------------------

    def _command(self) -> None:
        text = self.console.text().strip()
        if not text:
            return
        try:
            self.answer.setText(self.run_command(text))
            self.console.clear()
        except Exception as e:
            self.answer.setText(f"Error: {e}")

    def run_command(self, text: str) -> str:
        name, sep, value = text.partition("=")
        if sep and valid_var_name(name.strip()) and not value.startswith("="):
            v = value.strip()
            v = evaluate(interpolate(v[1:], self._vars)) if v.startswith("=") else coerce(interpolate(v, self._vars))
            self._engine.set_variable(name.strip(), v)
            self._vars[name.strip()] = v
            log.info("Debug: %s set to %s", name.strip(), fmt(v))
            return f"{name.strip()} = {fmt(v)}"
        if valid_var_name(text):
            if text not in self._vars:
                return f"{text} is not set"
            return f"{text} = {fmt(self._vars[text])}"
        if text.startswith("="):
            return fmt(evaluate(interpolate(text[1:], self._vars)))
        return interpolate(text, self._vars)
