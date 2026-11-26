"""Runs ONE user script inside a separate, restricted Python process.

Started by automation/sandbox.py as `python -I script_host.py` in an empty working folder, with a minimal
environment, inside a Windows Job Object (no child processes, capped memory) and a timeout.
Standard library only: it must not import the project.

Restrictions applied to the script (also used by the Script Editor to highlight errors):
  - only the modules in ALLOWED_MODULES can be imported, and only their public, non-module attributes;
  - dangerous builtins (open, eval, exec, getattr, __import__, …) are removed;
  - attribute names that lead to interpreter internals (_x, __x__, frames, code objects) are rejected.
This is a best-effort Python sandbox, NOT a security boundary: CPython cannot fully isolate untrusted code.
The process isolation and Job Object limits are the second line of defence. Only run scripts you trust.

Protocol: JSON lines. Parent → child: {"source", "inputs", "api": [names]}, then one reply per call:
{"value": …} or {"error": "…"}. Child → parent: {"type": "call", "name", "args", "kwargs"} |
{"type": "print", "text"} | {"type": "done", "outputs"} | {"type": "error", "message", "line"}.
"""
from __future__ import annotations

import ast
import builtins
import json
import sys
import types

ALLOWED_MODULES = ("math", "random", "re", "json", "time", "datetime", "statistics", "itertools", "functools",
                   "collections", "string", "decimal", "fractions")
BLOCKED_NAMES = {"eval", "exec", "compile", "open", "input", "breakpoint", "globals", "locals", "vars",
                 "getattr", "setattr", "delattr", "hasattr", "__import__", "memoryview", "help", "exit", "quit",
                 "__builtins__", "__loader__", "__spec__"}
BLOCKED_ATTR_PREFIXES = ("_", "f_", "gi_", "cr_", "ag_", "tb_", "co_", "mro")
# get_field/get_value: string.Formatter would otherwise walk attributes the checker cannot see.
BLOCKED_ATTRS = {"format_map", "func_globals", "func_code", "get_field", "get_value"}


def validate(source: str) -> list[tuple[int, int, str]]:
    """Problems as (line, column, message); empty when the script may run."""
    try:
        tree = ast.parse(source, "<script>")
    except SyntaxError as e:
        return [(e.lineno or 1, e.offset or 0, f"Syntax error: {e.msg}")]
    problems = []

    def bad(node, msg):
        problems.append((getattr(node, "lineno", 1), getattr(node, "col_offset", 0), msg))

    for node in ast.walk(tree):
        if isinstance(node, ast.Import):
            for alias in node.names:
                if alias.name not in ALLOWED_MODULES:
                    bad(node, f"Module '{alias.name}' is not allowed. Allowed: {', '.join(ALLOWED_MODULES)}")
        elif isinstance(node, ast.ImportFrom):
            if node.level or node.module not in ALLOWED_MODULES:
                bad(node, f"Module '{node.module}' is not allowed. Allowed: {', '.join(ALLOWED_MODULES)}")
            for alias in node.names:
                if alias.name == "*" or alias.name.startswith("_"):
                    bad(node, f"'from {node.module} import {alias.name}' is not allowed")
        elif isinstance(node, ast.Name) and (node.id in BLOCKED_NAMES or node.id.startswith("__")):
            bad(node, f"'{node.id}' is not available in scripts")
        elif isinstance(node, ast.Attribute) and (node.attr.startswith(BLOCKED_ATTR_PREFIXES)
                                                  or node.attr in BLOCKED_ATTRS):
            bad(node, f"Attribute '.{node.attr}' is not allowed in scripts")
        elif isinstance(node, ast.MatchClass) and any(a.startswith(BLOCKED_ATTR_PREFIXES) or a in BLOCKED_ATTRS
                                                       for a in node.kwd_attrs):
            bad(node, "That match pattern reads an attribute that is not allowed in scripts")
        elif isinstance(node, (ast.FunctionDef, ast.AsyncFunctionDef, ast.ClassDef)) and node.name.startswith("_"):
            bad(node, f"Names starting with '_' are not allowed ('{node.name}')")
        elif isinstance(node, (ast.AsyncFunctionDef, ast.Await, ast.AsyncFor, ast.AsyncWith)):
            bad(node, "async code is not supported in scripts")
    return sorted(set(problems))


def _module_proxy(module: types.ModuleType) -> types.SimpleNamespace:
    """Public attributes only, and no sub-modules (json.codecs would reach codecs.open, for example)."""
    return types.SimpleNamespace(**{k: v for k, v in vars(module).items()
                                    if not k.startswith("_") and not isinstance(v, types.ModuleType)})


def safe_builtins() -> dict:
    proxies: dict[str, types.SimpleNamespace] = {}

    def restricted_import(name, globals=None, locals=None, fromlist=(), level=0):
        if level or name not in ALLOWED_MODULES:
            raise ImportError(f"Module '{name}' is not allowed in scripts")
        if name not in proxies:
            proxies[name] = _module_proxy(__import__(name))
        return proxies[name]

    safe = {k: v for k, v in vars(builtins).items() if not k.startswith("_") and k not in BLOCKED_NAMES}
    safe["__build_class__"] = builtins.__build_class__  # needed for 'class' statements
    safe["__name__"] = "script"
    safe["__import__"] = restricted_import
    return safe


class _Printer:
    def __init__(self, send) -> None:
        self._send = send

    def write(self, text: str) -> int:
        if text:
            self._send({"type": "print", "text": text})
        return len(text)

    def flush(self) -> None:
        pass


def _script_line(tb) -> int | None:
    line = None
    while tb is not None:
        if tb.tb_frame.f_code.co_filename == "<script>":
            line = tb.tb_lineno
        tb = tb.tb_next
    return line


def main() -> None:
    channel_out, channel_in = sys.stdout.buffer, sys.stdin.buffer

    def send(msg: dict) -> None:
        channel_out.write((json.dumps(msg, default=repr) + "\n").encode("utf-8"))
        channel_out.flush()

    def receive() -> dict:
        line = channel_in.readline()
        if not line:
            raise SystemExit(1)  # parent went away
        return json.loads(line)

    request = receive()
    sys.stdout = sys.stderr = _Printer(send)  # script output never touches the protocol channel
    problems = validate(request["source"])
    if problems:
        line, _, msg = problems[0]
        send({"type": "error", "message": msg, "line": line})
        return

    outputs: dict = {}

    def call(name: str, args, kwargs):
        send({"type": "call", "name": name, "args": list(args), "kwargs": kwargs})
        reply = receive()
        if "error" in reply:
            raise RuntimeError(reply["error"])
        return reply.get("value")

    def api_function(name: str):
        def f(*args, **kwargs):
            return call(name, args, kwargs)
        f.__name__ = name
        return f

    def output(name, value) -> None:
        outputs[str(name)] = value

    env = {"__builtins__": safe_builtins(), "inputs": dict(request.get("inputs", {})), "output": output}
    env.update({name: api_function(name) for name in request.get("api", [])})
    try:
        exec(compile(request["source"], "<script>", "exec"), env)
    except Exception as e:  # reported to the workflow, with the script line when there is one
        send({"type": "error", "message": f"{type(e).__name__}: {e}", "line": _script_line(e.__traceback__)})
        return
    send({"type": "done", "outputs": outputs})


if __name__ == "__main__":
    main()
