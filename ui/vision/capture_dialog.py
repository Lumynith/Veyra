"""Capture tool: shows a frozen frame, lets the user drag a selection, previews it and saves it.

mode="asset":  name + folder, returns the cropped image.
mode="region": returns the selected rectangle (frame pixel coordinates).
"""
from __future__ import annotations

import cv2
import numpy as np
from PySide6.QtCore import QPointF, QRect, QRectF, Qt, Signal
from PySide6.QtGui import QColor, QImage, QPainter, QPen, QPixmap
from PySide6.QtWidgets import (QComboBox, QDialog, QFormLayout, QGraphicsPixmapItem, QGraphicsRectItem,
                               QGraphicsScene, QGraphicsView, QHBoxLayout, QLabel, QLineEdit, QPushButton,
                               QVBoxLayout, QWidget)

from ui.theme import C


def to_pixmap(img: np.ndarray) -> QPixmap:
    img = np.ascontiguousarray(img)
    if img.ndim == 2:
        img = cv2.cvtColor(img, cv2.COLOR_GRAY2BGR)
    h, w = img.shape[:2]
    return QPixmap.fromImage(QImage(img.data, w, h, 3 * w, QImage.Format.Format_BGR888).copy())


class SelectionView(QGraphicsView):
    """Shows an image 1 image pixel = 1 scene unit. Left-drag selects; arrows nudge (Shift resizes)."""
    changed = Signal(QRect)

    def __init__(self, img: np.ndarray, parent=None) -> None:
        super().__init__(parent)
        self._size = QRect(0, 0, img.shape[1], img.shape[0])
        self.setScene(QGraphicsScene(self))
        self.scene().addItem(QGraphicsPixmapItem(to_pixmap(img)))
        self.scene().setSceneRect(QRectF(self._size))
        dim = QColor(0, 0, 0, 110)
        self._shade = [self.scene().addRect(QRectF(), QPen(Qt.PenStyle.NoPen), dim) for _ in range(4)]
        self._box = QGraphicsRectItem()
        self._box.setPen(QPen(QColor(C["accent_hi"]), 0))
        self.scene().addItem(self._box)
        self.selection = QRect()
        self._anchor: QPointF | None = None
        self.setBackgroundBrush(QColor(C["bg0"]))
        self.setDragMode(QGraphicsView.DragMode.NoDrag)
        self.setCursor(Qt.CursorShape.CrossCursor)
        self.setFocusPolicy(Qt.FocusPolicy.StrongFocus)

    def fit_if_larger(self) -> None:
        view = self.viewport().size()
        if self._size.width() > view.width() or self._size.height() > view.height():
            self.fitInView(QRectF(self._size), Qt.AspectRatioMode.KeepAspectRatio)

    def set_selection(self, rect: QRect) -> None:
        self.selection = rect.normalized().intersected(self._size)
        r = QRectF(self.selection)
        self._box.setRect(r)
        full = QRectF(self._size)
        if r.isEmpty():
            for s in self._shade:
                s.setRect(QRectF())
        else:  # darken everything outside the selection
            self._shade[0].setRect(QRectF(full.left(), full.top(), full.width(), r.top() - full.top()))
            self._shade[1].setRect(QRectF(full.left(), r.bottom(), full.width(), full.bottom() - r.bottom()))
            self._shade[2].setRect(QRectF(full.left(), r.top(), r.left() - full.left(), r.height()))
            self._shade[3].setRect(QRectF(r.right(), r.top(), full.right() - r.right(), r.height()))
        self.changed.emit(self.selection)

    def _pixel(self, pos) -> QPointF:
        p = self.mapToScene(pos.toPoint())
        return QPointF(round(p.x()), round(p.y()))

    def mousePressEvent(self, e) -> None:
        if e.button() == Qt.MouseButton.LeftButton:
            self._anchor = self._pixel(e.position())
            self.set_selection(QRect())
        else:
            super().mousePressEvent(e)

    def mouseMoveEvent(self, e) -> None:
        if self._anchor is not None:
            p = self._pixel(e.position())
            self.set_selection(QRectF(self._anchor, p).normalized().toRect())
        super().mouseMoveEvent(e)

    def mouseReleaseEvent(self, e) -> None:
        self._anchor = None
        super().mouseReleaseEvent(e)

    def wheelEvent(self, e) -> None:
        if e.modifiers() & Qt.KeyboardModifier.ControlModifier:
            factor = 1.25 if e.angleDelta().y() > 0 else 0.8
            if 0.1 <= self.transform().m11() * factor <= 16:
                self.setTransformationAnchor(QGraphicsView.ViewportAnchor.AnchorUnderMouse)
                self.scale(factor, factor)
            return
        super().wheelEvent(e)

    def keyPressEvent(self, e) -> None:
        moves = {Qt.Key.Key_Left: (-1, 0), Qt.Key.Key_Right: (1, 0), Qt.Key.Key_Up: (0, -1), Qt.Key.Key_Down: (0, 1)}
        if e.key() in moves and not self.selection.isEmpty():
            dx, dy = moves[e.key()]
            r = QRect(self.selection)
            if e.modifiers() & Qt.KeyboardModifier.ShiftModifier:
                r.setWidth(max(1, r.width() + dx))
                r.setHeight(max(1, r.height() + dy))
            else:
                r.translate(dx, dy)
            self.set_selection(r)
            return
        super().keyPressEvent(e)


class CaptureDialog(QDialog):
    def __init__(self, frame: np.ndarray, title: str, mode: str = "asset", folders: list[str] = (),
                 folder: str = "", name: str = "", parent=None) -> None:
        super().__init__(parent)
        self.setWindowTitle(title)
        self.frame, self.mode = frame, mode
        self.crop: np.ndarray | None = None
        self.view = SelectionView(frame)
        self.view.changed.connect(self._on_selection)

        self.info = QLabel("Drag to select.  Arrows nudge · Shift+arrows resize · Ctrl+wheel zooms",
                           objectName="muted", wordWrap=True)
        self.size_label = QLabel("No selection")
        self.preview = QLabel(alignment=Qt.AlignmentFlag.AlignCenter)
        self.preview.setFixedSize(220, 160)
        self.preview.setStyleSheet(f"background:{C['bg0']}; border:1px solid {C['line']};")
        side = QVBoxLayout()
        side.addWidget(QLabel("PREVIEW", objectName="panelTitle"))
        side.addWidget(self.preview)
        side.addWidget(self.size_label)
        side.addWidget(self.info)
        if mode == "asset":
            self.name = QLineEdit(name, placeholderText="Name, e.g. Play button")
            self.folder = QComboBox(editable=True)
            self.folder.addItems([""] + list(folders))
            self.folder.setCurrentText(folder)
            self.folder.lineEdit().setPlaceholderText("(no folder)")
            form = QFormLayout()
            form.addRow("Name", self.name)
            form.addRow("Folder", self.folder)
            side.addLayout(form)
        side.addStretch(1)
        self.ok = QPushButton("Save image" if mode == "asset" else "Use region", objectName="primary")
        self.ok.setEnabled(False)
        self.ok.clicked.connect(self.accept)
        if mode == "region":
            whole = QPushButton("Whole area")
            whole.setToolTip("Clear the region so the whole viewport / screen is searched")
            whole.clicked.connect(self._whole)
            side.addWidget(whole)
        cancel = QPushButton("Cancel")
        cancel.clicked.connect(self.reject)
        buttons = QHBoxLayout()
        buttons.addWidget(cancel)
        buttons.addWidget(self.ok)
        side.addLayout(buttons)

        panel = QWidget()
        panel.setFixedWidth(240)
        panel.setLayout(side)
        layout = QHBoxLayout(self)
        layout.addWidget(self.view, 1)
        layout.addWidget(panel)
        screen = self.screen().availableGeometry()
        self.resize(min(frame.shape[1] + 290, int(screen.width() * 0.9)),
                    min(frame.shape[0] + 40, int(screen.height() * 0.9)))
        self.whole = False

    def showEvent(self, e) -> None:
        super().showEvent(e)
        self.view.fit_if_larger()
        self.view.setFocus()

    def _whole(self) -> None:
        self.whole = True
        self.accept()

    def _on_selection(self, r: QRect) -> None:
        valid = r.width() >= 4 and r.height() >= 4
        self.ok.setEnabled(valid)
        if not valid:
            self.size_label.setText("No selection" if r.isEmpty() else "Too small (min 4 × 4)")
            self.preview.clear()
            self.crop = None
            return
        self.size_label.setText(f"{r.width()} × {r.height()} px at ({r.x()}, {r.y()})")
        self.crop = self.frame[r.y():r.y() + r.height(), r.x():r.x() + r.width()].copy()
        pm = to_pixmap(self.crop)
        # Nearest-neighbour upscaling keeps small UI elements crisp in the preview.
        self.preview.setPixmap(pm.scaled(self.preview.size() - QRect(0, 0, 8, 8).size(),
                                         Qt.AspectRatioMode.KeepAspectRatio,
                                         Qt.TransformationMode.FastTransformation if r.width() < 110
                                         else Qt.TransformationMode.SmoothTransformation))

    def selection(self) -> QRect:
        return self.view.selection

    def asset_name(self) -> str:
        return self.name.text().strip() or "image"

    def asset_folder(self) -> str:
        return self.folder.currentText().strip("/ ")


def overlay(frame: np.ndarray, box: tuple[int, int, int, int] | None, max_w: int, max_h: int) -> QPixmap:
    """Frame with the match box drawn on it, scaled to fit."""
    pm = to_pixmap(frame)
    if box:
        p = QPainter(pm)
        p.setPen(QPen(QColor(C["accent_hi"]), max(2, frame.shape[1] // 300)))
        p.drawRect(QRect(*box))
        p.end()
    return pm.scaled(max_w, max_h, Qt.AspectRatioMode.KeepAspectRatio, Qt.TransformationMode.SmoothTransformation)
