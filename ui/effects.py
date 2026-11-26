"""Interface effects that also say something: a shimmer along the status bar while a macro runs, a toast when
it ends, and a red edge flash when it is stopped. Cheap when idle: no timers run unless something animates."""
from __future__ import annotations

from PySide6.QtCore import QEasingCurve, QElapsedTimer, QEvent, QObject, QPoint, QPropertyAnimation, QRectF, Qt, QTimer
from PySide6.QtGui import QColor, QLinearGradient, QPainter
from PySide6.QtWidgets import QFrame, QGraphicsOpacityEffect, QHBoxLayout, QLabel, QWidget

from automation.engine import IDLE, PAUSED, RUNNING, STOPPING
from ui.theme import C


def _alpha(color: str, a: float) -> QColor:
    c = QColor(color)
    c.setAlphaF(max(0.0, min(1.0, a)))
    return c


class RunGlow(QWidget):
    """A 2 px line along the top of the status bar: a band of light sweeps along it while a macro runs,
    amber while paused, hidden when idle."""

    PERIOD = 1600  # ms for one sweep

    def __init__(self, bar: QWidget, animate: bool = True) -> None:
        super().__init__(bar)
        self.setAttribute(Qt.WidgetAttribute.WA_TransparentForMouseEvents)
        self._animate = animate
        self._state = IDLE
        self._clock = QElapsedTimer()
        self._timer = QTimer(self, interval=33, timeout=self.update)
        bar.installEventFilter(self)
        self._fit()
        self.hide()

    def eventFilter(self, obj, e) -> bool:
        if e.type() == QEvent.Type.Resize:
            self._fit()
        return False

    def _fit(self) -> None:
        self.setGeometry(0, 0, self.parentWidget().width(), 2)

    def set_state(self, state: str) -> None:
        self._state = state
        running = state in (RUNNING, STOPPING)
        if running and self._animate:
            self._clock.start()
            self._timer.start()
        else:
            self._timer.stop()
        self.setVisible(state != IDLE)
        self.raise_()
        self.update()

    def paintEvent(self, _) -> None:
        p = QPainter(self)
        w = self.width()
        if self._state == PAUSED:
            p.fillRect(self.rect(), _alpha(C["warn"], 0.8))
            return
        p.fillRect(self.rect(), _alpha(C["accent"], 0.45))
        if not self._timer.isActive():
            return
        x = (self._clock.elapsed() % self.PERIOD) / self.PERIOD * (w * 1.4) - w * 0.2
        band = QLinearGradient(x - w * 0.15, 0, x + w * 0.15, 0)
        band.setColorAt(0, _alpha(C["accent_hi"], 0))
        band.setColorAt(0.5, _alpha("#ffffff", 0.9))
        band.setColorAt(1, _alpha(C["accent_hi"], 0))
        p.fillRect(QRectF(x - w * 0.15, 0, w * 0.3, 2), band)


class Toast(QFrame):
    """A short message that slides up in the bottom-right corner and fades away. Click to dismiss."""

    def __init__(self, window: QWidget) -> None:
        super().__init__(window, objectName="toast")
        self._window = window
        self.dot = QLabel("●", objectName="toastDot")
        self.text = QLabel(objectName="toastText", wordWrap=True)
        self.text.setMaximumWidth(340)
        layout = QHBoxLayout(self)
        layout.setContentsMargins(12, 9, 14, 9)
        layout.setSpacing(8)
        layout.addWidget(self.dot, 0, Qt.AlignmentFlag.AlignTop)
        layout.addWidget(self.text)
        self._fade = QGraphicsOpacityEffect(self)
        self.setGraphicsEffect(self._fade)
        self._anims: list[QPropertyAnimation] = []
        self._hide_timer = QTimer(self, singleShot=True, timeout=self._out)
        self.hide()

    def show_message(self, text: str, kind: str = "info", ms: int = 3000) -> None:
        self.setProperty("kind", kind)
        for w in (self, self.dot):
            w.style().unpolish(w)
            w.style().polish(w)
        self.text.setText(text)
        self.text.setFixedWidth(min(340, self.text.fontMetrics().horizontalAdvance(text) + 6))  # wrap only long ones
        self.adjustSize()
        end = self._corner()
        self._animate(end + QPoint(0, 14), end, 0.0, 1.0, 220)
        self.show()
        self.raise_()
        self._hide_timer.start(ms)

    def _corner(self) -> QPoint:
        bar = self._window.statusBar().height() if hasattr(self._window, "statusBar") else 0
        return QPoint(self._window.width() - self.width() - 18, self._window.height() - bar - self.height() - 14)

    def _animate(self, start: QPoint, end: QPoint, a: float, b: float, ms: int, done=None) -> None:
        for anim in self._anims:
            anim.stop()
        move = QPropertyAnimation(self, b"pos", self, duration=ms, startValue=start, endValue=end,
                                  easingCurve=QEasingCurve.Type.OutCubic)
        fade = QPropertyAnimation(self._fade, b"opacity", self, duration=ms, startValue=a, endValue=b)
        if done:
            fade.finished.connect(done)
        self._anims = [move, fade]
        move.start()
        fade.start()

    def _out(self) -> None:
        here = self.pos()
        self._animate(here, here + QPoint(0, 8), self._fade.opacity(), 0.0, 260, self.hide)

    def mousePressEvent(self, _) -> None:
        self._hide_timer.stop()
        self._out()


class EdgeFlash(QWidget):
    """A red glow around the window's inner edge that fades out: the macro was stopped."""

    def __init__(self, window: QWidget) -> None:
        super().__init__(window)
        self.setAttribute(Qt.WidgetAttribute.WA_TransparentForMouseEvents)
        self._level = 0.0
        self._clock = QElapsedTimer()
        self._timer = QTimer(self, interval=16, timeout=self._tick)
        self.hide()

    def flash(self, color: str | None = None, ms: int = 750) -> None:
        self._color, self._ms = color or C["err"], ms
        self.setGeometry(self.parentWidget().rect())
        self._clock.start()
        self._timer.start()
        self.show()
        self.raise_()

    def _tick(self) -> None:
        t = self._clock.elapsed() / self._ms
        if t >= 1:
            self._timer.stop()
            self.hide()
            return
        self._level = QEasingCurve(QEasingCurve.Type.OutQuad).valueForProgress(1 - t)
        self.update()

    def paintEvent(self, _) -> None:
        p = QPainter(self)
        w, h, d = self.width(), self.height(), 70
        for x0, y0, x1, y1, rect in ((0, 0, 0, d, QRectF(0, 0, w, d)), (0, h, 0, h - d, QRectF(0, h - d, w, d)),
                                     (0, 0, d, 0, QRectF(0, 0, d, h)), (w, 0, w - d, 0, QRectF(w - d, 0, d, h))):
            g = QLinearGradient(x0, y0, x1, y1)
            g.setColorAt(0, _alpha(self._color, 0.55 * self._level))
            g.setColorAt(1, _alpha(self._color, 0))
            p.fillRect(rect, g)


class Effects(QObject):
    """Wires the effects to engine events. animate=False keeps the information but drops the motion."""

    def __init__(self, window, engine, bus, animate: bool = True) -> None:
        super().__init__(window)
        self._engine = engine
        self._animate = animate
        self.glow = RunGlow(window.statusBar(), animate)
        self.toast = Toast(window)
        self.flash = EdgeFlash(window)
        bus.subscribe("engine.state", self._state)
        bus.subscribe("engine.finished", self._finished)

    def _state(self, state: str) -> None:
        self.glow.set_state(state)
        if state == STOPPING and self._animate and not self._engine.restarting:
            self.flash.flash()

    def _finished(self, r: dict) -> None:
        seconds = f"{r.get('elapsed', 0):.1f} s"
        if r["result"] == "completed":
            self.toast.show_message(f"Macro finished · {seconds}", "ok")
        elif r["result"] == "stopped":
            if self._engine.state != IDLE:  # Restart: the next run is already going
                self.toast.show_message("Restarted from the beginning", "info", 1800)
            else:
                self.toast.show_message(f"Macro stopped after {seconds} · held keys and buttons released", "warn")
        else:
            error = (r.get("error") or "unknown error").splitlines()[0]
            self.toast.show_message(f"Macro failed: {error[:140]}", "err", 5000)
