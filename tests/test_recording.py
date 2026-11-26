import json

import pytest

from automation.recording import (Options, PRESETS, clean_events, load_recording, new_recording, save_recording,
                                  to_actions, to_workflow, validate_recording)
from blocks import registry

registry.load_builtin()
A, S, CTRL, LSHIFT = 0x41, 0x53, 0xA2, 0xA0


def mv(t, x, y):
    return {"t": t, "k": "move", "x": x, "y": y}


def btn(t, k, x, y, b="left"):
    return {"t": t, "k": k, "x": x, "y": y, "b": b}


def key(t, k, vk):
    return {"t": t, "k": k, "vk": vk}


def kinds(actions):
    return [a["a"] for a in actions]


def test_clicks_double_clicks_drags_and_scroll():
    ev = [btn(0.0, "down", 100, 100), btn(0.08, "up", 101, 100),            # click
          btn(1.0, "down", 200, 50), btn(1.05, "up", 200, 50),                # double click
          btn(1.2, "down", 201, 51), btn(1.25, "up", 201, 51),
          btn(2.0, "down", 10, 10, "right"), btn(2.1, "up", 10, 10, "right"),  # right click
          btn(3.0, "down", 0, 0), mv(3.1, 50, 50), btn(3.3, "up", 100, 100),  # drag
          {"t": 4.0, "k": "wheel", "x": 5, "y": 5, "d": -1}, mv(4.05, 6, 5),
          {"t": 4.1, "k": "wheel", "x": 6, "y": 5, "d": -1}, {"t": 4.2, "k": "wheel", "x": 6, "y": 5, "d": -1}]
    acts = to_actions(ev, Options(moves="clicks"))
    assert kinds(acts) == ["click", "click", "click", "drag", "scroll"]
    assert acts[1]["count"] == 2 and acts[2]["b"] == "right" and acts[4]["amount"] == -3
    assert (acts[3]["x2"], acts[3]["y2"]) == (100, 100)


def test_keys_taps_holds_combos_and_autorepeat():
    ev = [key(0.0, "kdown", A), key(0.1, "kup", A),                                    # tap
          key(1.0, "kdown", CTRL), key(1.1, "kdown", S), key(1.15, "kup", S), key(1.2, "kup", CTRL),  # ctrl+s
          key(2.0, "kdown", 0x57), key(2.03, "kdown", 0x57), key(2.06, "kdown", 0x57),  # W auto-repeat…
          btn(2.5, "down", 5, 5), btn(2.55, "up", 5, 5),                               # …clicked while held
          key(3.0, "kup", 0x57), key(3.5, "kup", 0x44)]                                # D was held before start
    acts = to_actions(ev, Options(moves="clicks"))
    assert kinds(acts) == ["key", "combo", "key_down", "click", "key_up"]
    assert acts[0]["key"] == "a" and acts[0]["hold"] == pytest.approx(0.1) and acts[1]["keys"] == "ctrl+s"
    assert acts[2]["key"] == "w"
    shift = [key(0, "kdown", LSHIFT), key(0.1, "kdown", A), key(0.2, "kup", A), key(0.3, "kup", LSHIFT)]
    assert to_actions(shift, Options())[0]["keys"] == "shift+a"


def test_movement_filtering_and_trailing_moves():
    ev = [mv(i * 0.01, i, 0) for i in range(100)] + [btn(1.0, "down", 99, 0), btn(1.05, "up", 99, 0)]
    ev += [mv(1.2, 300, 300), mv(1.3, 400, 400)]  # heading to the Stop button: dropped
    fine = to_actions(ev, Options(**PRESETS["exact"]))
    coarse = to_actions(ev, Options(**PRESETS["medium"]))
    assert kinds(coarse) == ["path", "click"]
    assert len(fine[0]["points"]) == 100 and 2 <= len(coarse[0]["points"]) <= 25
    assert coarse[0]["points"][-1][:2] == (99, 0)  # the path always ends where the pointer stopped
    assert kinds(to_actions(ev, Options(**PRESETS["low"]))) == ["click"]
    assert clean_events([mv(0, 1, 1), mv(0.1, 1, 1)]) == []


def test_conversion_to_editable_blocks_with_timing():
    rec = new_recording((1000, 500), (800, 600))
    rec["events"] = [mv(0.0, 1100, 600), mv(0.2, 1150, 650), btn(0.3, "down", 1150, 650), btn(0.35, "up", 1150, 650),
                     key(1.337, "kdown", 0x45), key(1.4, "kup", 0x45),
                     btn(2.0, "down", 1010, 510, "right"), btn(2.05, "up", 1010, 510, "right")]
    wf, stats = to_workflow(rec, Options(timing_ms=100, speed=1.0, repeat=2))
    script = wf.scripts[0]["blocks"]
    assert [b["type"] for b in script[:2]] == ["flow.start", "flow.comment"] and script[2]["type"] == "flow.repeat"
    body = script[2]["body"]
    types = [b["type"] for b in body]
    assert types == ["mouse.path", "flow.wait", "mouse.left_click", "flow.wait", "key.press", "flow.wait",
                     "mouse.move", "mouse.right_click"]
    assert body[0]["params"]["path"] == "100,100,0; 150,150,200" and body[0]["params"]["space"] == "viewport"
    assert body[3]["params"]["seconds"] == 1.0  # 0.987 s rounded to 100 ms
    assert body[4]["params"] == {"key": "e", "hold": 0.1}
    assert (body[6]["params"]["x"], body[6]["params"]["y"]) == (10, 10)  # viewport-relative
    assert wf.validate() == [] and stats["blocks"] == len(body)
    fast, _ = to_workflow(rec, Options(timing_ms=1, speed=2.0))
    waits = [b["params"]["seconds"] for b in fast.scripts[0]["blocks"] if b["type"] == "flow.wait"]
    assert waits[1] == pytest.approx(0.4935, abs=0.001)


def test_recording_files_are_validated(tmp_path):
    rec = new_recording(None, None)
    rec["events"] = [mv(0, 1, 2), key(0.5, "kdown", A)]
    save_recording(rec, tmp_path / "r.json")
    assert load_recording(tmp_path / "r.json")["events"][1]["vk"] == A
    for bad in ({"format": "x"}, {**rec, "events": [{"t": 0, "k": "explode"}]},
                {**rec, "events": [mv(1, 0, 0), mv(0.5, 0, 0)]}, {**rec, "version": 99}):
        with pytest.raises(ValueError):
            validate_recording(json.loads(json.dumps(bad)))


def test_real_hooks_record_and_filter(qapp):
    """Drives the real Windows hooks with SendInput (moves and F13, which nothing uses)."""
    import time as _time
    from automation.input_controller import SendInputBackend
    from automation.recorder import InputRecorder, VK_F9
    out = SendInputBackend()
    x, y = out.cursor()
    hotkeys = []
    rec = InputRecorder(on_hotkey=hotkeys.append, exclude_pid=0, include_injected=True)
    rec.start()
    try:
        for i in range(1, 6):
            out.move(x + i * 4, y)
            _time.sleep(0.02)
        out.key(0x7C, True)
        out.key(0x7C, False)
        out.key(VK_F9, True)   # hotkey: reported, not recorded
        out.key(VK_F9, False)
        _time.sleep(0.1)
        rec.pause()
        out.key(0x7C, True)    # ignored while paused
        out.key(0x7C, False)
        _time.sleep(0.1)
        rec.resume()
    finally:
        data = rec.stop()
        out.move(x, y)
    kinds = [e["k"] for e in data["events"]]
    assert kinds.count("move") >= 3 and kinds.count("kdown") == 1 and kinds.count("kup") == 1
    assert hotkeys == ["pause"] and all(e.get("vk") != VK_F9 for e in data["events"])
    assert rec.state == "idle" and data["events"][-1]["t"] < 1.0

    strict = InputRecorder(exclude_pid=0)  # default: software input is ignored
    strict.start()
    out.key(0x7C, True)
    out.key(0x7C, False)
    _time.sleep(0.05)
    assert strict.stop()["events"] == []
