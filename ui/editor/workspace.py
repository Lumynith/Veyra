"""Automation workspace: workflow controls, project files, block library, canvas and properties."""
from __future__ import annotations

import logging
from pathlib import Path

from PySide6.QtCore import Qt, QTimer, Signal
from PySide6.QtGui import QAction, QKeySequence
from PySide6.QtWidgets import (QFileDialog, QInputDialog, QLineEdit, QMenu, QMessageBox, QSplitter,
                               QTabWidget, QToolButton, QVBoxLayout, QWidget)

from automation import project
from core.startup import ROOT
from automation.engine import IDLE, PAUSED, RUNNING, STOPPING
from automation.workflow import START, Workflow, WorkflowError, new_node
from ui.editor.canvas import WorkflowScene, WorkflowView
from ui.editor.debug_panel import RunLogPanel, VariablesPanel
from ui.editor.history import UndoHistory
from ui.editor.library import BlockLibrary
from ui.editor.properties import PropertiesPanel
from ui.widgets import IconButton, Panel

log = logging.getLogger(__name__)
PROJECT_FILTER = "Veyra project (*.lcp.json);;Veyra workflow (*.lcw.json);;All JSON (*.json)"
WORKFLOW_FILTER = "Veyra workflow (*.lcw.json);;Veyra project (*.lcp.json);;All JSON (*.json)"
MAX_RECENT = 10
EXAMPLES = ROOT / "examples"


class EditorPage(QWidget):
    file_changed = Signal(str)  # title: project name, " *" when there are unsaved changes

    def __init__(self, engine, bus, autosave: Path, resources=None, settings=None, templates: Path | None = None,
                 parent=None) -> None:
        super().__init__(parent)
        self._templates = templates
        self._engine = engine
        self._autosave = autosave
        self._settings = settings
        self.path: Path | None = None
        self.meta = project.new_meta()
        self._clean: str | None = None  # Workflow.dumps() at the last load/save; "" = never saved
        self._autosaved: tuple | None = None
        self.scene = WorkflowScene(self)
        self.view = WorkflowView(self.scene)
        self.history = UndoHistory(self.scene, self)
        self.library = BlockLibrary(settings)
        self.library.add_requested.connect(lambda t: self.scene.append_block(t, self.view.center()))
        self.properties = PropertiesPanel(self.scene, resources)

        save_btn = IconButton("save", "Save project", "Ctrl+S")
        save_btn.clicked.connect(self.save)
        menu_btn = IconButton("open", "Project: new, open, recent, Save As, duplicate, import / export, templates")
        menu_btn.setPopupMode(QToolButton.ToolButtonPopupMode.InstantPopup)
        menu_btn.setMenu(self._project_menu())
        self.undo_btn = IconButton("undo", "Undo", "Ctrl+Z")
        self.undo_btn.clicked.connect(self.undo)
        self.redo_btn = IconButton("redo", "Redo", "Ctrl+Y")
        self.redo_btn.clicked.connect(self.redo)
        self.history.changed.connect(self._update_undo)
        self.scene.changed_workflow.connect(self._update_undo)
        self.find_box = QLineEdit(placeholderText="Find in workflow", clearButtonEnabled=True)
        self.find_box.setToolTip("Find blocks by title or setting. Enter jumps to the next match.")
        self.find_box.setMinimumWidth(56)
        self.find_box.setMaximumWidth(150)
        self.find_box.returnPressed.connect(self.find_next)
        self.find_box.textChanged.connect(lambda _: setattr(self, "_find_at", -1))
        self._find_at = -1
        self.run_btn = IconButton("play", "Run workflow", "F5")
        self.run_btn.clicked.connect(self.run)
        self.step_btn = IconButton("step", "Step: run the next block, then pause", "F7")
        self.step_btn.clicked.connect(self.step)
        self.pause_btn = IconButton("pause", "Pause / resume", "F6")
        self.pause_btn.clicked.connect(self.toggle_pause)
        self.restart_btn = IconButton("restart", "Restart from the beginning", "Ctrl+Shift+F5")
        self.restart_btn.clicked.connect(self.restart)
        self.stop_btn = IconButton("stop", "Stop now: cancels waits and searches and releases held keys/buttons",
                                   "Shift+F5")
        self.stop_btn.clicked.connect(engine.stop)

        canvas_panel = Panel("Workflow", self.view, [menu_btn, save_btn, self.undo_btn, self.redo_btn,
                                                     self.find_box, self.run_btn, self.step_btn, self.pause_btn,
                                                     self.restart_btn, self.stop_btn])
        self.variables = VariablesPanel(self.scene, engine, bus)
        self.run_log = RunLogPanel(engine, bus)
        self.run_log.go_to.connect(self.view.go_to)
        self.tabs = QTabWidget(objectName="editorTabs", documentMode=True)
        self.tabs.addTab(self.properties, "Properties")
        self.tabs.addTab(self.variables, "Variables")
        self.tabs.addTab(self.run_log, "Run log")
        self.tabs.setTabToolTip(1, "Workflow variables: initial values and live values while running")
        self.tabs.setTabToolTip(2, "Execution history, errors, detection results and the debug console")
        self.scene.selection_node.connect(lambda nid: nid and self.tabs.setCurrentWidget(self.properties))
        right = QSplitter(Qt.Orientation.Vertical, objectName="editorRight", childrenCollapsible=False)
        right.addWidget(canvas_panel)
        right.addWidget(self.tabs)
        right.setSizes([480, 220])
        main = QSplitter(Qt.Orientation.Horizontal, objectName="editorMain", childrenCollapsible=False)
        blocks_panel = Panel("Blocks", self.library)
        blocks_panel.setMinimumWidth(120)
        main.addWidget(blocks_panel)
        main.addWidget(right)
        main.setSizes([160, 350])
        main.setStretchFactor(1, 1)
        layout = QVBoxLayout(self)
        layout.setContentsMargins(0, 0, 0, 0)
        layout.addWidget(main)

        bus.subscribe("engine.state", self._on_state)
        bus.subscribe("engine.block", lambda p: self.scene.set_running(p["id"]))
        bus.subscribe("blocks.changed", self._blocks_changed)
        bus.subscribe("engine.finished", self._finished)
        self._title_timer = QTimer(self, singleShot=True, interval=250, timeout=self._update_title)
        self.scene.changed_workflow.connect(self._title_timer.start)  # one dirty check per burst of edits
        self.scene.run_requested.connect(self.run_part)
        for keys, slot in ((QKeySequence.StandardKey.Undo, self.undo), ("Ctrl+Y", self.redo),
                           ("Ctrl+Shift+Z", self.redo)):
            act = QAction(self, shortcut=QKeySequence(keys), shortcutContext=Qt.ShortcutContext.WidgetWithChildrenShortcut)
            act.triggered.connect(slot)
            self.view.addAction(act)
        self._on_state(IDLE)
        self._autosave_timer = QTimer(self, timeout=self.write_autosave)
        self.apply_settings()

    def apply_settings(self, _=None) -> None:
        seconds = int(self._setting("projects.autosave_s", 60))
        if seconds > 0:
            self._autosave_timer.start(seconds * 1000)
        else:
            self._autosave_timer.stop()

    def _setting(self, key, default):
        return self._settings.get(key, default) if self._settings else default

    def _blocks_changed(self, _=None) -> None:
        """A custom block was saved or deleted: refresh the library and redraw (titles/inputs may differ)."""
        self.library.populate()
        self.scene.relayout()
        self.properties.show_node(self.properties._node_id)

    def uses(self, type_id: str) -> bool:
        return any(n["type"] == type_id for n in self.scene.wf.nodes())

    # -- execution -------------------------------------------------------

    def run(self, mode: str = "all", node_id: str | None = None, step: bool = False) -> bool:
        if self._engine.state != IDLE:
            if self._engine.state == PAUSED and mode == "all" and not step:
                self._engine.resume()  # Start while paused = continue
            return False
        try:
            self._engine.run(self.scene.wf.to_dict(), mode, node_id, step)
            return True
        except WorkflowError as e:
            log.warning("Cannot run: %s", e)
            QMessageBox.information(self, "Cannot run workflow", str(e))
            return False

    def run_part(self, mode: str, node_id: str) -> None:
        """Context menu: run one block, or a block and the ones after it."""
        self.run(mode, node_id)

    def step(self) -> None:
        if self._engine.state == IDLE:
            selected = self.scene.selected_blocks()
            if len(selected) == 1 and not selected[0].block.hat:  # step from the selected block
                self.run("from", selected[0].node_id, step=True)
            else:
                self.run(step=True)
        else:
            self._engine.step()

    def restart(self) -> None:
        try:
            self._engine.plan(self.scene.wf.to_dict())
        except WorkflowError as e:
            QMessageBox.information(self, "Cannot run workflow", str(e))
            return
        self._engine.restart(self.scene.wf.to_dict())

    def toggle_pause(self) -> None:
        (self._engine.resume if self._engine.state == PAUSED else self._engine.pause)()

    def _finished(self, payload: dict) -> None:
        if payload.get("result") == "error":
            self.tabs.setCurrentWidget(self.run_log)

    # -- undo / find -------------------------------------------------------

    def undo(self) -> None:
        self.history.undo()

    def redo(self) -> None:
        self.history.redo()

    def _update_undo(self) -> None:
        self.undo_btn.setEnabled(self.history.can_undo())
        self.redo_btn.setEnabled(self.history.can_redo())

    def find_next(self) -> None:
        hits = [h for h in self.scene.find(self.find_box.text()) if self.scene.items_by_id[h].isVisible()]
        if not hits:
            self.find_box.setToolTip("No visible block matches (blocks inside collapsed containers are skipped)")
            return
        self._find_at = (self._find_at + 1) % len(hits)
        self.view.go_to(hits[self._find_at])
        self.find_box.setToolTip(f"Match {self._find_at + 1} of {len(hits)}. Enter for the next one.")

    def _on_state(self, state: str) -> None:
        self.run_btn.setEnabled(state in (IDLE, PAUSED))
        self.step_btn.setEnabled(state in (IDLE, RUNNING, PAUSED))
        self.restart_btn.setEnabled(state != STOPPING)
        self.pause_btn.setEnabled(state in (RUNNING, PAUSED))
        self.pause_btn.set_icon_name("play" if state == PAUSED else "pause")
        self.stop_btn.setEnabled(state != IDLE)

    # -- project files -----------------------------------------------------

    def _project_menu(self) -> QMenu:
        menu = QMenu(self)
        menu.addAction("New project", self.new)
        self._template_menu = menu.addMenu("New from template")
        self._template_menu.aboutToShow.connect(self._fill_templates)
        menu.addAction("Open project…", lambda: self.open())
        self._recent_menu = menu.addMenu("Open recent")
        self._recent_menu.aboutToShow.connect(self._fill_recent)
        menu.addSeparator()
        menu.addAction("Save", self.save)
        menu.addAction("Save As…", self.save_as)
        menu.addAction("Duplicate project…", self.duplicate)
        menu.addSeparator()
        menu.addAction("Import workflow…", self.import_workflow)
        menu.addAction("Export workflow…", self.export_workflow)
        menu.addSeparator()
        menu.addAction("Save as template…", self.save_template)
        return menu

    @property
    def dirty(self) -> bool:
        return self._clean is not None and self.scene.wf.dumps() != self._clean

    def _update_title(self) -> None:
        self.file_changed.emit(self.meta["name"] + (" *" if self.dirty else ""))

    def load(self, wf: Workflow, path: Path | None, meta: dict | None = None, clean: bool = True) -> None:
        self.meta = meta or project.new_meta(project.display_name(path) if path else "Untitled")
        self.path = path
        self._clean = wf.dumps() if clean else ""
        self.scene.set_workflow(wf)
        self._title_timer.stop()
        self._update_title()
        self.history.reset()
        self._update_undo()
        QTimer.singleShot(0, self.view.show_top)  # after the view has its real size

    def open_workflow(self, wf: Workflow, name: str) -> bool:
        """Open generated content (e.g. a converted recording) as a new, unsaved project."""
        if not self.confirm_discard():
            return False
        self.load(wf, None, project.new_meta(name), clean=False)
        return True

    def confirm_discard(self) -> bool:
        """True if it is fine to replace the current project (saved, discarded, or nothing to lose)."""
        if not self.dirty:
            return True
        buttons = QMessageBox.StandardButton
        answer = QMessageBox.question(self, "Unsaved changes", f"Save changes to {self.meta['name']}?",
                                      buttons.Save | buttons.Discard | buttons.Cancel, buttons.Save)
        if answer == buttons.Save:
            return self.save()
        return answer == buttons.Discard

    def new(self) -> None:
        if not self.confirm_discard():
            return
        wf = Workflow()
        wf.add_script([new_node(START)], (40, 40))
        self.load(wf, None)

    def _start_dir(self) -> Path:
        return self.path.parent if self.path else Path()

    def open(self, path: str | Path | None = None) -> bool:
        if not self.confirm_discard():
            return False
        if path is None:
            path, _ = QFileDialog.getOpenFileName(self, "Open project", str(self._start_dir()), PROJECT_FILTER)
            if not path:
                return False
        path = Path(path)
        try:
            wf, meta = project.read_document(path)
        except WorkflowError as e:
            log.error("Open failed: %s", e)
            if not path.exists():
                self._forget_recent(path)
            QMessageBox.warning(self, "Cannot open project", str(e))
            return False
        self.load(wf, path, meta)
        self._remember(path)
        log.info("Opened %s", path)
        self._report_problems(wf, f"{path.name} opened")
        return True

    def _report_problems(self, wf: Workflow, what: str) -> None:
        """Imported content is validated up front; the engine refuses to run it until it is fixed."""
        problems = wf.validate()
        if problems:
            more = f"\n…and {len(problems) - 12} more" if len(problems) > 12 else ""
            log.warning("%s with %d problem(s): %s", what, len(problems), "; ".join(problems))
            QMessageBox.warning(self, "Workflow has problems",
                                f"{what}, but it cannot run until these are fixed:\n\n• "
                                + "\n• ".join(problems[:12]) + more)

    def save(self) -> bool:
        if self.path is None:
            return self.save_as()
        return self._write(self.path)

    def _write(self, path: Path) -> bool:
        try:
            self.meta = project.write_project(path, self.scene.wf, self.meta)
        except OSError as e:
            log.error("Save failed: %s", e)
            QMessageBox.warning(self, "Cannot save project", str(e))
            return False
        self.path = path
        self._clean = self.scene.wf.dumps()
        self._remember(path)
        self._update_title()
        log.info("Saved %s", path)
        return True

    def save_to(self, path: Path, name: str) -> bool:
        """Save under a chosen path and name (the Macro Library) and keep working on that file."""
        self.meta = {**self.meta, "name": name}
        return self._write(path)

    def file_moved(self, old: Path, new: Path | None, name: str | None = None) -> None:
        """The library renamed, moved (new) or deleted (None) a file; follow it if it is the open project."""
        self._forget_recent(old)
        if not self.path or self.path.resolve() != old.resolve():
            return
        self.path = new
        if new is None:
            self._clean = ""  # the file is gone: what is on the canvas now counts as unsaved
        else:
            self._remember(new)
        if name:
            self.meta = {**self.meta, "name": name}
        self._update_title()

    def _ask_save_path(self, title: str, name: str) -> Path | None:
        start = self._start_dir() / (name + project.EXT)
        path, _ = QFileDialog.getSaveFileName(self, title, str(start), PROJECT_FILTER)
        return Path(path) if path else None

    def save_as(self) -> bool:
        path = self._ask_save_path("Save project as", self.meta["name"])
        if not path:
            return False
        self.meta = {**self.meta, "name": project.display_name(path)}
        return self._write(path)

    def duplicate(self) -> None:
        """Save the current state as a new project and continue working on the copy."""
        path = self._ask_save_path("Duplicate project", self.meta["name"] + " copy")
        if not path:
            return
        if self.path and path.resolve() == self.path.resolve():
            QMessageBox.information(self, "Duplicate project", "Choose a different file for the copy.")
            return
        self.meta = project.new_meta(project.display_name(path))
        if self._write(path):
            log.info("Duplicated project to %s", path)

    def import_workflow(self) -> None:
        path, _ = QFileDialog.getOpenFileName(self, "Import workflow", str(self._start_dir()), WORKFLOW_FILTER)
        if path:
            self.import_path(Path(path))

    def import_path(self, path: Path) -> bool:
        try:
            other, _ = project.read_document(path)
        except WorkflowError as e:
            log.error("Import failed: %s", e)
            QMessageBox.warning(self, "Cannot import workflow", str(e))
            return False
        wf = self.scene.wf
        right = max((s["pos"][0] for s in wf.scripts), default=0) + 360  # to the right of what is there
        left = min((s["pos"][0] for s in other.scripts), default=0)
        count = project.merge(wf, other, right - left, 0)
        self.scene.relayout()
        self.scene.changed_workflow.emit()
        self.view.fit()
        log.info("Imported %d stack(s) from %s", count, path)
        self._report_problems(wf, f"Imported {path.name}")
        return True

    def export_workflow(self) -> None:
        start = self._start_dir() / (self.meta["name"] + ".lcw.json")
        path, _ = QFileDialog.getSaveFileName(self, "Export workflow", str(start), WORKFLOW_FILTER)
        if not path:
            return
        try:
            project.export_workflow(path, self.scene.wf)
        except OSError as e:
            log.error("Export failed: %s", e)
            QMessageBox.warning(self, "Cannot export workflow", str(e))
            return
        log.info("Exported workflow to %s", path)

    # -- templates -------------------------------------------------------------

    def templates(self) -> list[tuple[str, Path, bool]]:
        """(name, path, user-made) for user templates and the bundled examples."""
        out = []
        if self._templates and self._templates.exists():
            out += [(project.display_name(f), f, True) for f in sorted(self._templates.glob("*" + project.EXT))]
        if EXAMPLES.exists():
            out += [(project.display_name(f), f, False) for f in sorted(EXAMPLES.glob("*.lcw.json"))]
        return out

    def _fill_templates(self) -> None:
        menu = self._template_menu
        menu.clear()
        items = self.templates()
        for name, path, user in items:
            menu.addAction(name if user else f"{name}  (example)", lambda p=path: self.new_from_template(p))
        if not items:
            menu.addAction("No templates yet: use Save as template…").setEnabled(False)
        users = [(n, p) for n, p, u in items if u]
        if users:
            menu.addSeparator()
            menu.addAction("Delete a template…", lambda: self.delete_template(users))

    def new_from_template(self, path: Path) -> bool:
        if not self.confirm_discard():
            return False
        try:
            wf, _ = project.read_document(path)
        except WorkflowError as e:
            QMessageBox.warning(self, "Cannot use template", str(e))
            return False
        self.load(wf, None, project.new_meta(project.display_name(path)), clean=False)
        log.info("New project from template %s", path.name)
        return True

    def save_template(self) -> bool:
        if not self._templates:
            return False
        name, ok = QInputDialog.getText(self, "Save as template", "Template name:", text=self.meta["name"])
        name = name.strip()
        if not ok or not name:
            return False
        safe = "".join(ch for ch in name if ch.isalnum() or ch in " -_").strip()
        if not safe:
            QMessageBox.information(self, "Save as template", "Use letters or digits in the name.")
            return False
        path = self._templates / (safe + project.EXT)
        if path.exists():
            b = QMessageBox.StandardButton
            if QMessageBox.question(self, "Save as template", f"Replace the template {safe}?", b.Yes | b.No,
                                    b.No) != b.Yes:
                return False
        try:
            project.write_project(path, self.scene.wf, project.new_meta(safe))
        except OSError as e:
            QMessageBox.warning(self, "Save as template", str(e))
            return False
        log.info("Saved template %s", path.name)
        return True

    def delete_template(self, users: list[tuple[str, Path]]) -> None:
        names = [n for n, _ in users]
        name, ok = QInputDialog.getItem(self, "Delete template", "Template:", names, 0, False)
        if not ok:
            return
        b = QMessageBox.StandardButton
        if QMessageBox.question(self, "Delete template", f"Delete the template {name}? This cannot be undone.",
                                b.Yes | b.No, b.No) != b.Yes:
            return
        path = dict(users)[name]
        try:
            path.unlink()
            log.info("Deleted template %s", path.name)
        except OSError as e:
            QMessageBox.warning(self, "Delete template", str(e))

    # -- recent projects -----------------------------------------------------

    def recent(self) -> list[str]:
        items = self._setting("projects.recent", [])
        return [p for p in items if isinstance(p, str)] if isinstance(items, list) else []

    def _set_recent(self, items: list[str]) -> None:
        if self._settings:
            self._settings.set("projects.recent", items[:MAX_RECENT])
            try:
                self._settings.save()
            except OSError as e:
                log.warning("Could not save the recent list: %s", e)

    def _remember(self, path: Path) -> None:
        p = str(path.resolve())
        self._set_recent([p] + [r for r in self.recent() if r.lower() != p.lower()])

    def _forget_recent(self, path: Path) -> None:
        p = str(path.resolve()).lower()
        self._set_recent([r for r in self.recent() if r.lower() != p])

    def _clear_recent(self) -> None:
        b = QMessageBox.StandardButton
        if QMessageBox.question(self, "Recent projects", "Clear the list of recent projects? (The files stay.)",
                                b.Yes | b.No, b.No) == b.Yes:
            self._set_recent([])

    def _fill_recent(self) -> None:
        menu = self._recent_menu
        menu.clear()
        items = self.recent()
        for p in items:
            act = menu.addAction(Path(p).name, lambda p=p: self.open(p))
            act.setToolTip(p)
            act.setEnabled(Path(p).exists())
        if not items:
            menu.addAction("No recent projects").setEnabled(False)
        else:
            menu.addSeparator()
            menu.addAction("Clear list…", self._clear_recent)

    # -- autosave and recovery -------------------------------------------------

    def write_autosave(self) -> None:
        state = (self.scene.wf.dumps(), str(self.path) if self.path else None, self.dirty)
        if state == self._autosaved:
            return  # nothing changed since the last autosave
        try:
            project.write_autosave(self._autosave, self.scene.wf, self.meta, state[1], state[2])
            self._autosaved = state
        except OSError as e:
            log.warning("Autosave failed: %s", e)

    def restore_session(self, fallback, crashed: bool = False) -> None:
        """Reopen the last session: the project it had open, plus any unsaved changes.

        After a crash, unsaved changes are offered rather than restored silently."""
        try:
            wf, meta, session = project.read_autosave(self._autosave)
        except WorkflowError as e:
            if self._autosave.exists():
                log.warning("Autosave could not be restored (%s); starting fresh", e)
            self.load(fallback(), None)
            return
        path = Path(session["path"]) if session["path"] else None
        if crashed and session["dirty"]:
            buttons = QMessageBox.StandardButton
            answer = QMessageBox.question(
                self, "Recover unsaved work",
                f"Veyra did not close properly last time.\n\nRecover the unsaved changes to "
                f"{meta['name']} (autosaved {meta['modified'].replace('T', ' ')})?",
                buttons.Yes | buttons.No, buttons.Yes)
            if answer != buttons.Yes:
                if not (path and path.exists() and self.open(path)):
                    self.load(fallback(), None)
                return
            log.info("Recovered unsaved changes to %s", meta["name"])
        self.load(wf, path, meta)
        if session["dirty"]:  # still unsaved: compare against the file on disk (or nothing)
            try:
                self._clean = project.read_document(path)[0].dumps() if path else ""
            except WorkflowError:
                self._clean = ""
            self._update_title()
