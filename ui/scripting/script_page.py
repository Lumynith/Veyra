"""Script Editor page: write, check, test-run and save sandboxed Python scripts."""
from __future__ import annotations

import logging
import re
from typing import Callable

from PySide6.QtCore import Qt
from PySide6.QtGui import QKeySequence, QShortcut
from PySide6.QtWidgets import (QCheckBox, QDoubleSpinBox, QHBoxLayout, QInputDialog, QLabel, QLineEdit,
                               QListWidget, QMenu, QMessageBox, QPlainTextEdit, QPushButton, QSplitter,
                               QToolButton, QVBoxLayout, QWidget)

from automation.engine import IDLE
from automation.sandbox import API_DOC, PERMISSIONS, api_names, parse_inputs
from automation.script_host import ALLOWED_MODULES, validate
from automation.script_library import check_name
from automation.workflow import Workflow, new_node
from ui.scripting.code_editor import CodeEditor
from ui.theme import C
from ui.widgets import AdaptiveSplitter, IconButton, Panel, Spinner

log = logging.getLogger(__name__)
TEMPLATE = '''# Runs in a restricted, separate Python process.
# inputs['name'] holds input values; output('name', value) returns values to the workflow.
# Functions marked [permission] need that permission ticked. Ctrl+Space completes, Ctrl+Enter runs.

count = inputs.get('count', 3)
total = 0
for i in range(count):
    total += i
    log('step', i)
output('total', total)
'''
SECURITY_NOTE = ("Scripts run in a separate Python process with a restricted set of modules and builtins, "
                 "a memory cap, no child processes, a timeout, and explicit permissions for mouse/keyboard, "
                 "screen, variables and files. Python sandboxing is not a perfect security boundary: "
                 "only run scripts you understand or trust.")


class ScriptPage(QWidget):
    def __init__(self, engine, bus, scripts, make_block: Callable[[str, str, dict], None], parent=None) -> None:
        super().__init__(parent)
        self._engine, self._scripts, self._make_block = engine, scripts, make_block
        self.name: str | None = None
        self._saved_text = ""
        self._running = False

        # -- script list ----------------------------------------------------
        self.list = QListWidget()
        self.list.itemClicked.connect(lambda item: self.open(item.text()))
        self.list.setContextMenuPolicy(Qt.ContextMenuPolicy.CustomContextMenu)
        self.list.customContextMenuRequested.connect(self._list_menu)
        new = IconButton("new", "New script")
        new.clicked.connect(self.new)
        scripts_panel = Panel("Scripts", self.list, [new])
        scripts_panel.setMinimumWidth(130)

        # -- editor ---------------------------------------------------------------
        self.editor = CodeEditor(api_names(), validate)
        self.editor.textChanged.connect(self._update_title)
        self.editor.problems_changed.connect(self._show_problems)
        self.problems = QLabel(objectName="muted", wordWrap=True)
        save = IconButton("save", "Save script")
        save.clicked.connect(self.save)
        save_as = IconButton("import", "Save as…")
        save_as.clicked.connect(self.save_as)
        insert = QToolButton(text="Insert ▾", toolTip="Insert an automation function")
        insert.setPopupMode(QToolButton.ToolButtonPopupMode.InstantPopup)
        menu = QMenu(insert)
        for name, (call, text) in API_DOC.items():
            menu.addAction(f"{call}    — {text}", lambda c=call: self.editor.insertPlainText(c))
        menu.addSeparator()
        menu.addAction(f"Allowed modules: {', '.join(ALLOWED_MODULES)}").setEnabled(False)
        insert.setMenu(menu)
        block = QPushButton("Make block…")
        block.setToolTip("Turn this script into a reusable block in Custom Blocks")
        block.clicked.connect(self._to_block)
        self.title = QLabel(objectName="muted")
        editor_box = QWidget()
        ev = QVBoxLayout(editor_box)
        ev.setContentsMargins(6, 2, 6, 6)
        ev.addWidget(self.editor, 1)
        ev.addWidget(self.problems)
        self.editor_panel = Panel("Script", editor_box, [self.title, insert, block, save, save_as])

        # -- test run + output ------------------------------------------------
        self.inputs = QLineEdit(placeholderText="Test inputs: count=5; name=Bob")
        self.timeout = QDoubleSpinBox(minimum=0, maximum=3600, value=30, suffix=" s", decimals=0)
        self.timeout.setToolTip("Kill the script after this long (0 = no limit)")
        self.perms = {name: QCheckBox(name) for name in PERMISSIONS}
        for name, box in self.perms.items():
            box.setToolTip(PERMISSIONS[name])
        self.run_btn = QPushButton("Run", objectName="primary")
        self.run_btn.setToolTip("Run the script now (Ctrl+Enter)")
        self.run_btn.clicked.connect(self.run)
        self.stop_btn = QPushButton("Stop")
        self.stop_btn.clicked.connect(engine.stop)
        self.spinner = Spinner()
        self.output = QPlainTextEdit(readOnly=True, placeholderText="Script output appears here")
        self.output.setObjectName("codeOutput")
        clear = IconButton("trash", "Clear output")
        clear.clicked.connect(self.output.clear)
        row1 = QHBoxLayout()
        row1.addWidget(self.inputs, 1)
        row1.addWidget(QLabel("Timeout"))
        row1.addWidget(self.timeout)
        row1.addWidget(self.spinner)
        row1.addWidget(self.stop_btn)
        row1.addWidget(self.run_btn)
        row2 = QHBoxLayout()
        row2.addWidget(QLabel("Allow:"))
        for box in self.perms.values():
            row2.addWidget(box)
        row2.addStretch(1)
        run_box = QWidget()
        rv = QVBoxLayout(run_box)
        rv.setContentsMargins(6, 2, 6, 6)
        rv.addLayout(row1)
        rv.addLayout(row2)
        rv.addWidget(self.output, 1)
        note = QLabel("ⓘ", toolTip=SECURITY_NOTE)
        run_panel = Panel("Test run & output", run_box, [note, clear])

        right = QSplitter(Qt.Orientation.Vertical, objectName="scriptRight", childrenCollapsible=False)
        right.addWidget(self.editor_panel)
        right.addWidget(run_panel)
        right.setSizes([480, 260])
        main = AdaptiveSplitter(objectName="scriptMain", childrenCollapsible=False)
        main.addWidget(scripts_panel)
        main.addWidget(right)
        main.setSizes([140, 460])
        main.setStretchFactor(1, 1)
        layout = QVBoxLayout(self)
        layout.setContentsMargins(0, 0, 0, 0)
        layout.addWidget(main)

        QShortcut(QKeySequence("Ctrl+Return"), self, activated=self.run,
                  context=Qt.ShortcutContext.WidgetWithChildrenShortcut)
        bus.subscribe("engine.state", self._on_state)
        bus.subscribe("engine.finished", self._on_finished)
        bus.subscribe("script.output", self._on_output)
        bus.subscribe("script.result", self._on_result)
        self._on_state(engine.state)
        self.refresh_list()
        names = scripts.names()
        if names:
            self.open(names[0])
        else:
            self.new()

    # -- files -----------------------------------------------------------------

    def refresh_list(self) -> None:
        self.list.clear()
        self.list.addItems(self._scripts.names())
        for i in range(self.list.count()):
            if self.list.item(i).text() == self.name:
                self.list.setCurrentRow(i)

    @property
    def dirty(self) -> bool:
        return self.editor.toPlainText() != self._saved_text

    def _update_title(self) -> None:
        self.title.setText(f"{self.name or 'Untitled'}{' •' if self.dirty else ''}")

    def _confirm_discard(self) -> bool:
        if not self.dirty:
            return True
        answer = QMessageBox.question(self, "Unsaved script", f"Save changes to {self.name or 'Untitled'}?",
                                      QMessageBox.StandardButton.Save | QMessageBox.StandardButton.Discard
                                      | QMessageBox.StandardButton.Cancel)
        if answer == QMessageBox.StandardButton.Save:
            return self.save()
        return answer == QMessageBox.StandardButton.Discard

    def _load(self, name: str | None, text: str) -> None:
        self.name, self._saved_text = name, text
        self.editor.setPlainText(text)
        self.editor.validate_now()
        self._update_title()
        self.refresh_list()

    def new(self) -> None:
        if self._confirm_discard():
            self._load(None, TEMPLATE)  # an untouched template is not worth a "save changes?" prompt

    def open(self, name: str) -> None:
        if name == self.name or not self._confirm_discard():
            self.refresh_list()
            return
        try:
            self._load(name, self._scripts.read(name))
        except (OSError, ValueError) as e:
            QMessageBox.warning(self, "Cannot open script", str(e))

    def save(self) -> bool:
        if self.name is None:
            return self.save_as()
        try:
            self._scripts.write(self.name, self.editor.toPlainText())
        except (OSError, ValueError) as e:
            QMessageBox.warning(self, "Cannot save script", str(e))
            return False
        self._saved_text = self.editor.toPlainText()
        self._update_title()
        log.info("Saved script %s", self.name)
        return True

    def save_as(self) -> bool:
        name, ok = QInputDialog.getText(self, "Save script", "Name:", text=self.name or "my_script")
        if not ok:
            return False
        try:
            name = check_name(name)
        except ValueError as e:
            QMessageBox.warning(self, "Invalid name", str(e))
            return False
        if name in self._scripts.names() and name != self.name and QMessageBox.question(
                self, "Replace script", f"A script named {name} exists. Replace it?") != QMessageBox.StandardButton.Yes:
            return False
        self.name = name
        ok = self.save()
        self.refresh_list()
        return ok

    def _list_menu(self, pos) -> None:
        item = self.list.itemAt(pos)
        if not item:
            return
        name = item.text()
        menu = QMenu(self)
        menu.addAction("Open", lambda: self.open(name))
        menu.addAction("Delete", lambda: self._delete(name))
        menu.exec(self.list.viewport().mapToGlobal(pos))

    def _delete(self, name: str) -> None:
        if QMessageBox.question(self, "Delete script", f"Delete {name}? Run Python Script blocks that use it "
                                                       "will fail.") != QMessageBox.StandardButton.Yes:
            return
        self._scripts.delete(name)
        if name == self.name:
            self.name, self._saved_text = None, ""  # keep the text in the editor, as an unsaved script
            self._update_title()
        self.refresh_list()

    # -- checking ------------------------------------------------------------------

    def _show_problems(self, problems: list) -> None:
        if not problems:
            self.problems.setText(f"<span style='color:{C['ok']}'>✓ No problems</span>")
        else:
            line, _, msg = problems[0]
            more = f" (+{len(problems) - 1} more)" if len(problems) > 1 else ""
            self.problems.setText(f"<span style='color:{C['err']}'>Line {line}: {msg}{more}</span>")

    # -- running ---------------------------------------------------------------------

    def run(self) -> None:
        if self._engine.state != IDLE:
            QMessageBox.information(self, "Busy", "A workflow is running. Stop it before testing a script.")
            return
        try:
            inputs = parse_inputs(self.inputs.text())
        except ValueError as e:
            QMessageBox.warning(self, "Test inputs", str(e))
            return
        params = {"source": self.editor.toPlainText(), "timeout": self.timeout.value(),
                  "inputs": "; ".join(f"{k}={v}" for k, v in inputs.items()),
                  **{f"allow_{name}": box.isChecked() for name, box in self.perms.items()}}
        wf = Workflow()
        wf.add_script([new_node("flow.start"), new_node("script.run", params)], (0, 0))
        self.output.appendPlainText(f"── Run {self.name or 'Untitled'} ──\n")
        self._running = True
        try:
            self._engine.run(wf.to_dict())
        except Exception as e:
            self._running = False
            self.output.appendPlainText(f"Could not start: {e}")

    def _on_state(self, state: str) -> None:
        self.run_btn.setEnabled(state == IDLE)
        self.stop_btn.setEnabled(state != IDLE and self._running)
        (self.spinner.start if state != IDLE and self._running else self.spinner.stop)()

    def _on_output(self, text: str) -> None:
        if self._running:
            cursor = self.output.textCursor()
            cursor.movePosition(cursor.MoveOperation.End)
            cursor.insertText(text)
            self.output.ensureCursorVisible()

    def _on_result(self, outputs: dict) -> None:
        if self._running and outputs:
            self.output.appendPlainText("Outputs: " + ", ".join(f"{k} = {v!r}" for k, v in outputs.items()))

    def _on_finished(self, payload: dict) -> None:
        if not self._running:
            return
        self._running = False
        self._on_state(IDLE)
        if payload["result"] == "error":
            error = re.sub(r"^Run Python Script: ", "", payload["error"] or "")
            self.output.appendPlainText(f"✗ {error}")
            m = re.match(r"Line (\d+):", error)
            self.editor.set_runtime_error(int(m.group(1)) if m else None)
        else:
            self.output.appendPlainText(f"✓ {payload['result']} in {payload['elapsed']:.2f} s")

    def _to_block(self) -> None:
        try:
            inputs = parse_inputs(self.inputs.text())
        except ValueError:
            inputs = {}
        permissions = {name: box.isChecked() for name, box in self.perms.items()}
        self._make_block(self.name or "Script block", self.editor.toPlainText(),
                         {"inputs": inputs, "permissions": permissions, "timeout": self.timeout.value()})
