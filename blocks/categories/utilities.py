"""Utility blocks. (Log Message lives in flow.py next to the other text-output blocks.)"""
import json
import os
import random
import threading

from automation.expressions import coerce
from blocks.base_block import Block, Param
from blocks.registry import register
from vision import image_capture

CAT = "Utilities"
_store_lock = threading.Lock()


def _store_path(ctx):
    return ctx.services.data_dir / "variables.json"


def _read_store(ctx) -> dict:
    path = _store_path(ctx)
    if not path.exists():
        return {}
    try:
        return json.loads(path.read_text("utf-8"))
    except ValueError as e:
        raise ValueError(f"Saved variables file is damaged: {e}") from e


@register
class RandomNumber(Block):
    type_id, title, category = "util.random", "Random Number", CAT
    icon = "dice"
    description = "Store a random number between min and max (inclusive)."
    params = (Param("minimum", "Min", "float", 1, minimum=-1e9, maximum=1e9),
              Param("maximum", "Max", "float", 10, minimum=-1e9, maximum=1e9),
              Param("integer", "Whole number", "bool", True),
              Param("output", "Output variable", "var", "random"))
    summary_fmt = "{minimum:g}–{maximum:g} → {output}"

    def execute(self, ctx, p, node):
        lo, hi = sorted((p["minimum"], p["maximum"]))
        value = random.randint(int(lo), int(hi)) if p["integer"] else round(random.uniform(lo, hi), 4)
        ctx.set(p["output"], value)
        return value


@register
class RandomDelay(Block):
    type_id, title, category = "util.random_delay", "Random Delay", CAT
    icon = "clock"
    description = "Wait a random time between min and max seconds."
    params = (Param("minimum", "Min (s)", "float", 0.5, minimum=0, maximum=86400),
              Param("maximum", "Max (s)", "float", 1.5, minimum=0, maximum=86400))
    summary_fmt = "{minimum:g}–{maximum:g} s"

    def execute(self, ctx, p, node):
        ctx.wait(random.uniform(*sorted((p["minimum"], p["maximum"]))))


@register
class Screenshot(Block):
    type_id, title, category = "util.screenshot", "Screenshot", CAT
    icon = "camera"
    description = "Save the whole desktop (all monitors) to a PNG file."
    params = (Param("file", "Save as", "str", "captures/screen_{timestamp}.png"),
              Param("output", "Output variable", "var", "screenshot_path"))
    summary_fmt = "{file}"

    def execute(self, ctx, p, node):
        path = image_capture.save_image(ctx.capture(image_capture.virtual_screen()), ctx.path(p["file"]))
        ctx.set(p["output"], str(path))
        ctx.log(f"Saved {path}")


@register
class SaveVariable(Block):
    type_id, title, category = "util.save_var", "Save Variable", CAT
    icon = "variable"
    description = "Keep a variable on disk so later runs can load it."
    params = (Param("name", "Variable", "var", "counter"),)
    summary_fmt = "{name}"

    def execute(self, ctx, p, node):
        value = ctx.get(p["name"])
        try:
            json.dumps(value)
        except TypeError:
            raise ValueError(f"{p['name']} cannot be saved (unsupported value type)") from None
        with _store_lock:
            data = _read_store(ctx)
            data[p["name"]] = value
            path = _store_path(ctx)
            path.parent.mkdir(parents=True, exist_ok=True)
            tmp = path.with_suffix(".tmp")
            tmp.write_text(json.dumps(data, indent=2), "utf-8")
            os.replace(tmp, path)


@register
class LoadVariable(Block):
    type_id, title, category = "util.load_var", "Load Variable", CAT
    icon = "variable"
    description = "Load a variable saved by Save Variable (or use the default)."
    params = (Param("name", "Variable", "var", "counter"),
              Param("default", "Default", "str", "0", tooltip="Used when nothing was saved yet"))
    summary_fmt = "{name}"

    def execute(self, ctx, p, node):
        with _store_lock:
            data = _read_store(ctx)
        value = data.get(p["name"], coerce(p["default"]))
        ctx.set(p["name"], value)
        return value
