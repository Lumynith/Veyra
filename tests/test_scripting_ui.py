import time

from automation.engine import IDLE, AutomationEngine, Services
from automation.input_controller import InputController
from automation.script_library import ScriptLibrary
from blocks import registry
from blocks.custom import CustomBlockStore
from tests.test_engine import FakeInput

registry.load_builtin()


def wait_idle(qapp, engine, timeout=15):
    end = time.time() + timeout
    while time.time() < end:
        qapp.processEvents()
        if engine.state == IDLE and engine.wait_finished(0):
            for _ in range(5):
                qapp.processEvents()
            return
        time.sleep(0.01)
    raise AssertionError("engine did not finish")


def test_code_editor_marks_problems(qapp):
    from automation.script_host import validate
    from ui.scripting.code_editor import CodeEditor
    editor = CodeEditor(["log"], validate)
    editor.setPlainText("x = 1\nimport os\n")
    editor.validate_now()
    assert [p[0] for p in editor.problems] == [2]
    assert "not allowed" in editor.problem_at(2)
    editor.set_runtime_error(1)
    assert editor.textCursor().blockNumber() == 0


def test_script_page_runs_and_reports(qapp, bus, tmp_path):
    from ui.scripting.script_page import ScriptPage
    engine = AutomationEngine(bus, Services(input=InputController(FakeInput()), data_dir=tmp_path))
    made = []
    page = ScriptPage(engine, bus, ScriptLibrary(tmp_path / "scripts"), lambda *a: made.append(a))
    page.editor.setPlainText("print('hello')\noutput('n', inputs['n'] * 2)\n")
    page.inputs.setText("n=21")
    page.run()
    wait_idle(qapp, engine)
    text = page.output.toPlainText()
    assert "hello" in text and "n = 42" in text and "✓ completed" in text

    page.editor.setPlainText("a = 1\nb = a / 0\n")
    page.run()
    wait_idle(qapp, engine)
    assert "ZeroDivisionError" in page.output.toPlainText() and page.editor._runtime_line == 2

    page.name = None
    page._scripts.write("doubler", page.editor.toPlainText())
    page.refresh_list()
    assert page.list.count() == 1
    page._to_block()
    assert made and made[0][1] == "a = 1\nb = a / 0\n" and made[0][2]["inputs"] == {"n": 21}


def test_custom_blocks_page_builds_and_saves_a_no_code_block(qapp, bus, tmp_path):
    from ui.editor.properties import EditorResources
    from ui.scripting.custom_blocks_page import CustomBlocksPage
    store = CustomBlockStore(tmp_path / "blocks")
    changed = []
    bus.subscribe("blocks.changed", changed.append)
    page = CustomBlocksPage(store, bus, EditorResources(), lambda _id: False, tmp_path / "plugins")
    page.create("steps")
    page.name.setText("Collect Reward")
    page._name_edited("Collect Reward")
    assert page.block_id.text() == "custom.collect_reward"
    page.inputs.add(["times", "Times", "whole number", "3", "", "1", "10"])
    page.outputs.add(["collected", "How many were collected"])
    page.scene.append_block("var.set", page.view.center())
    assert page.save(), page.problems.text()
    for _ in range(5):
        qapp.processEvents()
    block = registry.get("custom.collect_reward")
    assert block.title == "Collect Reward" and [p.name for p in block.params] == ["times", "output"]
    assert block.params[0].kind == "int" and block.params[0].default == 3 and block.params[0].maximum == 10
    assert changed == ["custom.collect_reward"] and page.list.count() == 1
    assert page.block_id.isReadOnly()

    page.inputs.add(["1bad", "", "text", "", "", "", ""])
    assert not page.save() and "'1bad'" in page.problems.text()
    page.inputs.removeRow(1)

    store.delete("custom.collect_reward")
    assert "custom.collect_reward" not in {b.type_id for b in registry.all_blocks()}
