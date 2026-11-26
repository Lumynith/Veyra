"""Startup animation and run effects: they must say the right thing and never leave anything behind."""
import time

from PySide6.QtWidgets import QMainWindow, QStatusBar

from automation.engine import IDLE, PAUSED, RUNNING, STOPPING
from tests.conftest import dispose
from ui.effects import Effects


class FakeEngine:
    state = IDLE
    restarting = False


def make(qapp, bus):
    w = QMainWindow()
    w.setStatusBar(QStatusBar())
    w.resize(900, 600)
    w.show()
    engine = FakeEngine()
    return w, engine, Effects(w, engine, bus)


def deliver(qapp, bus, topic, payload):
    bus.publish(topic, payload)
    qapp.processEvents()


def test_toasts_flash_and_glow_follow_the_engine(qapp, bus):
    w, engine, fx = make(qapp, bus)
    deliver(qapp, bus, "engine.state", RUNNING)
    assert fx.glow.isVisible() and fx.glow._timer.isActive()
    deliver(qapp, bus, "engine.state", PAUSED)
    assert fx.glow.isVisible() and not fx.glow._timer.isActive()  # steady amber, no animation
    engine.restarting = True
    deliver(qapp, bus, "engine.state", STOPPING)
    assert not fx.flash.isVisible()  # Restart is not an emergency
    engine.restarting = False
    deliver(qapp, bus, "engine.state", STOPPING)
    assert fx.flash.isVisible()
    deliver(qapp, bus, "engine.finished", {"result": "stopped", "elapsed": 1.25})
    assert "stopped after 1.2 s" in fx.toast.text.text() and "released" in fx.toast.text.text()
    deliver(qapp, bus, "engine.state", IDLE)
    assert not fx.glow.isVisible() and not fx.glow._timer.isActive()
    deliver(qapp, bus, "engine.finished", {"result": "error", "error": "Find Image: not found\ntrace", "elapsed": 2})
    assert fx.toast.text.text() == "Macro failed: Find Image: not found" and fx.toast.property("kind") == "err"
    engine.state = RUNNING  # a Restart already started the next run
    deliver(qapp, bus, "engine.finished", {"result": "stopped", "elapsed": 1})
    assert fx.toast.text.text().startswith("Restarted")
    end = time.time() + 1.5
    while fx.flash.isVisible() and time.time() < end:
        qapp.processEvents()
        time.sleep(0.01)
    assert not fx.flash.isVisible() and not fx.flash._timer.isActive()  # nothing keeps animating
    w.close()
    dispose(qapp, w)


def test_splash_hands_over_to_a_fully_visible_window(qapp):
    from PySide6.QtWidgets import QWidget

    from ui.splash import Splash
    splash = Splash()
    splash.show()
    splash._skip_intro()
    splash.play_intro()  # returns at once once skipped
    splash.step("Loading", 0.5)
    window = QWidget()
    started = time.time()
    splash.finish(window)
    assert time.time() - started < 2
    assert window.isVisible() and window.windowOpacity() == 1.0 and not splash.isVisible()
    assert not splash._frame.isActive()
    window.close()
    dispose(qapp, window, splash)
