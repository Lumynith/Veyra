"""Roblox blocks. They only use window, screen and input APIs — never the Roblox client itself."""
import ctypes

import win32gui

from blocks.base_block import Block, Param
from blocks.registry import register
from vision import image_capture

CAT = "Roblox"
_user32 = ctypes.WinDLL("user32")


def _responding(hwnd: int) -> bool:
    return not _user32.IsHungAppWindow(hwnd)


def _viewport_rect(ctx) -> tuple[int, int, int, int]:
    return ctx.area("", "viewport")


@register
class FocusRoblox(Block):
    type_id, title, category = "rbx.focus", "Focus Roblox", CAT
    description = "Bring Roblox to the front and give it keyboard focus."

    def execute(self, ctx, p, node):
        ctx.roblox_hwnd()  # fails clearly when nothing is attached
        if not ctx.focus_roblox():
            # Windows only lets the foreground app hand over focus; a tap of Alt counts as user input.
            ctx.input.tap(0x12)
            if not ctx.focus_roblox():
                raise RuntimeError("Windows would not bring Roblox to the front. Click Roblox once, then run again.")
        ctx.wait(0.05)


@register
class CheckConnection(Block):
    type_id, title, category = "rbx.check", "Check Roblox Connection", CAT
    description = "Store true if a Roblox window is attached and responding."
    params = (Param("output", "Output variable", "var", "connected"),)
    summary_fmt = "→ {output}"

    def execute(self, ctx, p, node):
        hwnd = ctx.services.roblox_hwnd()
        ok = bool(hwnd and win32gui.IsWindow(hwnd) and _responding(hwnd))
        ctx.set(p["output"], ok)
        return ok


@register
class CaptureViewport(Block):
    type_id, title, category = "rbx.capture", "Capture Roblox Viewport", CAT
    description = "Save the Roblox viewport to a PNG file (relative paths go to the data folder)."
    params = (Param("file", "Save as", "str", "captures/roblox_{timestamp}.png"),
              Param("output", "Output variable", "var", "capture_path"))
    summary_fmt = "{file}"

    def execute(self, ctx, p, node):
        path = image_capture.save_image(ctx.capture(_viewport_rect(ctx)), ctx.path(p["file"]))
        ctx.set(p["output"], str(path))
        ctx.log(f"Saved {path}")


@register
class ViewportCoordinates(Block):
    type_id, title, category = "rbx.coords", "Get Viewport Coordinates", CAT
    description = "Store the viewport's screen position and size ({output}_x/_y/_w/_h) and the pointer " \
                  "position inside it ({output}_mouse_x/_mouse_y)."
    params = (Param("output", "Output variable", "var", "viewport"),)
    summary_fmt = "→ {output}"

    def execute(self, ctx, p, node):
        x, y, w, h = _viewport_rect(ctx)
        mx, my = ctx.input.cursor()
        out = p["output"]
        for key, value in (("x", x), ("y", y), ("w", w), ("h", h), ("mouse_x", mx - x), ("mouse_y", my - y)):
            ctx.set(f"{out}_{key}", value)


@register
class WaitForLoad(Block):
    type_id, title, category = "rbx.wait_load", "Wait For Roblox To Load", CAT
    description = "Wait until Roblox is attached, responding, and its viewport shows more than a blank screen."
    params = (Param("timeout", "Timeout (s)", "float", 60.0, minimum=0, maximum=86400, tooltip="0 waits forever"),
              Param("settle", "Then wait (s)", "float", 1.0, minimum=0, maximum=600))

    def execute(self, ctx, p, node):
        def ready():
            hwnd = ctx.services.roblox_hwnd()
            if not hwnd or not win32gui.IsWindow(hwnd) or not _responding(hwnd):
                return False
            return float(ctx.capture(_viewport_rect(ctx)).std()) > 8.0  # loading screens are near-flat

        if not ctx.poll(ready, p["timeout"], 0.5):
            raise TimeoutError(f"Roblox did not finish loading within {p['timeout']:g} s")
        ctx.wait(p["settle"])
