"""Built-in demonstration workflow: nested loops, If/Else, Repeat Until, Break, variables."""
from automation.workflow import Workflow, new_node


def _c(type_id: str, body: list, **params) -> dict:
    node = new_node(type_id, params)
    node["body"] = body
    return node


def build_demo() -> Workflow:
    n = new_node
    wf = Workflow()
    wf.add_script([
        n("flow.start"),
        n("flow.comment", {"text": "Demo: nested loops, branches and variables"}),
        n("var.set", {"name": "total", "value": "0"}),
        _c("flow.repeat", [
            _c("flow.repeat", [
                n("var.set", {"name": "total", "value": "={total} + 1"}),
            ], times=2, counter="inner"),
            _c("flow.if", [
                n("util.log", {"message": "Outer pass {outer}: this is the middle pass"}),
            ], left="{outer}", op="==", right="2"),
            _c("flow.else", [
                n("util.log", {"message": "Outer pass {outer}: total so far {total}"}),
            ]),
        ], times=3, counter="outer"),
        n("var.set", {"name": "n", "value": "0"}),
        _c("flow.repeat_until", [
            n("var.set", {"name": "n", "value": "={n} + 1"}),
            _c("flow.if", [n("flow.break")], left="{n}", op="==", right="4"),
        ], left="{n}", op=">=", right="5"),
        n("util.random", {"minimum": 1, "maximum": 6, "output": "dice"}),
        n("util.log", {"message": "Finished: total={total}, n={n}, dice={dice}"}),
        n("rbx.check", {"output": "connected"}),
        _c("flow.if", [n("rbx.capture")], left="{connected}", op="is true", right=""),
        _c("flow.else", [n("util.log", {"message": "Roblox not attached, skipping capture", "level": "warning"})]),
        n("flow.end"),
    ], (0, 0))
    return wf


def build_text_demo() -> Workflow:
    """Phase 5 example: find "Play", click it, wait 2 s, find "Continue"; retry until both worked."""
    n = new_node
    q = {"match": "exact", "space": "viewport"}
    wf = Workflow()
    wf.add_script([
        n("flow.start"),
        n("flow.comment", {"text": "Text demo: Play → Continue, retrying until it works"}),
        n("var.set", {"name": "done", "value": "false"}),
        n("var.set", {"name": "tries", "value": "0"}),
        _c("flow.repeat_until", [
            n("var.set", {"name": "tries", "value": "={tries} + 1"}),
            n("txt.find", {"text": "Play", "output": "play", **q}),
            _c("flow.if", [
                n("txt.click", {"text": "Play", "timeout": 2, "output": "play", **q}),
                n("flow.wait", {"seconds": 2}),
                n("txt.find", {"text": "Continue", "output": "cont", **q}),
                _c("flow.if", [
                    n("txt.click", {"text": "Continue", "timeout": 2, "output": "cont", **q}),
                    n("var.set", {"name": "done", "value": "true"}),
                ], left="{cont}", op="is true", right=""),
                _c("flow.else", [n("util.log", {"message": "Continue not visible yet (try {tries})"})]),
            ], left="{play}", op="is true", right=""),
            _c("flow.else", [
                n("util.log", {"message": "Play not visible, retrying (try {tries})"}),
                n("flow.wait", {"seconds": 1}),
            ]),
            _c("flow.if", [
                n("util.log", {"message": "Giving up after {tries} tries", "level": "warning"}),
                n("flow.break"),
            ], left="{tries}", op=">=", right="20"),
        ], left="{done}", op="is true", right=""),
        n("util.log", {"message": "Text demo finished: done={done} after {tries} tries"}),
        n("flow.end"),
    ], (0, 0))
    return wf


def build_custom_examples() -> list[dict]:
    """Example custom blocks, written to examples/custom_blocks/*.lcb.json (import them on the Custom Blocks page)."""
    from blocks.custom import blank
    n = new_node
    # No-code block: look for text, click it when it is there, then pause.
    steps = blank("steps", "Click Text If Visible")
    steps.update(id="custom.click_text_if_visible", category="Custom", icon="text",
                 description="Clicks the text when it appears within the timeout, then waits. "
                             "Never fails: 'clicked' tells you whether it was there.",
                 inputs=[{"name": "label", "label": "Text", "type": "text", "default": "Play"},
                         {"name": "after", "label": "Wait after (s)", "type": "number", "default": 1}],
                 outputs=[{"name": "clicked"}])
    q = {"text": "{label}", "match": "exact", "space": "viewport"}
    wf = Workflow()
    wf.add_script([n("flow.start"),
                   n("var.set", {"name": "clicked", "value": "false"}),
                   n("txt.find", {**q, "timeout": 3, "output": "seen"}),
                   _c("flow.if", [n("txt.click", {**q, "timeout": 1, "output": "hit"}),
                                  n("var.set", {"name": "clicked", "value": "true"}),
                                  n("flow.wait", {"seconds": "{after}"})], left="{seen}", op="is true", right="")],
                  (0, 0))
    steps["workflow"] = wf.to_dict()
    # Script block: pure computation, needs no permissions.
    script = blank("script", "Format Duration")
    script.update(id="custom.format_duration", category="Custom", icon="script",
                  description="Turns a number of seconds into text like 1h 02m 03s.",
                  inputs=[{"name": "seconds", "label": "Seconds", "type": "number", "default": 0}],
                  outputs=[{"name": "text"}],
                  script="total = int(inputs['seconds'])\n"
                         "h, rest = divmod(total, 3600)\n"
                         "m, s = divmod(rest, 60)\n"
                         "output('text', f'{h}h {m:02d}m {s:02d}s' if h else f'{m}m {s:02d}s')\n")
    return [steps, script]


if __name__ == "__main__":  # python -m automation.demo  → regenerates examples/
    import json
    from pathlib import Path
    from blocks import registry
    registry.load_builtin()
    out = Path(__file__).resolve().parent.parent / "examples"
    (out / "custom_blocks").mkdir(parents=True, exist_ok=True)
    for d in build_custom_examples():
        path = out / "custom_blocks" / f"{d['id'].split('.', 1)[1]}.lcb.json"
        path.write_text(json.dumps(d, indent=2, ensure_ascii=False), "utf-8")
        print("wrote", path)
