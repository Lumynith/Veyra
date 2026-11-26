"""Small reusable widgets used across the UI."""
from __future__ import annotations

from PySide6.QtCore import QRectF, QSize, Qt, QVariantAnimation
from PySide6.QtGui import QColor, QPainter, QPen
from PySide6.QtWidgets import QFrame, QHBoxLayout, QLabel, QSplitter, QToolButton, QVBoxLayout, QWidget

from ui.theme import C, icon


class IconButton(QToolButton):
    def __init__(self, icon_name: str, tooltip: str, shortcut: str | None = None, checkable: bool = False,
                 size: int = 16, parent=None) -> None:
        super().__init__(parent)
        self.setIcon(icon(icon_name))
        self.setIconSize(QSize(size, size))
        self.setCheckable(checkable)
        self.setCursor(Qt.CursorShape.PointingHandCursor)
        if shortcut:  # display only; shortcuts are registered once on the main window
            tooltip = f"{tooltip}  ({shortcut})"
        self.setToolTip(tooltip)

    def set_icon_name(self, name: str) -> None:
        self.setIcon(icon(name))


class Spinner(QWidget):
    """Indeterminate loading indicator. Hidden while stopped."""

    def __init__(self, size: int = 14, parent=None) -> None:
        super().__init__(parent)
        self.setFixedSize(size, size)
        self._angle = 0
        self._anim = QVariantAnimation(self, startValue=0, endValue=360, duration=900, loopCount=-1)
        self._anim.valueChanged.connect(self._tick)
        self.hide()

    def _tick(self, value) -> None:
        self._angle = value
        self.update()

    def start(self) -> None:
        self.show()
        self._anim.start()

    def stop(self) -> None:
        self._anim.stop()
        self.hide()

    def paintEvent(self, _) -> None:
        p = QPainter(self)
        p.setRenderHint(QPainter.RenderHint.Antialiasing)
        r = QRectF(self.rect()).adjusted(2, 2, -2, -2)
        p.setPen(QPen(QColor(C["line"]), 2))
        p.drawEllipse(r)
        p.setPen(QPen(QColor(C["accent_hi"]), 2, Qt.PenStyle.SolidLine, Qt.PenCapStyle.RoundCap))
        p.drawArc(r, int(-self._angle * 16), 100 * 16)


class StatusDot(QWidget):
    """Coloured dot with a faint glow."""

    def __init__(self, color: str = C["faint"], parent=None) -> None:
        super().__init__(parent)
        self.setFixedSize(14, 14)
        self._color = QColor(color)

    def set_color(self, color: str) -> None:
        self._color = QColor(color)
        self.update()

    def paintEvent(self, _) -> None:
        p = QPainter(self)
        p.setRenderHint(QPainter.RenderHint.Antialiasing)
        p.setPen(Qt.PenStyle.NoPen)
        glow = QColor(self._color)
        glow.setAlpha(50)
        p.setBrush(glow)
        p.drawEllipse(QRectF(1, 1, 12, 12))
        p.setBrush(self._color)
        p.drawEllipse(QRectF(4, 4, 6, 6))


class EmptyState(QWidget):
    def __init__(self, icon_name: str, title: str, text: str, note: str = "", parent=None) -> None:
        super().__init__(parent)
        pic = QLabel(alignment=Qt.AlignmentFlag.AlignCenter)
        pic.setPixmap(icon(icon_name, C["faint"]).pixmap(36, 36))
        heading = QLabel(title, objectName="emptyTitle", alignment=Qt.AlignmentFlag.AlignCenter)
        body = QLabel(text, objectName="emptyText", alignment=Qt.AlignmentFlag.AlignCenter, wordWrap=True)
        body.setMinimumWidth(160)
        body.setMaximumWidth(320)
        self._body = body
        layout = QVBoxLayout(self)
        layout.addStretch(1)
        widgets = [pic, heading, body]
        if note:
            widgets.append(QLabel(note, objectName="emptyNote", alignment=Qt.AlignmentFlag.AlignCenter))
        for w in widgets:
            layout.addWidget(w, 0, Qt.AlignmentFlag.AlignHCenter)
        layout.addStretch(1)
        layout.setSpacing(8)

    def showEvent(self, e) -> None:
        super().showEvent(e)
        self._fit_text()

    def resizeEvent(self, e) -> None:
        super().resizeEvent(e)
        self._fit_text()

    def _fit_text(self) -> None:
        # Aligned layout items ignore heightForWidth; size the wrapped text for the width it actually gets.
        width = max(160, min(320, self.width() - 24))
        self._body.setFixedWidth(width) if self._body.width() != width else None
        self._body.setMinimumHeight(self._body.heightForWidth(width))


class AdaptiveSplitter(QSplitter):
    """Two panels side by side when there is room, stacked when there is not.

    Its minimum width is that of the widest panel, so a page never forces the window wider than the
    1436 px layout (where pages get about 515 px next to the 841 px Roblox viewport)."""

    def __init__(self, parent=None, **kw) -> None:
        super().__init__(Qt.Orientation.Horizontal, parent, **kw)

    def _mins(self) -> list[int]:
        return [self.widget(i).minimumSizeHint().width() for i in range(self.count())]

    def minimumSizeHint(self) -> QSize:
        hint = super().minimumSizeHint()
        return QSize(max(self._mins(), default=0), hint.height())

    def resizeEvent(self, e) -> None:
        need = sum(self._mins()) + self.handleWidth() * max(0, self.count() - 1)
        want = Qt.Orientation.Horizontal if e.size().width() >= need else Qt.Orientation.Vertical
        if want != self.orientation():
            self.setOrientation(want)
            self.setSizes([10_000] * self.count())  # sizes from the other orientation make no sense: share evenly
        super().resizeEvent(e)


class Panel(QFrame):
    """Bordered panel with an uppercase caption and an optional row of header widgets."""

    def __init__(self, title: str, body: QWidget, header: list[QWidget] = (), parent=None) -> None:
        super().__init__(parent, objectName="panel")
        top = QHBoxLayout()
        top.setContentsMargins(8, 6, 6, 2)
        top.addWidget(QLabel(title.upper(), objectName="panelTitle"))
        top.addStretch(1)
        for w in header:
            top.addWidget(w)
        layout = QVBoxLayout(self)
        layout.setContentsMargins(0, 0, 0, 0)
        layout.setSpacing(0)
        layout.addLayout(top)
        layout.addWidget(body, 1)
