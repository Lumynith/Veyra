from PySide6.QtCore import QPointF

from automation.demo import build_demo
from automation.workflow import Workflow
from blocks import registry
from ui.editor.canvas import ARM, H, WorkflowScene

registry.load_builtin()


def drag(sc, node_id, to: QPointF, single=False):
    """Press exactly on the block's top-left so the stack's top-left lands on `to`."""
    start = sc.items_by_id[node_id].pos()
    sc.begin_drag(node_id, start, single)
    sc.drag_to(to)
    sc.end_drag()


def ids(blocks):
    return [b["type"] for b in blocks]


def test_layout_matches_model(qapp):
    sc = WorkflowScene()
    sc.set_workflow(build_demo())
    assert len(sc.items_by_id) == sum(1 for _ in sc.wf.nodes())
    main = sc.wf.scripts[0]["blocks"]
    repeat = next(n for n in main if n["type"] == "flow.repeat")
    item = sc.items_by_id[repeat["id"]]
    child = sc.items_by_id[repeat["body"][0]["id"]]
    assert child.pos() == item.pos() + QPointF(ARM, item.header)  # nested inside the C arm
    assert item.h > 3 * H


def test_drop_into_loop_body_and_detach_tail(qapp):
    sc = WorkflowScene()
    sc.set_workflow(build_demo())
    main = sc.wf.scripts[0]["blocks"]
    repeat = next(n for n in main if n["type"] == "flow.repeat")
    wait_id = sc.add_block("flow.wait", QPointF(900, 900))
    assert len(sc.wf.scripts) == 2  # far away: its own stack

    first_child = sc.items_by_id[repeat["body"][0]["id"]].pos()
    drag(sc, wait_id, first_child + QPointF(6, 4))  # near enough to snap
    assert repeat["body"][0]["id"] == wait_id and len(sc.wf.scripts) == 1

    # Dragging a middle block carries everything below it into a new stack.
    n_before = len(main)
    set_n = next(i for i, n in enumerate(main) if n["type"] == "var.set" and n["params"]["name"] == "n")
    drag(sc, main[set_n]["id"], QPointF(1500, 0))
    assert len(main) == set_n and len(sc.wf.scripts) == 2
    tail = sc.wf.scripts[1]
    assert len(main) + len(tail["blocks"]) == n_before

    # Snap it back under the last block of the Start stack.
    last = sc.items_by_id[main[-1]["id"]]
    drag(sc, tail["blocks"][0]["id"], last.pos() + QPointF(0, last.h))
    assert len(sc.wf.scripts) == 1 and len(main) == n_before and not sc.wf.validate()


def test_snap_above_and_structural_rules(qapp):
    sc = WorkflowScene()
    log_id = sc.add_block("util.log", QPointF(0, 0), snap=False)
    start_id = sc.add_block("flow.start", QPointF(400, 400), snap=False)
    start = sc.items_by_id[start_id]
    drag(sc, start_id, QPointF(0, -start.h))  # Start attaches above the Log stack
    assert len(sc.wf.scripts) == 1 and ids(sc.wf.scripts[0]["blocks"]) == ["flow.start", "util.log"]

    end_id = sc.add_block("flow.end", QPointF(500, 500), snap=False)
    drag(sc, end_id, sc.items_by_id[log_id].pos())  # End may not go between Start and Log...
    assert len(sc.wf.scripts) == 2
    log_item = sc.items_by_id[log_id]
    drag(sc, end_id, log_item.pos() + QPointF(0, log_item.h))  # ...but may close the stack
    assert ids(sc.wf.scripts[0]["blocks"]) == ["flow.start", "util.log", "flow.end"]

    wait_id = sc.add_block("flow.wait", QPointF(700, 700), snap=False)
    end_item = sc.items_by_id[end_id]
    drag(sc, wait_id, end_item.pos() + QPointF(0, end_item.h))  # nothing after a cap
    assert len(sc.wf.scripts) == 2


def test_alt_drag_moves_single_block(qapp):
    sc = WorkflowScene()
    sc.set_workflow(build_demo())
    main = sc.wf.scripts[0]["blocks"]
    count = len(main)
    drag(sc, main[2]["id"], QPointF(2000, 0), single=True)
    assert len(main) == count - 1 and len(sc.wf.scripts[1]["blocks"]) == 1


def test_collapse_duplicate_delete_group_and_round_trip(qapp):
    sc = WorkflowScene()
    sc.set_workflow(build_demo())
    main = sc.wf.scripts[0]["blocks"]
    repeat = next(n for n in main if n["type"] == "flow.repeat")
    sc.toggle_collapse(repeat["id"])
    assert not sc.items_by_id[repeat["body"][0]["id"]].isVisible()
    assert sc.items_by_id[repeat["id"]].h == sc.items_by_id[repeat["id"]].header

    count = len(main)
    sc.items_by_id[repeat["id"]].setSelected(True)
    sc.duplicate_selected()
    assert len(main) == count + 1 and main[main.index(repeat) + 1]["type"] == "flow.repeat"
    clone = main[main.index(repeat) + 1]
    assert clone["id"] != repeat["id"] and clone["body"][0]["id"] != repeat["body"][0]["id"]

    sc.clearSelection()
    sc.items_by_id[clone["id"]].setSelected(True)
    sc.remove_selected()
    assert len(main) == count and clone["id"] not in sc.items_by_id

    sc.items_by_id[main[0]["id"]].setSelected(True)
    group = sc.group_selected("Main")
    assert group["scripts"] == [sc.wf.scripts[0]["id"]] and group["id"] in sc.groups_by_id

    data = sc.wf.to_dict()
    sc2 = WorkflowScene()
    sc2.set_workflow(Workflow.from_dict(data))
    assert sc2.wf.to_dict() == data and len(sc2.items_by_id) == len(sc.items_by_id)
