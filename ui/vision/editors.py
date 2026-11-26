"""Capture flows and the property editors for 'image' and 'region' parameters."""
from __future__ import annotations

import logging
from pathlib import Path
from typing import Callable

from PySide6.QtCore import Signal
from PySide6.QtGui import QIcon
from PySide6.QtWidgets import QComboBox, QFileDialog, QHBoxLayout, QLineEdit, QMessageBox, QWidget

from ui.vision.capture_dialog import CaptureDialog
from ui.widgets import IconButton
from ui.workers import run_async

log = logging.getLogger(__name__)
IMAGE_FILTER = "Images (*.png *.jpg *.jpeg *.bmp *.webp)"
_thumbs: dict[tuple[str, float], QIcon] = {}


def thumbnail(path: Path) -> QIcon:
    try:
        key = (str(path), path.stat().st_mtime)
    except OSError:
        return QIcon()
    if key not in _thumbs:
        _thumbs[key] = QIcon(str(path))
    return _thumbs[key]


def _fail(parent, title: str):
    def show(msg: str) -> None:
        log.warning("%s: %s", title, msg)
        QMessageBox.warning(parent, title, msg)
    return show


def capture_asset(parent, vision, on_saved: Callable[[dict], None], source: str = "viewport",
                  hwnd: int | None = None, folder: str = "") -> None:
    """Capture (in the background), let the user select, then save the selection as an asset."""
    def grab():
        if source == "screen":
            img, _ = vision.capture_screen()
            return img, None  # desktop captures have no Roblox reference size
        if source == "window":
            img = vision.capture_window(hwnd)
            return img, (img.shape[1], img.shape[0])
        img, rect = vision.capture_viewport()
        return img, (rect[2], rect[3])

    def selected(result) -> None:
        img, ref_size = result
        dlg = CaptureDialog(img, "Capture image", "asset", vision.assets.folders(), folder, parent=parent)
        if dlg.exec() and dlg.crop is not None:
            try:
                meta = vision.assets.add_image(dlg.crop, dlg.asset_name(), dlg.asset_folder(), ref_size)
            except OSError as e:
                _fail(parent, "Could not save image")(str(e))
                return
            on_saved(meta)

    run_async(grab, selected, _fail(parent, "Capture failed"))


def import_assets(parent, vision, folder: str = "") -> list[dict]:
    names, _ = QFileDialog.getOpenFileNames(parent, "Import images", "", IMAGE_FILTER)
    added = []
    for name in names:
        try:
            added.append(vision.assets.import_file(name, folder))
        except (OSError, ValueError) as e:
            _fail(parent, "Import failed")(f"{Path(name).name}: {e}")
    return added


def pick_region(parent, vision, space: str, on_picked: Callable[[str], None]) -> None:
    """Capture the viewport (or desktop) and let the user drag a search region."""
    def grab():
        return vision.capture_screen() if space == "screen" else vision.capture_viewport()

    def selected(result) -> None:
        img, rect = result
        dlg = CaptureDialog(img, "Select search region", "region", parent=parent)
        if not dlg.exec():
            return
        if dlg.whole:
            on_picked("")
            return
        r = dlg.selection()
        # Screen regions are absolute screen coordinates; viewport regions are relative to the viewport.
        ox, oy = (rect[0], rect[1]) if space == "screen" else (0, 0)
        on_picked(f"{r.x() + ox}, {r.y() + oy}, {r.width()}, {r.height()}")

    run_async(grab, selected, _fail(parent, "Capture failed"))


class AssetCombo(QComboBox):
    """Lists image assets; refreshes every time it opens so new captures appear."""

    def __init__(self, vision, value: str, parent=None) -> None:
        super().__init__(parent)
        self._vision = vision
        self.populate(value)

    def populate(self, value: str) -> None:
        self.blockSignals(True)
        self.clear()
        self.addItem("(no image)", "")
        lib = self._vision.assets
        recent = lib.recent()
        for meta in recent:
            ref = lib.ref(meta["id"])
            self.addItem(thumbnail(lib.root / meta["file"]), f"Recent: {lib.label(ref)}", ref)
        if recent:
            self.insertSeparator(self.count())
        for meta in lib.list():
            ref = lib.ref(meta["id"])
            self.addItem(thumbnail(lib.root / meta["file"]), lib.label(ref), ref)
        if value and self.findData(value) < 0:  # a plain file path or a deleted asset
            label = lib.label(value) if value.startswith("asset:") else f"File: {Path(value).name}"
            self.addItem(label, value)
        self.setCurrentIndex(max(0, self.findData(value)))
        self.setToolTip(value)
        self.blockSignals(False)

    def showPopup(self) -> None:
        self.populate(self.currentData() or "")
        super().showPopup()


class ImagePicker(QWidget):
    changed = Signal(str)

    def _picked(self, _=None) -> None:
        ref = self.combo.currentData() or ""
        if ref.startswith("asset:"):
            self._vision.assets.touch(ref)
        self.changed.emit(ref)

    def __init__(self, vision, value: str, parent=None) -> None:
        super().__init__(parent)
        self._vision = vision
        self.combo = AssetCombo(vision, value)
        self.combo.currentIndexChanged.connect(self._picked)
        capture = IconButton("camera", "Capture from the Roblox viewport…")
        capture.clicked.connect(lambda: capture_asset(self, vision, self._set_asset))
        imp = IconButton("import", "Import image file…")
        imp.clicked.connect(self._import)
        row = QHBoxLayout(self)
        row.setContentsMargins(0, 0, 0, 0)
        row.setSpacing(2)
        row.addWidget(self.combo, 1)
        row.addWidget(capture)
        row.addWidget(imp)

    def _set_asset(self, meta: dict) -> None:
        ref = self._vision.assets.ref(meta["id"])
        self.combo.populate(ref)
        self.changed.emit(ref)

    def _import(self) -> None:
        added = import_assets(self, self._vision)
        if added:
            self._set_asset(added[0])


class RegionPicker(QWidget):
    changed = Signal(str)

    def __init__(self, vision, value: str, space: Callable[[], str], parent=None) -> None:
        super().__init__(parent)
        self.edit = QLineEdit(value, placeholderText="x, y, width, height — blank = everything")
        self.edit.textChanged.connect(self.changed)
        pick = IconButton("crop", "Select the region on a capture…")
        pick.clicked.connect(lambda: pick_region(self, vision, space(), self.edit.setText))
        row = QHBoxLayout(self)
        row.setContentsMargins(0, 0, 0, 0)
        row.setSpacing(2)
        row.addWidget(self.edit, 1)
        row.addWidget(pick)


class TargetCombo(QComboBox):
    """Saved text targets; refreshes every time it opens so targets saved meanwhile appear."""
    changed = Signal(str)

    def __init__(self, targets, value: str, parent=None) -> None:
        super().__init__(parent)
        self._targets = targets
        self.populate(value)
        self.currentIndexChanged.connect(lambda _: self.changed.emit(self.currentData() or ""))

    def populate(self, value: str) -> None:
        self.blockSignals(True)
        self.clear()
        self.addItem("(none — use the fields below)", "")
        for t in self._targets.list():
            ref = self._targets.ref(t["id"])
            self.addItem(self._targets.label(ref), ref)
        if value and self.findData(value) < 0:
            self.addItem(self._targets.label(value), value)
        self.setCurrentIndex(max(0, self.findData(value)))
        self.blockSignals(False)

    def showPopup(self) -> None:
        self.populate(self.currentData() or "")
        super().showPopup()
