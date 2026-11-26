"""The whole application: startup, the emergency-stop hotkey path, shutdown, and crash recovery.

Runs the real Application against a temporary home folder (settings, data, logs), so nothing of the user's
is touched. Global hotkeys are disabled here so a running copy of the app is not disturbed.
"""
import json
import time

from PySide6.QtWidgets import QMessageBox

from automation.engine import IDLE, RUNNING
from automation.workflow import Workflow, new_node


def make_home(tmp_path):
    (tmp_path / "config").mkdir(parents=True, exist_ok=True)
    blank = {a: "" for a in ("start", "pause", "resume", "stop", "record", "record_stop", "toggle_window")}
    (tmp_path / "config" / "settings.json").write_text(json.dumps({"hotkeys": blank}), "utf-8")
    return tmp_path


def start(qapp, home):
    from core.application import Application
    app = Application([], home=home)
    app.main_window.show()
    for _ in range(10):
        qapp.processEvents()
    return app


def test_startup_run_emergency_stop_and_clean_shutdown(qapp, tmp_path):
    home = make_home(tmp_path)
    app = start(qapp, home)
    w = app.main_window
    try:
        assert (home / "data" / "session.lock").exists()
        assert w.windowTitle().endswith("Veyra") and w.pages.count() == 9
        assert app.engine.state == IDLE and not w.hotkeys.active  # all hotkeys blank in this test

        wf = Workflow()
        loop = new_node("flow.repeat", {"times": 0})  # forever
        loop["body"] = [new_node("key.hold", {"key": "f13"}), new_node("flow.wait", {"seconds": 0.05})]
        wf.add_script([new_node("flow.start"), loop])
        w.editor.load(wf, None)
        w.editor.run()
        time.sleep(0.2)
        assert app.engine.state == RUNNING
        held = set(app.engine.services.input._held_keys)
        assert held  # F13 is held down
        app.bus.publish("hotkey", "stop")  # the global emergency-stop hotkey path
        end = time.time() + 3
        while app.engine.state != IDLE and time.time() < end:
            qapp.processEvents()
            time.sleep(0.01)
        assert app.engine.state == IDLE and not app.engine.services.input._held_keys
    finally:
        w.editor._clean = w.editor.scene.wf.dumps()
        w.close()
        app._shutdown()
    assert not (home / "data" / "session.lock").exists()  # clean exit
    assert (home / "data" / "autosave.lcw.json").exists()
    assert (home / "logs" / "veyra.log").stat().st_size > 0


def test_recovery_after_unexpected_shutdown(qapp, tmp_path, monkeypatch):
    home = make_home(tmp_path)
    app = start(qapp, home)
    w = app.main_window
    w.editor.scene.append_block("mouse.left_click", w.editor.view.center())
    w.editor.write_autosave()  # periodic autosave ran...
    expected = w.editor.scene.wf.dumps()
    w.hotkeys.stop()
    w.recorder.shutdown()
    # ...and then the process "died": no closeEvent, no _shutdown, the session lock stays
    assert (home / "data" / "session.lock").exists()
    w.deleteLater()

    asked = []
    monkeypatch.setattr(QMessageBox, "question",
                        lambda *a, **k: asked.append(a[1]) or QMessageBox.StandardButton.Yes)
    app2 = start(qapp, home)
    w2 = app2.main_window
    try:
        assert asked == ["Recover unsaved work"]
        assert w2.editor.scene.wf.dumps() == expected and w2.editor.dirty
    finally:
        w2.editor._clean = w2.editor.scene.wf.dumps()
        w2.close()
        app2._shutdown()
