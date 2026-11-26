"""Tutorials: a page listing lessons, a floating guide card that walks through one, and a spotlight
that points at whatever the current step talks about."""
from __future__ import annotations

import logging
import math
import re

from PySide6.QtCore import QEvent, QPoint, QRect, Qt, QTimer, QVariantAnimation, Signal
from PySide6.QtGui import QColor, QPainter, QPen
from PySide6.QtWidgets import (QAbstractButton, QFrame, QHBoxLayout, QLabel, QLineEdit, QPlainTextEdit,
                               QProgressBar, QPushButton, QScrollArea, QTabBar, QToolButton, QTreeWidget,
                               QVBoxLayout, QWidget)

from ui.help.lessons import LESSONS, Lesson, Step
from ui.theme import C, icon

log = logging.getLogger(__name__)


# -- finding what a step points at -----------------------------------------------------------------

def _norm(text: str) -> str:
    return re.sub(r"&(.)", r"\1", text).replace("…", "").strip().lower()  # drop mnemonics and ellipses


def find_target(window, root: QWidget, target: str) -> tuple[QWidget, QRect] | None:
    """The widget (and the part of it) a lesson target names. See ui/help/lessons.py for the syntax."""
    if target.startswith("@"):
        obj = window
        for part in target[1:].split("."):
            obj = getattr(obj, part, None)
        return (obj, obj.rect()) if isinstance(obj, QWidget) and obj.isVisible() else None
    if target.startswith("nav:"):
        root, target = window.sidebar, target[4:]
    label = target.strip().lower()  # exact matches first, then prefixes of tooltips, placeholders and labels
    widgets = [w for w in root.findChildren(QWidget) if w.isVisible()]
    for w in widgets:
        if isinstance(w, QAbstractButton) and w.text() and _norm(w.text()) == label:
            return w, w.rect()
    for w in widgets:
        if isinstance(w, QTabBar):
            for i in range(w.count()):
                if _norm(w.tabText(i)) == label:
                    return w, w.tabRect(i)
    for w in widgets:
        if isinstance(w, QTreeWidget):
            items = [i for i in w.findItems(target, Qt.MatchFlag.MatchExactly | Qt.MatchFlag.MatchRecursive)
                     if not i.isHidden() and i.parent() is not None]  # blocks, not their category rows
            if items:
                item = items[0]
                item.parent().setExpanded(True)
                w.scrollToItem(item)
                return w.viewport(), w.visualItemRect(item)
    for w in widgets:
        if isinstance(w, QLabel) and w.objectName() == "panelTitle" and _norm(w.text()) == label and w.parentWidget():
            return w.parentWidget(), w.parentWidget().rect()
    for w in widgets:
        if isinstance(w, QAbstractButton) and w.toolTip().lower().startswith(label):
            return w, w.rect()
    for w in widgets:
        if isinstance(w, (QLineEdit, QPlainTextEdit)) and w.placeholderText().lower().startswith(label):
            return w, w.rect()
    for w in widgets:
        if isinstance(w, QLabel) and _norm(w.text()).startswith(label):
            return w, w.rect()
    return None


class Spotlight(QWidget):
    """A pulsing ring drawn over the main window around one widget. Clicks pass straight through."""

    def __init__(self, window: QWidget) -> None:
        super().__init__(window)
        self.setAttribute(Qt.WidgetAttribute.WA_TransparentForMouseEvents)
        self._rect = QRect()
        self._t = 0.0
        self._anim = QVariantAnimation(self, startValue=0.0, endValue=1.0, duration=2600)
        self._anim.valueChanged.connect(self._tick)
        self._anim.finished.connect(self.hide)
        self.hide()

    def point_at(self, widget: QWidget, rect: QRect) -> None:
        window = self.parentWidget()
        self.setGeometry(window.rect())
        self._rect = QRect(widget.mapTo(window, rect.topLeft()), rect.size()).adjusted(-5, -5, 5, 5)
        self.show()
        self.raise_()
        self._anim.stop()
        self._anim.start()

    def _tick(self, t) -> None:
        self._t = t
        self.update()

    def paintEvent(self, _) -> None:
        p = QPainter(self)
        p.setRenderHint(QPainter.RenderHint.Antialiasing)
        pulse = abs(math.sin(self._t * math.pi * 3))  # three pulses
        fade = min(1.0, (1 - self._t) * 4)  # fade out over the last quarter
        glow = QColor(C["accent_hi"])
        glow.setAlphaF(0.35 * pulse * fade)
        p.setPen(QPen(glow, 10))
        p.drawRoundedRect(self._rect, 8, 8)
        ring = QColor(C["accent_hi"])
        ring.setAlphaF(fade)
        p.setPen(QPen(ring, 3))
        p.drawRoundedRect(self._rect, 6, 6)


# -- the floating guide card -------------------------------------------------------------------------

class GuideCard(QWidget):
    """Walks through one lesson. A small window of its own, so it stays above the app (and the docked
    Roblox window) while the user follows the steps on other pages. Drag it anywhere."""

    finished = Signal(str)  # lesson id

    def __init__(self, window) -> None:
        super().__init__(window, Qt.WindowType.Tool | Qt.WindowType.FramelessWindowHint
                         | Qt.WindowType.WindowDoesNotAcceptFocus)  # keyboard focus stays in the app
        self.setAttribute(Qt.WidgetAttribute.WA_TranslucentBackground)
        self.setAttribute(Qt.WidgetAttribute.WA_ShowWithoutActivating)
        self._window = window
        self.spotlight = Spotlight(window)
        self.lesson: Lesson | None = None
        self.index = 0
        self._drag: QPoint | None = None
        self._offset: QPoint | None = None  # where the user dragged it, relative to the main window
        self._left = False  # which bottom corner it sits in while not dragged
        self._pending: tuple[QWidget, QRect] | None = None
        self._point = QTimer(self, singleShot=True, interval=30, timeout=self._point_now)

        frame = QFrame(objectName="guideCard")
        self.heading = QLabel(objectName="guideHeading")
        self.count = QLabel(objectName="muted")
        close = QToolButton(text="✕", toolTip="Close the lesson (start it again any time from the Tutorials page)")
        close.clicked.connect(self.close_lesson)
        self.progress = QProgressBar(objectName="guideProgress", textVisible=False, maximumHeight=4)
        self.text = QLabel(objectName="guideText", wordWrap=True, textFormat=Qt.TextFormat.RichText)
        self.tip = QLabel(objectName="guideTip", wordWrap=True, textFormat=Qt.TextFormat.RichText)
        self.show_btn = QPushButton(icon("target"), "Show me")
        self.show_btn.setToolTip("Highlight what this step is talking about")
        self.show_btn.clicked.connect(self.show_me)
        self.back_btn = QPushButton("Back")
        self.back_btn.clicked.connect(lambda: self.go(self.index - 1))
        self.next_btn = QPushButton("Next", objectName="primary")
        self.next_btn.clicked.connect(self.next)
        for b in (self.show_btn, self.back_btn, self.next_btn, close):
            b.setFocusPolicy(Qt.FocusPolicy.NoFocus)

        top = QHBoxLayout()
        top.addWidget(self.heading, 1)
        top.addWidget(self.count)
        top.addWidget(close)
        buttons = QHBoxLayout()
        buttons.addWidget(self.show_btn)
        buttons.addStretch(1)
        buttons.addWidget(self.back_btn)
        buttons.addWidget(self.next_btn)
        inner = QVBoxLayout(frame)
        inner.setContentsMargins(14, 10, 12, 12)
        inner.setSpacing(8)
        inner.addLayout(top)
        inner.addWidget(self.progress)
        inner.addWidget(self.text)
        inner.addWidget(self.tip)
        inner.addLayout(buttons)
        outer = QVBoxLayout(self)
        outer.setContentsMargins(0, 0, 0, 0)
        outer.addWidget(frame)
        self.setFixedWidth(380)
        window.installEventFilter(self)

    # -- lesson flow ---------------------------------------------------------

    def start(self, lesson: Lesson) -> None:
        self.lesson = lesson
        self.heading.setText(lesson.title)
        self.progress.setRange(0, len(lesson.steps))
        if not self.isVisible():
            self._offset, self._left = None, False  # a fresh lesson starts in the default corner
        self.go(0)

    @property
    def step(self) -> Step:
        return self.lesson.steps[self.index]

    def go(self, index: int) -> None:
        if self.lesson is None or not 0 <= index < len(self.lesson.steps):
            return
        self.index = index
        last = index == len(self.lesson.steps) - 1
        step = self.step
        self.count.setText(f"Step {index + 1} of {len(self.lesson.steps)}")
        self.progress.setValue(index + 1)
        self.text.setText(step.text)
        self.tip.setText(f"💡 {step.tip}")
        self.tip.setVisible(bool(step.tip))
        self.show_btn.setVisible(bool(step.page or step.target))
        self.back_btn.setEnabled(index > 0)
        self.next_btn.setText("Finish ✓" if last else "Next")
        self.adjustSize()
        self._place()
        self.show()
        if step.page or step.target:
            self.show_me()  # each step opens the right page and points at its subject

    def next(self) -> None:
        if self.index < len(self.lesson.steps) - 1:
            self.go(self.index + 1)
        else:
            lesson = self.lesson
            self.close_lesson()
            self.finished.emit(lesson.id)

    def close_lesson(self) -> None:
        self._point.stop()
        self.spotlight.hide()
        self.hide()
        self.lesson = None

    def show_me(self) -> bool:
        """Open the step's page and highlight its target. False when the target is not on screen."""
        step = self.step
        window = self._window
        if step.page:
            window.sidebar.select(window.sidebar.index_of(step.page))
        if not step.target:
            return True
        root = window.pages.currentWidget() if step.page else window
        found = find_target(window, root, step.target)
        if found is None:
            log.warning("Tutorial target not found: %s (page %s)", step.target, step.page or "-")
            return False
        widget, rect = found
        for parent in _ancestors(widget):
            if isinstance(parent, QScrollArea):
                parent.ensureWidgetVisible(widget)
        target = QRect(widget.mapToGlobal(rect.topLeft()), rect.size()).adjusted(-12, -12, 12, 12)
        if self._offset is None and target.intersects(self.geometry()):  # don't cover what we point at
            other = QRect(self._corner(not self._left), self.size())
            if not target.intersects(other):
                self._left = not self._left
                self._place()
        self._pending = (widget, rect)
        self._point.start()  # after the page has laid out; a newer step replaces a pending one
        return True

    def _point_now(self) -> None:
        if self._pending and self.isVisible():
            self.spotlight.point_at(*self._pending)
        self._pending = None

    # -- position: a corner of the main window by default; dragging moves it -----------------

    def _corner(self, left: bool) -> QPoint:
        window = self._window
        bottom = window.mapToGlobal(window.rect().bottomLeft()).y() - self.height() - 40
        if left:  # just right of the sidebar
            return QPoint(window.mapToGlobal(QPoint(window.sidebar.width() + 16, 0)).x(), bottom)
        return QPoint(window.mapToGlobal(window.rect().topRight()).x() - self.width() - 24, bottom)

    def _place(self) -> None:
        if self._offset is None:
            self.move(self._corner(self._left))
        else:
            self.move(self._window.pos() + self._offset)

    def eventFilter(self, obj, e) -> bool:
        if obj is self._window and self.isVisible():
            if e.type() in (QEvent.Type.Move, QEvent.Type.Resize):
                self._place()
            elif e.type() == QEvent.Type.WindowStateChange and self._window.isMinimized():
                self.spotlight.hide()
        return False

    def mousePressEvent(self, e) -> None:
        if e.button() == Qt.MouseButton.LeftButton:
            self._drag = e.globalPosition().toPoint() - self.pos()

    def mouseMoveEvent(self, e) -> None:
        if self._drag is not None:
            self.move(e.globalPosition().toPoint() - self._drag)
            self._offset = self.pos() - self._window.pos()

    def mouseReleaseEvent(self, _) -> None:
        self._drag = None


def _ancestors(w: QWidget):
    while (w := w.parentWidget()) is not None:
        yield w


# -- the page -------------------------------------------------------------------------------------------

class _LessonCard(QFrame):
    clicked = Signal()

    def __init__(self, number: int, lesson: Lesson, done: bool, is_next: bool) -> None:
        super().__init__(objectName="tutorialCardNext" if is_next else "tutorialCard")
        self.setCursor(Qt.CursorShape.PointingHandCursor)
        self.setFocusPolicy(Qt.FocusPolicy.StrongFocus)
        self.setToolTip(f"Start “{lesson.title}”")
        badge = QLabel("✓" if done else str(number), objectName="tutorialBadgeDone" if done else "tutorialBadge",
                       alignment=Qt.AlignmentFlag.AlignCenter)
        badge.setFixedSize(26, 26)
        title = QLabel(lesson.title, objectName="tutorialTitle", wordWrap=True)
        summary = QLabel(lesson.summary, objectName="muted", wordWrap=True)
        minutes = QLabel(f"{lesson.minutes} min" + ("\nStart here" if is_next else ""), objectName="muted",
                         alignment=Qt.AlignmentFlag.AlignRight | Qt.AlignmentFlag.AlignTop)
        text = QVBoxLayout()
        text.setSpacing(2)
        text.addWidget(title)
        text.addWidget(summary)
        row = QHBoxLayout(self)
        row.setContentsMargins(10, 8, 10, 8)
        row.addWidget(badge, 0, Qt.AlignmentFlag.AlignTop)
        row.addLayout(text, 1)
        row.addWidget(minutes)

    def mouseReleaseEvent(self, e) -> None:
        if e.button() == Qt.MouseButton.LeftButton and self.rect().contains(e.position().toPoint()):
            self.clicked.emit()

    def keyPressEvent(self, e) -> None:
        if e.key() in (Qt.Key.Key_Return, Qt.Key.Key_Enter, Qt.Key.Key_Space):
            self.clicked.emit()
        else:
            super().keyPressEvent(e)


class TutorialPage(QWidget):
    def __init__(self, window, settings) -> None:
        super().__init__()
        self._settings = settings
        self.guide = GuideCard(window)
        self.guide.finished.connect(self._finished)
        heading = QLabel("Tutorials", objectName="emptyTitle")
        intro = QLabel("Short, step-by-step lessons. No coding needed. Pick a lesson and a small guide appears "
                       "that you can drag anywhere. It opens the right page and points at each thing as you go.",
                       objectName="muted", wordWrap=True)
        self.progress_label = QLabel(objectName="muted")
        self._list = QVBoxLayout()
        self._list.setSpacing(6)
        body = QWidget()
        bl = QVBoxLayout(body)
        bl.setContentsMargins(0, 0, 4, 0)
        bl.addLayout(self._list)
        bl.addStretch(1)
        scroll = QScrollArea(widgetResizable=True, frameShape=QFrame.Shape.NoFrame)
        scroll.setWidget(body)
        layout = QVBoxLayout(self)
        layout.setContentsMargins(12, 12, 12, 12)
        layout.setSpacing(8)
        layout.addWidget(heading)
        layout.addWidget(intro)
        layout.addWidget(self.progress_label)
        layout.addWidget(scroll, 1)
        self.refresh()

    def done(self) -> list[str]:
        return list(self._settings.get("tutorials.done", []) or [])

    def refresh(self) -> None:
        while self._list.count():
            self._list.takeAt(0).widget().deleteLater()
        done = set(self.done())
        upcoming = next((lesson.id for lesson in LESSONS if lesson.id not in done), None)
        for n, lesson in enumerate(LESSONS, 1):
            card = _LessonCard(n, lesson, lesson.id in done, lesson.id == upcoming)
            card.clicked.connect(lambda lesson=lesson: self.guide.start(lesson))
            self._list.addWidget(card)
        finished = sum(lesson.id in done for lesson in LESSONS)
        self.progress_label.setText(f"{finished} of {len(LESSONS)} lessons done"
                                    + (" — nicely done! 🎉" if finished == len(LESSONS) else ""))

    def start(self, lesson_id: str) -> None:
        self.guide.start(next(lesson for lesson in LESSONS if lesson.id == lesson_id))

    def _finished(self, lesson_id: str) -> None:
        if lesson_id not in self.done():
            self._settings.set("tutorials.done", self.done() + [lesson_id])
        self.refresh()
        window = self.guide._window
        window.sidebar.select(window.pages.indexOf(self))
