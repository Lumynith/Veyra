import threading
import time
from pathlib import Path

import pytest

from automation.engine import AutomationEngine, Services
from automation.input_controller import InputController
from automation.sandbox import parse_inputs
from automation.script_host import validate
from automation.workflow import Workflow, new_node
from blocks import registry
from blocks.custom import CustomBlockStore, blank, validate as validate_block
from tests.test_engine import FakeInput, run, script

registry.load_builtin()
ROOT = Path(__file__).resolve().parent.parent


def engine_for(bus, tmp_path):
    fake = FakeInput()
    return AutomationEngine(bus, Services(input=InputController(fake), data_dir=tmp_path)), fake


def steps_block(**over) -> dict:
    d = blank("steps", "Add Numbers")
    d.update(id="custom.add_numbers", inputs=[
        {"name": "a", "label": "A", "type": "whole number", "default": 2},
        {"name": "b", "label": "B", "type": "number", "default": 0.5},
        {"name": "mode", "label": "Mode", "type": "dropdown", "options": ["sum", "double"], "default": "sum"}],
        outputs=[{"name": "total"}])
    wf = Workflow()
    wf.add_script([new_node("flow.start"),
                   new_node("var.set", {"name": "total", "value": "={a} + {b}"}),
                   {**new_node("flow.if", {"left": "{mode}", "op": "==", "right": "double"}),
                    "body": [new_node("var.set", {"name": "total", "value": "={total} * 2"})]},
                   new_node("var.set", {"name": "scratch", "value": "local only"})], (0, 0))
    d["workflow"] = wf.to_dict()
    d.update(over)
    return d


def test_no_code_block_runs_with_its_own_scope(qapp, bus, tmp_path):
    store = CustomBlockStore(tmp_path / "blocks")
    store.save(steps_block())
    assert CustomBlockStore(tmp_path / "blocks").load_all() == 1  # persisted and reloadable
    engine, _ = engine_for(bus, tmp_path)
    wf = script(new_node("var.set", {"name": "x", "value": "10"}),
                new_node("custom.add_numbers", {"a": "{x}", "mode": "double"}),
                new_node("custom.add_numbers", {"a": 1, "output": "second"}))
    r = run(qapp, bus, engine, wf)
    assert r["result"] == "completed", r["error"]
    v = r["variables"]
    assert v["total"] == 21 and v["second_total"] == 1.5
    assert "scratch" not in v and "a" not in v and v["x"] == 10  # body variables stay inside
    registry.unregister("custom.add_numbers")


def test_block_validation_catches_mistakes():
    d = steps_block(id="Bad Id", inputs=[{"name": "1x", "type": "dropdown"}], outputs=[{"name": "o"}, {"name": "o"}])
    problems = " | ".join(validate_block(d))
    for expected in ("custom.my_block", "'1x'", "needs options", "same name"):
        assert expected in problems
    d = blank("script")
    d.update(id="custom.s", script="import os")
    assert "Module 'os'" in " ".join(validate_block(d))
    CustomBlockStore(Path("unused")).install(steps_block())  # registers it
    looped = steps_block()
    looped["workflow"]["scripts"][0]["blocks"].append(new_node("custom.add_numbers"))
    assert "cannot use itself" in " ".join(validate_block(looped))
    registry.unregister("custom.add_numbers")


def test_script_block_permissions_outputs_and_errors(qapp, bus, tmp_path):
    d = blank("script", "Greeter")
    d.update(id="custom.greeter", inputs=[{"name": "who", "type": "text", "default": "Bob"}],
             outputs=[{"name": "greeting"}],
             script="print('working')\noutput('greeting', 'Hi ' + inputs['who'])\noutput('junk', 1)\n"
                    "if inputs['who'] == 'mouse':\n    click()\n")
    store = CustomBlockStore(tmp_path / "blocks")
    store.save(d)
    engine, fake = engine_for(bus, tmp_path)
    printed = []
    bus.subscribe("script.output", printed.append)
    r = run(qapp, bus, engine, script(new_node("custom.greeter", {"who": "Ann"})))
    assert r["result"] == "completed", r["error"]
    assert r["variables"]["greeting"] == "Hi Ann" and "junk" not in r["variables"]  # only declared outputs
    assert "working" in "".join(printed)
    r = run(qapp, bus, engine, script(new_node("custom.greeter", {"who": "mouse"})))
    assert r["result"] == "error" and "'input' permission" in r["error"] and "Line 5" in r["error"]
    d["permissions"] = {"input": True}
    store.save(d)
    r = run(qapp, bus, engine, script(new_node("custom.greeter", {"who": "mouse"})))
    assert r["result"] == "completed", r["error"]
    assert fake.events[-2:] == [("button", "left", True), ("button", "left", False)]
    registry.unregister("custom.greeter")


def test_run_script_block_and_stop_kills_script(qapp, bus, tmp_path):
    (tmp_path / "scripts").mkdir()
    (tmp_path / "scripts" / "count.py").write_text("set_var('seen', get_var('n') * 2)\noutput('done', True)\n")
    engine, _ = engine_for(bus, tmp_path)
    r = run(qapp, bus, engine, script(new_node("var.set", {"name": "n", "value": "4"}),
                                      new_node("script.run", {"script": "count", "allow_variables": True,
                                                              "output": "job"})))
    assert r["result"] == "completed", r["error"]
    assert r["variables"]["seen"] == 8 and r["variables"]["job_done"] is True

    hang = new_node("script.run", {"timeout": 0, "source": "while True:\n    pass\n"})
    threading.Timer(1.0, engine.stop).start()
    started = time.perf_counter()
    r = run(qapp, bus, engine, script(hang))
    assert r["result"] == "stopped" and time.perf_counter() - started < 5


def test_developer_plugin_api(qapp, bus, tmp_path):
    registry.load_plugins(ROOT / "plugins")
    assert registry.plugin_status["example_block.py"].startswith("loaded: custom.example_short_number")
    engine, _ = engine_for(bus, tmp_path)
    r = run(qapp, bus, engine, script(new_node("custom.example_short_number", {"value": "1,250"})))
    assert r["result"] == "completed", r["error"]
    assert r["variables"]["short_text"] == "1.25K" and r["variables"]["short_success"] is True
    bad = tmp_path / "bad_plugin.py"
    bad.write_text("from blocks.base_block import BaseBlock\nclass X(BaseBlock):\n    BLOCK_ID = 'img.find'\n")
    registry.load_plugins(tmp_path)
    assert registry.plugin_status["bad_plugin.py"].startswith("failed")  # cannot replace a built-in block
    assert registry.get("img.find").__module__ == "blocks.categories.image"


def test_inputs_parsing_and_validator():
    assert parse_inputs("count=3; name = Bob\nflag=true") == {"count": 3, "name": "Bob", "flag": True}
    with pytest.raises(ValueError):
        parse_inputs("oops")
    assert validate("x = 1\n") == []
    assert validate("def f(:\n")[0][0] == 1
    assert [p[0] for p in validate("a = 1\nimport os\nb = ().__class__\n")] == [2, 3]


def test_example_custom_blocks_are_valid_and_run(qapp, bus, tmp_path):
    import json
    from automation.demo import build_custom_examples
    files = sorted((ROOT / "examples" / "custom_blocks").glob("*.lcb.json"))
    ds = [json.loads(f.read_text("utf-8")) for f in files]
    assert sorted(d["id"] for d in ds) == sorted(d["id"] for d in build_custom_examples())  # files are current
    store = CustomBlockStore(tmp_path / "blocks")
    for d in ds:
        assert validate_block(d) == [], d["id"]
        store.save(d)
    engine, _ = engine_for(bus, tmp_path)
    r = run(qapp, bus, engine, script(new_node("custom.format_duration", {"seconds": 3723})))
    assert r["result"] == "completed", r["error"]
    assert r["variables"]["text"] == "1h 02m 03s"
    for d in ds:
        registry.unregister(d["id"])
