"""Phase 8 quality-of-life: undo/redo, clipboard, find, favorites, templates, variables, debug console, hotkeys."""
import time

import numpy as np
import pytest
from PySide6.QtCore import QPointF
from PySide6.QtWidgets import QApplication, QInputDialog, QMessageBox

from automation.workflow import Workflow, new_node
from blocks import registry
from tests.test_engine import make
from tests.test_projects import FakeEngine

registry.load_builtin()


@pytest.fixture
def page(qapp, bus, settings, tmp_path):
    from ui.editor.workspace import EditorPage
    p = EditorPage(FakeEngine(), bus, tmp_path / "auto.json", settings=settings, templates=tmp_path / "templates")
    wf = Workflow()
    wf.add_script([new_node("flow.start"), new_node("flow.wait", {"seconds": 1.0})], (0, 0))
    p.load(wf, None)
    return p


def types(page):
    return [n["type"] for n in page.scene.wf.nodes()]


def test_undo_redo(page):
    page.scene.append_block("mouse.left_click", QPointF())
    page.history.commit()
    page.scene.append_block("mouse.right_click", QPointF())
    assert types(page)[-1] == "mouse.right_click"
    assert page.history.undo() and types(page)[-1] == "mouse.left_click"  # pending edit undone first
    assert page.history.undo() and types(page) == ["flow.start", "flow.wait"]
    assert not page.history.undo()
    assert page.history.redo() and types(page)[-1] == "mouse.left_click"
    page.load(Workflow(), None)
    assert not page.history.can_undo()


def test_copy_paste_find(page, qapp):
    wait = page.scene.wf.scripts[0]["blocks"][1]
    page.scene.items_by_id[wait["id"]].setSelected(True)
    page.scene.items_by_id[page.scene.wf.scripts[0]["blocks"][0]["id"]].setSelected(True)  # Start too
    assert page.view.copy() == 2
    page.view.paste()
    assert types(page).count("flow.start") == 1 and types(page).count("flow.wait") == 2  # no second Start
    ids = [n["id"] for n in page.scene.wf.nodes()]
    assert len(ids) == len(set(ids)) and page.scene.wf.validate() == []
    QApplication.clipboard().setText("not blocks")
    page.view.paste()  # ignored quietly
    assert len(page.scene.find("wait")) == 2 and page.scene.find("1") and page.scene.find("zzz") == []
    page.find_box.setText("wait")
    page.find_next()
    assert page.scene.selected_blocks()[0].block.type_id == "flow.wait"


def test_favorites_and_templates(page, settings, monkeypatch, tmp_path):
    lib = page.library
    lib.toggle_favorite("mouse.left_click")
    top = lib.tree.topLevelItem(0)
    assert "FAVORITES" in top.text(0) and top.child(0).text(0) == "Left Click"
    assert settings.get("editor.favorites") == ["mouse.left_click"]
    lib.toggle_favorite("mouse.left_click")
    assert "FAVORITES" not in lib.tree.topLevelItem(0).text(0)

    monkeypatch.setattr(QInputDialog, "getText", lambda *a, **k: ("My farm", True))
    assert page.save_template()
    names = [(n, user) for n, _, user in page.templates()]
    assert ("My farm", True) in names and any(not user for _, user in names)  # bundled examples too
    page.scene.append_block("mouse.left_click", QPointF())
    monkeypatch.setattr(QMessageBox, "question", lambda *a, **k: QMessageBox.StandardButton.Discard)
    path = next(p for n, p, u in page.templates() if u)
    assert page.new_from_template(path) and types(page) == ["flow.start", "flow.wait"] and page.dirty


def test_variables_panel_and_debug_console(qapp, bus, tmp_path, page):
    from ui.editor.debug_panel import RunLogPanel
    page.scene.wf.variables["gold"] = 5
    page.variables.refresh()
    assert page.variables.table.item(0, 0).text() == "gold" and page.variables.table.item(0, 1).text() == "5"
    page.variables.table.item(0, 1).setText("12")
    assert page.scene.wf.variables["gold"] == 12 and page.dirty
    assert Workflow.from_dict(page.scene.wf.to_dict()).variables == {"gold": 12}

    engine, _ = make(bus, tmp_path)
    log_panel = RunLogPanel(engine, bus)
    log_panel._vars = {"hp": 40}
    assert log_panel.run_command("hp") == "hp = 40"
    assert log_panel.run_command("={hp} * 2") == "80"
    assert log_panel.run_command("HP is {hp}") == "HP is 40"
    with pytest.raises(RuntimeError):
        log_panel.run_command("hp = 3")  # nothing is running
    log_panel.add([{"time": "t", "id": "x", "title": "Find", "status": "not found", "detail": "", "ms": 3},
                   {"time": "t", "id": "y", "title": "Wait", "status": "ok", "detail": "", "ms": 1}])
    log_panel.filter.setCurrentIndex(log_panel.filter.findData("problems"))
    assert log_panel.table.rowCount() == 1


def test_detection_results_reach_the_trace(qapp, bus, tmp_path):
    from tests.test_engine import FakeOcr, run, script
    from vision.text_detector import Word
    shot = np.zeros((50, 80, 3), np.uint8)
    engine, _ = make(bus, tmp_path, capture=lambda rect: shot, ocr=lambda language="": FakeOcr([Word("Play", 5, 5, 20, 10, 0)]))
    traces = []
    bus.subscribe("engine.trace", traces.extend)
    res = run(qapp, bus, engine, script(new_node("txt.find", {"text": "Play", "space": "screen", "region": "0,0,80,50"})))
    assert res["result"] == "completed"
    found = [t for t in traces if t["status"] == "found"]
    assert found and "Play" in found[0]["title"] and "1 match" in found[0]["detail"]


def test_recent_image_assets(tmp_path):
    from vision.assets import AssetLibrary
    lib = AssetLibrary(tmp_path)
    a = lib.add_image(np.zeros((4, 4, 3), np.uint8), "a")
    b = lib.add_image(np.zeros((4, 4, 3), np.uint8), "b")
    lib.touch(lib.ref(a["id"]))
    lib.touch(lib.ref(b["id"]))
    lib.touch("asset:missing")
    assert [m["name"] for m in AssetLibrary(tmp_path).recent()] == ["b", "a"]  # persisted


def test_global_hotkeys_fire_and_parse():
    from automation.input_controller import SendInputBackend
    from core.hotkeys import GlobalHotkeys, parse_hotkey
    assert parse_hotkey("Ctrl+Alt+F5") == (0x3, 0x74)
    with pytest.raises(ValueError):
        parse_hotkey("Ctrl+Alt")
    got = []
    hk = GlobalHotkeys(got.append)
    failed = hk.apply({"stop": "Ctrl+Alt+Shift+F13", "pause": "Ctrl+Alt+Shift+F13", "start": "nonsense+"})
    try:
        assert set(failed) == {"pause", "start"} and hk.active == {"stop": "Ctrl+Alt+Shift+F13"}
        out = SendInputBackend()
        for vk in (0x11, 0x12, 0x10, 0x7C):
            out.key(vk, True)
        for vk in (0x7C, 0x10, 0x12, 0x11):
            out.key(vk, False)
        end = time.time() + 2
        while not got and time.time() < end:
            time.sleep(0.02)
        assert got == ["stop"]
    finally:
        hk.stop()


def test_theme_palettes(qapp):
    from ui import theme
    before = dict(theme.C)
    try:
        theme.set_palette("light", "green")
        assert theme.C["bg1"] == "#f4f3f7" and theme.C["accent"] == "#22a565"
        assert "$" not in theme.stylesheet()
    finally:
        theme.C.update(before)
        theme.icon.cache_clear()
