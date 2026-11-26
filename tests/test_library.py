from datetime import datetime, timedelta

import pytest
from PySide6.QtWidgets import QInputDialog, QMessageBox

from automation import macro_library, project
from automation.macro_library import MacroLibrary, safe_name
from automation.workflow import Workflow, new_node
from blocks import registry
from tests.conftest import dispose

registry.load_builtin()


class FakeEngine:
    state = "idle"

    def __init__(self):
        self.ran = []

    def run(self, wf, *args):
        self.ran.append(wf)

    def stop(self):
        pass

    pause = resume = emergency_stop = stop


def sample(n: int = 1) -> Workflow:
    wf = Workflow()
    wf.add_script([new_node("flow.start")] + [new_node("flow.wait", {"seconds": 1})] * n, (0, 0))
    return wf


@pytest.fixture
def recycled(monkeypatch):
    """Record deletes instead of filling the real Recycle Bin."""
    gone = []
    monkeypatch.setattr(macro_library, "_recycle", lambda p: (gone.append(p), p.unlink() if p.is_file() else None))
    return gone


def test_library_saves_organises_and_recovers_from_bad_files(tmp_path, recycled):
    lib = MacroLibrary(tmp_path / "macros")
    assert lib.entries() == [] and lib.folders() == []
    a = lib.add(sample(2), "Auto farm")
    b = lib.add(sample(), "Auto farm")  # same name: numbered, never overwritten
    assert a.name == "Auto farm.lcp.json" and b.name == "Auto farm (2).lcp.json"
    assert {e.blocks for e in lib.entries()} == {2, 3}
    assert lib.contains(a) and not lib.contains(tmp_path / "x.lcp.json")

    renamed = lib.rename(b, "Fishing: night/day?")
    assert renamed.name == "Fishing nightday.lcp.json" and not b.exists()
    assert project.read_document(renamed)[1]["name"] == "Fishing: night/day?"  # display name keeps its characters
    with pytest.raises(ValueError):
        lib.rename(renamed, "???")

    folder = lib.new_folder("Grinding")
    moved = lib.move(renamed, folder)
    assert moved.parent.name == "Grinding" and lib.contains(moved)
    assert {e.folder for e in lib.entries()} == {"", "Grinding"}
    copy = lib.duplicate(moved)
    assert copy.parent == moved.parent and project.read_document(copy)[1]["name"].endswith(" copy")

    outside = tmp_path / "shared.lcw.json"
    project.export_workflow(outside, sample(4))
    imported = lib.import_file(outside, "Grinding")
    assert imported.parent.name == "Grinding" and outside.exists()

    (tmp_path / "macros" / "broken.lcp.json").write_text("{nope", "utf-8")
    broken = next(e for e in lib.entries() if e.name == "broken")
    assert broken.error  # listed, so it can be deleted, but marked
    lib.delete(broken.path)
    assert recycled == [broken.path] and not broken.path.exists()

    pairs = lib.delete_folder("Grinding")  # macros survive: they move to the top level
    assert len(pairs) == 3 and all(new.parent == lib.root and new.exists() for _, new in pairs)
    assert lib.folders() == [] and len(lib.entries()) == 4


def test_safe_name():
    assert safe_name(' a<b>:"c" . ') == "abc" and safe_name("CON") == "" and safe_name("x" * 200) == "x" * 80


def test_page_saves_opens_renames_and_deletes_with_the_editor(qapp, bus, settings, tmp_path, monkeypatch, recycled):
    from ui.editor.workspace import EditorPage
    from ui.library.library_page import LibraryPage, ago
    engine = FakeEngine()
    editor = EditorPage(engine, bus, tmp_path / "auto.lcw.json", settings=settings)
    editor.load(sample(), None)
    shown = []
    page = LibraryPage(MacroLibrary(tmp_path / "macros"), editor, lambda: shown.append(1))
    assert page.stack.currentIndex() == 1  # empty state
    assert not page.open_btn.isEnabled()

    monkeypatch.setattr(QInputDialog, "getText", lambda *a, **k: ("Jump test", True))
    page.save_current()
    assert editor.path and page.lib.contains(editor.path) and not editor.dirty
    assert page.tree.topLevelItemCount() == 1 and page.selected() == editor.path

    monkeypatch.setattr(QInputDialog, "getText", lambda *a, **k: ("Jump forever", True))
    page.rename_selected()  # the open project follows the rename
    assert editor.path.name == "Jump forever.lcp.json" and editor.meta["name"] == "Jump forever"

    editor.new()
    assert page.open_selected() and editor.path.name == "Jump forever.lcp.json" and shown
    page.run_selected()
    assert len(engine.ran) == 1 and engine.ran[0]["scripts"]

    page.search.setText("nothing like this")
    assert page.stack.currentIndex() == 2 and not page.open_btn.isEnabled()
    page.search.clear()

    monkeypatch.setattr(QMessageBox, "question", lambda *a, **k: QMessageBox.StandardButton.Yes)
    page.tree.setCurrentItem(page.tree.topLevelItem(0))
    page.delete_selected()  # the open project's file is gone: what is on the canvas becomes unsaved
    assert recycled and editor.path is None and editor.dirty and page.stack.currentIndex() == 1

    now = datetime(2026, 1, 10, 12, 0)
    assert ago(now, now) == "just now" and ago(now - timedelta(minutes=5), now) == "5 min ago"
    assert ago(now - timedelta(days=30), now) == "11 Dec 2025"
    qapp.processEvents()  # let the editor's queued scroll-to-top run before its items go
    dispose(qapp, page, editor)
