"""Text Detection page: capture, select an area, run OCR, pick words, save reusable text targets."""
from __future__ import annotations

import logging
import time

from PySide6.QtCore import QRect, QRectF, Qt
from PySide6.QtGui import QColor, QPen
from PySide6.QtWidgets import (QAbstractItemView, QCheckBox, QComboBox, QFormLayout, QHBoxLayout, QHeaderView,
                               QInputDialog, QLabel, QLineEdit, QListWidget, QListWidgetItem, QMenu, QMessageBox,
                               QPlainTextEdit, QPushButton, QStackedWidget, QTableWidget,
                               QTableWidgetItem, QVBoxLayout, QWidget)

from ui.theme import C
from ui.vision.capture_dialog import SelectionView
from ui.widgets import AdaptiveSplitter, EmptyState, IconButton, Panel, Spinner
from ui.workers import run_async
from vision.text_detector import MATCH_MODES, find_all_text, full_text, languages

log = logging.getLogger(__name__)


class TextDetectionPage(QWidget):
    def __init__(self, vision, targets, ocr, settings, parent=None) -> None:
        super().__init__(parent)
        self.vision, self.targets, self._ocr, self._settings = vision, targets, ocr, settings
        self.frame = None
        self.source = ""  # "viewport" | "screen": what self.frame shows
        self.words: list = []
        self._offset = (0, 0)  # selection origin the words are relative to
        self._boxes: list = []

        # -- capture (left) ---------------------------------------------
        cap = IconButton("camera", "Capture the Roblox viewport")
        cap.clicked.connect(lambda: self._capture("viewport"))
        scr = IconButton("crop", "Capture the whole screen")
        scr.clicked.connect(lambda: self._capture("screen"))
        self.language = QComboBox(editable=True)
        self.language.setToolTip("OCR language. Blank = the default from Settings")
        self.language.lineEdit().setPlaceholderText("default language")
        self.read_btn = QPushButton("Read text", objectName="primary")
        self.read_btn.setToolTip("Run OCR on the selected area (or the whole capture when nothing is selected)")
        self.read_btn.clicked.connect(self._read)
        self.read_btn.setEnabled(False)
        self.spinner = Spinner()
        self.hint = QLabel("Capture the viewport, drag over the text, then Read text.", objectName="muted",
                           wordWrap=True)
        self.view_stack = QStackedWidget()
        self.view_stack.addWidget(EmptyState("text", "Nothing captured",
                                             "Capture the Roblox viewport, select the area with the text, "
                                             "and read it with OCR."))
        left = QWidget()
        lv = QVBoxLayout(left)
        lv.setContentsMargins(8, 4, 8, 8)
        row = QHBoxLayout()
        row.addWidget(QLabel("Language"))
        row.addWidget(self.language, 1)
        row.addWidget(self.spinner)
        row.addWidget(self.read_btn)
        lv.addLayout(row)
        lv.addWidget(self.view_stack, 1)
        lv.addWidget(self.hint)
        capture_panel = Panel("Capture", left, [cap, scr])

        # -- results + target (right) -----------------------------------
        self.status = QLabel(objectName="muted", wordWrap=True)
        self.table = QTableWidget(0, 3)
        self.table.setHorizontalHeaderLabels(["Text", "Conf.", "Position"])
        self.table.horizontalHeader().setSectionResizeMode(0, QHeaderView.ResizeMode.Stretch)
        for col in (1, 2):
            self.table.horizontalHeader().setSectionResizeMode(col, QHeaderView.ResizeMode.ResizeToContents)
        self.table.verticalHeader().hide()
        self.table.setSelectionBehavior(QAbstractItemView.SelectionBehavior.SelectRows)
        self.table.setEditTriggers(QAbstractItemView.EditTrigger.NoEditTriggers)
        self.table.setToolTip("Select a word (Ctrl/Shift for several) to use it as the target text")
        self.table.itemSelectionChanged.connect(self._words_selected)
        self.full = QPlainTextEdit(readOnly=True, placeholderText="Recognised text appears here")
        self.full.setMaximumHeight(70)

        self.name = QLineEdit(placeholderText="e.g. Play button")
        self.text = QLineEdit(placeholderText="Text to look for")
        self.match = QComboBox()
        self.match.addItems(MATCH_MODES)
        self.match.setCurrentText("exact")
        self.case = QCheckBox("Case sensitive")
        self.limit = QCheckBox("Only search the selected area")
        self.limit.setToolTip("Save the selection as the target's search region (viewport captures only)")
        save = QPushButton("Save target", objectName="primary")
        save.clicked.connect(self._save_target)
        form = QFormLayout()
        form.addRow("Name", self.name)
        form.addRow("Text", self.text)
        form.addRow("Match", self.match)
        form.addRow("", self.case)
        form.addRow("", self.limit)

        self.saved = QListWidget()
        self.saved.setContextMenuPolicy(Qt.ContextMenuPolicy.CustomContextMenu)
        self.saved.customContextMenuRequested.connect(self._target_menu)
        self.saved.itemDoubleClicked.connect(lambda _: self._test_target())
        self.saved.setToolTip("Double-click to test on the viewport · right-click for more")
        self.test_result = QLabel(objectName="muted", wordWrap=True)

        right = QWidget()
        rv = QVBoxLayout(right)
        rv.setContentsMargins(10, 4, 10, 10)
        rv.addWidget(self.status)
        rv.addWidget(self.table, 2)
        rv.addWidget(self.full)
        rv.addWidget(QLabel("NEW TARGET", objectName="panelTitle"))
        rv.addLayout(form)
        rv.addWidget(save)
        rv.addWidget(QLabel("SAVED TARGETS", objectName="panelTitle"))
        rv.addWidget(self.saved, 1)
        rv.addWidget(self.test_result)

        split = AdaptiveSplitter(objectName="textPage", childrenCollapsible=False)
        split.addWidget(capture_panel)
        split.addWidget(Panel("Recognised text", right))
        split.setSizes([320, 260])
        layout = QVBoxLayout(self)
        layout.setContentsMargins(0, 0, 0, 0)
        layout.addWidget(split)
        self._fill_languages()
        self._refresh_targets()

    # -- capture -------------------------------------------------------------

    def _fill_languages(self) -> None:
        get = self._settings.get
        key = (get("ocr.backend", "windows"), get("ocr.tesseract_cmd", ""))
        if key == getattr(self, "_lang_key", None):
            return
        self._lang_key = key
        try:
            langs = languages(get("ocr.backend", "windows"), get("ocr.tesseract_cmd", ""))
        except Exception as e:  # backend missing: still usable with a typed language
            log.warning("Could not list OCR languages: %s", e)
            langs = []
        current = self.language.currentText()
        self.language.clear()
        self.language.addItems([""] + langs)
        self.language.setCurrentText(current)

    def _capture(self, source: str) -> None:
        grab = self.vision.capture_screen if source == "screen" else self.vision.capture_viewport

        def done(result) -> None:
            self.frame, _ = result
            self.source = source
            self._set_frame()

        run_async(grab, done, lambda msg: self._error("Capture failed", msg))

    def _set_frame(self) -> None:
        if self.view_stack.count() > 1:
            old = self.view_stack.widget(1)
            self.view_stack.removeWidget(old)
            old.deleteLater()
        self.view = SelectionView(self.frame)
        self.view.changed.connect(lambda _: self.limit.setEnabled(self.source == "viewport"))
        self.view_stack.addWidget(self.view)
        self.view_stack.setCurrentIndex(1)
        self.view.fit_if_larger()
        self._boxes = []
        self.read_btn.setEnabled(True)
        self.limit.setEnabled(self.source == "viewport")
        self.hint.setText("Drag over the text, then Read text.  Ctrl+wheel zooms.")

    # -- OCR -------------------------------------------------------------------

    def _read(self) -> None:
        if self.frame is None:
            return
        sel = self.view.selection
        if sel.width() < 4 or sel.height() < 4:
            sel = QRect(0, 0, self.frame.shape[1], self.frame.shape[0])
        crop = self.frame[sel.y():sel.y() + sel.height(), sel.x():sel.x() + sel.width()].copy()
        language = self.language.currentText().strip()

        def work():
            ocr = self._ocr(language)
            hits = getattr(ocr, "stats", {}).get("hits", 0)
            t = time.perf_counter()
            words = ocr.words(crop)
            return words, time.perf_counter() - t, getattr(ocr, "stats", {}).get("hits", 0) > hits

        def done(result) -> None:
            self._busy(False)
            self.words, elapsed, cached = result
            self._offset = (sel.x(), sel.y())
            self._show_words()
            self.status.setText(f"{len(self.words)} words in {elapsed * 1000:.0f} ms"
                                f"{' (cached result)' if cached else ''} · "
                                f"area {sel.width()} × {sel.height()} at ({sel.x()}, {sel.y()})")

        self._busy(True)
        run_async(work, done, lambda msg: (self._busy(False), self._error("OCR failed", msg)))

    def _busy(self, on: bool) -> None:
        self.read_btn.setEnabled(not on)
        (self.spinner.start if on else self.spinner.stop)()

    def _show_words(self) -> None:
        ox, oy = self._offset
        self.table.setRowCount(len(self.words))
        for row, w in enumerate(self.words):
            pos = QTableWidgetItem(f"{ox + w.x + w.w // 2}, {oy + w.y + w.h // 2}")
            for col, item in enumerate((QTableWidgetItem(w.text), QTableWidgetItem(f"{w.conf:.2f}"), pos)):
                self.table.setItem(row, col, item)
        self.full.setPlainText(full_text(self.words))
        self._draw_boxes([])

    def _draw_boxes(self, selected: list[int]) -> None:
        scene = self.view.scene()
        for item in self._boxes:
            scene.removeItem(item)
        ox, oy = self._offset
        self._boxes = []
        for i, w in enumerate(self.words):
            on = i in selected
            pen = QPen(QColor(C["accent_hi"] if on else C["ok"]), 2 if on else 1)
            pen.setCosmetic(True)
            self._boxes.append(scene.addRect(QRectF(ox + w.x, oy + w.y, w.w, w.h), pen))

    def _words_selected(self) -> None:
        rows = sorted({i.row() for i in self.table.selectedIndexes()})
        self._draw_boxes(rows)
        if rows:
            text = " ".join(self.words[r].text for r in rows)
            self.text.setText(text)
            if not self.name.text():
                self.name.setText(text)

    # -- targets -----------------------------------------------------------

    def _save_target(self) -> None:
        region = ""
        if self.limit.isChecked() and self.limit.isEnabled() and self.frame is not None:
            r = self.view.selection
            if r.width() >= 4 and r.height() >= 4:
                region = f"{r.x()}, {r.y()}, {r.width()}, {r.height()}"
        try:
            t = self.targets.add(self.name.text(), text=self.text.text().strip(), match=self.match.currentText(),
                                 case_sensitive=self.case.isChecked(), region=region,
                                 language=self.language.currentText().strip())
        except (ValueError, OSError) as e:
            self._error("Cannot save target", str(e))
            return
        self.name.clear()
        self._refresh_targets(self.targets.ref(t["id"]))

    def _refresh_targets(self, select: str = "") -> None:
        self.saved.clear()
        for t in self.targets.list():
            ref = self.targets.ref(t["id"])
            where = f"region {t['region']}" if t["region"] else "whole area"
            item = QListWidgetItem(f"{self.targets.label(ref)}  ·  {t['match']} · {where}")
            item.setData(Qt.ItemDataRole.UserRole, ref)
            self.saved.addItem(item)
            if ref == select:
                self.saved.setCurrentItem(item)

    def _current_target(self) -> str | None:
        item = self.saved.currentItem()
        return item.data(Qt.ItemDataRole.UserRole) if item else None

    def _target_menu(self, pos) -> None:
        item = self.saved.itemAt(pos)
        if not item:
            return
        self.saved.setCurrentItem(item)
        menu = QMenu(self)
        menu.addAction("Test on viewport", self._test_target)
        menu.addAction("Rename…", self._rename_target)
        menu.addSeparator()
        menu.addAction("Delete", self._delete_target)
        menu.exec(self.saved.viewport().mapToGlobal(pos))

    def _rename_target(self) -> None:
        ref = self._current_target()
        if not ref:
            return
        name, ok = QInputDialog.getText(self, "Rename target", "Name:", text=self.targets.get(ref)["name"])
        if ok and name.strip():
            self.targets.update(ref, name=name.strip())
            self._refresh_targets(ref)

    def _delete_target(self) -> None:
        ref = self._current_target()
        if ref and QMessageBox.question(self, "Delete target",
                                        f"Delete {self.targets.label(ref)}? Blocks that use it will fail "
                                        "until you pick another.") == QMessageBox.StandardButton.Yes:
            self.targets.delete(ref)
            self._refresh_targets()

    def _test_target(self) -> None:
        ref = self._current_target()
        if not ref:
            return
        t = self.targets.get(ref)

        def work():
            frame, _ = self.vision.capture_viewport()
            x = y = 0
            if t["region"]:
                x, y, w, h = (int(float(v)) for v in t["region"].split(","))
                frame = frame[y:y + h, x:x + w]
            start = time.perf_counter()
            words = self._ocr(t["language"]).words(frame)
            found = find_all_text(words, t["text"], t["case_sensitive"], t["match"], t["confidence"])
            return [(m.center[0] + x, m.center[1] + y, m.text) for m in found], time.perf_counter() - start

        def done(result) -> None:
            found, elapsed = result
            if found:
                where = ", ".join(f"“{text}” at ({x}, {y})" for x, y, text in found[:5])
                self.test_result.setText(f"<b style='color:{C['ok']}'>Found {len(found)}</b> — {where} "
                                         f"· {elapsed * 1000:.0f} ms")
            else:
                self.test_result.setText(f"<b style='color:{C['err']}'>Not found</b> on the viewport "
                                         f"· {elapsed * 1000:.0f} ms")

        self.test_result.setText("Testing…")
        run_async(work, done, lambda msg: self.test_result.setText(f"<span style='color:{C['err']}'>{msg}</span>"))

    # -- misc ----------------------------------------------------------------

    def _error(self, title: str, msg: str) -> None:
        log.warning("%s: %s", title, msg)
        QMessageBox.warning(self, title, msg)

    def showEvent(self, e) -> None:
        super().showEvent(e)
        self._fill_languages()  # the OCR backend may have changed in Settings
        self._refresh_targets(self._current_target() or "")
