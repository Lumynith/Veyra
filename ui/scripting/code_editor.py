"""Python code editor: line numbers, syntax highlighting, autocomplete, auto-indent and live error marks."""
from __future__ import annotations

import builtins
import keyword
import re
from typing import Callable

from PySide6.QtCore import QRect, QSize, QStringListModel, Qt, QTimer, Signal
from PySide6.QtGui import (QColor, QFont, QFontDatabase, QPainter, QSyntaxHighlighter, QTextCharFormat, QTextCursor,
                           QTextFormat)
from PySide6.QtWidgets import QCompleter, QPlainTextEdit, QTextEdit, QToolTip, QWidget

from ui.theme import C

Problem = tuple[int, int, str]  # line (1-based), column, message


def _fmt(color: str, bold: bool = False, italic: bool = False) -> QTextCharFormat:
    f = QTextCharFormat()
    f.setForeground(QColor(color))
    if bold:
        f.setFontWeight(QFont.Weight.Bold)
    f.setFontItalic(italic)
    return f


class PythonHighlighter(QSyntaxHighlighter):
    def __init__(self, document, api_names: list[str]) -> None:
        super().__init__(document)
        kw = _fmt(C["accent_hi"], bold=True)
        words = lambda names: re.compile(r"\b(?:" + "|".join(map(re.escape, names)) + r")\b")  # noqa: E731
        self.rules = [  # (pattern, format, group to colour)
            (words(keyword.kwlist + ["self"]), kw, 0),
            (words([n for n in dir(builtins) if not n.startswith("_")]), _fmt("#6cb6ff"), 0),
            (words(api_names or ["__none__"]), _fmt("#e3b341"), 0),
            (re.compile(r"\b\d+(?:\.\d+)?\b"), _fmt("#f0883e"), 0),
            (re.compile(r"@\w+"), _fmt("#e3b341"), 0),
            (re.compile(r"\b(?:def|class)\s+(\w+)"), _fmt("#d2a8ff"), 1),
        ]
        self.string = _fmt("#7ee787")
        self.comment = _fmt(C["muted"], italic=True)
        self.inline = re.compile(r"""(?P<s>[rbfuRBFU]{0,2}("[^"\\\n]*(\\.[^"\\\n]*)*"|'[^'\\\n]*(\\.[^'\\\n]*)*'))"""
                                 r"""|(?P<c>#.*)""")

    def highlightBlock(self, text: str) -> None:
        for rx, fmt, group in self.rules:
            for m in rx.finditer(text):
                start, end = m.span(group)
                self.setFormat(start, end - start, fmt)
        # Triple-quoted strings span lines: block state 1 = inside \"\"\", 2 = inside '''.
        start = 0
        state = self.previousBlockState()
        self.setCurrentBlockState(0)
        if state in (1, 2):
            quote = '"""' if state == 1 else "'''"
            end = text.find(quote)
            if end < 0:
                self.setFormat(0, len(text), self.string)
                self.setCurrentBlockState(state)
                return
            self.setFormat(0, end + 3, self.string)
            start = end + 3
        pos = start
        while pos < len(text):
            triple = re.compile(r"(\"\"\"|''')").search(text, pos)
            m = self.inline.search(text, pos)
            if triple and (not m or triple.start() <= m.start()):
                quote = triple.group(1)
                end = text.find(quote, triple.end())
                if end < 0:
                    self.setFormat(triple.start(), len(text) - triple.start(), self.string)
                    self.setCurrentBlockState(1 if quote == '"""' else 2)
                    return
                self.setFormat(triple.start(), end + 3 - triple.start(), self.string)
                pos = end + 3
            elif m:
                self.setFormat(m.start(), m.end() - m.start(), self.comment if m.group("c") else self.string)
                pos = m.end()
            else:
                break


class _Gutter(QWidget):
    def __init__(self, editor: "CodeEditor") -> None:
        super().__init__(editor)
        self.editor = editor
        self.setMouseTracking(True)

    def sizeHint(self) -> QSize:
        return QSize(self.editor.gutter_width(), 0)

    def paintEvent(self, e) -> None:
        self.editor.paint_gutter(e)

    def mouseMoveEvent(self, e) -> None:
        msg = self.editor.problem_at(self.editor.cursorForPosition(e.position().toPoint()).blockNumber() + 1)
        if msg:
            QToolTip.showText(e.globalPosition().toPoint(), msg, self)
        else:
            QToolTip.hideText()


class CodeEditor(QPlainTextEdit):
    problems_changed = Signal(list)

    def __init__(self, api_names: list[str] = (), validator: Callable[[str], list[Problem]] | None = None,
                 parent=None) -> None:
        super().__init__(parent)
        font = QFontDatabase.systemFont(QFontDatabase.SystemFont.FixedFont)
        font.setFamily("Consolas")
        font.setPointSize(10)
        self.setFont(font)
        self.setTabStopDistance(self.fontMetrics().horizontalAdvance(" ") * 4)
        self.setLineWrapMode(QPlainTextEdit.LineWrapMode.NoWrap)
        self.setObjectName("codeEditor")
        self.problems: list[Problem] = []
        self._runtime_line: int | None = None
        self._validator = validator
        self.highlighter = PythonHighlighter(self.document(), list(api_names))

        self._gutter = _Gutter(self)
        self.blockCountChanged.connect(lambda _: self.setViewportMargins(self.gutter_width(), 0, 0, 0))
        self.updateRequest.connect(self._scroll_gutter)
        self.cursorPositionChanged.connect(self._decorate)
        self.setViewportMargins(self.gutter_width(), 0, 0, 0)

        self._base_words = sorted(set(keyword.kwlist) | set(api_names)
                                  | {n for n in dir(builtins) if not n.startswith("_")})
        self._model = QStringListModel(self._base_words)
        self.completer = QCompleter(self._model, self)
        self.completer.setWidget(self)
        self.completer.setCaseSensitivity(Qt.CaseSensitivity.CaseInsensitive)
        self.completer.setCompletionMode(QCompleter.CompletionMode.PopupCompletion)
        self.completer.activated.connect(self._insert_completion)

        self._check = QTimer(self, singleShot=True, interval=400)
        self._check.timeout.connect(self.validate_now)
        self.textChanged.connect(self._text_changed)
        self._decorate()

    # -- gutter ----------------------------------------------------------------

    def gutter_width(self) -> int:
        return 18 + self.fontMetrics().horizontalAdvance("9") * max(3, len(str(self.blockCount())))

    def _scroll_gutter(self, rect: QRect, dy: int) -> None:
        if dy:
            self._gutter.scroll(0, dy)
        else:
            self._gutter.update(0, rect.y(), self._gutter.width(), rect.height())

    def resizeEvent(self, e) -> None:
        super().resizeEvent(e)
        r = self.contentsRect()
        self._gutter.setGeometry(QRect(r.left(), r.top(), self.gutter_width(), r.height()))

    def paint_gutter(self, e) -> None:
        p = QPainter(self._gutter)
        p.fillRect(e.rect(), QColor(C["bg1"]))
        bad = {line for line, _, _ in self.problems} | ({self._runtime_line} if self._runtime_line else set())
        current = self.textCursor().blockNumber()
        block = self.firstVisibleBlock()
        top = round(self.blockBoundingGeometry(block).translated(self.contentOffset()).top())
        h = self.fontMetrics().height()
        while block.isValid() and top <= e.rect().bottom():
            if block.isVisible():
                n = block.blockNumber() + 1
                if n in bad:
                    p.setBrush(QColor(C["err"]))
                    p.setPen(Qt.PenStyle.NoPen)
                    p.drawEllipse(4, top + h // 2 - 3, 6, 6)
                p.setPen(QColor(C["text"] if n - 1 == current else C["faint"]))
                p.drawText(0, top, self._gutter.width() - 6, h, Qt.AlignmentFlag.AlignRight, str(n))
            top += round(self.blockBoundingRect(block).height())
            block = block.next()

    # -- problems ------------------------------------------------------------

    def problem_at(self, line: int) -> str:
        msgs = [m for ln, _, m in self.problems if ln == line]
        return "\n".join(msgs)

    def set_problems(self, problems: list[Problem]) -> None:
        self.problems = problems
        self._decorate()
        self._gutter.update()
        self.problems_changed.emit(problems)

    def set_runtime_error(self, line: int | None) -> None:
        """Mark the line a run failed on (cleared on the next edit)."""
        self._runtime_line = line
        self._decorate()
        self._gutter.update()
        if line:
            cursor = QTextCursor(self.document().findBlockByNumber(line - 1))
            self.setTextCursor(cursor)
            self.centerCursor()

    def validate_now(self) -> None:
        if self._validator:
            self.set_problems(self._validator(self.toPlainText()))

    def _text_changed(self) -> None:
        if self._runtime_line:
            self._runtime_line = None
            self._decorate()
        self._check.start()

    def _decorate(self) -> None:
        selections = []
        line = QTextEdit.ExtraSelection()
        line.format.setBackground(QColor(C["bg2"]))
        line.format.setProperty(QTextFormat.Property.FullWidthSelection, True)
        line.cursor = self.textCursor()
        line.cursor.clearSelection()
        selections.append(line)
        bad = [(ln, col) for ln, col, _ in self.problems] + ([(self._runtime_line, 0)] if self._runtime_line else [])
        for ln, col in bad:
            block = self.document().findBlockByNumber(ln - 1)
            if not block.isValid():
                continue
            sel = QTextEdit.ExtraSelection()
            sel.format.setUnderlineStyle(QTextCharFormat.UnderlineStyle.WaveUnderline)
            sel.format.setUnderlineColor(QColor(C["err"]))
            bg = QColor(C["err"])
            bg.setAlpha(28)
            sel.format.setBackground(bg)
            sel.cursor = QTextCursor(block)
            sel.cursor.movePosition(QTextCursor.MoveOperation.EndOfBlock, QTextCursor.MoveMode.KeepAnchor)
            selections.append(sel)
        self.setExtraSelections(selections)

    # -- typing ----------------------------------------------------------------

    def _word_before_cursor(self) -> str:
        cursor = self.textCursor()
        text = cursor.block().text()[:cursor.positionInBlock()]
        m = re.search(r"[A-Za-z_]\w*$", text)
        return m.group(0) if m else ""

    def _insert_completion(self, word: str) -> None:
        prefix = self.completer.completionPrefix()
        self.textCursor().insertText(word[len(prefix):])

    def _update_words(self) -> None:
        words = set(re.findall(r"[A-Za-z_]\w{2,}", self.toPlainText()))
        self._model.setStringList(sorted(set(self._base_words) | words, key=str.lower))

    def keyPressEvent(self, e) -> None:
        popup = self.completer.popup()
        if popup.isVisible() and e.key() in (Qt.Key.Key_Enter, Qt.Key.Key_Return, Qt.Key.Key_Tab,
                                             Qt.Key.Key_Escape, Qt.Key.Key_Backtab):
            e.ignore()  # the completer handles these
            return
        ctrl_space = e.key() == Qt.Key.Key_Space and e.modifiers() & Qt.KeyboardModifier.ControlModifier
        if e.key() == Qt.Key.Key_Tab and not e.modifiers():
            self.textCursor().insertText("    ")
            return
        if e.key() == Qt.Key.Key_Backtab:
            cursor = self.textCursor()
            cursor.movePosition(QTextCursor.MoveOperation.StartOfBlock)
            spaces = len(cursor.block().text()) - len(cursor.block().text().lstrip(" "))
            for _ in range(min(4, spaces)):
                cursor.deleteChar()
            return
        if e.key() in (Qt.Key.Key_Return, Qt.Key.Key_Enter):
            text = self.textCursor().block().text()[:self.textCursor().positionInBlock()]
            indent = len(text) - len(text.lstrip(" "))
            if text.rstrip().endswith(":"):
                indent += 4
            super().keyPressEvent(e)
            self.textCursor().insertText(" " * indent)
            return
        if not ctrl_space:
            super().keyPressEvent(e)
        prefix = self._word_before_cursor()
        if ctrl_space or (len(prefix) >= 2 and e.text() and (e.text().isalnum() or e.text() == "_")):
            self._update_words()
            self.completer.setCompletionPrefix(prefix)
            if self.completer.completionCount() == 0 or (self.completer.completionCount() == 1
                                                         and self.completer.currentCompletion() == prefix):
                popup.hide()
                return
            popup.setCurrentIndex(self.completer.completionModel().index(0, 0))
            rect = self.cursorRect()
            rect.setWidth(popup.sizeHintForColumn(0) + popup.verticalScrollBar().sizeHint().width() + 16)
            self.completer.complete(rect)
        elif popup.isVisible() and not prefix:
            popup.hide()
