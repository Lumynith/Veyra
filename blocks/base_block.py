"""Base class every automation block derives from."""
from __future__ import annotations

from dataclasses import dataclass
from typing import Any

# Library order and colour of each category.
CATEGORIES = {
    "Flow Control": "#d99a3d",
    "Mouse": "#4f8ff7",
    "Keyboard": "#33b39a",
    "Image Detection": "#d0628e",
    "Text Detection": "#9a7cf0",
    "Roblox": "#e0604f",
    "Utilities": "#8a93a6",
    "Scripting": "#e3b341",
    "Custom": "#5cc8e0",
}
CATEGORY_ICONS = {
    "Flow Control": "flag", "Mouse": "mouse", "Keyboard": "keyboard", "Image Detection": "image",
    "Text Detection": "text", "Roblox": "cube", "Utilities": "settings",
    "Scripting": "script", "Custom": "blocks",
}


@dataclass(frozen=True)
class Param:
    name: str
    label: str
    kind: str = "str"  # str | int | float | bool | choice | file | region | key | var | image | text_target | script
    default: Any = ""
    options: tuple = ()
    minimum: float | None = None
    maximum: float | None = None
    tooltip: str = ""


class Block:
    type_id = ""
    title = ""
    category = ""
    icon = ""
    color = ""             # overrides the category colour
    description = ""
    params: tuple[Param, ...] = ()
    container = False      # has a nested body (loops, if/else)
    hat = False            # must start a stack (Start)
    cap = False            # nothing may follow it (End, Stop, Break)
    summary_fmt = ""       # e.g. "{seconds:g} s", formatted with the block's params

    @classmethod
    def defaults(cls) -> dict[str, Any]:
        return {p.name: p.default for p in cls.params}

    @classmethod
    def summary(cls, params: dict[str, Any]) -> str:
        try:
            return cls.summary_fmt.format(**{**cls.defaults(), **params})
        except (KeyError, ValueError, TypeError):
            return ""

    @classmethod
    def block_color(cls) -> str:
        return cls.color or CATEGORIES.get(cls.category, "#8a93a6")

    @classmethod
    def block_icon(cls) -> str:
        return cls.icon or CATEGORY_ICONS.get(cls.category, "blocks")

    def execute(self, ctx, p: dict[str, Any], node: dict) -> Any:
        """p holds resolved parameter values. Containers run node['body'] via ctx.run_blocks."""
        raise NotImplementedError


class BaseBlock(Block):
    """Developer API for Python plugin blocks (put the module in plugins/). Subclasses register themselves.

        from blocks.base_block import BaseBlock, Param

        class MyCustomBlock(BaseBlock):
            BLOCK_ID = "custom.example"
            DISPLAY_NAME = "My Custom Block"
            CATEGORY = "Custom"
            INPUTS = (Param("times", "Times", "int", 2),)

            def execute(self, context, inputs):
                context.log(f"asked for {inputs['times']}")
                return {"success": True, "double": inputs["times"] * 2}

    context is the same Runtime the built-in blocks get (input, capture, vision, OCR, variables, wait…).
    The returned dict is stored as workflow variables {output}_<key> (bare <key> when Output is blank).
    Plugins are trusted code: they are NOT sandboxed. Only install plugins you trust.
    """
    BLOCK_ID = ""
    DISPLAY_NAME = ""
    CATEGORY = "Custom"
    DESCRIPTION = ""
    ICON = ""
    INPUTS: tuple[Param, ...] = ()
    OUTPUT_VARIABLE = "result"

    def __init_subclass__(cls, **kwargs) -> None:
        super().__init_subclass__(**kwargs)
        if "execute" in cls.__dict__:
            user_execute = cls.__dict__["execute"]

            def execute(self, ctx, p, node, _user=user_execute):
                result = _user(self, ctx, {k: v for k, v in p.items() if k != "output"})
                if isinstance(result, dict):
                    prefix = p.get("output", "").strip()
                    for key, value in result.items():
                        ctx.set(f"{prefix}_{key}" if prefix else str(key), value)
                    return result.get("success", True)
                return result
            cls.execute = execute
        if cls.BLOCK_ID:
            cls.type_id = cls.BLOCK_ID
            cls.title = cls.DISPLAY_NAME or cls.__name__
            cls.category = cls.CATEGORY
            cls.description = cls.DESCRIPTION or (cls.__doc__ or "").strip()
            cls.icon = cls.ICON
            cls.params = tuple(cls.INPUTS) + (
                Param("output", "Output variable", "var", cls.OUTPUT_VARIABLE,
                      tooltip="Results are stored as {output}_<name>; blank stores them under their own names"),)
            from blocks import registry  # here: registry imports this module
            registry.register(cls, replace=True)
