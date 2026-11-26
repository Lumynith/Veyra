"""Keyboard blocks. Keys are names like a, 5, enter, space, shift, ctrl, f1, up, num4."""
from automation.input_controller import parse_combo, parse_key
from blocks.base_block import Block, Param
from blocks.registry import register

CAT = "Keyboard"
KEY = Param("key", "Key", "key", "e", tooltip="e.g. a, space, enter, shift, f5, up")


@register
class PressKey(Block):
    type_id, title, category = "key.press", "Press Key", CAT
    description = "Press and release a key."
    params = (KEY, Param("hold", "Hold for (s)", "float", 0.05, minimum=0, maximum=60))
    summary_fmt = "{key}"

    def execute(self, ctx, p, node):
        vk = parse_key(p["key"])
        ctx.input.key_down(vk)
        try:
            ctx.wait(p["hold"])
        finally:
            ctx.input.key_up(vk)


@register
class HoldKey(Block):
    type_id, title, category = "key.hold", "Hold Key", CAT
    description = "Press a key and keep it down (released automatically when the workflow ends)."
    params = (KEY,)
    summary_fmt = "{key}"

    def execute(self, ctx, p, node):
        ctx.input.key_down(parse_key(p["key"]))


@register
class ReleaseKey(Block):
    type_id, title, category = "key.release", "Release Key", CAT
    description = "Release a key held by Hold Key."
    params = (KEY,)
    summary_fmt = "{key}"

    def execute(self, ctx, p, node):
        ctx.input.key_up(parse_key(p["key"]))


@register
class TypeText(Block):
    type_id, title, category = "key.type", "Type Text", CAT
    description = "Type text character by character. Use {name} to type a variable."
    params = (Param("text", "Text", "str", "Hello"),
              Param("interval", "Delay per character (s)", "float", 0.03, minimum=0, maximum=5))
    summary_fmt = "{text}"

    def execute(self, ctx, p, node):
        for ch in p["text"]:
            if ch == "\n":
                ctx.input.tap(parse_key("enter"))
            else:
                ctx.input.type_char(ch)
            ctx.wait(p["interval"])


@register
class KeyCombination(Block):
    type_id, title, category = "key.combo", "Key Combination", CAT
    description = "Press keys together, e.g. ctrl+shift+s."
    params = (Param("keys", "Keys", "key", "ctrl+a"),)
    summary_fmt = "{keys}"

    def execute(self, ctx, p, node):
        keys = parse_combo(p["keys"])
        pressed = []
        try:
            for vk in keys:
                ctx.input.key_down(vk)
                pressed.append(vk)
                ctx.wait(0.02)
        finally:
            for vk in reversed(pressed):
                ctx.input.key_up(vk)


@register
class WaitForKey(Block):
    type_id, title, category = "key.wait", "Wait For Key", CAT
    description = "Wait until you press a key (anywhere in Windows)."
    params = (KEY, Param("timeout", "Timeout (s)", "float", 0.0, minimum=0, maximum=86400, tooltip="0 waits forever"))
    summary_fmt = "{key}"

    def execute(self, ctx, p, node):
        vk = parse_key(p["key"])
        while ctx.input.key_is_down(vk):  # ignore a key that was already held when we started
            ctx.wait(0.02)
        if not ctx.poll(lambda: ctx.input.key_is_down(vk), p["timeout"], 0.02):
            raise TimeoutError(f"{p['key']} was not pressed within {p['timeout']:g} s")
