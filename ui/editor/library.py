"""Searchable block library. Drag items onto the canvas or double-click to append to the Start stack."""
from __future__ import annotations

from PySide6.QtCore import QMimeData, QSize, Qt, Signal
from PySide6.QtGui import QColor
from PySide6.QtWidgets import (QAbstractItemView, QLabel, QLineEdit, QMenu, QTreeWidget, QTreeWidgetItem,
                               QVBoxLayout, QWidget)

from blocks import registry
from blocks.base_block import CATEGORIES
from ui.editor.canvas import MIME
from ui.theme import C, icon

TYPE_ROLE = Qt.ItemDataRole.UserRole


class _BlockTree(QTreeWidget):
    def mimeTypes(self):
        return [MIME]

    def mimeData(self, items):
        data = QMimeData()
        type_id = items[0].data(0, TYPE_ROLE) if items else None
        if type_id:
            data.setData(MIME, type_id.encode())
        return data


class BlockLibrary(QWidget):
    add_requested = Signal(str)

    def __init__(self, settings=None, parent=None) -> None:
        super().__init__(parent)
        self._settings = settings
        self.search = QLineEdit(placeholderText="Search blocks", clearButtonEnabled=True)
        self.search.addAction(icon("search"), QLineEdit.ActionPosition.LeadingPosition)
        self.search.setToolTip("Search blocks  (Ctrl+F)")
        self.search.textChanged.connect(self.filter)
        self.tree = _BlockTree(headerHidden=True, rootIsDecorated=True, indentation=10)
        self.tree.setIconSize(QSize(15, 15))
        self.tree.setDragEnabled(True)
        self.tree.setDragDropMode(QAbstractItemView.DragDropMode.DragOnly)
        self.tree.itemDoubleClicked.connect(self._activate)
        self.tree.setContextMenuPolicy(Qt.ContextMenuPolicy.CustomContextMenu)
        self.tree.customContextMenuRequested.connect(self._menu)
        self.empty = QLabel("No blocks match", objectName="muted", alignment=Qt.AlignmentFlag.AlignCenter)
        self.empty.hide()

        layout = QVBoxLayout(self)
        layout.setContentsMargins(6, 4, 6, 6)
        layout.setSpacing(6)
        layout.addWidget(self.search)
        layout.addWidget(self.tree, 1)
        layout.addWidget(self.empty)
        self.populate()

    # -- favorites ---------------------------------------------------------------

    def favorites(self) -> list[str]:
        fav = self._settings.get("editor.favorites", []) if self._settings else []
        return [f for f in fav if isinstance(f, str)] if isinstance(fav, list) else []

    def toggle_favorite(self, type_id: str) -> None:
        if not self._settings:
            return
        fav = self.favorites()
        self._settings.set("editor.favorites", [f for f in fav if f != type_id] if type_id in fav else fav + [type_id])
        try:
            self._settings.save()
        except OSError:
            pass  # kept in memory for this session
        self.populate()

    def populate(self) -> None:
        self.tree.clear()
        known = {b.type_id: b for b in registry.all_blocks()}
        fav = [known[t] for t in self.favorites() if t in known]
        extra = sorted({b.category for b in registry.all_blocks()} - set(CATEGORIES))  # user-made categories
        for category in (["★ Favorites"] if fav else []) + list(CATEGORIES) + extra:
            if category == "★ Favorites":
                blocks = fav
            else:
                blocks = sorted((b for b in registry.all_blocks() if b.category == category), key=lambda b: b.title)
            if not blocks:
                continue
            cat = QTreeWidgetItem(self.tree, [category.upper()])
            cat.setFlags(Qt.ItemFlag.ItemIsEnabled)
            cat.setForeground(0, QColor(C["warn"] if category == "★ Favorites"
                                        else CATEGORIES.get(category, blocks[0].block_color())))
            f = cat.font(0)
            f.setPointSizeF(7.5)
            f.setBold(True)
            cat.setFont(0, f)
            for block in blocks:
                item = QTreeWidgetItem(cat, [block.title])
                item.setIcon(0, icon(block.block_icon(), block.block_color()))
                item.setToolTip(0, block.description)
                item.setData(0, TYPE_ROLE, block.type_id)
        self.tree.expandAll()
        self.filter(self.search.text())

    def filter(self, text: str) -> None:
        needle = text.strip().lower()
        any_visible = False
        for i in range(self.tree.topLevelItemCount()):
            cat = self.tree.topLevelItem(i)
            shown = 0
            for j in range(cat.childCount()):
                item = cat.child(j)
                block = registry.get(item.data(0, TYPE_ROLE))
                match = not needle or needle in f"{block.title} {block.description} {block.category}".lower()
                item.setHidden(not match)
                shown += match
            cat.setHidden(shown == 0)
            if needle and shown:
                cat.setExpanded(True)
            any_visible |= shown > 0
        self.empty.setVisible(not any_visible)

    def focus_search(self) -> None:
        self.search.setFocus()
        self.search.selectAll()

    def _activate(self, item: QTreeWidgetItem) -> None:
        if type_id := item.data(0, TYPE_ROLE):
            self.add_requested.emit(type_id)

    def _menu(self, pos) -> None:
        item = self.tree.itemAt(pos)
        if not item or not item.data(0, TYPE_ROLE):
            return
        menu = QMenu(self)
        type_id = item.data(0, TYPE_ROLE)
        menu.addAction("Add to workflow").triggered.connect(lambda: self._activate(item))
        if self._settings:
            text = "Remove from favorites" if type_id in self.favorites() else "Add to favorites"
            menu.addAction(icon("star"), text).triggered.connect(lambda: self.toggle_favorite(type_id))
        menu.exec(self.tree.viewport().mapToGlobal(pos))
