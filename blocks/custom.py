"""User-made blocks, saved as JSON in data/custom_blocks/ and registered like built-in blocks.

A definition's body is either
  "steps":  a small workflow built from existing blocks (no code). Inputs are variables inside it;
            variables named like the outputs are handed back to the workflow.
  "script": Python run in the sandbox. inputs['name'] holds the inputs; output(name, value) returns values.
"""
from __future__ import annotations

import json
import logging
import os
import re
from pathlib import Path

from automation.workflow import Workflow, WorkflowError, new_node
from blocks import registry
from blocks.base_block import Block, Param

log = logging.getLogger(__name__)
FORMAT = "lumencrest.block"
ID = re.compile(r"custom\.[a-z0-9_]{1,40}")
# Friendly input types shown in the builder → Param kinds the properties panel knows how to edit.
INPUT_TYPES = {"text": "str", "number": "float", "whole number": "int", "true/false": "bool",
               "dropdown": "choice", "image": "image", "region": "region", "key": "key"}
RESERVED = {"output", "inputs", "source"}
MAX_DEPTH = 16


def slug(title: str) -> str:
    return re.sub(r"[^a-z0-9]+", "_", title.lower()).strip("_")[:40] or "block"


def blank(kind: str = "steps", title: str = "New block") -> dict:
    wf = Workflow()
    wf.add_script([new_node("flow.start"),
                   new_node("flow.comment", {"text": "Inputs are {variables} here. Set variables named "
                                                     "like your outputs to return them."})], (0, 0))
    return {"format": FORMAT, "version": 1, "id": "", "title": title, "description": "", "category": "Custom",
            "icon": "blocks", "inputs": [], "outputs": [], "body": kind, "workflow": wf.to_dict(),
            "script": "# inputs['name'] holds each input; output('name', value) returns a value.\n"
                      "log('Hello from', inputs)\n",
            "permissions": {}, "timeout": 30.0}


def _default(inp: dict):
    kind, value = INPUT_TYPES[inp["type"]], inp.get("default", "")
    if kind == "bool":
        return value in (True, "true", "True", "1", 1, "yes")
    if kind in ("int", "float"):
        return (int if kind == "int" else float)(float(value or 0))
    if kind == "choice":
        options = inp.get("options", [])
        return value if value in options else (options[0] if options else "")
    return str(value)


def validate(d: dict) -> list[str]:
    problems = []
    if not ID.fullmatch(d.get("id", "")):
        problems.append("Block id must look like custom.my_block (lowercase letters, digits, _)")
    if not d.get("title", "").strip():
        problems.append("The block needs a name")
    seen = set()
    for i, inp in enumerate(d.get("inputs", []), 1):
        name = inp.get("name", "")
        if not name.isidentifier() or name.startswith("_") or name in RESERVED:
            problems.append(f"Input {i}: '{name}' is not a valid name (letters, digits, _; not {', '.join(RESERVED)})")
        if name in seen:
            problems.append(f"Input '{name}' is used twice")
        seen.add(name)
        if inp.get("type") not in INPUT_TYPES:
            problems.append(f"Input '{name}': unknown type {inp.get('type')!r}")
            continue
        if inp["type"] == "dropdown" and not inp.get("options"):
            problems.append(f"Input '{name}': a dropdown needs options")
        try:
            _default(inp)
        except ValueError:
            problems.append(f"Input '{name}': default {inp.get('default')!r} is not a number")
    outs = [o.get("name", "") for o in d.get("outputs", [])]
    for name in outs:
        if not name.isidentifier() or name.startswith("_"):
            problems.append(f"Output '{name}' is not a valid name")
    if len(set(outs)) != len(outs):
        problems.append("Two outputs have the same name")
    if d.get("body") == "script":
        from automation.script_host import validate as check_script
        problems += [f"Script line {line}: {msg}" for line, _, msg in check_script(d.get("script", ""))]
    elif d.get("body") == "steps":
        try:
            wf = Workflow.from_dict(d.get("workflow", {}))
            problems += wf.validate()
            if any(n["type"] == d.get("id") for n in wf.nodes()):
                problems.append("A block cannot use itself")
        except WorkflowError as e:
            problems.append(str(e))
    else:
        problems.append("Body must be 'steps' or 'script'")
    return problems


def make_block(d: dict) -> type[Block]:
    params = tuple(Param(i["name"], i.get("label") or i["name"], INPUT_TYPES[i["type"]], _default(i),
                         tuple(i.get("options", ())),
                         i.get("min") if i.get("min") not in ("", None) else None,
                         i.get("max") if i.get("max") not in ("", None) else None,
                         i.get("tooltip", "")) for i in d["inputs"])
    params += (Param("output", "Output prefix", "var", "",
                     tooltip="Blank: outputs keep their own names. Otherwise they become {prefix}_{name}"),)
    outputs = ", ".join("{" + o["name"] + "}" for o in d["outputs"])
    first = d["inputs"][0]["name"] if d["inputs"] else ""
    attrs = {
        "type_id": d["id"], "title": d["title"].strip(), "category": d.get("category") or "Custom",
        "icon": d.get("icon", ""), "params": params, "definition": d, "user_defined": True,
        "description": (d.get("description", "").strip() + (f"\nOutputs: {outputs}" if outputs else "")).strip(),
        "summary_fmt": "{" + first + "}" if first else "",
        "execute": lambda self, ctx, p, node: run_custom(ctx, d, p),
    }
    return type(f"Custom_{d['id'].split('.', 1)[1]}", (Block,), attrs)


def run_custom(ctx, d: dict, p: dict):
    from automation.engine import BlockFailed, BreakLoop, EndWorkflow
    inputs = {i["name"]: p[i["name"]] for i in d["inputs"]}
    if d["body"] == "script":
        from automation.sandbox import run_script
        from blocks.categories.scripting import store_outputs
        outputs = run_script(ctx, d["script"], inputs, d.get("permissions", {}), float(d.get("timeout", 30)),
                             on_print=lambda text: ctx.emit("script.output", text))
        declared = {o["name"] for o in d["outputs"]}
        store_outputs(ctx, p["output"], {k: v for k, v in outputs.items() if not declared or k in declared})
        return True

    depth = getattr(ctx, "custom_depth", 0)
    if depth >= MAX_DEPTH:
        raise RecursionError("Custom blocks are nested too deeply (does a block use itself?)")
    blocks = Workflow.from_dict(d["workflow"]).start_scripts()[0]["blocks"][1:]
    caller_vars, ctx.vars = ctx.vars, dict(inputs)  # the body has its own variables
    ctx.custom_depth = depth + 1
    try:
        ctx.run_blocks(blocks)
    except EndWorkflow as e:
        if e.stopped:  # Stop Macro stops everything; End just leaves the block
            raise
    except BreakLoop:
        pass
    except BlockFailed as e:  # report as "My Block: Find Image: …"
        raise RuntimeError(str(e)) from e
    finally:
        local, ctx.vars, ctx.custom_depth = ctx.vars, caller_vars, depth
    prefix = p["output"].strip()
    for o in d["outputs"]:
        if o["name"] in local:
            ctx.set(f"{prefix}_{o['name']}" if prefix else o["name"], local[o["name"]])
    return True


class CustomBlockStore:
    def __init__(self, folder: Path) -> None:
        self.folder = Path(folder)
        self.errors: dict[str, str] = {}  # file name -> why it was not loaded

    def _path(self, block_id: str) -> Path:
        return self.folder / f"{block_id.split('.', 1)[1]}.json"

    def definitions(self) -> list[dict]:
        out, self.errors = [], {}
        for path in sorted(self.folder.glob("*.json")) if self.folder.exists() else []:
            try:
                d = json.loads(path.read_text("utf-8"))
                if d.get("format") != FORMAT:
                    raise ValueError("not a Veyra block file")
                out.append(d)
            except (OSError, ValueError) as e:
                self.errors[path.name] = str(e)
                log.error("Custom block %s unreadable: %s", path.name, e)
        return out

    def load_all(self) -> int:
        """Register every saved block. Blocks may use each other, so retry until nothing changes."""
        pending = self.definitions()
        loaded = 0
        while pending:
            failed = []
            for d in pending:
                try:
                    self.install(d)
                    loaded += 1
                except (ValueError, KeyError) as e:
                    failed.append((d, e))
            if len(failed) == len(pending):
                for d, e in failed:
                    self.errors[f"{d.get('id', '?')}"] = str(e)
                    log.error("Custom block %s not loaded: %s", d.get("id"), e)
                break
            pending = [d for d, _ in failed]
        return loaded

    def install(self, d: dict) -> type[Block]:
        problems = validate(d)
        if problems:
            raise ValueError("; ".join(problems))
        return registry.register(make_block(d), replace=True)

    def save(self, d: dict) -> type[Block]:
        cls = self.install(d)  # validates first; nothing is written for a broken block
        self.folder.mkdir(parents=True, exist_ok=True)
        path = self._path(d["id"])
        tmp = path.with_suffix(".tmp")
        tmp.write_text(json.dumps(d, indent=2, ensure_ascii=False), "utf-8")
        os.replace(tmp, path)
        log.info("Saved custom block %s (%s)", d["title"], d["id"])
        return cls

    def users(self, block_id: str) -> list[str]:
        """Titles of other custom blocks whose steps use block_id."""
        out = []
        for d in self.definitions():
            if d.get("body") == "steps" and d["id"] != block_id:
                try:
                    if any(n["type"] == block_id for n in Workflow.from_dict(d["workflow"]).nodes()):
                        out.append(d["title"])
                except WorkflowError:
                    pass
        return out

    def delete(self, block_id: str) -> None:
        self._path(block_id).unlink(missing_ok=True)
        registry.unregister(block_id)
