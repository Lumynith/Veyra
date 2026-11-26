"""Phase 8: execution controls, error handling, tracing and the emergency stop."""
import time

from automation.engine import IDLE, PAUSED
from automation.workflow import Workflow, new_node
from blocks import registry
from blocks.base_block import Block, Param
from tests.test_engine import make, run, script


class Flaky(Block):
    """Fails until it has been tried `fails` times (counted in a variable)."""
    type_id, title, category = "test.flaky", "Flaky", "Utilities"
    params = (Param("fails", "Fails", "int", 2),)

    def execute(self, ctx, p, node):
        n = ctx.vars.get("tries", 0) + 1
        ctx.set("tries", n)
        if n <= p["fails"]:
            raise RuntimeError(f"try {n} failed")
        return n


class Blocking(Block):
    """Stands in for a slow OCR / search call that does not check for Stop."""
    type_id, title, category = "test.blocking", "Blocking", "Utilities"
    params = ()

    def execute(self, ctx, p, node):
        time.sleep(0.6)
        ctx.input.move(1, 1)


registry.register(Flaky, replace=True)
registry.register(Blocking, replace=True)


def node(type_id, **fields):
    params = fields.pop("params", {})
    n = new_node(type_id, params)
    n.update(fields)
    return n


def test_retries_then_success_and_trace(qapp, bus, tmp_path):
    engine, _ = make(bus, tmp_path)
    traces = []
    bus.subscribe("engine.trace", traces.extend)
    res = run(qapp, bus, engine, script(node("test.flaky", retries=2, retry_delay=0.01)))
    assert res["result"] == "completed" and res["variables"]["tries"] == 3
    assert [t["status"] for t in traces] == ["retry", "retry", "ok"] and traces[-1]["ms"] is not None
    assert all(t["time"].count(":") == 2 for t in traces)


def test_on_error_skip_stop_and_restart(qapp, bus, tmp_path):
    engine, _ = make(bus, tmp_path)
    skip = script(node("test.flaky", on_error="continue", params={"fails": 5}), node("var.set", params={"name": "after", "value": "1"}))
    res = run(qapp, bus, engine, skip)
    assert res["result"] == "completed" and res["variables"]["after"] == 1 and "try 1" in res["variables"]["last_error"]
    res = run(qapp, bus, engine, script(node("test.flaky", params={"fails": 5})))
    assert res["result"] == "error" and "try 1 failed" in res["error"]


def test_restart_action_restarts(qapp, bus, tmp_path):
    engine, _ = make(bus, tmp_path)
    finished = []
    bus.subscribe("engine.finished", finished.append)
    engine.run(script(node("test.flaky", on_error="restart", params={"fails": 99})))
    time.sleep(0.3)
    engine.stop()
    assert engine.wait_finished(3)
    qapp.processEvents()
    assert finished[-1]["result"] == "stopped" and finished[-1]["restarts"] >= 3


def test_time_limit_interrupts_a_wait(qapp, bus, tmp_path):
    engine, _ = make(bus, tmp_path)
    t = time.perf_counter()
    res = run(qapp, bus, engine, script(node("flow.wait", timeout=0.2, params={"seconds": 5})))
    assert res["result"] == "error" and "time limit" in res["error"] and time.perf_counter() - t < 2
    res = run(qapp, bus, engine, script(node("flow.wait", timeout=0.2, on_error="continue", params={"seconds": 5}),
                                        node("var.set", params={"name": "ok", "value": "1"})))
    assert res["result"] == "completed" and res["variables"]["ok"] == 1


def test_run_selected_block_and_from_block(qapp, bus, tmp_path):
    engine, _ = make(bus, tmp_path)
    a, b, c = (node("var.set", params={"name": n, "value": "1"}) for n in "abc")
    wf = script(a, b, c)
    finished = []
    bus.subscribe("engine.finished", finished.append)
    engine.run(wf, "block", b["id"])
    engine.wait_finished(3)
    qapp.processEvents()
    assert set(finished[-1]["variables"]) == {"b"}
    engine.run(wf, "from", b["id"])
    engine.wait_finished(3)
    qapp.processEvents()
    assert set(finished[-1]["variables"]) == {"b", "c"}


def test_step_mode_and_workflow_variables(qapp, bus, tmp_path):
    engine, _ = make(bus, tmp_path)
    a, b = node("var.set", params={"name": "x", "value": "={start} + 1"}), node("var.set", params={"name": "y", "value": "2"})
    wf = Workflow.from_dict(script(a, b))
    wf.variables["start"] = 10
    engine.run(wf.to_dict(), step=True)
    time.sleep(0.15)
    assert engine.state == PAUSED and engine._rt.vars.get("x") is None  # paused before the first block
    engine.step()
    time.sleep(0.15)
    assert engine.state == PAUSED and engine._rt.vars["x"] == 11 and "y" not in engine._rt.vars
    engine.set_variable("y", 5)
    engine.resume()
    assert engine.wait_finished(3) and engine.state == IDLE


def test_stop_releases_input_while_a_block_is_busy(qapp, bus, tmp_path):
    engine, fake = make(bus, tmp_path)
    engine.run(script(node("key.hold", params={"key": "a"}), node("test.blocking")))
    time.sleep(0.2)
    engine.stop()  # the block is inside a non-cooperative call
    assert ("key", 0x41, False) in fake.events  # released at once, not when the block returns
    assert engine.wait_finished(3)
    qapp.processEvents()
    assert ("move", 1, 1) not in fake.events  # and it could not send anything afterwards
    res = run(qapp, bus, engine, script(node("mouse.move", params={"x": 3, "y": 4, "space": "screen"})))
    assert res["result"] == "completed" and ("move", 3, 4) in fake.events  # a new run works again
