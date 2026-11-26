"""Versioned project files: a workflow plus metadata.

{"format": "lumencrest.project", "version": 1,
 "meta": {"name", "created", "modified", "app"}, "workflow": {lumencrest.workflow}}

Bare workflow files (older saves, exports) open too; they are migrated to a project in memory.
Everything read from disk goes through Workflow.from_dict (unknown blocks / damaged data -> WorkflowError);
callers run Workflow.validate() to show structural and parameter problems before anything executes.
"""
from __future__ import annotations

import json
import os
from datetime import datetime
from pathlib import Path

from automation.workflow import FORMAT as WORKFLOW_FORMAT, Workflow, WorkflowError

FORMAT, VERSION = "lumencrest.project", 1
APP_VERSION = "0.7"
MAX_BYTES = 32 * 1024 * 1024  # refuse absurd files instead of freezing on them
EXT = ".lcp.json"


def _now() -> str:
    return datetime.now().isoformat(timespec="seconds")


def new_meta(name: str = "Untitled") -> dict:
    t = _now()
    return {"name": name, "created": t, "modified": t, "app": APP_VERSION}


def migrate(data: dict) -> dict:
    """Bring any supported document up to the current project version."""
    if not isinstance(data, dict):
        raise WorkflowError("Not a Veyra project or workflow file.")
    if data.get("format") == WORKFLOW_FORMAT:  # bare workflow -> project v1
        data = {"format": FORMAT, "version": 1, "meta": new_meta(), "workflow": data}
    if data.get("format") != FORMAT:
        raise WorkflowError("Not a Veyra project or workflow file.")
    try:
        version = int(data.get("version", 0))
    except (TypeError, ValueError):
        raise WorkflowError("The project version is not a number.") from None
    if version > VERSION:
        raise WorkflowError("This project was made by a newer version of Veyra.")
    # (future: `if version == 1: ...upgrade...; version = 2`)
    meta = data.get("meta") if isinstance(data.get("meta"), dict) else {}
    clean = new_meta()
    clean.update({k: str(meta[k]) for k in clean if isinstance(meta.get(k), (str, int, float))})
    return {"format": FORMAT, "version": VERSION, "meta": clean, "workflow": data.get("workflow")}


def parse_document(data: dict) -> tuple[Workflow, dict]:
    doc = migrate(data)
    return Workflow.from_dict(doc["workflow"]), doc["meta"]


def read_document(path: str | Path) -> tuple[Workflow, dict]:
    """Open a project or a bare workflow. Raises WorkflowError with a readable message."""
    path = Path(path)
    try:
        if path.stat().st_size > MAX_BYTES:
            raise WorkflowError(f"{path.name} is too large to be a workflow ({path.stat().st_size // 1024} KB).")
        data = json.loads(path.read_text("utf-8"))
    except WorkflowError:
        raise
    except RecursionError:
        raise WorkflowError(f"{path.name} is nested too deeply to open.") from None
    except (OSError, ValueError) as e:
        raise WorkflowError(f"Cannot read {path.name}: {e}") from None
    wf, meta = parse_document(data)
    if meta["name"] == "Untitled":
        meta["name"] = display_name(path)
    return wf, meta


def display_name(path: str | Path) -> str:
    name = Path(path).name
    for ext in (EXT, ".lcw.json", ".json"):
        if name.lower().endswith(ext):
            return name[: -len(ext)]
    return name


def _atomic_write(path: Path, data: dict) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    tmp = path.with_suffix(path.suffix + ".tmp")
    tmp.write_text(json.dumps(data, indent=2), "utf-8")
    os.replace(tmp, path)


def project_dict(wf: Workflow, meta: dict) -> dict:
    return {"format": FORMAT, "version": VERSION, "meta": {**meta, "modified": _now(), "app": APP_VERSION},
            "workflow": wf.to_dict()}


def write_project(path: str | Path, wf: Workflow, meta: dict) -> dict:
    """Atomic save. Returns the meta that was written (with the new modified time)."""
    doc = project_dict(wf, meta)
    _atomic_write(Path(path), doc)
    return doc["meta"]


def export_workflow(path: str | Path, wf: Workflow) -> None:
    """A bare workflow file, without project metadata, for sharing."""
    _atomic_write(Path(path), wf.to_dict())


def merge(into: Workflow, other: Workflow, dx: float = 0.0, dy: float = 0.0) -> int:
    """Import another workflow's stacks next to the current ones (fresh ids). Returns the stack count.

    A workflow has one Start: if `into` already has one, the imported Start block is dropped and its
    blocks stay as a loose stack the user can attach where they want."""
    from automation.workflow import START, _id, _reid
    has_start = bool(into.start_scripts())
    id_map = {}
    for s in other.scripts:
        blocks = [_reid(json.loads(json.dumps(b))) for b in s["blocks"]]
        if has_start and blocks[0]["type"] == START:
            blocks = blocks[1:]
        if blocks:
            script = into.add_script(blocks, (s["pos"][0] + dx, s["pos"][1] + dy))
            id_map[s["id"]] = script["id"]
    for g in other.groups:
        members = [id_map[i] for i in g["scripts"] if i in id_map]
        if members:
            into.groups.append({"id": _id(), "title": g["title"], "scripts": members})
    return len(id_map)


# -- autosave and crash recovery ---------------------------------------------------------------

def write_autosave(path: str | Path, wf: Workflow, meta: dict, project_path: str | None, dirty: bool) -> None:
    doc = project_dict(wf, meta)
    doc["session"] = {"path": project_path, "dirty": dirty}
    _atomic_write(Path(path), doc)


def read_autosave(path: str | Path) -> tuple[Workflow, dict, dict]:
    """(workflow, meta, {"path", "dirty"}); older autosaves (bare workflows) have no session info."""
    wf, meta = read_document(path)
    try:
        session = json.loads(Path(path).read_text("utf-8")).get("session") or {}
    except (OSError, ValueError, AttributeError):
        session = {}
    p = session.get("path")
    return wf, meta, {"path": p if isinstance(p, str) and p else None, "dirty": session.get("dirty") is True}


def acquire_session(lock: str | Path) -> bool:
    """Mark the app as running. True if the previous session did not shut down cleanly.

    shortcut: a second app instance also sees the lock and reports a crash; add a pid check if
    running two instances at once becomes a real use."""
    lock = Path(lock)
    crashed = lock.exists()
    lock.parent.mkdir(parents=True, exist_ok=True)
    lock.write_text(str(os.getpid()), "utf-8")
    return crashed


def release_session(lock: str | Path) -> None:
    Path(lock).unlink(missing_ok=True)
