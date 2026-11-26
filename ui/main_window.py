"""Main window: icon sidebar | Roblox viewport + console | page stack, status bar at the bottom."""
from __future__ import annotations

import base64
import logging

from PySide6.QtCore import QByteArray, QTimer
from PySide6.QtGui import QKeySequence, QShortcut
from PySide6.QtWidgets import (QHBoxLayout, QMainWindow, QScrollArea, QSplitter, QStackedWidget, QVBoxLayout,
                               QWidget)

from automation.demo import build_demo
from automation.engine import IDLE
from core.hotkeys import GlobalHotkeys, bindings_from, hotkey_vks
from ui.editor.workspace import EditorPage
from ui.effects import Effects
from ui.help.tutorial_page import TutorialPage
from ui.library.library_page import LibraryPage
from automation.macro_library import MacroLibrary
from automation.script_library import ScriptLibrary
from ui.editor.properties import EditorResources
from ui.panels.console import Console
from ui.recorder.recorder_page import RecorderPage
from ui.scripting.custom_blocks_page import CustomBlocksPage
from ui.scripting.script_page import ScriptPage
from ui.panels.settings_panel import SettingsPanel
from ui.sidebar import Sidebar
from ui.status_bar import AppStatusBar
from ui.viewport import ViewportPanel
from ui.vision.image_page import ImageDetectionPage
from ui.vision.text_page import TextDetectionPage
from ui.widgets import IconButton, Panel

log = logging.getLogger(__name__)


class MainWindow(QMainWindow):
    def __init__(self, settings, bus, window_manager, engine, data_dir, vision, text_targets, ocr,
                 custom_blocks, plugins_dir, crashed: bool = False) -> None:
        super().__init__()
        self._settings, self._wm = settings, window_manager
        self.setWindowTitle("Veyra")
        self.resize(settings.get("app.window_width", 1436), settings.get("app.window_height", 878))

        self.viewport_panel = ViewportPanel(window_manager, settings, bus)
        self.console = Console(bus)
        clear = IconButton("trash", "Clear console")
        clear.clicked.connect(self.console.clear)
        self.console_panel = Panel("Console", self.console, [clear])

        self.pages = QStackedWidget()
        self.sidebar = Sidebar(self.pages)
        scripts = ScriptLibrary(data_dir / "scripts")
        resources = EditorResources(vision=vision, targets=text_targets, scripts=scripts)
        self._engine, self._bus = engine, bus
        self.editor = EditorPage(engine, bus, data_dir / "autosave.lcw.json", resources, settings,
                                 data_dir / "templates")
        self.editor.file_changed.connect(lambda name: self.setWindowTitle(f"{name} — Veyra"))
        bus.subscribe("settings.changed", self.editor.apply_settings)
        self.sidebar.add_page("editor", "Editor", self.editor)
        self.custom_blocks = CustomBlocksPage(custom_blocks, bus, resources, self.editor.uses, plugins_dir)
        self.scripts = ScriptPage(engine, bus, scripts, self._make_script_block)
        self.recorder = RecorderPage(settings, bus, engine, vision, self._open_recording)
        self.sidebar.add_page("recorder", "Recorder", self.recorder)
        self.sidebar.add_page("image", "Image Detection", ImageDetectionPage(vision, window_manager, settings))
        self.sidebar.add_page("text", "Text Detection", TextDetectionPage(vision, text_targets, ocr, settings))
        self.sidebar.add_page("blocks", "Custom Blocks", self.custom_blocks)
        self.sidebar.add_page("script", "Script Editor", self.scripts)
        self.library = LibraryPage(MacroLibrary(data_dir / "macros"), self.editor,
                                   lambda: self.sidebar.select(self.pages.indexOf(self.editor)))
        self.sidebar.add_page("library", "Macro Library", self.library)
        self.tutorials = TutorialPage(self, settings)
        self.sidebar.add_page("help", "Tutorials", self.tutorials, bottom=True)
        settings_page = QScrollArea(widgetResizable=True, frameShape=QScrollArea.Shape.NoFrame)
        settings_page.setWidget(SettingsPanel(settings, bus, lambda: self.apply_hotkeys()))
        self.sidebar.add_page("settings", "Settings", settings_page, bottom=True)

        left = QVBoxLayout()
        left.setSpacing(8)
        left.addWidget(self.viewport_panel)
        left.addWidget(self.console_panel, 1)
        content = QHBoxLayout()
        content.setContentsMargins(8, 8, 8, 8)
        content.setSpacing(8)
        content.addLayout(left)
        content.addWidget(self.pages, 1)
        central = QWidget(objectName="central")
        root = QHBoxLayout(central)
        root.setContentsMargins(0, 0, 0, 0)
        root.setSpacing(0)
        root.addWidget(self.sidebar)
        root.addLayout(content, 1)
        self.setCentralWidget(central)

        self.status = AppStatusBar(bus)
        self.status.errors_clicked.connect(self.console.setFocus)
        self.setStatusBar(self.status)
        self.effects = Effects(self, engine, bus, bool(settings.get("ui.animations", True)))

        self.viewport_panel.toolbar.maximize_toggled.connect(self._set_maximized)
        bus.subscribe("settings.changed", self._on_settings_changed)
        self._shortcuts(engine)
        self._restore_layout()
        self._startup(crashed)
        self.hotkeys = GlobalHotkeys(lambda action: bus.publish("hotkey", action))
        bus.subscribe("hotkey", self._on_hotkey)
        self.apply_hotkeys()

    # -- startup & global hotkeys --------------------------------------------------------------

    def _startup(self, crashed: bool) -> None:
        what = self._settings.get("startup.open", "last")
        if crashed or what == "last":  # after a crash, recovery always gets its chance
            self.editor.restore_session(build_demo, crashed)
        elif what == "demo":
            self.editor.load(build_demo(), None)
        else:
            self.editor.load(self._blank(), None)
        if not self._settings.get("tutorials.seen", False):  # first start: offer the lessons
            self._settings.set("tutorials.seen", True)
            self.sidebar.select(self.pages.indexOf(self.tutorials))
            return
        page = self._settings.get("startup.page", "last")
        if page != "last":
            index = {"editor": self.editor, "recorder": self.recorder}.get(page)
            if index is not None:
                self.sidebar.select(self.pages.indexOf(index))

    @staticmethod
    def _blank():
        from automation.workflow import START, Workflow, new_node
        wf = Workflow()
        wf.add_script([new_node(START)], (40, 40))
        return wf

    def apply_hotkeys(self) -> dict:
        bindings = bindings_from(self._settings)
        failed = self.hotkeys.apply(bindings)
        self.recorder.recorder.ignore_vks = hotkey_vks(self.hotkeys.active)
        return failed

    def _on_hotkey(self, action: str) -> None:
        log.info("Hotkey: %s", action)
        engine = self._engine
        if action == "start":
            self.editor.run()
        elif action == "pause":
            engine.pause()
        elif action == "resume":
            engine.resume()
        elif action == "stop":
            engine.emergency_stop()
        elif action == "record":
            if engine.state == IDLE:
                self.recorder.start()
        elif action == "record_stop":
            self.recorder.stop()
        elif action == "toggle_window":
            self.toggle_window()

    def toggle_window(self) -> None:
        if self.isMinimized() or not self.isVisible():
            self.showNormal()
            self.raise_()
            self.activateWindow()
        else:
            self.showMinimized()  # minimised, not hidden: an embedded Roblox window stays alive

    def _open_recording(self, wf, name: str) -> bool:
        if not self.editor.open_workflow(wf, name):
            return False
        self.sidebar.select(self.pages.indexOf(self.editor))
        return True

    def _make_script_block(self, title: str, source: str, extra: dict) -> None:
        self.sidebar.select(self.pages.indexOf(self.custom_blocks))
        self.custom_blocks.new_script_block(title, source, extra)

    # -- shortcuts -------------------------------------------------------

    def _shortcuts(self, engine) -> None:
        toolbar = self.viewport_panel.toolbar
        bindings = [(f"Ctrl+{i + 1}", lambda i=i: self.sidebar.select(i)) for i in range(self.pages.count())]
        bindings += [
            ("F5", lambda: self.editor.run()),
            ("F6", self.editor.toggle_pause),
            ("F7", self.editor.step),
            ("Ctrl+Shift+F5", self.editor.restart),
            ("Shift+F5", engine.stop),
            ("Esc", self._escape_stop),
            ("Ctrl+R", toolbar.reconnect),
            ("F11", toolbar.max_btn.toggle),
            ("Ctrl+F", self._focus_search),
            ("Ctrl+N", self.editor.new),
            ("Ctrl+O", lambda: self.editor.open()),
            ("Ctrl+S", self.editor.save),
            ("Ctrl+Shift+S", self.editor.save_as),
        ]
        for keys, slot in bindings:
            QShortcut(QKeySequence(keys), self, activated=slot)

    def _escape_stop(self) -> None:
        if self._engine.state != IDLE:  # Esc in the app is a quick emergency stop while a macro runs
            self._engine.emergency_stop()

    def _focus_search(self) -> None:
        self.sidebar.select(0)
        self.editor.library.focus_search()

    # -- viewport maximize -----------------------------------------------

    def _set_maximized(self, on: bool) -> None:
        self.pages.setVisible(not on)
        self.console_panel.setVisible(not on)
        self.viewport_panel.set_fill(on)
        self._wm.fill_host = on
        QTimer.singleShot(0, lambda: self._wm.sync(force=True))  # after the layout settles

    def _on_settings_changed(self, _) -> None:
        self.viewport_panel.viewport.apply_size()
        self._wm.sync(force=True)

    # -- persistence -----------------------------------------------------

    def _restore_layout(self) -> None:
        for splitter in self.findChildren(QSplitter):
            state = self._settings.get(f"ui.splitters.{splitter.objectName()}")
            if state:
                splitter.restoreState(QByteArray(base64.b64decode(state)))
        self.sidebar.select(int(self._settings.get("ui.page", 0)))

    def _save_layout(self) -> None:
        for splitter in self.findChildren(QSplitter):
            if splitter.objectName():
                state = base64.b64encode(bytes(splitter.saveState())).decode()
                self._settings.set(f"ui.splitters.{splitter.objectName()}", state)
        self._settings.set("ui.page", self.pages.currentIndex())
        try:
            self._settings.save()
        except OSError as e:  # never block closing over a layout save
            log.warning("Could not save layout: %s", e)

    # -- window events ---------------------------------------------------

    def moveEvent(self, e) -> None:  # anchored mode follows the window
        super().moveEvent(e)
        self._wm.sync()

    def resizeEvent(self, e) -> None:
        super().resizeEvent(e)
        self._wm.sync()

    def closeEvent(self, e) -> None:
        self.hotkeys.stop()
        self.recorder.shutdown()
        self._save_layout()
        self.editor.write_autosave()
        self._wm.detach()
        super().closeEvent(e)
