"""Icon navigation sidebar with a sliding indicator and fading page changes."""
from __future__ import annotations

from PySide6.QtCore import QEasingCurve, QPoint, QPropertyAnimation, QSize, QTimer, Signal
from PySide6.QtWidgets import (QButtonGroup, QGraphicsOpacityEffect, QStackedWidget, QToolButton, QVBoxLayout,
                               QWidget)

from ui.theme import icon


class Sidebar(QWidget):
    page_changed = Signal(int)

    def __init__(self, stack: QStackedWidget, parent=None) -> None:
        super().__init__(parent, objectName="sidebar")
        self.setFixedWidth(56)
        self._stack = stack
        self._group = QButtonGroup(self, exclusive=True)
        self._names: dict[str, int] = {}
        self._group.idClicked.connect(self.select)
        self._top = QVBoxLayout()
        self._top.setSpacing(4)
        self._bottom = QVBoxLayout()
        layout = QVBoxLayout(self)
        layout.setContentsMargins(8, 10, 8, 10)
        layout.addLayout(self._top)
        layout.addStretch(1)
        layout.addLayout(self._bottom)
        self._indicator = QWidget(self, objectName="navIndicator")
        self._indicator.resize(3, 20)
        self._slide = QPropertyAnimation(self._indicator, b"pos", self, duration=180,
                                         easingCurve=QEasingCurve.Type.OutCubic)

    def add_page(self, icon_name: str, tooltip: str, page: QWidget, bottom: bool = False) -> int:
        index = self._stack.addWidget(page)
        self._names[icon_name] = index
        btn = QToolButton(objectName="navButton", checkable=True, toolTip=tooltip)
        btn.setIcon(icon(icon_name))
        btn.setIconSize(QSize(20, 20))
        btn.setFixedSize(40, 40)
        self._group.addButton(btn, index)
        (self._bottom if bottom else self._top).addWidget(btn)
        return index

    def index_of(self, name: str) -> int:
        """Page index by its icon name (editor, recorder, image, …); -1 if there is none."""
        return self._names.get(name, -1)

    def select(self, index: int) -> None:
        btn = self._group.button(index)
        if btn is None:
            return
        btn.setChecked(True)
        target = QPoint(0, btn.y() + (btn.height() - self._indicator.height()) // 2)
        self._slide.stop()
        self._slide.setEndValue(target)
        self._slide.start()
        if index != self._stack.currentIndex():
            self._stack.setCurrentIndex(index)
            self._fade_in(self._stack.currentWidget())
            self.page_changed.emit(index)

    @staticmethod
    def _fade_in(page: QWidget) -> None:
        effect = QGraphicsOpacityEffect(page)
        page.setGraphicsEffect(effect)
        anim = QPropertyAnimation(effect, b"opacity", page, duration=140, startValue=0.0, endValue=1.0)
        anim.finished.connect(lambda: page.setGraphicsEffect(None))  # effects are costly; drop when done
        anim.start(QPropertyAnimation.DeletionPolicy.DeleteWhenStopped)

    def resizeEvent(self, e) -> None:
        super().resizeEvent(e)
        QTimer.singleShot(0, self._place_indicator)  # buttons get real positions after this layout pass

    def _place_indicator(self) -> None:
        if btn := self._group.checkedButton():
            self._slide.stop()  # a slide started before layout would land on a stale position
            self._indicator.move(0, btn.y() + (btn.height() - self._indicator.height()) // 2)
