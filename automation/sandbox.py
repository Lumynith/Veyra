"""Runs user Python scripts in an isolated child process and serves its automation API calls.

Layers (none of them alone is a security boundary; together they make accidents and casual abuse hard):
  1. script_host.validate: only whitelisted modules, no dangerous builtins, no interpreter internals.
  2. A separate `python -I` process in an empty folder with a minimal environment.
  3. A Windows Job Object: no child processes, memory capped, killed with the job.
  4. A timeout, and Stop kills the process immediately.
  5. Automation functions run HERE, in the app, and each sensitive group needs an explicit permission.
"""
from __future__ import annotations

import json
import logging
import os
import queue
import re
import subprocess
import sys
import tempfile
import threading
import time
from pathlib import Path
from typing import Any, Callable

from automation.input_controller import parse_combo
from automation.script_host import validate

log = logging.getLogger(__name__)
HOST = Path(__file__).with_name("script_host.py")
MEMORY_LIMIT = 512 * 1024 * 1024
MAX_FILE = 1024 * 1024
PERMISSIONS = {
    "input": "Control the mouse and keyboard",
    "screen": "Look at the screen (find images, read text)",
    "variables": "Read and change workflow variables",
    "files": "Read and write files in data/script_files",
}


class ScriptError(RuntimeError):
    def __init__(self, message: str, line: int | None = None) -> None:
        super().__init__(f"Line {line}: {message}" if line else message)
        self.line = line


# -- the API a script can call ---------------------------------------------------------------------

_api: dict[str, tuple[str | None, Callable]] = {}


def api(name: str, permission: str | None = None):
    def wrap(fn):
        _api[name] = (permission, fn)
        return fn
    return wrap


# Signature and one-line help per function, for the Script Editor's Insert menu and docs.
API_DOC = {
    "log": ("log('message', value)", "Write to the macro console"),
    "wait": ("wait(1.5)", "Pause; Stop and Pause still work"),
    "output": ("output('name', value)", "Return a value to the workflow as a variable"),
    "inputs": ("inputs['name']", "The block's input values"),
    "roblox_connected": ("roblox_connected()", "True when a Roblox window is attached"),
    "viewport_size": ("viewport_size()", "[width, height] of the Roblox viewport"),
    "get_var": ("get_var('name', default)", "Read a workflow variable  [variables]"),
    "set_var": ("set_var('name', value)", "Change a workflow variable  [variables]"),
    "move": ("move(x, y)", "Move the mouse (viewport coordinates)  [input]"),
    "click": ("click(x, y, button='left')", "Click, optionally at a point  [input]"),
    "press": ("press('ctrl+s')", "Press a key or key combination  [input]"),
    "type_text": ("type_text('hello')", "Type text  [input]"),
    "find_image": ("find_image('Menus/Play', confidence=0.85)", "Image match → {x, y, score} or None  [screen]"),
    "find_text": ("find_text('Play', match='exact')", "Text matches → [{x, y, text}, …]  [screen]"),
    "read_text": ("read_text('x, y, w, h')", "All text in a region  [screen]"),
    "read_numbers": ("read_numbers('x, y, w, h')", "Numbers in a region  [screen]"),
    "read_file": ("read_file('notes.txt')", "Read data/script_files/notes.txt  [files]"),
    "write_file": ("write_file('notes.txt', text)", "Write data/script_files/notes.txt  [files]"),
}


def api_names() -> list[str]:
    return sorted(_api) + ["output", "inputs"]


@api("log")
def _log(ctx, *parts):
    text = " ".join(str(p) for p in parts)
    ctx.log("[script] " + text)
    ctx.emit("script.output", text + "\n")


@api("wait")
def _wait(ctx, seconds):
    ctx.wait(float(seconds))


@api("roblox_connected")
def _connected(ctx):
    try:
        ctx.roblox_hwnd()
        return True
    except RuntimeError:
        return False


@api("viewport_size")
def _viewport_size(ctx):
    return list(ctx.space_size("viewport"))


@api("get_var", "variables")
def _get_var(ctx, name, default=None):
    return ctx.vars.get(str(name), default)


@api("set_var", "variables")
def _set_var(ctx, name, value):
    ctx.set(str(name), value)


@api("move", "input")
def _move(ctx, x, y):
    ctx.input.move(*ctx.to_screen(float(x), float(y), "viewport"))


@api("click", "input")
def _click(ctx, x=None, y=None, button="left", clicks=1):
    if x is not None and y is not None:
        _move(ctx, x, y)
        ctx.wait(0.02)
    ctx.input.click(str(button), int(clicks))


@api("press", "input")
def _press(ctx, keys):
    vks = parse_combo(str(keys))
    for vk in vks:
        ctx.input.key_down(vk)
    for vk in reversed(vks):
        ctx.input.key_up(vk)


@api("type_text", "input")
def _type_text(ctx, text):
    for ch in str(text):
        ctx.checkpoint()
        ctx.input.type_char(ch)


def _resolve_image(ctx, image: str) -> str:
    """Accept 'asset:<id>', a library label such as 'Menus/Play', or a plain name."""
    lib = ctx.vision.assets
    if image.startswith("asset:"):
        return image
    for meta in lib.list():
        ref = lib.ref(meta["id"])
        if image in (lib.label(ref), meta["name"]):
            return ref
    raise ValueError(f"No image named {image!r} in the image library")


@api("find_image", "screen")
def _find_image(ctx, image, confidence=0.85, region=""):
    from blocks.categories.image import FindImage, detect
    p = {**FindImage.defaults(), "image": _resolve_image(ctx, str(image)), "confidence": float(confidence),
         "region": str(region)}
    m = detect(ctx, p)
    return None if m is None else {"x": m.center[0], "y": m.center[1], "score": round(m.score, 4),
                                   "w": m.w, "h": m.h}


@api("find_text", "screen")
def _find_text(ctx, text, match="partial", region="", case_sensitive=False):
    from blocks.categories.text import FindText, find_all
    p = {**FindText.defaults(), "text": str(text), "match": match, "region": str(region),
         "case_sensitive": bool(case_sensitive)}
    return [{"x": m.center[0], "y": m.center[1], "text": m.text, "conf": round(m.conf, 3)}
            for m in find_all(ctx, p)]


@api("read_text", "screen")
def _read_text(ctx, region=""):
    from blocks.categories.text import read
    from vision.text_detector import full_text
    words, _ = read(ctx, {"region": str(region), "space": "viewport", "language": ""})
    return full_text(words)


@api("read_numbers", "screen")
def _read_numbers(ctx, region=""):
    from blocks.categories.text import read
    from vision.text_detector import find_numbers
    words, _ = read(ctx, {"region": str(region), "space": "viewport", "language": ""})
    return [v for v, _ in find_numbers(words)]


def _script_file(ctx, name: str) -> Path:
    if not re.fullmatch(r"[\w\- .]{1,80}", name) or name.strip(".") != name:
        raise ValueError(f"Invalid file name {name!r}: use letters, digits, spaces, '-', '_' and '.' only")
    folder = ctx.path("script_files")
    folder.mkdir(parents=True, exist_ok=True)
    return folder / name


@api("read_file", "files")
def _read_file(ctx, name):
    path = _script_file(ctx, str(name))
    if path.stat().st_size > MAX_FILE:
        raise ValueError("File is larger than 1 MB")
    return path.read_text("utf-8")


@api("write_file", "files")
def _write_file(ctx, name, text):
    text = str(text)
    if len(text.encode("utf-8")) > MAX_FILE:
        raise ValueError("Text is larger than 1 MB")
    _script_file(ctx, str(name)).write_text(text, "utf-8")


# -- process management ----------------------------------------------------------------------------

def _python() -> str:
    # The base interpreter: a venv's python.exe is a launcher that spawns a second process,
    # which the one-process job limit would block. The host needs only the standard library.
    return getattr(sys, "_base_executable", None) or sys.executable


def _contain(proc: subprocess.Popen):
    """Put the child in a Job Object: one process only, capped memory, dies with the job handle."""
    try:
        import win32job
        job = win32job.CreateJobObject(None, "")
        info = win32job.QueryInformationJobObject(job, win32job.JobObjectExtendedLimitInformation)
        info["BasicLimitInformation"]["LimitFlags"] = (win32job.JOB_OBJECT_LIMIT_KILL_ON_JOB_CLOSE
                                                      | win32job.JOB_OBJECT_LIMIT_ACTIVE_PROCESS
                                                      | win32job.JOB_OBJECT_LIMIT_PROCESS_MEMORY)
        info["BasicLimitInformation"]["ActiveProcessLimit"] = 1
        info["ProcessMemoryLimit"] = MEMORY_LIMIT
        win32job.SetInformationJobObject(job, win32job.JobObjectExtendedLimitInformation, info)
        win32job.AssignProcessToJobObject(job, int(proc._handle))
        return job
    except Exception as e:  # still isolated by process + restrictions; say so rather than fail silently
        log.warning("Script process limits could not be applied: %s", e)
        return None


def _json_safe(value: Any) -> Any:
    return json.loads(json.dumps(value, default=lambda o: list(o) if isinstance(o, (tuple, set)) else repr(o)))


def run_script(ctx, source: str, inputs: dict, permissions: dict[str, bool], timeout: float = 30.0,
               on_print: Callable[[str], None] | None = None) -> dict:
    """Run source in the sandbox. Returns the outputs the script set with output(name, value)."""
    problems = validate(source)
    if problems:
        line, _, msg = problems[0]
        raise ScriptError(msg, line)
    workdir = tempfile.mkdtemp(prefix="veyra_script_")
    env = {"SYSTEMROOT": os.environ.get("SYSTEMROOT", r"C:\Windows"), "PYTHONIOENCODING": "utf-8",
           "PYTHONDONTWRITEBYTECODE": "1", "PYTHONHASHSEED": "0"}
    # A release build has no python.exe: Veyra.exe runs the same host when given --script-host.
    command = [sys.executable, "--script-host"] if getattr(sys, "frozen", False) else [_python(), "-I", "-S", str(HOST)]
    proc = subprocess.Popen(command, stdin=subprocess.PIPE, stdout=subprocess.PIPE,
                            stderr=subprocess.PIPE, cwd=workdir, env=env,
                            creationflags=getattr(subprocess, "CREATE_NO_WINDOW", 0))
    job = _contain(proc)  # before the request is sent, so the script has not started yet
    messages: queue.Queue = queue.Queue()

    def reader() -> None:
        for raw in proc.stdout:
            try:
                messages.put(json.loads(raw))
            except ValueError:
                messages.put({"type": "print", "text": raw.decode("utf-8", "replace")})
        messages.put(None)

    threading.Thread(target=reader, daemon=True, name="script-reader").start()

    def reply(msg: dict) -> None:
        proc.stdin.write((json.dumps(msg) + "\n").encode("utf-8"))
        proc.stdin.flush()

    try:
        reply({"source": source, "inputs": _json_safe(inputs), "api": sorted(_api)})
        deadline = time.perf_counter() + timeout if timeout > 0 else None
        while True:
            try:
                msg = messages.get(timeout=0.05)
            except queue.Empty:
                msg = ...
            paused = time.perf_counter()
            ctx.checkpoint()  # raises StopRequested on Stop; blocks while paused
            if deadline is not None:
                deadline += time.perf_counter() - paused  # paused time does not count
                if time.perf_counter() > deadline:
                    raise ScriptError(f"Script timed out after {timeout:g} s")
            if msg is ...:
                continue
            if msg is None:
                err = proc.stderr.read().decode("utf-8", "replace").strip() if proc.poll() is not None else ""
                raise ScriptError(f"Script process ended unexpectedly{': ' + err[-400:] if err else ''}")
            kind = msg.get("type")
            if kind == "print":
                text = msg.get("text", "")
                if on_print:
                    on_print(text)
                if text.strip():
                    log.info("[script] %s", text.rstrip())
            elif kind == "call":
                reply(_serve(ctx, msg, permissions))
            elif kind == "done":
                return msg.get("outputs", {})
            elif kind == "error":
                raise ScriptError(msg.get("message", "Script failed"), msg.get("line"))
    finally:
        if proc.poll() is None:
            proc.kill()
        if job is not None:
            job.Close()
        for stream in (proc.stdin, proc.stdout, proc.stderr):
            try:
                stream.close()
            except OSError:
                pass
        try:
            os.rmdir(workdir)
        except OSError:
            pass


def _serve(ctx, msg: dict, permissions: dict[str, bool]) -> dict:
    from automation.engine import StopRequested
    name = msg.get("name", "")
    if name not in _api:
        return {"error": f"Unknown function {name!r}"}
    permission, fn = _api[name]
    if permission and not permissions.get(permission):
        return {"error": f"{name}() needs the '{permission}' permission ({PERMISSIONS[permission].lower()}). "
                         f"Enable it in the block's settings."}
    try:
        return {"value": _json_safe(fn(ctx, *msg.get("args", []), **msg.get("kwargs", {})))}
    except StopRequested:
        raise
    except TypeError as e:
        return {"error": f"{name}(): {e}"}
    except Exception as e:  # becomes a RuntimeError inside the script, which it may catch
        return {"error": str(e)}


def parse_inputs(text: str) -> dict:
    """'count=3; name=Bob' → {'count': 3, 'name': 'Bob'} (values coerced like workflow variables)."""
    from automation.expressions import coerce
    out = {}
    for part in re.split(r"[;\n]", text):
        if part.strip():
            key, sep, value = part.partition("=")
            if not sep or not key.strip().isidentifier():
                raise ValueError(f"Inputs must look like 'name=value; other=value', got {part.strip()!r}")
            out[key.strip()] = coerce(value.strip())
    return out
