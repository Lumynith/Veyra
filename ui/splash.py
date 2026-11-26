"""Startup animation: the Veyra emblem and wordmark animate in, then a progress bar follows the real loading
steps, and the splash fades out as the main window fades in. Click or press a key to skip the intro."""
from __future__ import annotations

import math

from PySide6.QtCore import QElapsedTimer, QEasingCurve, QEventLoop, QPointF, QPropertyAnimation, QRectF, Qt, QTimer
from PySide6.QtGui import (QColor, QFont, QFontMetricsF, QGuiApplication, QLinearGradient, QPainter, QPainterPath,
                           QPen, QRadialGradient)
from PySide6.QtWidgets import QApplication, QWidget

from ui.theme import C

W, H = 560, 340
INTRO_MS = 1150  # emblem + wordmark; loading starts after this
WORD = "VEYRA"
_OUT_BACK = QEasingCurve(QEasingCurve.Type.OutBack)
_OUT_CUBIC = QEasingCurve(QEasingCurve.Type.OutCubic)


def _phase(t: float, start: float, length: float, curve: QEasingCurve = _OUT_CUBIC) -> float:
    """0 before start, eased 0..1 over length ms, 1 after."""
    return curve.valueForProgress(min(1.0, max(0.0, (t - start) / length)))


def _alpha(color: str, a: float) -> QColor:
    c = QColor(color)
    c.setAlphaF(max(0.0, min(1.0, a)))
    return c


def star_path(r: float) -> QPainterPath:
    """Four-pointed star with curved sides, centred on (0, 0) — the Veyra emblem."""
    pts = [QPointF(0, -r), QPointF(r, 0), QPointF(0, r), QPointF(-r, 0)]
    pinch = r * 0.16
    path = QPainterPath(pts[0])
    for a, b in zip(pts, pts[1:] + pts[:1]):
        mid = QPointF((a.x() + b.x()) / 2, (a.y() + b.y()) / 2)
        path.quadTo(mid * (pinch / max(1e-6, math.hypot(mid.x(), mid.y()))), b)
    return path


class Splash(QWidget):
    def __init__(self) -> None:
        super().__init__(None, Qt.WindowType.SplashScreen | Qt.WindowType.FramelessWindowHint
                         | Qt.WindowType.WindowStaysOnTopHint)
        self.setAttribute(Qt.WidgetAttribute.WA_TranslucentBackground)
        self.setFixedSize(W, H)
        screen = QGuiApplication.primaryScreen().availableGeometry()
        self.move(screen.center().x() - W // 2, screen.center().y() - H // 2)
        self._clock = QElapsedTimer()
        self._skip = 0.0  # ms added when the user skips the intro
        self._target = 0.0  # loading progress 0..1
        self._shown = 0.0  # eased towards _target each frame
        self._status = ""
        self._frame = QTimer(self, interval=16, timeout=self._tick)
        self._word_font = QFont("Segoe UI Variable Display", 30, QFont.Weight.DemiBold)
        self._small = QFont("Segoe UI Variable Text", 9)

    # -- driving it --------------------------------------------------------------------------

    @property
    def t(self) -> float:
        return self._clock.elapsed() + self._skip

    def show(self) -> None:
        self._clock.start()
        self._frame.start()
        super().show()
        QApplication.processEvents()

    def play_intro(self) -> None:
        """Run the intro on its own (nothing else is loading yet), unless skipped."""
        loop = QEventLoop()
        check = QTimer(interval=15, timeout=lambda: self.t >= INTRO_MS and loop.quit())
        check.start()
        loop.exec()
        check.stop()

    def step(self, text: str, fraction: float) -> None:
        """A loading step started; called from Application between its stages."""
        self._status, self._target = text, fraction
        self._tick()
        self.repaint()  # the next stage blocks the event loop, so draw now
        QApplication.processEvents()

    def finish(self, window: QWidget) -> None:
        """Complete the bar, then cross-fade into the main window."""
        self._status, self._target = "Ready", 1.0
        loop = QEventLoop()
        settle = QTimer(interval=15, timeout=lambda: self._shown > 0.995 and loop.quit())
        settle.start()
        QTimer.singleShot(600, loop.quit)  # never wait long for the bar
        loop.exec()
        settle.stop()
        window.setWindowOpacity(0.0)
        window.show()
        out = QPropertyAnimation(self, b"windowOpacity", duration=320, startValue=1.0, endValue=0.0)
        fade = QPropertyAnimation(window, b"windowOpacity", duration=420, startValue=0.0, endValue=1.0,
                                  easingCurve=QEasingCurve.Type.OutCubic)
        fade.finished.connect(loop.quit)
        out.start()
        fade.start()
        loop.exec()
        window.setWindowOpacity(1.0)
        self._frame.stop()
        self.close()

    def _tick(self) -> None:
        self._shown += (self._target - self._shown) * 0.18
        self.update()

    def mousePressEvent(self, _) -> None:
        self._skip_intro()

    def keyPressEvent(self, _) -> None:
        self._skip_intro()

    def _skip_intro(self) -> None:
        self._skip = max(self._skip, INTRO_MS - self._clock.elapsed())

    # -- drawing ----------------------------------------------------------------------------------

    def paintEvent(self, _) -> None:
        t = self.t
        p = QPainter(self)
        p.setRenderHint(QPainter.RenderHint.Antialiasing)
        appear = _phase(t, 0, 280)
        p.setOpacity(appear)
        card = QRectF(10, 10, W - 20, H - 20)
        grow = 0.965 + 0.035 * appear
        p.translate(W / 2, H / 2)
        p.scale(grow, grow)
        p.translate(-W / 2, -H / 2)
        self._card(p, card)
        cx, cy = W / 2, 116
        self._glow(p, cx, cy, t)
        self._sparks(p, cx, cy, t)
        self._emblem(p, cx, cy, t)
        self._wordmark(p, cx, 226, t)
        self._footer(p, card, t)

    def _card(self, p: QPainter, card: QRectF) -> None:
        bg = QLinearGradient(card.topLeft(), card.bottomRight())
        bg.setColorAt(0, QColor(C["bg2"]))
        bg.setColorAt(1, QColor(C["bg0"]))
        border = QLinearGradient(card.topLeft(), card.bottomRight())
        border.setColorAt(0, _alpha(C["accent"], 0.55))
        border.setColorAt(0.5, QColor(C["line"]))
        border.setColorAt(1, _alpha(C["accent"], 0.25))
        p.setPen(QPen(border, 1.2))
        p.setBrush(bg)
        p.drawRoundedRect(card, 14, 14)

    def _glow(self, p: QPainter, cx: float, cy: float, t: float) -> None:
        strength = _phase(t, 150, 700) * (0.85 + 0.15 * math.sin(t / 420))
        g = QRadialGradient(QPointF(cx, cy), 120)
        g.setColorAt(0, _alpha(C["accent"], 0.42 * strength))
        g.setColorAt(0.45, _alpha(C["accent"], 0.12 * strength))
        g.setColorAt(1, _alpha(C["accent"], 0))
        p.setPen(Qt.PenStyle.NoPen)
        p.setBrush(g)
        p.drawEllipse(QPointF(cx, cy), 120, 120)

    def _sparks(self, p: QPainter, cx: float, cy: float, t: float) -> None:
        shown = _phase(t, 450, 500)
        if shown <= 0:
            return
        p.setPen(Qt.PenStyle.NoPen)
        for i in range(7):
            angle = t / 900 * (1 if i % 2 else -0.7) + i * (2 * math.pi / 7)
            rx, ry = 74 + (i % 3) * 9, 30 + (i % 2) * 8
            x, y = cx + math.cos(angle) * rx, cy + math.sin(angle) * ry
            front = math.sin(angle) > 0  # dimmer behind the emblem
            size = 1.6 + (i % 3) * 0.7
            p.setBrush(_alpha(C["accent_hi"], shown * (0.9 if front else 0.35)))
            p.drawEllipse(QPointF(x, y), size, size)

    def _emblem(self, p: QPainter, cx: float, cy: float, t: float) -> None:
        grow = _phase(t, 120, 650, _OUT_BACK)
        if grow <= 0:
            return
        ring = _phase(t, 350, 700)
        if ring > 0:  # a ring sweeps around the emblem
            p.setPen(QPen(_alpha(C["accent_hi"], 0.55), 1.4, Qt.PenStyle.SolidLine, Qt.PenCapStyle.RoundCap))
            p.setBrush(Qt.BrushStyle.NoBrush)
            p.drawArc(QRectF(cx - 46, cy - 46, 92, 92), 90 * 16, int(-360 * 16 * ring))
        p.save()
        p.translate(cx, cy)
        p.rotate(-90 * (1 - _phase(t, 120, 800)))
        p.scale(grow, grow)
        star = star_path(36)
        fill = QLinearGradient(QPointF(-36, -36), QPointF(36, 36))
        fill.setColorAt(0, QColor("#ffffff"))
        fill.setColorAt(0.35, QColor(C["accent_hi"]))
        fill.setColorAt(1, QColor(C["accent"]))
        p.setPen(Qt.PenStyle.NoPen)
        p.setBrush(fill)
        p.drawPath(star)
        p.rotate(45)  # a smaller star crossing it, like the icon's compass
        p.setBrush(_alpha("#ffffff", 0.22))
        p.drawPath(star_path(17))
        p.restore()

    def _wordmark(self, p: QPainter, cx: float, baseline: float, t: float) -> None:
        fm = QFontMetricsF(self._word_font)
        spacing = 10 + 14 * (1 - _phase(t, 450, 700))  # letters draw together as they appear
        widths = [fm.horizontalAdvance(ch) for ch in WORD]
        x = cx - (sum(widths) + spacing * (len(WORD) - 1)) / 2
        sweep = _phase(t, 950, 650, QEasingCurve(QEasingCurve.Type.InOutCubic))
        for i, (ch, w) in enumerate(zip(WORD, widths)):
            shown = _phase(t, 450 + i * 70, 380)
            if shown > 0:
                path = QPainterPath()
                path.addText(x, baseline + 10 * (1 - shown), self._word_font, ch)
                brush = QLinearGradient(QPointF(cx - 140, 0), QPointF(cx + 140, 0))
                base = _alpha(C["text"], shown)
                brush.setColorAt(0, base)
                brush.setColorAt(1, base)
                if 0 < sweep < 1:  # a band of light passes over the word once
                    brush.setColorAt(max(0.0, sweep - 0.12), base)
                    brush.setColorAt(sweep, _alpha(C["accent_hi"], shown))
                    brush.setColorAt(min(1.0, sweep + 0.12), base)
                p.setPen(Qt.PenStyle.NoPen)
                p.setBrush(brush)
                p.drawPath(path)
            x += w + spacing
        tag = _phase(t, 800, 400)
        if tag > 0:
            p.setFont(self._small)
            p.setPen(_alpha(C["muted"], tag))
            p.drawText(QRectF(0, baseline + 12, W, 22), Qt.AlignmentFlag.AlignHCenter,
                       "Visual automation for Roblox")

    def _footer(self, p: QPainter, card: QRectF, t: float) -> None:
        shown = _phase(t, 900, 300)
        if shown <= 0:
            return
        track = QRectF(card.left() + 40, card.bottom() - 40, card.width() - 80, 3)
        p.setPen(Qt.PenStyle.NoPen)
        p.setBrush(_alpha(C["line"], shown))
        p.drawRoundedRect(track, 1.5, 1.5)
        if self._shown > 0.001:
            done = QRectF(track.left(), track.top(), track.width() * self._shown, track.height())
            bar = QLinearGradient(track.topLeft(), track.topRight())
            bar.setColorAt(0, _alpha(C["accent"], shown))
            bar.setColorAt(1, _alpha(C["accent_hi"], shown))
            p.setBrush(bar)
            p.drawRoundedRect(done, 1.5, 1.5)
            p.setBrush(_alpha(C["accent_hi"], 0.35 * shown))  # glow at the leading edge
            p.drawEllipse(QPointF(done.right(), done.center().y()), 5, 5)
        p.setFont(self._small)
        p.setPen(_alpha(C["muted"], shown))
        p.drawText(QRectF(track.left(), track.bottom() + 8, track.width(), 18),
                   Qt.AlignmentFlag.AlignLeft, self._status)
        p.setPen(_alpha(C["faint"], shown))
        p.drawText(QRectF(track.left(), track.bottom() + 8, track.width(), 18),
                   Qt.AlignmentFlag.AlignRight, "Esc or click to skip" if self.t < INTRO_MS else "")
