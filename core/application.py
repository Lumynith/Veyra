"""Wires the core services together and starts the GUI."""
from __future__ import annotations

import logging
import threading
from pathlib import Path
from typing import Callable

from automation.engine import AutomationEngine, Services
from automation.input_controller import InputController
from blocks import registry
from core.event_bus import EventBus
from core.logger import set_level, setup_logging
from core.startup import ROOT, create_qt, user_settings
from core.window_manager import WindowManager
from vision.assets import AssetLibrary, set_display_library
from vision.recognition_manager import RecognitionManager
from vision.text_targets import TextTargets, set_display_targets

log = logging.getLogger(__name__)


class Application:
    def __init__(self, argv: list[str], home: Path | None = None,
                 progress: Callable[[str, float], None] | None = None) -> None:
        """home: folder for config/settings.json, data/, logs/ and plugins/ (default: the install folder).
        progress(text, fraction) is told about each loading stage (the startup animation's bar)."""
        home = Path(home) if home else ROOT
        self.home = home
        step = progress or (lambda text, fraction: None)
        self.qt = create_qt(argv)
        self.bus = EventBus()
        self.log_file = setup_logging(home / "logs", self.bus)
        self.settings = user_settings(home)
        set_level(self.settings.get("logging.level", "INFO"))
        step("Loading blocks", 0.15)
        registry.load_builtin()
        registry.load_plugins(home / "plugins")
        from blocks.custom import CustomBlockStore
        self.custom_blocks = CustomBlockStore(home / "data" / "custom_blocks")
        self.custom_blocks.load_all()

        from ui import theme  # after QApplication exists
        theme.set_palette(self.settings.get("ui.theme", "dark"), self.settings.get("ui.accent", "default"))
        self.qt.setStyleSheet(theme.stylesheet())

        step("Preparing image and text detection", 0.35)
        self.window_manager = WindowManager(self.settings, self.bus)
        self._ocr_lock = threading.Lock()
        self._ocr: dict[tuple, object] = {}  # settings key -> cached backend, created on first use
        assets = AssetLibrary(home / "data" / "assets" / "images")
        set_display_library(assets)
        self.vision = RecognitionManager(assets, roblox_hwnd=lambda: self.window_manager.hwnd,
                                         settings=self.settings.get)
        self.text_targets = TextTargets(home / "data" / "assets" / "text_targets.json")
        set_display_targets(self.text_targets)
        step("Starting the engine", 0.5)
        self.engine = AutomationEngine(self.bus, Services(
            input=InputController(),
            roblox_hwnd=lambda: self.window_manager.hwnd,
            focus_roblox=lambda: self.window_manager.focus_from_any_thread(),
            ocr=self.ocr_backend,
            data_dir=home / "data",
            vision=self.vision,
            text_targets=self.text_targets,
        ))
        self.qt.aboutToQuit.connect(self._shutdown)
        from automation.project import acquire_session
        self._lock = home / "data" / "session.lock"
        crashed = acquire_session(self._lock)
        if crashed:
            log.warning("The previous session did not close cleanly")

        step("Building the workspace", 0.6)
        from ui.main_window import MainWindow
        self.main_window = MainWindow(self.settings, self.bus, self.window_manager, self.engine, home / "data",
                                      self.vision, self.text_targets, self.ocr_backend,
                                      self.custom_blocks, home / "plugins", crashed)
        step("Ready", 1.0)
        log.info("Veyra started")

    def ocr_backend(self, language: str = ""):
        """Thread-safe; called from the automation thread and UI workers. Rebuilt when OCR settings change."""
        from vision.text_detector import CachedOcr, make_backend
        get = self.settings.get
        key = (get("ocr.backend", "windows"), float(get("ocr.scale", 2.0)), get("ocr.tesseract_cmd", ""),
               language or get("ocr.language", ""), bool(get("ocr.cache", True)))
        with self._ocr_lock:
            if key not in self._ocr:
                self._ocr[key] = CachedOcr(make_backend(*key[:4]), 32 if key[4] else 0)
            return self._ocr[key]

    def _shutdown(self) -> None:
        self.engine.stop()
        if not self.engine.wait_finished(3):
            log.warning("The workflow did not finish within 3 s of Stop; exiting anyway (input is released)")
        # Never leave Roblox parented to a window that is about to be destroyed.
        self.window_manager.detach()
        from automation.project import release_session
        release_session(self._lock)  # clean exit: no recovery prompt next time
        from PySide6.QtCore import QThreadPool
        if not QThreadPool.globalInstance().waitForDone(3000):  # captures / OCR started from the UI
            log.warning("Background tasks were still running at exit")
        from core.logger import detach_ui
        detach_ui()

    def run(self) -> int:
        self.main_window.show()
        return self.qt.exec()
