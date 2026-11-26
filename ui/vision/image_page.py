"""Image Detection page: capture, import, organise and test image assets."""
from __future__ import annotations

import logging
import subprocess
import time

from PySide6.QtCore import QSize, Qt
from PySide6.QtGui import QKeySequence, QShortcut
from PySide6.QtWidgets import (QCheckBox, QComboBox, QDoubleSpinBox, QFormLayout, QHBoxLayout, QInputDialog,
                               QLabel, QLineEdit, QMenu, QMessageBox, QPushButton, QStackedWidget,
                               QToolButton, QTreeWidget, QTreeWidgetItem, QVBoxLayout, QWidget)

from core.window_manager import list_windows
from ui.vision.capture_dialog import overlay, to_pixmap
from ui.vision.editors import capture_asset, import_assets, thumbnail
from ui.theme import C
from ui.widgets import AdaptiveSplitter, EmptyState, IconButton, Panel, Spinner
from ui.workers import run_async
from vision.image_capture import read_image
from vision.image_detector import METHODS, MODES, DetectOptions

log = logging.getLogger(__name__)
ROLE = Qt.ItemDataRole.UserRole  # ("asset", id) or ("folder", path)


class ImageDetectionPage(QWidget):
    def __init__(self, vision, window_manager, settings, parent=None) -> None:
        super().__init__(parent)
        self.vision, self._wm, self._settings = vision, window_manager, settings
        lib = vision.assets

        # -- library (left) ---------------------------------------------
        cap = IconButton("camera", "Capture from the Roblox viewport")
        cap.clicked.connect(lambda: self._capture("viewport"))
        region = IconButton("crop", "Capture a region of the screen")
        region.clicked.connect(lambda: self._capture("screen"))
        self.win_btn = IconButton("window", "Capture a Roblox window (even when covered)")
        self.win_menu = QMenu(self.win_btn)
        self.win_menu.aboutToShow.connect(self._fill_window_menu)
        self.win_btn.setMenu(self.win_menu)
        self.win_btn.setPopupMode(QToolButton.ToolButtonPopupMode.InstantPopup)
        imp = IconButton("import", "Import image files")
        imp.clicked.connect(self._import)
        folder = IconButton("folderplus", "New folder")
        folder.clicked.connect(self._new_folder)
        self.search = QLineEdit(placeholderText="Search images…", clearButtonEnabled=True)
        self.search.textChanged.connect(self._filter)

        self.tree = QTreeWidget(headerHidden=True, iconSize=QSize(32, 32))
        self.tree.setContextMenuPolicy(Qt.ContextMenuPolicy.CustomContextMenu)
        self.tree.customContextMenuRequested.connect(self._context_menu)
        self.tree.currentItemChanged.connect(lambda *_: self._show_selected())
        self.tree.itemDoubleClicked.connect(lambda *_: self._rename())
        QShortcut(QKeySequence("F2"), self.tree, activated=self._rename,
                  context=Qt.ShortcutContext.WidgetWithChildrenShortcut)
        QShortcut(QKeySequence.StandardKey.Delete, self.tree, activated=self._delete,
                  context=Qt.ShortcutContext.WidgetWithChildrenShortcut)
        self.lib_stack = QStackedWidget()
        self.lib_stack.addWidget(EmptyState("image", "No images yet",
                                            "Capture part of the Roblox viewport or import an image file. "
                                            "Saved images can be picked in any Image Detection block."))
        self.lib_stack.addWidget(self.tree)
        left = QWidget()
        lv = QVBoxLayout(left)
        lv.setContentsMargins(8, 4, 8, 8)
        lv.addWidget(self.search)
        lv.addWidget(self.lib_stack, 1)
        library = Panel("Images", left, [cap, region, self.win_btn, imp, folder])

        # -- preview + test (right) -------------------------------------
        self.preview = QLabel("Select an image", alignment=Qt.AlignmentFlag.AlignCenter, objectName="muted")
        self.preview.setMinimumHeight(140)
        self.info = QLabel(objectName="muted", wordWrap=True)
        self.confidence = QDoubleSpinBox(minimum=0.1, maximum=1.0, singleStep=0.05, decimals=2, value=0.85)
        self.mode = QComboBox()
        self.mode.addItems(MODES)
        self.method = QComboBox()
        self.method.addItems(list(METHODS))
        self.multiscale = QCheckBox("Multi-scale", checked=True)
        self.test_btn = QPushButton("Test on viewport", objectName="primary")
        self.test_btn.setToolTip("Capture the Roblox viewport now and search it for the selected image")
        self.test_btn.clicked.connect(self._test)
        self.spinner = Spinner()
        self.result = QLabel(wordWrap=True)
        self.shot = QLabel(alignment=Qt.AlignmentFlag.AlignCenter)
        self.stats = QLabel(objectName="muted")
        clear = QPushButton("Clear cache")
        clear.setToolTip("Forget decoded templates and the last captured frame")
        clear.clicked.connect(self._clear_cache)

        form = QFormLayout()
        form.addRow("Confidence", self.confidence)
        form.addRow("Mode", self.mode)
        form.addRow("Method", self.method)
        form.addRow("", self.multiscale)
        run_row = QHBoxLayout()
        run_row.addWidget(self.test_btn)
        run_row.addWidget(self.spinner)
        run_row.addStretch(1)
        cache_row = QHBoxLayout()
        cache_row.addWidget(self.stats, 1)
        cache_row.addWidget(clear)
        right = QWidget()
        rv = QVBoxLayout(right)
        rv.setContentsMargins(10, 4, 10, 10)
        rv.addWidget(self.preview)
        rv.addWidget(self.info)
        rv.addLayout(form)
        rv.addLayout(run_row)
        rv.addWidget(self.result)
        rv.addWidget(self.shot, 1)
        rv.addLayout(cache_row)

        split = AdaptiveSplitter(objectName="imagePage", childrenCollapsible=False)
        split.addWidget(library)
        split.addWidget(Panel("Preview & test", right))
        split.setSizes([260, 300])
        layout = QVBoxLayout(self)
        layout.setContentsMargins(0, 0, 0, 0)
        layout.addWidget(split)
        self._lib = lib
        self.refresh()

    # -- tree --------------------------------------------------------------

    def refresh(self, select: tuple | None = None) -> None:
        select = select or self._current()
        self.tree.clear()
        folders: dict[str, QTreeWidgetItem] = {}

        def folder_item(path: str):
            if not path:
                return self.tree.invisibleRootItem()
            if path not in folders:
                parent_path, _, name = path.rpartition("/")
                item = QTreeWidgetItem(folder_item(parent_path), [name])
                item.setData(0, ROLE, ("folder", path))
                item.setExpanded(True)
                folders[path] = item
            return folders[path]

        for f in self._lib.folders():
            folder_item(f)
        assets = self._lib.list()
        for meta in assets:
            item = QTreeWidgetItem(folder_item(meta["folder"]), [meta["name"]])
            item.setIcon(0, thumbnail(self._lib.root / meta["file"]))
            item.setData(0, ROLE, ("asset", meta["id"]))
            item.setToolTip(0, f"{meta['size'][0]} × {meta['size'][1]} px")
        for item in folders.values():
            item.setExpanded(True)
        self.lib_stack.setCurrentIndex(1 if assets or folders else 0)
        if select:
            for item in self._items():
                if item.data(0, ROLE) == tuple(select):
                    self.tree.setCurrentItem(item)
                    break
        self._filter(self.search.text())
        self._show_selected()
        self._update_stats()

    def _items(self):
        stack = [self.tree.topLevelItem(i) for i in range(self.tree.topLevelItemCount())]
        while stack:
            item = stack.pop()
            yield item
            stack.extend(item.child(i) for i in range(item.childCount()))

    def _filter(self, text: str) -> None:
        text = text.strip().lower()

        def visit(item) -> bool:
            kids = [visit(item.child(i)) for i in range(item.childCount())]
            visible = not text or text in item.text(0).lower() or any(kids)
            item.setHidden(not visible)
            return visible

        for i in range(self.tree.topLevelItemCount()):
            visit(self.tree.topLevelItem(i))

    def _current(self) -> tuple | None:
        item = self.tree.currentItem()
        return item.data(0, ROLE) if item else None

    def _current_folder(self) -> str:
        cur = self._current()
        if not cur:
            return ""
        return cur[1] if cur[0] == "folder" else self._lib.get(cur[1])["folder"]

    # -- preview -----------------------------------------------------------

    def _show_selected(self) -> None:
        cur = self._current()
        is_asset = bool(cur) and cur[0] == "asset"
        self.test_btn.setEnabled(is_asset)
        if not is_asset:
            self.preview.clear()
            self.preview.setText("Select an image")
            self.info.setText("")
            return
        try:
            meta = self._lib.get(cur[1])
            img = read_image(self._lib.root / meta["file"])
        except (KeyError, FileNotFoundError) as e:
            self.preview.setText(str(e))
            return
        pm = to_pixmap(img)
        small = pm.width() < 120 and pm.height() < 80
        self.preview.setPixmap(pm.scaled(min(pm.width() * 3, 300) if small else min(pm.width(), 300), 140,
                                         Qt.AspectRatioMode.KeepAspectRatio,
                                         Qt.TransformationMode.FastTransformation if small
                                         else Qt.TransformationMode.SmoothTransformation))
        ref = meta.get("reference_size")
        self.info.setText(f"<b>{self._lib.label(self._lib.ref(meta['id']))}</b><br>"
                          f"{meta['size'][0]} × {meta['size'][1]} px · "
                          + (f"captured at viewport {ref[0]} × {ref[1]}" if ref else "no reference size")
                          + f"<br>Added {meta.get('created', '?')}")

    # -- capture / import ------------------------------------------------

    def _saved(self, meta: dict) -> None:
        self.refresh(("asset", meta["id"]))

    def _capture(self, source: str, hwnd: int | None = None) -> None:
        capture_asset(self, self.vision, self._saved, source, hwnd, self._current_folder())

    def _fill_window_menu(self) -> None:
        self.win_menu.clear()
        windows = {w.hwnd: w.label() for w in list_windows(self._settings.get("roblox.process_names", []))}
        if self._wm.hwnd:  # an embedded window is a child, so EnumWindows does not list it
            windows.setdefault(self._wm.hwnd, "Attached Roblox window")
        if not windows:
            self.win_menu.addAction("No Roblox windows found").setEnabled(False)
        for hwnd, label in windows.items():
            self.win_menu.addAction(label, lambda h=hwnd: self._capture("window", h))

    def _import(self) -> None:
        added = import_assets(self, self.vision, self._current_folder())
        if added:
            self.refresh(("asset", added[-1]["id"]))

    # -- organise ----------------------------------------------------------

    def _ask(self, title: str, label: str, text: str = "") -> str | None:
        value, ok = QInputDialog.getText(self, title, label, text=text)
        return value.strip() if ok and value.strip() else None

    def _guard(self, fn, *args) -> None:
        try:
            fn(*args)
        except (KeyError, ValueError, OSError) as e:
            QMessageBox.warning(self, "Image library", str(e))
        self.refresh()

    def _new_folder(self) -> None:
        base = self._current_folder()
        name = self._ask("New folder", f"Folder name{f' (inside {base})' if base else ''}:")
        if name:
            path = f"{base}/{name}" if base else name
            self._guard(self._lib.add_folder, path)
            self.refresh(("folder", path.strip("/ ")))

    def _rename(self) -> None:
        cur = self._current()
        if not cur:
            return
        if cur[0] == "asset":
            name = self._ask("Rename image", "Name:", self._lib.get(cur[1])["name"])
            if name:
                self._guard(self._lib.rename, cur[1], name)
        else:
            parent, _, old = cur[1].rpartition("/")
            name = self._ask("Rename folder", "Folder name:", old)
            if name:
                new = f"{parent}/{name}" if parent else name
                self._guard(self._lib.rename_folder, cur[1], new)
                self.refresh(("folder", new))

    def _move(self) -> None:
        cur = self._current()
        if not cur or cur[0] != "asset":
            return
        meta = self._lib.get(cur[1])
        options = ["(top level)"] + self._lib.folders()
        current = options.index(meta["folder"]) if meta["folder"] in options else 0
        choice, ok = QInputDialog.getItem(self, "Move image", "Folder (type a new name to create one):",
                                          options, current, True)
        if ok:
            self._guard(self._lib.move, cur[1], "" if choice == "(top level)" else choice)

    def _delete(self) -> None:
        cur = self._current()
        if not cur:
            return
        if cur[0] == "asset":
            label = self._lib.label(self._lib.ref(cur[1]))
            text = f"Delete “{label}”? Blocks that use it will fail until you pick another image."
            fn = self._lib.delete
        else:
            label = cur[1]
            text = f"Remove folder “{label}”? Its images move to the parent folder."
            fn = self._lib.remove_folder
        if QMessageBox.question(self, "Delete", text) == QMessageBox.StandardButton.Yes:
            self._guard(fn, cur[1])

    def _reveal(self) -> None:
        cur = self._current()
        if cur and cur[0] == "asset":
            subprocess.Popen(f'explorer /select,"{self._lib.path(cur[1])}"')

    def _context_menu(self, pos) -> None:
        item = self.tree.itemAt(pos)
        if item:
            self.tree.setCurrentItem(item)
        cur = self._current() if item else None
        menu = QMenu(self)
        if cur and cur[0] == "asset":
            menu.addAction("Test on viewport", self._test)
            menu.addAction("Rename\tF2", self._rename)
            menu.addAction("Move to folder…", self._move)
            menu.addAction("Show in Explorer", self._reveal)
            menu.addSeparator()
            menu.addAction("Delete\tDel", self._delete)
        elif cur:
            menu.addAction("Rename folder\tF2", self._rename)
            menu.addAction("Remove folder\tDel", self._delete)
        menu.addSeparator()
        menu.addAction("New folder…", self._new_folder)
        menu.addAction("Capture from viewport…", lambda: self._capture("viewport"))
        menu.addAction("Import images…", self._import)
        menu.exec(self.tree.viewport().mapToGlobal(pos))

    # -- test ----------------------------------------------------------------

    def _test(self) -> None:
        cur = self._current()
        if not cur or cur[0] != "asset":
            return
        ref = self._lib.ref(cur[1])
        opts = DetectOptions(confidence=self.confidence.value(), mode=self.mode.currentText(),
                             method=self.method.currentText(), multiscale=self.multiscale.isChecked())

        def work():
            frame, rect = self.vision.capture_viewport()
            t = time.perf_counter()
            m = self.vision.locate(ref, rect, opts, adapt_to=(rect[2], rect[3]), frame=frame)
            return frame, m, time.perf_counter() - t

        def done(result) -> None:
            self._busy(False)
            frame, m, elapsed = result
            hit = m is not None and m.score >= opts.confidence
            if m is None:
                self.result.setText("No match (the image is larger than the viewport at this scale).")
            else:
                verdict = f"<b style='color:{C['ok']}'>Found</b>" if hit else f"<b style='color:{C['err']}'>Not found</b>"
                self.result.setText(f"{verdict} — best score {m.score:.3f} (threshold {opts.confidence:.2f})<br>"
                                    f"centre ({m.center[0]}, {m.center[1]}) · {m.w} × {m.h} px · "
                                    f"scale {m.scale:.2f} · {elapsed * 1000:.0f} ms")
            box = (m.x, m.y, m.w, m.h) if m is not None else None
            self.shot.setPixmap(overlay(frame, box, max(200, self.shot.width()), max(150, self.shot.height())))
            self._update_stats()

        def failed(msg: str) -> None:
            self._busy(False)
            self.result.setText(f"<span style='color:{C['err']}'>{msg}</span>")

        self._busy(True)
        run_async(work, done, failed)

    def _busy(self, on: bool) -> None:
        self.test_btn.setEnabled(not on)
        (self.spinner.start if on else self.spinner.stop)()

    def _clear_cache(self) -> None:
        self.vision.clear_cache()
        self._update_stats()
        log.info("Image detection cache cleared")

    def _update_stats(self) -> None:
        s = self.vision.stats
        self.stats.setText(f"Captures {s['captures']} · reused {s['reused']} · templates decoded {s['decoded']}")

    def showEvent(self, e) -> None:
        super().showEvent(e)
        self.refresh()  # blocks (Capture Screenshot) may have added assets meanwhile
