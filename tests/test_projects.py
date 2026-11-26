import json

import pytest
from PySide6.QtTest import QTest
from PySide6.QtWidgets import QMessageBox

from automation import project
from automation.recording import new_recording, save_recording
from automation.workflow import Workflow, WorkflowError, new_node
from blocks import registry

registry.load_builtin()


def sample() -> Workflow:
    wf = Workflow()
    wf.add_script([new_node("flow.start"), new_node("flow.wait", {"seconds": 2.5})], (10, 20))
    return wf


class FakeEngine:
    state = "idle"

    def __init__(self):
        self.ran = []

    def run(self, wf):
        self.ran.append(wf)

    def stop(self):
        pass

    pause = resume = stop


def test_project_round_trip_and_workflow_migration(tmp_path):
    wf = sample()
    meta = project.write_project(tmp_path / "a.lcp.json", wf, project.new_meta("Farm"))
    raw = json.loads((tmp_path / "a.lcp.json").read_text("utf-8"))
    assert raw["format"] == project.FORMAT and raw["version"] == project.VERSION and raw["meta"]["name"] == "Farm"
    back, m = project.read_document(tmp_path / "a.lcp.json")
    assert back.to_dict() == wf.to_dict() and m["name"] == "Farm" and m["created"] == meta["created"]
    wf.save(tmp_path / "old.lcw.json")  # bare workflow (phase 1-6 files) still opens
    back, m = project.read_document(tmp_path / "old.lcw.json")
    assert back.to_dict() == wf.to_dict() and m["name"] == "old"
    project.export_workflow(tmp_path / "x.lcw.json", wf)
    assert json.loads((tmp_path / "x.lcw.json").read_text("utf-8"))["format"] == "lumencrest.workflow"


@pytest.mark.parametrize("doc", [
    {"format": "lumencrest.project", "version": 99, "workflow": {}},
    {"format": "lumencrest.project", "version": "x"},
    {"format": "lumencrest.project", "version": 1, "workflow": {"format": "lumencrest.workflow", "scripts": "zz"}},
    {"format": "lumencrest.workflow", "scripts": [{"blocks": [{"type": "evil.block"}]}]},
    {"format": "something else"},
    [1, 2, 3],
])
def test_bad_documents_are_rejected(tmp_path, doc):
    (tmp_path / "bad.json").write_text(json.dumps(doc), "utf-8")
    with pytest.raises(WorkflowError):
        project.read_document(tmp_path / "bad.json")


def test_wrong_parameter_types_block_execution(tmp_path):
    d = sample().to_dict()
    d["scripts"][0]["blocks"][1]["params"]["seconds"] = "soon"
    wf = Workflow.from_dict(d)
    assert any("Seconds" in p for p in wf.validate())
    d["scripts"][0]["blocks"][1]["params"]["seconds"] = -4
    assert any("between" in p for p in Workflow.from_dict(d).validate())
    d["scripts"][0]["blocks"][1]["params"]["seconds"] = "{delay}"  # variables are fine
    assert Workflow.from_dict(d).validate() == []


def test_merge_keeps_one_start():
    into, other = sample(), sample()
    other.add_script([new_node("mouse.left_click")], (0, 300))
    other.group([other.scripts[1]["id"]], "Clicks")
    assert project.merge(into, other, 400, 0) == 2
    assert len(into.start_scripts()) == 1 and into.validate() == []
    assert into.scripts[1]["pos"] == [410.0, 20.0] and into.groups[0]["scripts"] == [into.scripts[2]["id"]]
    ids = [n["id"] for n in into.nodes()]
    assert len(ids) == len(set(ids))


def test_session_lock(tmp_path):
    lock = tmp_path / "session.lock"
    assert project.acquire_session(lock) is False  # first run
    assert project.acquire_session(lock) is True   # never released: crashed
    project.release_session(lock)
    assert project.acquire_session(lock) is False


def test_editor_dirty_save_recent_autosave_and_recovery(qapp, bus, settings, tmp_path, monkeypatch):
    from ui.editor.workspace import EditorPage
    auto = tmp_path / "autosave.lcw.json"
    titles = []
    page = EditorPage(FakeEngine(), bus, auto, settings=settings)
    page.file_changed.connect(titles.append)
    page.load(sample(), None)
    assert not page.dirty and titles[-1] == "Untitled"
    page.scene.append_block("mouse.left_click", page.view.center())
    QTest.qWait(300)  # the title follows edits after a short debounce
    assert page.dirty and titles[-1] == "Untitled *"
    path = tmp_path / "farm.lcp.json"
    page.meta["name"] = "farm"
    assert page._write(path) and not page.dirty and titles[-1] == "farm"
    assert page.recent()[0].lower() == str(path.resolve()).lower()

    page.scene.append_block("mouse.right_click", page.view.center())  # unsaved change, then a "crash"
    page.write_autosave()
    unsaved = page.scene.wf.to_dict()

    asked = []
    monkeypatch.setattr(QMessageBox, "question", lambda *a, **k: asked.append(a[2]) or QMessageBox.StandardButton.Yes)
    again = EditorPage(FakeEngine(), bus, auto, settings=settings)
    again.restore_session(Workflow, crashed=True)
    assert asked and again.scene.wf.to_dict() == unsaved and again.path == path and again.dirty

    monkeypatch.setattr(QMessageBox, "question", lambda *a, **k: QMessageBox.StandardButton.No)
    declined = EditorPage(FakeEngine(), bus, auto, settings=settings)
    declined.restore_session(Workflow, crashed=True)  # reopens the saved file instead
    assert declined.path == path and not declined.dirty
    assert len(declined.scene.wf.to_dict()["scripts"]) == len(json.loads(path.read_text("utf-8"))["workflow"]["scripts"])

    monkeypatch.setattr(QMessageBox, "warning", lambda *a, **k: None)
    bad = tmp_path / "bad.lcp.json"
    bad.write_text("{nope", "utf-8")
    assert not declined.open(bad) and declined.path == path  # a failed open leaves the project alone


def test_recorder_page_converts_imported_recording(qapp, bus, settings, tmp_path):
    from ui.recorder.recorder_page import RecorderPage
    rec = new_recording(None, None)
    rec["events"] = [{"t": 0.0, "k": "down", "x": 5, "y": 5, "b": "left"}, {"t": 0.05, "k": "up", "x": 5, "y": 5,
                                                                           "b": "left"},
                     {"t": 1.0, "k": "kdown", "vk": 0x45}, {"t": 1.1, "k": "kup", "vk": 0x45}]
    save_recording(rec, tmp_path / "r.lcr.json")
    opened, engine = [], FakeEngine()
    page = RecorderPage(settings, bus, engine, vision=None, open_workflow=lambda wf, name: opened.append(wf) or True)
    assert page.load(tmp_path / "r.lcr.json")
    assert page.preview.count() > 0 and "4 recorded events" in page.stats.text()
    page.preset.setCurrentIndex(page.preset.findData("high"))
    assert page.timing.value() == 5
    page.timing.setValue(7)
    assert page.preset.currentData() == "custom" and settings.get("recorder.options")["timing_ms"] == 7
    page.convert()
    types = [n["type"] for n in opened[0].nodes()]
    assert "mouse.left_click" in types and "key.press" in types and opened[0].validate() == []
    page.play()
    assert engine.ran and engine.ran[0]["format"] == "lumencrest.workflow"
