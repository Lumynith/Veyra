from pathlib import Path

import pytest
from PySide6.QtWidgets import QApplication

from core.event_bus import EventBus
from core.settings_manager import SettingsManager

DEFAULTS = Path(__file__).resolve().parent.parent / "config" / "defaults.json"


@pytest.fixture(scope="session")
def qapp():
    return QApplication.instance() or QApplication([])


@pytest.fixture(autouse=True)
def _collect_qt_leftovers(qapp):
    """Destroy each test's leftover widgets between tests. Otherwise Python's cycle collector may delete them in
    the middle of a later test's event processing, which crashes the whole run."""
    yield
    import gc

    from PySide6.QtCore import QCoreApplication, QEvent
    gc.collect()
    QCoreApplication.sendPostedEvents(None, QEvent.Type.DeferredDelete)


@pytest.fixture
def settings(tmp_path):
    return SettingsManager(DEFAULTS, tmp_path / "settings.json")


@pytest.fixture
def bus(qapp):
    return EventBus()


def dispose(qapp, *objects) -> None:
    """Delete Qt objects now. Left to Python's cycle collector they can be destroyed in the middle of a later
    test's event processing, which crashes the whole run."""
    from PySide6.QtCore import QCoreApplication, QEvent
    for obj in objects:
        obj.deleteLater()
    QCoreApplication.sendPostedEvents(None, QEvent.Type.DeferredDelete)
    qapp.processEvents()
