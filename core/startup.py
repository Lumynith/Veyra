"""The few things needed before anything heavy is imported, so the startup animation can appear at once."""
from __future__ import annotations

import ctypes
import sys
from pathlib import Path

from PySide6.QtGui import QIcon
from PySide6.QtWidgets import QApplication

from core.settings_manager import SettingsManager

# The install folder: next to Veyra.exe in a release build, the project folder when run from source.
ROOT = Path(sys.executable).parent if getattr(sys, "frozen", False) else Path(__file__).resolve().parent.parent


def create_qt(argv: list[str]) -> QApplication:
    """The QApplication, with Veyra's name, icon and taskbar identity. Safe to call more than once."""
    if (qt := QApplication.instance()) is not None:
        return qt
    # Own taskbar identity; without it Windows groups us under python.exe and shows Python's icon.
    ctypes.windll.shell32.SetCurrentProcessExplicitAppUserModelID("Veyra.Macro")
    qt = QApplication(argv)
    qt.setApplicationName("Veyra")
    qt.setWindowIcon(QIcon(str(ROOT / "icon.ico")))  # every window and dialog inherits it
    qt.setStyle("Fusion")  # consistent base for the stylesheet across Windows versions
    return qt


def user_settings(home: Path | None = None) -> SettingsManager:
    return SettingsManager(ROOT / "config" / "defaults.json", (Path(home) if home else ROOT) / "config" / "settings.json")
