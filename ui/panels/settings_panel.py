"""Edit and save user settings."""
from __future__ import annotations

import logging

from PySide6.QtGui import QKeySequence
from PySide6.QtWidgets import (QCheckBox, QComboBox, QDoubleSpinBox, QFormLayout, QHBoxLayout, QKeySequenceEdit,
                               QLabel, QMessageBox, QPushButton, QSpinBox, QVBoxLayout, QWidget)

from core.hotkeys import ACTIONS, bindings_from, parse_hotkey

from core.logger import set_level

log = logging.getLogger(__name__)


class SettingsPanel(QWidget):
    def __init__(self, settings, bus, apply_hotkeys=None, parent=None) -> None:
        super().__init__(parent)
        self._settings = settings
        self._bus = bus
        self._apply_hotkeys = apply_hotkeys  # -> {action: reason} for hotkeys that could not be registered

        self.width = QSpinBox(minimum=200, maximum=3840, value=settings.get("viewport.width", 841))
        self.height = QSpinBox(minimum=200, maximum=2160, value=settings.get("viewport.height", 661))
        self.size_mode = QComboBox()
        self.size_mode.addItem("Outer window size", "outer")
        self.size_mode.addItem("Client (render) area", "client")
        self.size_mode.setCurrentIndex(max(0, self.size_mode.findData(settings.get("viewport.size_mode"))))
        self.fallback = QCheckBox("Fall back to anchored mode if embedding fails")
        self.fallback.setChecked(bool(settings.get("viewport.auto_fallback", True)))
        self.level = QComboBox()
        self.level.addItems(["DEBUG", "INFO", "WARNING", "ERROR"])
        self.level.setCurrentText(str(settings.get("logging.level", "INFO")).upper())
        self.ocr = QComboBox()
        self.ocr.addItem("Windows OCR", "windows")
        self.ocr.addItem("Tesseract", "tesseract")
        self.ocr.setCurrentIndex(max(0, self.ocr.findData(settings.get("ocr.backend", "windows"))))
        self.ocr_lang = QComboBox(editable=True)
        self.ocr_lang.lineEdit().setPlaceholderText("Windows language / eng")
        self.ocr_lang.setToolTip("Default OCR language. Blank = your Windows display languages (Windows OCR) "
                                 "or eng (Tesseract). Blocks can override it.")
        self._fill_languages()
        self.ocr_lang.setCurrentText(settings.get("ocr.language", ""))
        self.ocr.currentIndexChanged.connect(lambda _: self._fill_languages())
        self.ocr_cache = QCheckBox("Reuse OCR results for unchanged screens")
        self.ocr_cache.setChecked(bool(settings.get("ocr.cache", True)))
        self.reuse = QSpinBox(minimum=0, maximum=1000, suffix=" ms", value=int(settings.get("vision.frame_reuse_ms", 40)))
        self.reuse.setToolTip("Searches of the same area within this time share one capture (0 = always capture)")
        self.min_interval = QDoubleSpinBox(minimum=0.0, maximum=5.0, singleStep=0.05, decimals=2, suffix=" s",
                                           value=float(settings.get("vision.min_interval", 0.05)))
        self.min_interval.setToolTip("Fastest allowed retry interval for waiting blocks; raise it to lower CPU use")
        self.cache = QCheckBox("Cache decoded and scaled images")
        self.cache.setChecked(bool(settings.get("vision.cache", True)))
        self.autosave = QSpinBox(minimum=0, maximum=3600, suffix=" s", value=int(settings.get("projects.autosave_s", 60)))
        self.autosave.setToolTip("How often unsaved work is written for crash recovery (0 = only on exit)")
        self.theme = QComboBox()
        for text, key in (("Dark", "dark"), ("Midnight", "midnight"), ("Light", "light")):
            self.theme.addItem(text, key)
        self.theme.setCurrentIndex(max(0, self.theme.findData(settings.get("ui.theme", "dark"))))
        self.accent = QComboBox()
        for key in ("default", "violet", "blue", "green", "orange", "pink"):
            self.accent.addItem(key.capitalize(), key)
        self.accent.setCurrentIndex(max(0, self.accent.findData(settings.get("ui.accent", "default"))))
        self.animations = QCheckBox("Animations and effects")
        self.animations.setChecked(bool(settings.get("ui.animations", True)))
        self.animations.setToolTip("Startup animation, the glow while a macro runs and the red flash when one is "
                                   "stopped. Toasts still appear when this is off.")
        self.start_open = QComboBox()
        for text, key in (("Last session (with unsaved changes)", "last"), ("A new, empty project", "new"),
                          ("The demo workflow", "demo")):
            self.start_open.addItem(text, key)
        self.start_open.setCurrentIndex(max(0, self.start_open.findData(settings.get("startup.open", "last"))))
        self.start_open.setToolTip("After a crash, Veyra always offers to recover unsaved work first")
        self.start_page = QComboBox()
        for text, key in (("The page that was open last", "last"), ("Editor", "editor"), ("Recorder", "recorder")):
            self.start_page.addItem(text, key)
        self.start_page.setCurrentIndex(max(0, self.start_page.findData(settings.get("startup.page", "last"))))
        self.hotkey_edits: dict[str, QKeySequenceEdit] = {}
        current = bindings_from(settings)
        for action in ACTIONS:
            edit = QKeySequenceEdit(QKeySequence(current[action]))
            edit.setMaximumSequenceLength(1)
            edit.setToolTip("Click and press the combination. Use Ctrl/Alt so it does not clash with Roblox keys.")
            self.hotkey_edits[action] = edit
        self.hotkey_status = QLabel("", objectName="muted", wordWrap=True)
        save = QPushButton("Save")
        save.clicked.connect(self._save)

        form = QFormLayout()
        form.addRow("Viewport width (px):", self.width)
        form.addRow("Viewport height (px):", self.height)
        form.addRow("Size applies to:", self.size_mode)
        form.addRow(self.fallback)
        form.addRow("Log level:", self.level)
        form.addRow("Text recognition:", self.ocr)
        form.addRow("OCR language:", self.ocr_lang)
        form.addRow(self.ocr_cache)
        form.addRow("Frame reuse:", self.reuse)
        form.addRow("Min. detection interval:", self.min_interval)
        form.addRow(self.cache)
        form.addRow("Autosave every:", self.autosave)
        form.addRow(QLabel("<b>Appearance</b> (applies the next time the app starts)"))
        form.addRow("Theme:", self.theme)
        form.addRow("Accent colour:", self.accent)
        form.addRow(self.animations)
        form.addRow(QLabel("<b>Startup</b>"))
        form.addRow("Open:", self.start_open)
        form.addRow("Show page:", self.start_page)
        form.addRow(QLabel("<b>Global hotkeys</b> (work while Roblox has focus; only these exact combinations "
                           "are taken from other apps)", wordWrap=True))
        for action, (label, _) in ACTIONS.items():
            row = QHBoxLayout()
            row.addWidget(self.hotkey_edits[action], 1)
            clear = QPushButton("Clear")
            clear.setToolTip("Disable this hotkey")
            clear.clicked.connect(self.hotkey_edits[action].clear)
            row.addWidget(clear)
            form.addRow(label + ":", row)
        form.addRow(self.hotkey_status)
        layout = QVBoxLayout(self)
        layout.addWidget(QLabel("<b>Settings</b>"))
        layout.addLayout(form)
        layout.addWidget(save)
        layout.addStretch(1)

    def _fill_languages(self) -> None:
        from vision.text_detector import languages
        try:
            langs = languages(self.ocr.currentData(), self._settings.get("ocr.tesseract_cmd", ""))
        except Exception as e:  # engine not installed; a language can still be typed
            log.warning("Could not list OCR languages: %s", e)
            langs = []
        current = self.ocr_lang.currentText()
        self.ocr_lang.clear()
        self.ocr_lang.addItems([""] + langs)
        self.ocr_lang.setCurrentText(current)

    def _save(self) -> None:
        s = self._settings
        s.set("viewport.width", self.width.value())
        s.set("viewport.height", self.height.value())
        s.set("viewport.size_mode", self.size_mode.currentData())
        s.set("viewport.auto_fallback", self.fallback.isChecked())
        s.set("logging.level", self.level.currentText())
        s.set("ocr.backend", self.ocr.currentData())
        s.set("ocr.language", self.ocr_lang.currentText().strip())
        s.set("ocr.cache", self.ocr_cache.isChecked())
        s.set("vision.frame_reuse_ms", self.reuse.value())
        s.set("vision.min_interval", self.min_interval.value())
        s.set("vision.cache", self.cache.isChecked())
        s.set("projects.autosave_s", self.autosave.value())
        s.set("ui.theme", self.theme.currentData())
        s.set("ui.accent", self.accent.currentData())
        s.set("ui.animations", self.animations.isChecked())
        s.set("startup.open", self.start_open.currentData())
        s.set("startup.page", self.start_page.currentData())
        hotkeys = {}
        for action, edit in self.hotkey_edits.items():
            text = edit.keySequence().toString(QKeySequence.SequenceFormat.PortableText)
            if text:
                try:
                    parse_hotkey(text)
                except ValueError as e:
                    QMessageBox.warning(self, "Hotkeys", f"{ACTIONS[action][0]}: {e}")
                    return
            hotkeys[action] = text
        s.set("hotkeys", hotkeys)
        try:
            s.save()
        except OSError as e:
            log.error("Saving settings failed: %s", e)
            QMessageBox.warning(self, "Save failed", str(e))
            return
        set_level(self.level.currentText())
        self._bus.publish("settings.changed")
        if self._apply_hotkeys:
            failed = self._apply_hotkeys()
            self.hotkey_status.setText("Some hotkeys are not available: " + "; ".join(
                f"{ACTIONS[a][0]} ({r})" for a, r in failed.items()) if failed else "Hotkeys active.")
