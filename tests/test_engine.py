import json
import time

import numpy as np
import pytest

from automation.demo import build_demo
from automation.engine import IDLE, AutomationEngine, Services
from automation.input_controller import InputController
from automation.workflow import Workflow, WorkflowError, new_node
from blocks import registry
from vision.text_detector import Word

registry.load_builtin()


class FakeInput:
    def __init__(self):
        self.events, self.pos, self.down = [], (0, 0), set()

    def move(self, x, y):
        self.pos = (x, y)
        self.events.append(("move", x, y))

    def button(self, name, down):
        self.events.append(("button", name, down))

    def wheel(self, n):
        self.events.append(("wheel", n))

    def key(self, vk, down):
        self.events.append(("key", vk, down))

    def char(self, ch):
        self.events.append(("char", ch))

    def cursor(self):
        return self.pos

    def key_is_down(self, vk):
        return vk in self.down


class FakeOcr:
    def __init__(self, words):
        self._words = words

    def words(self, img):
        return self._words


def make(bus, tmp_path, **kw):
    fake = FakeInput()
    services = Services(input=InputController(fake), data_dir=tmp_path, **kw)
    return AutomationEngine(bus, services), fake


def script(*nodes) -> dict:
    wf = Workflow()
    wf.add_script([new_node("flow.start"), *nodes])
    return wf.to_dict()


def c(type_id, body, **params):
    node = new_node(type_id, params)
    node["body"] = body
    return node


def run(qapp, bus, engine, wf_dict, timeout=10):
    results = []
    bus.subscribe("engine.finished", results.append)
    engine.run(wf_dict)
    assert engine.wait_finished(timeout)
    qapp.processEvents()
    return results[-1]


def test_demo_workflow_round_trips_and_runs(qapp, bus, tmp_path):
    data = json.loads(json.dumps(build_demo().to_dict()))  # through real JSON text
    assert Workflow.from_dict(data).to_dict() == data
    engine, _ = make(bus, tmp_path)
    r = run(qapp, bus, engine, data)
    assert r["result"] == "completed", r["error"]
    v = r["variables"]
    assert v["total"] == 6 and v["outer"] == 3 and v["inner"] == 2  # nested loops
    assert v["n"] == 4  # Repeat Until left via Break Loop
    assert 1 <= v["dice"] <= 6 and v["connected"] is False
    assert engine.state == IDLE


def test_if_else_and_expressions(qapp, bus, tmp_path):
    engine, _ = make(bus, tmp_path)
    wf = script(
        new_node("var.set", {"name": "x", "value": "=(2 + 3) * 4"}),
        c("flow.if", [new_node("var.set", {"name": "branch", "value": "if"})], left="{x}", op=">", right="100"),
        c("flow.else", [new_node("var.set", {"name": "branch", "value": "else"})]),
        new_node("var.set", {"name": "name", "value": "Lumen"}),
        c("flow.if", [new_node("var.set", {"name": "has", "value": "yes"})], left="{name}", op="contains",
          right="lum", case_sensitive=False),
    )
    v = run(qapp, bus, engine, wf)["variables"]
    assert v["x"] == 20 and v["branch"] == "else" and v["has"] == "yes"


def test_errors_stop_or_continue(qapp, bus, tmp_path):
    engine, _ = make(bus, tmp_path)
    bad = new_node("var.set", {"name": "x", "value": "=1 +"})
    r = run(qapp, bus, engine, script(bad, new_node("var.set", {"name": "after", "value": "1"})))
    assert r["result"] == "error" and "Set Variable" in r["error"] and "after" not in r["variables"]

    bad["on_error"] = "continue"
    r = run(qapp, bus, engine, script(bad, new_node("var.set", {"name": "after", "value": "1"})))
    assert r["result"] == "completed" and r["variables"]["after"] == 1 and "last_error" in r["variables"]


def test_validation_rejects_bad_structure(bus, tmp_path):
    engine, _ = make(bus, tmp_path)
    with pytest.raises(WorkflowError, match="Start"):
        engine.run(Workflow().to_dict())
    with pytest.raises(WorkflowError, match="Else"):
        engine.run(script(c("flow.else", [])))
    with pytest.raises(WorkflowError, match="follow"):
        engine.run(script(new_node("flow.end"), new_node("flow.wait")))
    with pytest.raises(WorkflowError, match="Unknown block"):
        Workflow.from_dict({"format": "lumencrest.workflow", "version": 1,
                            "scripts": [{"blocks": [{"type": "nope"}]}]})


def test_stop_cancels_forever_loop_and_releases_keys(qapp, bus, tmp_path):
    engine, fake = make(bus, tmp_path)
    wf = script(new_node("key.hold", {"key": "w"}),
                c("flow.repeat", [new_node("flow.wait", {"seconds": 0.05})], times=0))
    results = []
    bus.subscribe("engine.finished", results.append)
    engine.run(wf)
    time.sleep(0.3)
    engine.stop()
    assert engine.wait_finished(5)
    qapp.processEvents()
    assert results[-1]["result"] == "stopped"
    assert fake.events[0] == ("key", ord("W"), True) and fake.events[-1] == ("key", ord("W"), False)


def test_pause_freezes_wait(qapp, bus, tmp_path):
    engine, _ = make(bus, tmp_path)
    results = []
    bus.subscribe("engine.finished", results.append)
    engine.run(script(new_node("flow.wait", {"seconds": 0.3})))
    engine.pause()
    time.sleep(0.5)
    assert engine.state == "paused" and engine._thread.is_alive()
    engine.resume()
    assert engine.wait_finished(5)
    qapp.processEvents()
    assert results[-1]["elapsed"] >= 0.8


def test_mouse_and_keyboard_blocks(qapp, bus, tmp_path):
    engine, fake = make(bus, tmp_path)
    wf = script(new_node("mouse.click_at", {"x": 10, "y": 20, "space": "screen", "button": "right"}),
                new_node("key.combo", {"keys": "ctrl+a"}),
                new_node("key.type", {"text": "hi", "interval": 0}))
    assert run(qapp, bus, engine, wf)["result"] == "completed"
    assert ("move", 10, 20) in fake.events
    assert ("button", "right", True) in fake.events and ("button", "right", False) in fake.events
    keys = [e for e in fake.events if e[0] == "key"]
    assert keys == [("key", 0x11, True), ("key", ord("A"), True), ("key", ord("A"), False), ("key", 0x11, False)]
    assert [e[1] for e in fake.events if e[0] == "char"] == ["h", "i"]


def test_image_blocks_on_synthetic_screen(qapp, bus, tmp_path):
    from vision.image_capture import save_image
    rng = np.random.default_rng(1)
    screen = rng.integers(0, 255, (300, 400, 3), dtype=np.uint8)
    template = screen[100:140, 200:260].copy()
    save_image(template, tmp_path / "button.png")
    engine, fake = make(bus, tmp_path, capture=lambda rect: screen)
    wf = script(new_node("img.click", {"image": str(tmp_path / "button.png"), "space": "screen",
                                       "region": "0, 0, 400, 300", "timeout": 1, "output": "btn"}),
                new_node("img.exists", {"image": str(tmp_path / "button.png"), "space": "screen",
                                        "region": "0, 0, 400, 300", "output": "seen"}))
    r = run(qapp, bus, engine, wf)
    assert r["result"] == "completed", r["error"]
    assert (r["variables"]["btn_x"], r["variables"]["btn_y"]) == (230, 120) and r["variables"]["seen"] is True
    assert ("move", 230, 120) in fake.events


def test_text_blocks_with_fake_ocr(qapp, bus, tmp_path):
    words = [Word("Gold:", 10, 5, 40, 12, 0), Word("1,250", 55, 5, 40, 12, 0), Word("Play", 100, 80, 30, 12, 1)]
    blank = np.zeros((200, 200, 3), np.uint8)
    engine, fake = make(bus, tmp_path, capture=lambda rect: blank, ocr=lambda language="": FakeOcr(words))
    wf = script(new_node("txt.click", {"text": "play", "space": "screen", "region": "0,0,200,200", "timeout": 1}),
                new_node("txt.extract", {"space": "screen", "region": "0,0,200,200", "pattern": r"Gold:\s*([\d,]+)",
                                         "output": "gold"}))
    r = run(qapp, bus, engine, wf)
    assert r["result"] == "completed", r["error"]
    assert r["variables"]["gold"] == "1,250" and ("move", 115, 86) in fake.events


def test_save_and_load_variable(qapp, bus, tmp_path):
    engine, _ = make(bus, tmp_path)
    run(qapp, bus, engine, script(new_node("var.set", {"name": "best", "value": "42"}),
                                  new_node("util.save_var", {"name": "best"})))
    r = run(qapp, bus, engine, script(new_node("util.load_var", {"name": "best"})))
    assert r["variables"]["best"] == 42
