import numpy as np
import pytest

from automation.engine import Services
from blocks import registry
from automation.input_controller import InputController
from automation.engine import AutomationEngine
from automation.workflow import new_node
from vision.text_detector import CachedOcr, Word, find_all_text, find_numbers, find_text, parse_number
from vision.text_targets import TextTargets

registry.load_builtin()

WORDS = [Word("Play", 10, 10, 40, 12, 0, 0.95), Word("Playground", 60, 10, 80, 12, 0, 0.9),
         Word("Gold:", 10, 40, 40, 12, 1, 0.9), Word("1,250", 55, 40, 40, 12, 1, 0.4),
         Word("Gems", 10, 70, 40, 12, 2), Word("2.5K", 55, 70, 30, 12, 2), Word("play", 10, 100, 30, 12, 3)]


def test_match_modes_and_confidence():
    assert len(find_all_text(WORDS, "play")) == 3  # partial, case-insensitive
    assert [m.x for m in find_all_text(WORDS, "play", mode="exact")] == [10, 10]  # not "Playground"
    assert len(find_all_text(WORDS, "Play", case_sensitive=True, mode="exact")) == 1
    assert find_text(WORDS, r"gold:\s*[\d,]+", mode="regex").text == "Gold: 1,250"
    assert find_text(WORDS, r"gold:\s*[\d,]+", mode="regex", min_conf=0.5) is None  # 1,250 is low confidence
    assert find_text(WORDS, "contains-is-partial", mode="contains") is None
    with pytest.raises(ValueError):
        find_all_text(WORDS, "(", mode="regex")


def test_numbers():
    assert parse_number("1,250") == 1250 and parse_number("2.5K") == 2500 and parse_number("-3") == -3
    assert parse_number("x12y") is None and parse_number("Lv. 7") == 7
    assert [v for v, _ in find_numbers(WORDS)] == [1250, 2500]
    assert [v for v, _ in find_numbers(WORDS, min_conf=0.5)] == [2500]


def test_ocr_cache_skips_identical_frames():
    class Counting:
        calls = 0

        def words(self, img):
            Counting.calls += 1
            return WORDS
    ocr = CachedOcr(Counting())
    a = np.zeros((20, 20, 3), np.uint8)
    ocr.words(a)
    ocr.words(a.copy())
    b = a.copy()
    b[0, 0] = 1
    ocr.words(b)
    assert Counting.calls == 2 and ocr.stats == {"hits": 1, "runs": 2}


def test_text_targets_persist(tmp_path):
    t = TextTargets(tmp_path / "t.json")
    target = t.add("Play button", text="Play", match="exact", region="10, 10, 100, 40")
    ref = t.ref(target["id"])
    t.update(ref, name="Play")
    again = TextTargets(tmp_path / "t.json")
    assert again.get(ref)["text"] == "Play" and again.label(ref) == "Play · “Play”"
    again.delete(ref)
    assert again.label(ref) == "(missing target)"
    with pytest.raises(ValueError):
        again.add("empty", text=" ")


def test_number_and_target_blocks(qapp, bus, tmp_path):
    from tests.test_engine import FakeInput, FakeOcr, run, script
    fake = FakeInput()
    targets = TextTargets(tmp_path / "t.json")
    ref = targets.ref(targets.add("Btn", text="play", match="exact")["id"])
    services = Services(input=InputController(fake), data_dir=tmp_path, text_targets=targets,
                        capture=lambda rect: np.zeros((200, 200, 3), np.uint8),
                        ocr=lambda language="": FakeOcr(WORDS))
    engine = AutomationEngine(bus, services)
    area = {"space": "screen", "region": "0,0,200,200"}
    wf = script(new_node("txt.find", {"text": "play", **area}),
                new_node("txt.find_number", {**area, "select": "largest", "output": "n"}),
                new_node("txt.wait_number", {**area, "op": ">", "value": "2000", "timeout": 1, "output": "big"}),
                new_node("txt.click", {"target": ref, "occurrence": 2, "timeout": 1, "output": "clicked", **area}))
    r = run(qapp, bus, engine, wf)
    assert r["result"] == "completed", r["error"]
    v = r["variables"]
    assert v["text"] is True and v["text_count"] == 3 and v["text_2_x"] == 100  # "Play" inside "Playground"
    assert v["n"] == 2500 and v["n_list"] == [1250, 2500] and v["big"] == 2500 and v["big_count"] == 1
    # The target (exact "play") skips "Playground", so the 2nd match is the bottom "play" at (25, 106).
    assert ("move", 25, 106) in fake.events


def test_example_play_continue_workflow(qapp, bus, tmp_path, monkeypatch):
    """Play missing → retry; Play shown → click, wait, Continue → click, done."""
    from automation.demo import build_text_demo
    from automation.engine import Runtime
    from tests.test_engine import FakeInput, run
    screens = iter([[], [Word("Play", 50, 50, 40, 12, 0)], [Word("Play", 50, 50, 40, 12, 0)],
                    [Word("Continue", 80, 90, 70, 12, 0)], [Word("Continue", 80, 90, 70, 12, 0)]])
    current = {"words": []}

    class SteppingOcr:
        def words(self, img):
            current["words"] = next(screens, current["words"])
            return current["words"]

    monkeypatch.setattr(Runtime, "wait", lambda self, s: self.checkpoint())  # skip real delays
    monkeypatch.setattr(Runtime, "area", lambda self, region, space: (1000, 500, 300, 200))
    monkeypatch.setattr(Runtime, "origin", lambda self, space: (1000, 500))
    fake = FakeInput()
    engine = AutomationEngine(bus, Services(input=InputController(fake), data_dir=tmp_path,
                                            capture=lambda rect: np.zeros((200, 300, 3), np.uint8),
                                            ocr=lambda language="": SteppingOcr()))
    r = run(qapp, bus, engine, build_text_demo().to_dict())
    assert r["result"] == "completed", r["error"]
    assert r["variables"]["done"] is True and r["variables"]["tries"] == 2
    moves = [e for e in fake.events if e[0] == "move"]
    assert moves == [("move", 1070, 556), ("move", 1115, 596)]  # Play centre, then Continue centre
