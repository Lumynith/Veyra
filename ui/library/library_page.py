"""Macro Library page: every saved macro in one place — open, run, rename, organise into folders, import, delete."""
from __future__ import annotations

import logging
import os
from datetime import datetime
from pathlib import Path
from typing import Callable

from PySide6.QtCore import Qt
from PySide6.QtWidgets import (QAbstractItemView, QComboBox, QFileDialog, QHBoxLayout, QHeaderView, QInputDialog,
                               QLabel, QLineEdit, QMenu, QMessageBox, QPushButton, QStackedWidget, QTreeWidget,
                               QTreeWidgetItem, QVBoxLayout, QWidget)

from automation.macro_library import MacroLibrary
from automation.workflow import WorkflowError
from ui.editor.workspace import PROJECT_FILTER
from ui.widgets import EmptyState, IconButton, Panel

log = logging.getLogger(__name__)
PATH_ROLE = Qt.ItemDataRole.UserRole
ALL, TOP = "\x00all", ""  # folder filter values: every macro / macros not in a folder


def ago(when: datetime, now: datetime | None = None) -> str:
    seconds = ((now or datetime.now()) - when).total_seconds()
    for limit, unit, size in ((60, None, 1), (3600, "min", 60), (86400, "h", 3600), (86400 * 7, "d", 86400)):
        if seconds < limit:
            return "just now" if unit is None else f"{int(seconds // size)} {unit} ago"
    return when.strftime("%d %b %Y")


class _Item(QTreeWidgetItem):
    """Sorts the Changed and Blocks columns by value, not by their text."""

    def __lt__(self, other) -> bool:
        column = self.treeWidget().sortColumn() if self.treeWidget() else 0
        key = Qt.ItemDataRole.UserRole + 1
        mine, theirs = self.data(column, key), other.data(column, key)
        return mine < theirs if mine is not None and theirs is not None else super().__lt__(other)


class LibraryPage(QWidget):
    def __init__(self, library: MacroLibrary, editor, show_editor: Callable[[], None]) -> None:
        super().__init__()
        self.lib, self.editor, self._show_editor = library, editor, show_editor
        self.setAcceptDrops(True)

        self.save_btn = QPushButton("Save current macro here", objectName="primary")
        self.save_btn.setToolTip("Save the macro that is open in the Editor into the library (in the folder picked below)")
        self.save_btn.clicked.connect(self.save_current)
        import_btn = QPushButton("Import…")
        import_btn.setToolTip("Copy macro files (.lcp.json / .lcw.json) into the library. You can also drop files here.")
        import_btn.clicked.connect(self.import_files)
        top = QHBoxLayout()
        top.addWidget(self.save_btn)
        top.addWidget(import_btn)
        top.addStretch(1)

        self.search = QLineEdit(placeholderText="Search macros", clearButtonEnabled=True)
        self.search.textChanged.connect(self._filter)
        self.folder = QComboBox()
        self.folder.setToolTip("Show the macros in one folder")
        self.folder.currentIndexChanged.connect(self._folder_changed)
        new_folder = IconButton("folderplus", "New folder")
        new_folder.clicked.connect(self.new_folder)
        self.del_folder = IconButton("trash", "Delete this folder (its macros move to the top level)")
        self.del_folder.clicked.connect(self.delete_folder)
        filters = QHBoxLayout()
        filters.addWidget(self.search, 1)
        filters.addWidget(self.folder)
        filters.addWidget(new_folder)
        filters.addWidget(self.del_folder)

        self.tree = QTreeWidget(rootIsDecorated=False, uniformRowHeights=True, sortingEnabled=True)
        self.tree.setHeaderLabels(["Name", "Folder", "Blocks", "Changed"])
        self.tree.setSelectionMode(QAbstractItemView.SelectionMode.SingleSelection)
        header = self.tree.header()
        header.setSectionResizeMode(0, QHeaderView.ResizeMode.Stretch)
        for col in (1, 2, 3):
            header.setSectionResizeMode(col, QHeaderView.ResizeMode.ResizeToContents)
        self.tree.sortByColumn(3, Qt.SortOrder.DescendingOrder)
        self.tree.itemDoubleClicked.connect(lambda *_: self.open_selected())
        self.tree.itemSelectionChanged.connect(self._update_buttons)
        self.tree.setContextMenuPolicy(Qt.ContextMenuPolicy.CustomContextMenu)
        self.tree.customContextMenuRequested.connect(lambda pos: self._menu().exec(self.tree.viewport().mapToGlobal(pos)))

        empty = EmptyState("library", "No saved macros yet",
                           "Build a macro in the Editor, then press “Save current macro here”. "
                           "Or import macro files, or drop them on this page.")
        self.no_match = QLabel("No macros match.", objectName="emptyText", alignment=Qt.AlignmentFlag.AlignCenter)
        self.stack = QStackedWidget()
        for w in (self.tree, empty, self.no_match):
            self.stack.addWidget(w)

        self.open_btn = QPushButton("Open")
        self.open_btn.setToolTip("Open in the Editor (double-click does the same)")
        self.open_btn.clicked.connect(self.open_selected)
        self.run_btn = QPushButton("Run")
        self.run_btn.setToolTip("Open in the Editor and start it")
        self.run_btn.clicked.connect(self.run_selected)
        self.more_btn = QPushButton("More")
        self.more_btn.clicked.connect(lambda: self._menu().exec(self.more_btn.mapToGlobal(self.more_btn.rect().bottomLeft())))
        self.count = QLabel(objectName="hint")
        actions = QHBoxLayout()
        for w in (self.open_btn, self.run_btn, self.more_btn):
            actions.addWidget(w)
        actions.addStretch(1)
        actions.addWidget(self.count)

        body = QWidget()
        layout = QVBoxLayout(body)
        layout.setContentsMargins(8, 4, 8, 8)
        layout.setSpacing(8)
        layout.addLayout(top)
        layout.addLayout(filters)
        layout.addWidget(self.stack, 1)
        layout.addLayout(actions)
        outer = QVBoxLayout(self)
        outer.setContentsMargins(0, 0, 0, 0)
        outer.addWidget(Panel("Macro Library", body))
        self.refresh()

    # -- listing -------------------------------------------------------------------------------------

    def showEvent(self, e) -> None:
        super().showEvent(e)
        self.refresh()  # files may have changed in the Editor or in Explorer

    def refresh(self, select: Path | None = None) -> None:
        select = select or self.selected()
        try:
            entries, folders = self.lib.entries(), self.lib.folders()
        except OSError as e:
            log.error("Cannot read the macro library: %s", e)
            entries, folders = [], []
        self._fill_folders(folders)
        self.tree.setSortingEnabled(False)
        self.tree.clear()
        key = Qt.ItemDataRole.UserRole + 1
        for e in entries:
            item = _Item([e.name, e.folder, "?" if e.error else str(e.blocks), ago(e.modified)])
            item.setData(0, PATH_ROLE, str(e.path))
            item.setData(2, key, e.blocks)
            item.setData(3, key, e.modified.timestamp())
            item.setToolTip(0, f"Can't open: {e.error}" if e.error else str(e.path))
            if e.error:
                item.setForeground(0, Qt.GlobalColor.red)
            self.tree.addTopLevelItem(item)
            if select and e.path == select:
                self.tree.setCurrentItem(item)
        self.tree.setSortingEnabled(True)
        self._total = len(entries)
        self._filter()

    def _fill_folders(self, folders: list[str]) -> None:
        current = self.folder.currentData() if self.folder.count() else ALL
        self.folder.blockSignals(True)
        self.folder.clear()
        self.folder.addItem("All macros", ALL)
        self.folder.addItem("Not in a folder", TOP)
        for name in folders:
            self.folder.addItem(name, name)
        self.folder.setCurrentIndex(max(0, self.folder.findData(current)))
        self.folder.blockSignals(False)
        self.del_folder.setEnabled(self.folder.currentData() not in (ALL, TOP))

    def _folder_changed(self) -> None:
        self.del_folder.setEnabled(self.folder.currentData() not in (ALL, TOP))
        self._filter()

    def _filter(self) -> None:
        words = self.search.text().lower().split()
        folder = self.folder.currentData()
        shown = 0
        for i in range(self.tree.topLevelItemCount()):
            item = self.tree.topLevelItem(i)
            hide = (folder != ALL and item.text(1) != folder) or not all(w in item.text(0).lower() for w in words)
            item.setHidden(hide)
            shown += not hide
        self.stack.setCurrentIndex(1 if not self._total else 0 if shown else 2)
        self.count.setText(f"{shown} of {self._total}" if shown != self._total else f"{self._total} macros")
        if self.tree.currentItem() and self.tree.currentItem().isHidden():
            self.tree.setCurrentItem(None)
        self._update_buttons()

    def selected(self) -> Path | None:
        item = self.tree.currentItem()
        return Path(item.data(0, PATH_ROLE)) if item and not item.isHidden() and item.isSelected() else None

    def _update_buttons(self) -> None:
        on = self.selected() is not None
        for b in (self.open_btn, self.run_btn, self.more_btn):
            b.setEnabled(on)

    def _target_folder(self) -> str:
        folder = self.folder.currentData()
        return "" if folder == ALL else folder

    # -- actions -------------------------------------------------------------------------------------

    def _menu(self) -> QMenu:
        menu = QMenu(self)
        path = self.selected()
        if not path:
            menu.addAction("Select a macro first").setEnabled(False)
            return menu
        menu.addAction("Open", self.open_selected)
        menu.addAction("Run", self.run_selected)
        menu.addSeparator()
        menu.addAction("Rename…", self.rename_selected)
        menu.addAction("Duplicate", self.duplicate_selected)
        move = menu.addMenu("Move to folder")
        current = "" if path.parent == self.lib.root else path.parent.name
        for name in ["", *self.lib.folders()]:
            action = move.addAction(name or "(not in a folder)", lambda n=name: self.move_selected(n))
            action.setEnabled(name != current)
        move.addSeparator()
        move.addAction("New folder…", lambda: self.move_selected(None))
        menu.addAction("Show in Explorer", lambda: _explorer(path))
        menu.addSeparator()
        menu.addAction("Delete…", self.delete_selected)
        return menu

    def _fail(self, title: str, error: Exception) -> None:
        log.error("%s: %s", title, error)
        QMessageBox.warning(self, title, str(error))

    def save_current(self) -> None:
        if self.editor.path and self.lib.contains(self.editor.path):
            if self.editor.save():  # already a library macro: save it in place
                self.refresh(self.editor.path)
            return
        name, ok = QInputDialog.getText(self, "Save to library", "Name for this macro:", text=self.editor.meta["name"])
        if not ok or not name.strip():
            return
        path = self.lib.path_for(name.strip(), self._target_folder())
        if self.editor.save_to(path, name.strip()):
            self.refresh(path)

    def import_files(self, files: list[str] | None = None) -> None:
        if files is None:
            files, _ = QFileDialog.getOpenFileNames(self, "Import macros", "", PROJECT_FILTER)
        added, failed = None, []
        for f in files:
            try:
                added = self.lib.import_file(f, self._target_folder())
            except (WorkflowError, OSError) as e:
                failed.append(f"{Path(f).name}: {e}")
        if failed:
            log.warning("Import failed: %s", "; ".join(failed))
            QMessageBox.warning(self, "Some files were not imported", "\n".join(failed))
        self.refresh(added)

    def open_selected(self) -> bool:
        path = self.selected()
        if path and self.editor.open(path):
            self._show_editor()
            return True
        return False

    def run_selected(self) -> None:
        if self.open_selected():
            self.editor.run()

    def rename_selected(self) -> None:
        path = self.selected()
        if not path:
            return
        old = self.tree.currentItem().text(0)
        name, ok = QInputDialog.getText(self, "Rename macro", "New name:", text=old)
        if not ok or not name.strip() or name.strip() == old:
            return
        try:
            new = self.lib.rename(path, name.strip())
        except (WorkflowError, OSError, ValueError) as e:
            return self._fail("Cannot rename", e)
        self.editor.file_moved(path, new, name.strip())
        self.refresh(new)

    def duplicate_selected(self) -> None:
        path = self.selected()
        try:
            new = self.lib.duplicate(path) if path else None
        except (WorkflowError, OSError) as e:
            return self._fail("Cannot duplicate", e)
        self.refresh(new)

    def move_selected(self, folder: str | None) -> None:
        path = self.selected()
        if not path:
            return
        if folder is None:
            folder = self._ask_folder()
            if folder is None:
                return
        try:
            new = self.lib.move(path, folder)
        except OSError as e:
            return self._fail("Cannot move", e)
        self.editor.file_moved(path, new)
        self.refresh(new)

    def delete_selected(self) -> None:
        path = self.selected()
        if not path:
            return
        name = self.tree.currentItem().text(0)
        b = QMessageBox.StandardButton
        if QMessageBox.question(self, "Delete macro", f"Move “{name}” to the Recycle Bin?", b.Yes | b.No, b.No) != b.Yes:
            return
        try:
            self.lib.delete(path)
        except OSError as e:
            return self._fail("Cannot delete", e)
        self.editor.file_moved(path, None)
        self.refresh()

    def _ask_folder(self) -> str | None:
        name, ok = QInputDialog.getText(self, "New folder", "Folder name:")
        if not ok or not name.strip():
            return None
        try:
            return self.lib.new_folder(name)
        except (OSError, ValueError) as e:
            self._fail("Cannot create folder", e)
            return None

    def new_folder(self) -> None:
        name = self._ask_folder()
        if name is not None:
            self.refresh()
            self.folder.setCurrentIndex(self.folder.findData(name))

    def delete_folder(self) -> None:
        name = self.folder.currentData()
        if name in (ALL, TOP):
            return
        b = QMessageBox.StandardButton
        if QMessageBox.question(self, "Delete folder", f"Delete the folder “{name}”? Its macros are kept: they move "
                                "out of the folder.", b.Yes | b.No, b.No) != b.Yes:
            return
        try:
            for old, new in self.lib.delete_folder(name):
                self.editor.file_moved(old, new)  # follows the open project if it was in there
        except OSError as e:
            self._fail("Cannot delete folder", e)
        self.folder.setCurrentIndex(0)
        self.refresh()

    # -- drag and drop files onto the page ------------------------------------------------------------

    def dragEnterEvent(self, e) -> None:
        if any(u.toLocalFile().lower().endswith(".json") for u in e.mimeData().urls()):
            e.acceptProposedAction()

    def dropEvent(self, e) -> None:
        self.import_files([u.toLocalFile() for u in e.mimeData().urls() if u.toLocalFile().lower().endswith(".json")])


def _explorer(path: Path) -> None:
    import subprocess
    subprocess.Popen(["explorer", "/select,", os.path.normpath(path)])
