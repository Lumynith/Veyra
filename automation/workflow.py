"""Workflow data model: stacks ("scripts") of blocks; container blocks hold a nested body.

JSON shape:
{
  "format": "lumencrest.workflow", "version": 1,
  "scripts": [{"id", "pos": [x, y], "blocks": [node, ...]}],
  "groups":  [{"id", "title", "scripts": [script ids]}]
}
node = {"id", "type", "params": {...}, "collapsed": bool, "on_error": "stop"|"continue"|"restart",
        optional "retries", "retry_delay", "timeout" (seconds, 0 = none), "body": [...] (containers)}
"variables": {name: initial value} seeds the run. Positions are editor-only; execution follows list order.
"""
from __future__ import annotations

import copy
import json
import os
import re
import uuid
from dataclasses import dataclass, field
from pathlib import Path

from blocks import registry

FORMAT, VERSION = "lumencrest.workflow", 1
START = "flow.start"
ON_ERROR = ("stop", "continue", "restart")
# per-block error handling: (key, default, min, max)
ERROR_FIELDS = (("retries", 0, 0, 100), ("retry_delay", 1.0, 0.0, 3600.0), ("timeout", 0.0, 0.0, 86400.0))
_NAME = re.compile(r"^[A-Za-z_][A-Za-z0-9_]*$")


def valid_var_name(name: str) -> bool:
    return bool(_NAME.match(name))


class WorkflowError(ValueError):
    pass


def _id() -> str:
    return uuid.uuid4().hex[:10]


def new_node(type_id: str, params: dict | None = None) -> dict:
    block = registry.get(type_id)
    node = {"id": _id(), "type": type_id, "params": {**block.defaults(), **(params or {})},
            "collapsed": False, "on_error": "stop"}
    if block.container:
        node["body"] = []
    return node


def walk(blocks: list):
    """Yield (node, containing list, index) depth-first."""
    for i, node in enumerate(blocks):
        yield node, blocks, i
        if "body" in node:
            yield from walk(node["body"])


def _reid(node: dict) -> dict:
    node["id"] = _id()
    for child in node.get("body", []):
        _reid(child)
    return node


@dataclass
class Workflow:
    scripts: list[dict] = field(default_factory=list)
    groups: list[dict] = field(default_factory=list)
    variables: dict = field(default_factory=dict)  # initial values, set before the first block runs

    # -- editing ---------------------------------------------------------

    def add_script(self, blocks: list[dict], pos=(0.0, 0.0)) -> dict:
        script = {"id": _id(), "pos": [float(pos[0]), float(pos[1])], "blocks": blocks}
        self.scripts.append(script)
        return script

    def locate(self, node_id: str) -> tuple[list, int, dict]:
        """(containing list, index, script) of a node."""
        for script in self.scripts:
            for node, lst, i in walk(script["blocks"]):
                if node["id"] == node_id:
                    return lst, i, script
        raise KeyError(node_id)

    def node(self, node_id: str) -> dict:
        lst, i, _ = self.locate(node_id)
        return lst[i]

    def script_of(self, node_id: str) -> dict:
        return self.locate(node_id)[2]

    def detach_tail(self, node_id: str, pos) -> dict:
        """Move a block and everything after it in its list into a new top-level script."""
        lst, i, script = self.locate(node_id)
        if lst is script["blocks"] and i == 0:
            return script
        tail = lst[i:]
        del lst[i:]
        return self.add_script(tail, pos)

    def remove(self, node_id: str) -> dict:
        lst, i, script = self.locate(node_id)
        node = lst.pop(i)
        if not script["blocks"]:
            self.remove_script(script)
        return node

    def remove_script(self, script: dict) -> None:
        self.scripts.remove(script)
        for g in self.groups:
            if script["id"] in g["scripts"]:
                g["scripts"].remove(script["id"])
        self.groups = [g for g in self.groups if g["scripts"]]

    def duplicate(self, node_id: str) -> dict:
        lst, i, _ = self.locate(node_id)
        clone = _reid(copy.deepcopy(lst[i]))
        if registry.get(clone["type"]).hat or registry.get(lst[i]["type"]).cap:
            # A second Start, or a block after a cap, would be invalid in place: give it its own stack.
            pos = self.script_of(node_id)["pos"]
            self.add_script([clone], (pos[0] + 30, pos[1] + 30))
        else:
            lst.insert(i + 1, clone)
        return clone

    def group(self, script_ids: list[str], title: str = "Group") -> dict:
        for g in self.groups:  # a stack belongs to at most one group
            g["scripts"] = [s for s in g["scripts"] if s not in script_ids]
        self.groups = [g for g in self.groups if g["scripts"]]
        group = {"id": _id(), "title": title, "scripts": list(script_ids)}
        self.groups.append(group)
        return group

    # -- queries ---------------------------------------------------------

    def nodes(self):
        for script in self.scripts:
            for node, _, _ in walk(script["blocks"]):
                yield node

    def start_scripts(self) -> list[dict]:
        return [s for s in self.scripts if s["blocks"] and s["blocks"][0]["type"] == START]

    def validate(self) -> list[str]:
        problems = []
        starts = self.start_scripts()
        if not starts:
            problems.append("The workflow needs a Start block.")
        elif len(starts) > 1:
            problems.append("The workflow has more than one Start block.")

        def check(blocks: list, top: bool) -> None:
            for i, node in enumerate(blocks):
                block = registry.get(node["type"])
                if block.hat and not (top and i == 0):
                    problems.append(f"{block.title} must be the first block of a stack.")
                if block.cap and i != len(blocks) - 1:
                    problems.append(f"Nothing can follow {block.title}.")
                if node["type"] == "flow.else" and (i == 0 or blocks[i - 1]["type"] != "flow.if"):
                    problems.append("Else must directly follow an If block.")
                if "body" in node:
                    check(node["body"], False)

        for script in self.scripts:
            check(script["blocks"], True)
        problems += self.check_params()
        return problems

    def check_params(self) -> list[str]:
        """Parameter values of the wrong type (e.g. from a hand-edited or imported file)."""
        problems = []
        for node in self.nodes():
            block = registry.get(node["type"])
            for p in block.params:
                v = node["params"].get(p.name, p.default)
                ok = True
                if p.kind in ("int", "float"):
                    ok = (isinstance(v, (int, float)) and not isinstance(v, bool)) or (isinstance(v, str) and "{" in v)
                    if ok and not isinstance(v, str) and (p.minimum is not None and v < p.minimum
                                                          or p.maximum is not None and v > p.maximum):
                        problems.append(f"{block.title}: {p.label} must be between {p.minimum:g} and {p.maximum:g}"
                                        f" (is {v})")
                        continue
                elif p.kind == "bool":
                    ok = isinstance(v, bool)
                else:
                    ok = isinstance(v, str)
                if not ok:
                    problems.append(f"{block.title}: {p.label} has an invalid value {v!r}")
        return problems

    # -- serialization ---------------------------------------------------

    def to_dict(self) -> dict:
        return {"format": FORMAT, "version": VERSION, "scripts": copy.deepcopy(self.scripts),
                "groups": copy.deepcopy(self.groups), "variables": dict(self.variables)}

    snapshot = to_dict

    def dumps(self) -> str:
        """Compact JSON of the whole workflow without copying it: a cheap fingerprint for change tracking."""
        return json.dumps({"scripts": self.scripts, "groups": self.groups, "variables": self.variables},
                          separators=(",", ":"))

    @classmethod
    def from_dict(cls, data: dict) -> "Workflow":
        try:
            return cls._from_dict(data)
        except WorkflowError:
            raise
        except RecursionError:
            raise WorkflowError("The workflow is nested too deeply to open.") from None
        except (TypeError, ValueError, AttributeError, KeyError) as e:  # hand-edited or damaged file
            raise WorkflowError(f"The workflow file is damaged ({type(e).__name__}: {e}).") from None

    @classmethod
    def _from_dict(cls, data: dict) -> "Workflow":
        if not isinstance(data, dict) or data.get("format") != FORMAT:
            raise WorkflowError("Not a Veyra workflow file.")
        if int(data.get("version", 0)) > VERSION:
            raise WorkflowError("This workflow was made by a newer version of Veyra.")
        unknown: set[str] = set()
        seen: set[str] = set()

        def norm(raw: dict) -> dict | None:
            try:
                block = registry.get(raw["type"])
            except KeyError:
                unknown.add(str(raw.get("type")))
                return None
            node_id = str(raw.get("id") or _id())
            if node_id in seen:
                node_id = _id()
            seen.add(node_id)
            stored = raw.get("params") or {}
            node = {"id": node_id, "type": block.type_id,
                    "params": {p.name: stored.get(p.name, p.default) for p in block.params},
                    "collapsed": bool(raw.get("collapsed", False)),
                    "on_error": raw.get("on_error") if raw.get("on_error") in ON_ERROR else "stop"}
            for key, default, lo, hi in ERROR_FIELDS:
                v = raw.get(key, default)
                if v != default:
                    if isinstance(v, bool) or not isinstance(v, (int, float)):
                        raise WorkflowError(f"{block.title}: {key} must be a number")
                    node[key] = type(default)(min(hi, max(lo, v)))
            if block.container:
                node["body"] = [n for n in (norm(c) for c in raw.get("body", [])) if n]
            return node

        wf = cls()
        for s in data.get("scripts", []):
            blocks = [n for n in (norm(b) for b in s.get("blocks", [])) if n]
            if blocks:
                pos = s.get("pos", (0, 0))
                pos = (float(pos[0]), float(pos[1])) if isinstance(pos, (list, tuple)) and len(pos) == 2 else (0, 0)
                script = wf.add_script(blocks, pos)
                script["id"] = str(s.get("id") or script["id"])
        ids = {s["id"] for s in wf.scripts}
        for g in data.get("groups", []):
            members = [sid for sid in g.get("scripts", []) if sid in ids]
            if members:
                wf.groups.append({"id": str(g.get("id") or _id()), "title": str(g.get("title", "Group")),
                                  "scripts": members})
        variables = data.get("variables") or {}
        if not isinstance(variables, dict):
            raise WorkflowError("Workflow variables must be a name → value table.")
        for name, value in variables.items():
            if not valid_var_name(str(name)) or not isinstance(value, (str, int, float, bool)):
                raise WorkflowError(f"Invalid workflow variable {name!r}")
            wf.variables[str(name)] = value
        if unknown:
            raise WorkflowError("Unknown block types: " + ", ".join(sorted(unknown)))
        return wf

    def save(self, path: str | Path) -> None:
        path = Path(path)
        path.parent.mkdir(parents=True, exist_ok=True)
        tmp = path.with_suffix(path.suffix + ".tmp")
        tmp.write_text(json.dumps(self.to_dict(), indent=2), "utf-8")
        os.replace(tmp, path)  # atomic: never leaves a half-written workflow

    @classmethod
    def load(cls, path: str | Path) -> "Workflow":
        try:
            data = json.loads(Path(path).read_text("utf-8"))
        except (OSError, ValueError) as e:
            raise WorkflowError(f"Cannot read {path}: {e}") from e
        return cls.from_dict(data)
