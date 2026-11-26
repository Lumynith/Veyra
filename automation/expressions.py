"""Variable interpolation, safe arithmetic and comparisons used by blocks."""
from __future__ import annotations

import ast
import operator
import re
import time
from typing import Any

_VAR = re.compile(r"\{([A-Za-z_][A-Za-z0-9_]*)\}")
_BUILTINS = {
    "time": lambda: time.strftime("%H-%M-%S"),
    "date": lambda: time.strftime("%Y-%m-%d"),
    "timestamp": lambda: time.strftime("%Y%m%d_%H%M%S"),
}
OPERATORS = ("==", "!=", ">", "<", ">=", "<=", "contains", "is true", "is false")


def fmt(value: Any) -> str:
    if isinstance(value, bool):
        return "true" if value else "false"
    if isinstance(value, float) and value.is_integer():
        return str(int(value))
    return str(value)


def interpolate(text: str, variables: dict) -> str:
    """Replace {name} with the variable's value. Unknown names are left untouched."""
    def sub(m: re.Match) -> str:
        name = m.group(1)
        if name in variables:
            return fmt(variables[name])
        if name in _BUILTINS:
            return _BUILTINS[name]()
        return m.group(0)
    return _VAR.sub(sub, text)


def coerce(text: Any) -> Any:
    """'3' -> 3, '2.5' -> 2.5, 'true' -> True; anything else stays a string."""
    if not isinstance(text, str):
        return text
    s = text.strip()
    if s.lower() in ("true", "false"):
        return s.lower() == "true"
    for cast in (int, float):
        try:
            return cast(s)
        except ValueError:
            pass
    return text


_BIN = {ast.Add: operator.add, ast.Sub: operator.sub, ast.Mult: operator.mul, ast.Div: operator.truediv,
        ast.FloorDiv: operator.floordiv, ast.Mod: operator.mod, ast.Pow: operator.pow}
_UNARY = {ast.USub: operator.neg, ast.UAdd: operator.pos}


def evaluate(expr: str) -> float | int:
    """Arithmetic only: numbers, + - * / // % ** and parentheses. No names, calls or attributes."""
    def ev(n: ast.AST):
        if isinstance(n, ast.Expression):
            return ev(n.body)
        if isinstance(n, ast.Constant) and type(n.value) in (int, float):
            return n.value
        if isinstance(n, ast.BinOp) and type(n.op) in _BIN:
            left, right = ev(n.left), ev(n.right)
            if isinstance(n.op, ast.Pow) and abs(right) > 100:
                raise ValueError("Exponent too large")
            return _BIN[type(n.op)](left, right)
        if isinstance(n, ast.UnaryOp) and type(n.op) in _UNARY:
            return _UNARY[type(n.op)](ev(n.operand))
        raise ValueError(f"Not allowed in an expression: {expr!r}")
    try:
        return ev(ast.parse(expr.strip(), mode="eval"))
    except SyntaxError:
        raise ValueError(f"Invalid expression: {expr!r}") from None


def truthy(value: Any) -> bool:
    if isinstance(value, str):
        return value.strip().lower() not in ("", "0", "false", "no", "none")
    return bool(value)


def compare(left: Any, op: str, right: Any, case_sensitive: bool = True) -> bool:
    if op == "is true":
        return truthy(coerce(left))
    if op == "is false":
        return not truthy(coerce(left))
    a, b = coerce(left), coerce(right)
    numeric = isinstance(a, (int, float)) and isinstance(b, (int, float))
    if not numeric:
        a, b = fmt(a), fmt(b)
        if not case_sensitive:
            a, b = a.lower(), b.lower()
    if op == "contains":
        return str(b) in str(a)
    ops = {"==": operator.eq, "!=": operator.ne, ">": operator.gt, "<": operator.lt,
           ">=": operator.ge, "<=": operator.le}
    if op not in ops:
        raise ValueError(f"Unknown operator {op!r}")
    return ops[op](a, b)
