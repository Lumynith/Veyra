"""Roblox viewport: a native host widget plus its toolbar."""
from __future__ import annotations

import logging
import math

from PySide6.QtCore import QEvent, Qt, Signal
from PySide6.QtWidgets import (QButtonGroup, QComboBox, QHBoxLayout, QLabel, QMenu, QMessageBox, QSizePolicy,
                               QVBoxLayout, QWidget)

from core.window_manager import ANCHOR, EMBED
from ui.theme import C
from ui.widgets import IconButton, StatusDot

log = logging.getLogger(__name__)
QWIDGETSIZE_MAX = (1 << 24) - 1


class Viewport(QWidget):
    """Native host whose physical pixel size matches the configured Roblox viewport."""
    resized = Signal()

    def __init__(self, settings, parent=None) -> None:
        super().__init__(parent)
        self._settings = settings
        self._fill = False
        self.setObjectName("viewport")
        self.setAttribute(Qt.WidgetAttribute.WA_NativeWindow)  # gives us an HWND to parent Roblox into
        self.setAttribute(Qt.WidgetAttribute.WA_DontCreateNativeAncestors)
        self.setAttribute(Qt.WidgetAttribute.WA_StyledBackground)
        self.placeholder = QLabel("No Roblox window attached", alignment=Qt.AlignmentFlag.AlignCenter)
        layout = QVBoxLayout(self)
        layout.setContentsMargins(0, 0, 0, 0)
        layout.addWidget(self.placeholder)
        self.apply_size()

    def host_hwnd(self) -> int:
        return int(self.winId())

    def apply_size(self) -> None:
        if self._fill:
            self.setMinimumSize(200, 200)
            self.setMaximumSize(QWIDGETSIZE_MAX, QWIDGETSIZE_MAX)
            self.setSizePolicy(QSizePolicy.Policy.Expanding, QSizePolicy.Policy.Expanding)
            return
        dpr = self.devicePixelRatioF()
        self.setFixedSize(math.ceil(int(self._settings.get("viewport.width", 841)) / dpr),
                          math.ceil(int(self._settings.get("viewport.height", 661)) / dpr))

    def set_fill(self, fill: bool) -> None:
        self._fill = fill
        self.apply_size()

    def set_attached(self, attached: bool) -> None:
        self.placeholder.setVisible(not attached)

    def resizeEvent(self, e) -> None:
        super().resizeEvent(e)
        self.resized.emit()

    def event(self, e) -> bool:
        if e.type() == QEvent.Type.DevicePixelRatioChange:  # moved to another monitor / scale changed
            self.apply_size()
        return super().event(e)


class WindowCombo(QComboBox):
    """Refreshes the Roblox window list every time it is opened."""
    opening = Signal()

    def showPopup(self) -> None:
        self.opening.emit()
        super().showPopup()


class ViewportToolbar(QWidget):
    maximize_toggled = Signal(bool)

    def __init__(self, window_manager, viewport: Viewport, settings, bus, parent=None) -> None:
        super().__init__(parent, objectName="viewportToolbar")
        self.setAttribute(Qt.WidgetAttribute.WA_StyledBackground)
        self.setFixedHeight(34)
        self._wm, self._viewport, self._settings = window_manager, viewport, settings
        self._last_hwnd: int | None = None
        self._current = None  # WindowInfo of the attached window (it disappears from enumeration once docked)

        self.dot = StatusDot()
        self.state = QLabel("Disconnected", objectName="muted")
        self.windows = WindowCombo(minimumWidth=230, toolTip="Roblox instance")
        self.windows.opening.connect(self._refresh)
        self.connect_btn = IconButton("link", "Attach selected instance")
        self.connect_btn.clicked.connect(self._toggle_connection)
        self.reconnect_btn = IconButton("reconnect", "Reconnect", "Ctrl+R")
        self.reconnect_btn.clicked.connect(self.reconnect)
        self.embed_btn = IconButton("embed", "Embedded mode: Roblox becomes part of this window. Roblox ignores "
                                    "the keyboard like this, so key presses from macros do nothing.", checkable=True)
        self.anchor_btn = IconButton("anchor", "Anchored mode (recommended): Roblox stays its own window, held "
                                     "exactly over the viewport. Keyboard and mouse work normally.", checkable=True)
        self._modes = QButtonGroup(self, exclusive=True)
        self._modes.addButton(self.embed_btn)
        self._modes.addButton(self.anchor_btn)
        (self.embed_btn if settings.get("viewport.dock_mode") == EMBED else self.anchor_btn).setChecked(True)
        self._modes.buttonClicked.connect(self._mode_changed)
        self.max_btn = IconButton("maximize", "Maximize viewport", "F11", checkable=True)
        self.max_btn.toggled.connect(self._maximize)

        layout = QHBoxLayout(self)
        layout.setContentsMargins(8, 0, 4, 0)
        layout.setSpacing(4)
        layout.addWidget(self.dot)
        layout.addWidget(self.state)
        layout.addStretch(1)
        layout.addWidget(self.windows)
        layout.addWidget(self.connect_btn)
        layout.addWidget(self.reconnect_btn)
        layout.addSpacing(6)
        layout.addWidget(self.embed_btn)
        layout.addWidget(self.anchor_btn)
        layout.addSpacing(6)
        layout.addWidget(self.max_btn)

        bus.subscribe("roblox.windows", self._on_windows)
        bus.subscribe("roblox.attached", self._on_attached)
        bus.subscribe("roblox.detached", lambda _: self._on_lost("Disconnected", C["faint"]))
        bus.subscribe("roblox.closed", lambda _: self._on_lost("Roblox closed", C["err"]))
        bus.subscribe("roblox.hung", self._on_hung)
        bus.subscribe("roblox.geometry", self._on_geometry)
        self.setContextMenuPolicy(Qt.ContextMenuPolicy.CustomContextMenu)
        self.customContextMenuRequested.connect(self._menu)
        self._refresh()

    def mode(self) -> str:
        return ANCHOR if self.anchor_btn.isChecked() else EMBED

    # -- actions ---------------------------------------------------------

    def _refresh(self) -> None:
        try:
            self._wm.refresh()
        except Exception:
            log.exception("Could not list windows")

    def _attach(self, hwnd: int) -> None:
        try:
            self._wm.attach(hwnd, self._viewport.host_hwnd(), self.mode())
        except Exception as e:
            log.error("Attach failed: %s", e)
            QMessageBox.warning(self, "Attach failed", str(e))
            self._refresh()

    def _toggle_connection(self) -> None:
        if self._wm.attached:
            self._wm.detach()
        elif (info := self.windows.currentData()) is not None:
            self._attach(info.hwnd)

    def reconnect(self) -> None:
        if self._wm.attached:
            self._attach(self._wm.hwnd)
            return
        windows = self._wm.refresh()
        target = next((w for w in windows if w.hwnd == self._last_hwnd), windows[0] if windows else None)
        if target is None:
            log.warning("Reconnect: no Roblox window found")
            self._set_state("No Roblox window found", C["warn"])
            return
        self._attach(target.hwnd)

    def _mode_changed(self, _) -> None:
        self._settings.set("viewport.dock_mode", self.mode())
        self._settings.save()
        if self._wm.attached and self._wm.mode != self.mode():
            self._attach(self._wm.hwnd)

    def _maximize(self, on: bool) -> None:
        self.max_btn.set_icon_name("restore" if on else "maximize")
        self.max_btn.setToolTip(("Restore viewport" if on else "Maximize viewport") + "  (F11)")
        self.maximize_toggled.emit(on)

    def _menu(self, pos) -> None:
        menu = QMenu(self)
        menu.addAction("Refresh instances", self._refresh)
        menu.addAction("Reconnect", self.reconnect)
        detach = menu.addAction("Detach", self._wm.detach)
        detach.setEnabled(self._wm.attached)
        menu.exec(self.mapToGlobal(pos))

    # -- events ----------------------------------------------------------

    def _set_state(self, text: str, color: str) -> None:
        self.state.setText(text)
        self.dot.set_color(color)

    def _on_windows(self, windows) -> None:
        keep = self._current if self._wm.attached else None
        items = ([keep] if keep else []) + [w for w in windows if not keep or w.hwnd != keep.hwnd]
        self.windows.blockSignals(True)
        self.windows.clear()
        for w in items:
            self.windows.addItem(w.label(), w)
        if not items:
            self.windows.addItem("No Roblox instances found", None)
        self.windows.blockSignals(False)
        self._update_buttons()

    def _on_attached(self, payload) -> None:
        self._last_hwnd = payload["hwnd"]
        self._current = next((self.windows.itemData(i) for i in range(self.windows.count())
                              if self.windows.itemData(i) and self.windows.itemData(i).hwnd == payload["hwnd"]),
                             self._current)
        self._set_state("Connected · " + ("embedded" if payload["mode"] == EMBED else "anchored"), C["ok"])
        (self.embed_btn if payload["mode"] == EMBED else self.anchor_btn).setChecked(True)
        self._viewport.set_attached(True)
        self._update_buttons()

    def _on_lost(self, text: str, color: str) -> None:
        self._current = None
        self._set_state(text, color)
        self._viewport.set_attached(False)
        self._refresh()

    def _on_hung(self, hung: bool) -> None:
        if hung:
            self._set_state("Not responding", C["warn"])
        elif self._wm.attached:
            self._set_state("Connected · " + ("embedded" if self._wm.mode == EMBED else "anchored"), C["ok"])

    def _on_geometry(self, g) -> None:
        self.state.setToolTip("Outer %d × %d · client %d × %d" % (*g["outer"], *g["client"]))

    def _update_buttons(self) -> None:
        attached = self._wm.attached
        self.connect_btn.set_icon_name("unlink" if attached else "link")
        self.connect_btn.setToolTip("Detach" if attached else "Attach selected instance")
        self.connect_btn.setEnabled(attached or self.windows.currentData() is not None)


class ViewportPanel(QWidget):
    def __init__(self, window_manager, settings, bus, parent=None) -> None:
        super().__init__(parent)
        self.viewport = Viewport(settings)
        self.toolbar = ViewportToolbar(window_manager, self.viewport, settings, bus)
        self.viewport.resized.connect(window_manager.sync)
        layout = QVBoxLayout(self)
        layout.setContentsMargins(0, 0, 0, 0)
        layout.setSpacing(0)
        layout.addWidget(self.toolbar)
        layout.addWidget(self.viewport, 0, Qt.AlignmentFlag.AlignHCenter | Qt.AlignmentFlag.AlignTop)

    def set_fill(self, fill: bool) -> None:
        self.viewport.set_fill(fill)
        lay = self.layout()
        lay.setAlignment(self.viewport, Qt.AlignmentFlag(0) if fill
                         else Qt.AlignmentFlag.AlignHCenter | Qt.AlignmentFlag.AlignTop)
        lay.setStretchFactor(self.viewport, 1 if fill else 0)
