"""Custom Blocks page: build reusable blocks from existing blocks (no code) or from a Python script."""
from __future__ import annotations

import ast
import copy
import json
import logging
import os
from pathlib import Path
from typing import Callable

from PySide6.QtCore import Qt
from PySide6.QtWidgets import (QAbstractItemView, QCheckBox, QComboBox, QDoubleSpinBox, QFileDialog, QFormLayout,
                               QHBoxLayout, QHeaderView, QLabel, QLineEdit, QListWidget, QListWidgetItem, QMenu,
                               QMessageBox, QPlainTextEdit, QPushButton, QSplitter, QStackedWidget, QTableWidget,
                               QTableWidgetItem, QTabWidget, QToolButton, QVBoxLayout, QWidget)

from automation.sandbox import API_DOC, PERMISSIONS, api_names
from automation.script_host import validate as check_script
from automation.workflow import Workflow, WorkflowError
from blocks import registry
from blocks.base_block import CATEGORIES
from blocks.custom import FORMAT, INPUT_TYPES, blank, slug, validate
from ui.editor.canvas import WorkflowScene, WorkflowView
from ui.editor.library import BlockLibrary
from ui.editor.properties import PropertiesPanel
from ui.scripting.code_editor import CodeEditor
from ui.scripting.script_page import SECURITY_NOTE
from ui.theme import C, icon, icon_names
from ui.widgets import AdaptiveSplitter, EmptyState, IconButton, Panel

log = logging.getLogger(__name__)
ID_ROLE = Qt.ItemDataRole.UserRole
INPUT_COLUMNS = ("Name", "Label", "Type", "Default", "Options (a, b, c)", "Min", "Max")
FILE_FILTER = "Veyra block (*.lcb.json *.json)"


class _Table(QTableWidget):
    def __init__(self, columns) -> None:
        super().__init__(0, len(columns))
        self.setHorizontalHeaderLabels(columns)
        self.horizontalHeader().setSectionResizeMode(QHeaderView.ResizeMode.Stretch)
        self.verticalHeader().hide()
        self.setSelectionBehavior(QAbstractItemView.SelectionBehavior.SelectRows)
        self.setSelectionMode(QAbstractItemView.SelectionMode.SingleSelection)

    def text(self, row: int, col: int) -> str:
        item = self.item(row, col)
        return item.text().strip() if item else ""

    def move_row(self, delta: int) -> None:
        row = self.currentRow()
        target = row + delta
        if row < 0 or not 0 <= target < self.rowCount():
            return
        rows = [self._row(r) for r in range(self.rowCount())]
        rows[row], rows[target] = rows[target], rows[row]
        self.setRowCount(0)
        for values in rows:
            self.add(values)
        self.selectRow(target)

    def _row(self, r: int) -> list[str]:
        return [self.cellWidget(r, c).currentText() if self.cellWidget(r, c) else self.text(r, c)
                for c in range(self.columnCount())]

    def add(self, values: list[str]) -> None:
        r = self.rowCount()
        self.insertRow(r)
        for c, value in enumerate(values):
            self.setItem(r, c, QTableWidgetItem(str(value)))


class InputsTable(_Table):
    def __init__(self) -> None:
        super().__init__(INPUT_COLUMNS)
        for col in (2, 5, 6):
            self.horizontalHeader().setSectionResizeMode(col, QHeaderView.ResizeMode.ResizeToContents)

    def add(self, values: list[str]) -> None:
        super().add(values)
        r = self.rowCount() - 1
        kind = QComboBox()
        kind.addItems(list(INPUT_TYPES))
        kind.setCurrentText(values[2] if values[2] in INPUT_TYPES else "text")
        self.setCellWidget(r, 2, kind)

    def load(self, inputs: list[dict]) -> None:
        self.setRowCount(0)
        for i in inputs:
            self.add([i["name"], i.get("label", ""), i["type"], _show(i.get("default", "")),
                      ", ".join(i.get("options", [])), _show(i.get("min", "")), _show(i.get("max", ""))])

    def collect(self) -> list[dict]:
        out = []
        for r in range(self.rowCount()):
            name, label, kind, default, options, lo, hi = self._row(r)
            entry = {"name": name, "label": label or name, "type": kind, "default": default}
            if kind == "dropdown":
                entry["options"] = [o.strip() for o in options.split(",") if o.strip()]
            for key, value in (("min", lo), ("max", hi)):
                if value:
                    try:
                        entry[key] = float(value)
                    except ValueError:
                        entry[key] = value  # reported by validation below
            out.append(entry)
        return out


def output_names(source: str) -> list[str]:
    """Names passed to output('name', …) in the code (not in comments or strings)."""
    try:
        tree = ast.parse(source)
    except SyntaxError:
        return []
    names = [n.args[0].value for n in ast.walk(tree)
             if isinstance(n, ast.Call) and isinstance(n.func, ast.Name) and n.func.id == "output"
             and n.args and isinstance(n.args[0], ast.Constant) and isinstance(n.args[0].value, str)]
    return list(dict.fromkeys(n for n in names if n.isidentifier()))


def _show(value) -> str:
    if isinstance(value, float) and value.is_integer():
        return str(int(value))
    return "" if value is None else str(value)


class CustomBlocksPage(QWidget):
    def __init__(self, store, bus, resources, in_use: Callable[[str], bool], plugins_dir: Path,
                 parent=None) -> None:
        super().__init__(parent)
        self._store, self._bus, self._in_use, self._plugins_dir = store, bus, in_use, plugins_dir
        self.current: dict | None = None
        self._original = ""     # JSON of the block as last saved/loaded, for unsaved-change checks
        self._saved_id: str | None = None  # id on disk; None for a block never saved

        # -- block list -------------------------------------------------------------------------
        self.list = QListWidget()
        self.list.itemClicked.connect(lambda item: self.open(item.data(ID_ROLE)))
        self.list.setContextMenuPolicy(Qt.ContextMenuPolicy.CustomContextMenu)
        self.list.customContextMenuRequested.connect(self._list_menu)
        new = IconButton("new", "New custom block")
        new_menu = QMenu(new)
        new_menu.addAction("Built from blocks (no code)", lambda: self.create("steps"))
        new_menu.addAction("Python script", lambda: self.create("script"))
        new.setMenu(new_menu)
        new.setPopupMode(QToolButton.ToolButtonPopupMode.InstantPopup)
        imp = IconButton("import", "Import a block file…")
        imp.clicked.connect(self._import)
        self.plugins = QLabel(objectName="muted", wordWrap=True)
        self.plugins.setTextInteractionFlags(Qt.TextInteractionFlag.TextSelectableByMouse)
        open_plugins = QPushButton("Open plugins folder")
        open_plugins.setToolTip("Developer blocks written in Python (BaseBlock). They load at startup and are "
                                "NOT sandboxed: only add plugins you trust.")
        open_plugins.clicked.connect(self._open_plugins)
        left = QWidget()
        lv = QVBoxLayout(left)
        lv.setContentsMargins(6, 2, 6, 6)
        lv.addWidget(self.list, 1)
        lv.addWidget(QLabel("DEVELOPER PLUGINS", objectName="panelTitle"))
        lv.addWidget(self.plugins)
        lv.addWidget(open_plugins)
        list_panel = Panel("My blocks", left, [new, imp])
        list_panel.setMinimumWidth(150)

        # -- editor: general ------------------------------------------------------------------------
        self.name = QLineEdit(placeholderText="e.g. Collect Reward")
        self.name.textEdited.connect(self._name_edited)
        self.block_id = QLineEdit()
        self.block_id.setToolTip("Workflows refer to the block by this id. It cannot change after the first save.")
        self.category = QComboBox(editable=True)
        self.category.addItems(sorted(set(CATEGORIES) - {"Flow Control"}) + ["Flow Control"])
        self.icon = QComboBox()
        for name in icon_names():
            self.icon.addItem(icon(name, C["text"]), name)
        self.description = QPlainTextEdit(placeholderText="What does the block do? Shown in the library tooltip.")
        self.description.setMaximumHeight(70)
        self.kind_label = QLabel(objectName="muted", wordWrap=True)
        general = QWidget()
        form = QFormLayout(general)
        form.addRow("Name", self.name)
        form.addRow("Block id", self.block_id)
        form.addRow("Category", self.category)
        form.addRow("Icon", self.icon)
        form.addRow("Description", self.description)
        form.addRow(self.kind_label)

        # -- editor: inputs & outputs -------------------------------------------------------------
        self.inputs = InputsTable()
        self.outputs = _Table(("Name", "Description"))
        io = QWidget()
        iv = QVBoxLayout(io)
        iv.addWidget(QLabel("Inputs become fields in the block's properties, and {name} variables inside it.",
                            objectName="muted", wordWrap=True))
        iv.addLayout(self._table_buttons(self.inputs, lambda: self.inputs.add(
            [f"input{self.inputs.rowCount() + 1}", "", "text", "", "", "", ""])))
        iv.addWidget(self.inputs, 2)
        iv.addWidget(QLabel("Outputs are handed back to the workflow as variables.", objectName="muted",
                            wordWrap=True))
        iv.addLayout(self._table_buttons(self.outputs, lambda: self.outputs.add(
            [f"result{self.outputs.rowCount() + 1}", ""])))
        iv.addWidget(self.outputs, 1)

        # -- editor: behaviour (steps canvas or script) ------------------------------------------------
        self.scene = WorkflowScene(self)
        self.view = WorkflowView(self.scene)
        self.library = BlockLibrary()
        self.library.add_requested.connect(lambda t: self.scene.append_block(t, self.view.center()))
        steps = AdaptiveSplitter(objectName="customSteps", childrenCollapsible=False)
        steps.addWidget(self.library)
        steps_right = QSplitter(Qt.Orientation.Vertical, childrenCollapsible=False)
        steps_right.addWidget(self.view)
        steps_right.addWidget(PropertiesPanel(self.scene, resources))
        steps_right.setSizes([300, 180])
        steps.addWidget(steps_right)
        self.library.setMinimumWidth(170)
        steps.setSizes([180, 340])

        self.code = CodeEditor(api_names(), check_script)
        self.code.problems_changed.connect(lambda p: self.code_problems.setText(
            f"<span style='color:{C['err']}'>Line {p[0][0]}: {p[0][2]}</span>" if p else ""))
        self.code_problems = QLabel(wordWrap=True)
        self.perms = {name: QCheckBox(name, toolTip=text) for name, text in PERMISSIONS.items()}
        self.timeout = QDoubleSpinBox(minimum=0, maximum=3600, value=30, suffix=" s", decimals=0,
                                      toolTip="Kill the script after this long (0 = no limit)")
        perm_row = QHBoxLayout()
        perm_row.addWidget(QLabel("Allow:"))
        for box in self.perms.values():
            perm_row.addWidget(box)
        perm_row.addStretch(1)
        perm_row.addWidget(QLabel("Timeout"))
        perm_row.addWidget(self.timeout)
        perm_row.addWidget(QLabel("ⓘ", toolTip=SECURITY_NOTE))
        script = QWidget()
        sv = QVBoxLayout(script)
        sv.setContentsMargins(0, 0, 0, 0)
        sv.addWidget(QLabel("inputs['name'] reads an input · output('name', value) returns one · functions: "
                            + ", ".join(n for n in API_DOC if n not in ("inputs", "output")),
                            objectName="muted", wordWrap=True))
        sv.addWidget(self.code, 1)
        sv.addWidget(self.code_problems)
        sv.addLayout(perm_row)
        self.body = QStackedWidget()
        self.body.addWidget(steps)
        self.body.addWidget(script)

        self.tabs = QTabWidget()
        self.tabs.addTab(general, "General")
        self.tabs.addTab(io, "Inputs && outputs")
        self.tabs.addTab(self.body, "Behaviour")

        self.problems = QLabel(wordWrap=True)
        save = QPushButton("Save block", objectName="primary")
        save.clicked.connect(self.save)
        revert = QPushButton("Revert")
        revert.clicked.connect(self._revert)
        export = IconButton("save", "Export to a file…")
        export.clicked.connect(self._export)
        dup = IconButton("new", "Duplicate")
        dup.clicked.connect(self._duplicate)
        delete = IconButton("trash", "Delete block")
        delete.clicked.connect(self._delete)
        bottom = QHBoxLayout()
        bottom.addWidget(self.problems, 1)
        bottom.addWidget(revert)
        bottom.addWidget(save)
        editor = QWidget()
        ev = QVBoxLayout(editor)
        ev.setContentsMargins(6, 2, 6, 6)
        ev.addWidget(self.tabs, 1)
        ev.addLayout(bottom)
        self.editor_stack = QStackedWidget()
        self.editor_stack.addWidget(EmptyState("blocks", "No block selected",
                                               "Create a block from existing blocks (no code needed) or from a "
                                               "Python script. Saved blocks appear in the editor's block library."))
        self.editor_stack.addWidget(editor)
        self.title = QLabel(objectName="muted")
        editor_panel = Panel("Block", self.editor_stack, [self.title, dup, export, delete])

        split = AdaptiveSplitter(objectName="customMain", childrenCollapsible=False)
        split.addWidget(list_panel)
        split.addWidget(editor_panel)
        split.setSizes([160, 440])
        split.setStretchFactor(1, 1)
        layout = QVBoxLayout(self)
        layout.setContentsMargins(0, 0, 0, 0)
        layout.addWidget(split)
        bus.subscribe("blocks.changed", lambda _: self.library.populate())
        self.refresh()

    @staticmethod
    def _table_buttons(table: _Table, add) -> QHBoxLayout:
        row = QHBoxLayout()
        buttons = (("Add", add), ("Remove", lambda: table.removeRow(table.currentRow())),
                   ("Up", lambda: table.move_row(-1)), ("Down", lambda: table.move_row(1)))
        for text, slot in buttons:
            b = QPushButton(text)
            b.clicked.connect(slot)
            row.addWidget(b)
        row.addStretch(1)
        return row

    # -- list -------------------------------------------------------------------------------------

    def refresh(self) -> None:
        self.list.clear()
        for d in sorted(self._store.definitions(), key=lambda d: d.get("title", "").lower()):
            item = QListWidgetItem(icon(d.get("icon") or "blocks", C["text"]), d.get("title", d["id"]))
            item.setToolTip(f"{d['id']} · {'Python script' if d.get('body') == 'script' else 'built from blocks'}")
            item.setData(ID_ROLE, d["id"])
            self.list.addItem(item)
            if d["id"] == self._saved_id:
                self.list.setCurrentItem(item)
        errors = {**self._store.errors}
        lines = [f"✓ {name}: {status[8:]}" if status.startswith("loaded") else f"✗ {name}: {status}"
                 for name, status in sorted(registry.plugin_status.items())]
        lines += [f"✗ {name}: {err}" for name, err in errors.items()]
        self.plugins.setText("\n".join(lines) or "None yet. Put BaseBlock modules (.py) in the plugins folder.")
        self.plugins.setToolTip(str(self._plugins_dir))

    def _list_menu(self, pos) -> None:
        item = self.list.itemAt(pos)
        if not item:
            return
        self.open(item.data(ID_ROLE))
        menu = QMenu(self)
        menu.addAction("Duplicate", self._duplicate)
        menu.addAction("Export…", self._export)
        menu.addSeparator()
        menu.addAction("Delete", self._delete)
        menu.exec(self.list.viewport().mapToGlobal(pos))

    # -- load / collect -------------------------------------------------------------------------

    def _confirm_discard(self) -> bool:
        if self.current is None or json.dumps(self.collect(), sort_keys=True) == self._original:
            return True
        answer = QMessageBox.question(self, "Unsaved block", f"Save changes to {self.name.text() or 'this block'}?",
                                      QMessageBox.StandardButton.Save | QMessageBox.StandardButton.Discard
                                      | QMessageBox.StandardButton.Cancel)
        if answer == QMessageBox.StandardButton.Save:
            return self.save()
        return answer == QMessageBox.StandardButton.Discard

    def open(self, block_id: str) -> None:
        if block_id == self._saved_id and self.current is not None:
            return
        if not self._confirm_discard():
            self.refresh()
            return
        d = next((d for d in self._store.definitions() if d["id"] == block_id), None)
        if d:
            self._load(d, block_id)

    def _load(self, d: dict, saved_id: str | None) -> None:
        self.current = copy.deepcopy(d)
        self._saved_id = saved_id
        self.name.setText(d["title"])
        self.block_id.setText(d["id"])
        self.block_id.setReadOnly(saved_id is not None)
        self.category.setCurrentText(d.get("category", "Custom"))
        self.icon.setCurrentIndex(max(0, self.icon.findData(d.get("icon") or "blocks")))
        self.description.setPlainText(d.get("description", ""))
        self.inputs.load(d.get("inputs", []))
        self.outputs.setRowCount(0)
        for o in d.get("outputs", []):
            self.outputs.add([o["name"], o.get("description", "")])
        script = d.get("body") == "script"
        self.kind_label.setText("Python script block: runs sandboxed in a separate process." if script else
                                "Built from blocks: edit its steps on the Behaviour tab.")
        self.body.setCurrentIndex(1 if script else 0)
        if script:
            self.code.setPlainText(d.get("script", ""))
            self.code.validate_now()
            for name, box in self.perms.items():
                box.setChecked(bool(d.get("permissions", {}).get(name)))
            self.timeout.setValue(float(d.get("timeout", 30)))
        else:
            try:
                self.scene.set_workflow(Workflow.from_dict(d["workflow"]))
            except WorkflowError as e:
                QMessageBox.warning(self, "Block steps", f"The steps could not be loaded: {e}")
                self.scene.set_workflow(Workflow.from_dict(blank()["workflow"]))
            self.view.show_top()
        self.problems.setText("")
        self.editor_stack.setCurrentIndex(1)
        self._original = json.dumps(self.collect(), sort_keys=True)
        self.title.setText(d["id"])
        self.refresh()

    def collect(self) -> dict:
        d = copy.deepcopy(self.current)
        d.update(title=self.name.text().strip(), id=self.block_id.text().strip(),
                 category=self.category.currentText().strip() or "Custom", icon=self.icon.currentData(),
                 description=self.description.toPlainText().strip(), inputs=self.inputs.collect(),
                 outputs=[{"name": self.outputs.text(r, 0), "description": self.outputs.text(r, 1)}
                          for r in range(self.outputs.rowCount())])
        if d["body"] == "script":
            d.update(script=self.code.toPlainText(), timeout=self.timeout.value(),
                     permissions={name: box.isChecked() for name, box in self.perms.items()})
        else:
            d["workflow"] = self.scene.wf.to_dict()
        return d

    def _unique_id(self, title: str) -> str:
        base = f"custom.{slug(title)}"[:47]
        taken = {b.type_id for b in registry.all_blocks()} | {d["id"] for d in self._store.definitions()}
        block_id, n = base, 2
        while block_id in taken:
            block_id, n = f"{base}_{n}", n + 1
        return block_id

    def _name_edited(self, text: str) -> None:
        if self._saved_id is None:  # the id follows the name until the first save
            self.block_id.setText(self._unique_id(text or "block"))

    # -- actions -----------------------------------------------------------------------------------

    def create(self, kind: str, title: str = "") -> dict | None:
        if not self._confirm_discard():
            return None
        d = blank(kind, title or ("New script block" if kind == "script" else "New block"))
        d["id"] = self._unique_id(d["title"])
        self._load(d, None)
        self._original = ""  # unsaved
        self.tabs.setCurrentIndex(0)
        self.name.setFocus()
        self.name.selectAll()
        return d

    def new_script_block(self, title: str, source: str, extra: dict) -> None:
        """Called from the Script Editor's 'Make block…'."""
        d = self.create("script", title.replace("_", " ").title())
        if d is None:
            return
        types = {bool: "true/false", int: "whole number", float: "number"}
        d.update(script=source, permissions=extra.get("permissions", {}), timeout=extra.get("timeout", 30),
                 inputs=[{"name": k, "label": k.replace("_", " ").capitalize(), "type": types.get(type(v), "text"),
                          "default": v} for k, v in extra.get("inputs", {}).items()],
                 outputs=[{"name": n, "description": ""} for n in output_names(source)])
        self._load(d, None)
        self._original = ""
        self.problems.setText("Check the inputs and outputs, then Save block.")

    def save(self) -> bool:
        if self.current is None:
            return False
        d = self.collect()
        problems = validate(d)
        if d["id"] != self._saved_id and d["id"] in {b.type_id for b in registry.all_blocks()}:
            problems.insert(0, f"The id {d['id']} is already used by another block")
        if problems:
            self.problems.setText(f"<span style='color:{C['err']}'>" + "<br>".join(problems[:4]) + "</span>")
            return False
        try:
            self._store.save(d)
        except (OSError, ValueError) as e:
            self.problems.setText(f"<span style='color:{C['err']}'>{e}</span>")
            return False
        self._saved_id = d["id"]
        self.current = d
        self._original = json.dumps(d, sort_keys=True)
        self.block_id.setReadOnly(True)
        self.title.setText(d["id"])
        self.problems.setText(f"<span style='color:{C['ok']}'>Saved. It is in the block library under "
                              f"{d['category']}.</span>")
        self._bus.publish("blocks.changed", d["id"])
        self.refresh()
        return True

    def _delete(self) -> None:
        if self._saved_id is None:
            self.current = None
            self.editor_stack.setCurrentIndex(0)
            return
        users = self._store.users(self._saved_id)
        if self._in_use(self._saved_id) or users:
            where = ["the workflow open in the editor"] if self._in_use(self._saved_id) else []
            QMessageBox.information(self, "Block in use", f"{self.name.text()} is used by "
                                    f"{', '.join(where + users)}. Remove it there first.")
            return
        if QMessageBox.question(self, "Delete block", f"Delete {self.name.text()}? Saved workflows that use it "
                                "will no longer open.") != QMessageBox.StandardButton.Yes:
            return
        self._store.delete(self._saved_id)
        self._bus.publish("blocks.changed", self._saved_id)
        self.current, self._saved_id = None, None
        self.editor_stack.setCurrentIndex(0)
        self.refresh()

    def _revert(self) -> None:
        if self._saved_id is None:
            self.problems.setText("This block has not been saved yet; there is nothing to revert to.")
            return
        d = next((d for d in self._store.definitions() if d["id"] == self._saved_id), None)
        if d:
            self._load(d, self._saved_id)

    def _duplicate(self) -> None:
        if self.current is None:
            return
        d = self.collect()
        if not self._confirm_discard():
            return
        d["title"] += " copy"
        d["id"] = self._unique_id(d["title"])
        self._load(d, None)
        self._original = ""

    def _export(self) -> None:
        if self.current is None:
            return
        d = self.collect()
        name, _ = QFileDialog.getSaveFileName(self, "Export block", f"{slug(d['title'])}.lcb.json", FILE_FILTER)
        if name:
            try:
                Path(name).write_text(json.dumps(d, indent=2, ensure_ascii=False), "utf-8")
            except OSError as e:
                QMessageBox.warning(self, "Export failed", str(e))

    def _import(self) -> None:
        name, _ = QFileDialog.getOpenFileName(self, "Import block", "", FILE_FILTER)
        if not name or not self._confirm_discard():
            return
        try:
            d = json.loads(Path(name).read_text("utf-8"))
            if d.get("format") != FORMAT:
                raise ValueError("Not a Veyra block file")
        except (OSError, ValueError) as e:
            QMessageBox.warning(self, "Import failed", str(e))
            return
        if d.get("body") == "script":
            granted = [p for p, on in d.get("permissions", {}).items() if on]
            if granted and QMessageBox.question(
                    self, "Imported script permissions",
                    f"This block's script asks for: {', '.join(granted)}.\n\nRead the script on the Behaviour tab "
                    "before using it. Keep these permissions?") != QMessageBox.StandardButton.Yes:
                d["permissions"] = {}
        self._load(d, None)
        if d.get("id") in {b.type_id for b in registry.all_blocks()}:
            self.block_id.setText(self._unique_id(d.get("title", "block")))
            self.problems.setText("A block with that id already exists, so this copy got a new id.")
        self._original = ""

    def _open_plugins(self) -> None:
        self._plugins_dir.mkdir(parents=True, exist_ok=True)
        os.startfile(self._plugins_dir)  # noqa: S606 - opens the folder in Explorer
