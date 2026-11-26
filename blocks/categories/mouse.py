"""Mouse blocks. Coordinates are relative to the Roblox viewport (client area) or the screen."""
from automation.engine import SPACES
from automation.input_controller import BUTTONS
from blocks.base_block import Block, Param
from blocks.registry import register

CAT = "Mouse"
XY = (Param("x", "X", "int", 0, minimum=-100000, maximum=100000),
      Param("y", "Y", "int", 0, minimum=-100000, maximum=100000),
      Param("space", "Relative to", "choice", "viewport", options=SPACES))
BUTTON = Param("button", "Button", "choice", "left", options=BUTTONS)


def glide(ctx, x: int, y: int, duration: float) -> None:
    """Move to screen (x, y), smoothly over duration seconds."""
    if duration <= 0:
        ctx.input.move(x, y)
        return
    sx, sy = ctx.input.cursor()
    steps = max(1, int(duration / 0.01))
    for i in range(1, steps + 1):
        t = i / steps
        t = t * t * (3 - 2 * t)  # ease in/out
        ctx.input.move(round(sx + (x - sx) * t), round(sy + (y - sy) * t))
        ctx.wait(duration / steps)


@register
class MoveMouse(Block):
    type_id, title, category = "mouse.move", "Move Mouse", CAT
    description = "Move the pointer to a position."
    params = XY + (Param("duration", "Duration (s)", "float", 0.0, minimum=0, maximum=60),)
    summary_fmt = "{x}, {y} ({space})"

    def execute(self, ctx, p, node):
        glide(ctx, *ctx.to_screen(p["x"], p["y"], p["space"]), p["duration"])


class _Click(Block):
    category = CAT
    button, count = "left", 1
    description = "Click at the current pointer position."

    def execute(self, ctx, p, node):
        ctx.input.click(self.button, self.count)


@register
class LeftClick(_Click):
    type_id, title = "mouse.left_click", "Left Click"


@register
class RightClick(_Click):
    type_id, title, button = "mouse.right_click", "Right Click", "right"


@register
class DoubleClick(_Click):
    type_id, title, count = "mouse.double_click", "Double Click", 2


@register
class MouseDown(Block):
    type_id, title, category = "mouse.down", "Mouse Down", CAT
    description = "Press and hold a mouse button (released automatically when the workflow ends)."
    params = (BUTTON,)
    summary_fmt = "{button}"

    def execute(self, ctx, p, node):
        ctx.input.button_down(p["button"])


@register
class MouseUp(Block):
    type_id, title, category = "mouse.up", "Mouse Up", CAT
    description = "Release a mouse button."
    params = (BUTTON,)
    summary_fmt = "{button}"

    def execute(self, ctx, p, node):
        ctx.input.button_up(p["button"])


@register
class Scroll(Block):
    type_id, title, category = "mouse.scroll", "Scroll", CAT
    description = "Turn the mouse wheel. Positive scrolls up, negative down."
    params = (Param("amount", "Notches", "int", -3, minimum=-100, maximum=100),)
    summary_fmt = "{amount}"

    def execute(self, ctx, p, node):
        ctx.input.scroll(p["amount"])


@register
class DragMouse(Block):
    type_id, title, category = "mouse.drag", "Drag Mouse", CAT
    description = "Press at one point, move to another, release."
    params = (Param("x1", "From X", "int", 0, minimum=-100000, maximum=100000),
              Param("y1", "From Y", "int", 0, minimum=-100000, maximum=100000),
              Param("x2", "To X", "int", 100, minimum=-100000, maximum=100000),
              Param("y2", "To Y", "int", 100, minimum=-100000, maximum=100000),
              Param("space", "Relative to", "choice", "viewport", options=SPACES),
              BUTTON,
              Param("duration", "Duration (s)", "float", 0.3, minimum=0, maximum=60))
    summary_fmt = "{x1},{y1} → {x2},{y2}"

    def execute(self, ctx, p, node):
        ctx.input.move(*ctx.to_screen(p["x1"], p["y1"], p["space"]))
        ctx.wait(0.03)
        ctx.input.button_down(p["button"])
        try:
            glide(ctx, *ctx.to_screen(p["x2"], p["y2"], p["space"]), p["duration"])
            ctx.wait(0.03)
        finally:
            ctx.input.button_up(p["button"])


@register
class ClickAt(Block):
    type_id, title, category = "mouse.click_at", "Click At Coordinates", CAT
    description = "Move to a position and click."
    params = XY + (BUTTON, Param("clicks", "Clicks", "int", 1, minimum=1, maximum=10))
    summary_fmt = "{x}, {y} ({space})"

    def execute(self, ctx, p, node):
        ctx.input.move(*ctx.to_screen(p["x"], p["y"], p["space"]))
        ctx.wait(0.02)  # let the target register the hover before clicking
        ctx.input.click(p["button"], p["clicks"])


def parse_path(text: str) -> list[tuple[int, int, int]]:
    """'x,y,ms; x,y,ms' → points; ms is the time since the previous point."""
    points = []
    for part in text.replace("\n", ";").split(";"):
        if part.strip():
            try:
                x, y, ms = (int(float(v)) for v in part.split(","))
            except ValueError:
                raise ValueError(f"Path points must be 'x,y,ms', got {part.strip()!r}") from None
            points.append((x, y, max(0, ms)))
    return points


@register
class MoveAlongPath(Block):
    type_id, title, category = "mouse.path", "Move Along Path", CAT
    icon = "mouse"
    description = ("Move the pointer through recorded points. Path: 'x,y,ms; x,y,ms' where ms is the time "
                   "since the previous point.")
    params = (Param("path", "Path", "str", "0,0,0; 100,100,200"),
              Param("space", "Relative to", "choice", "viewport", options=SPACES),
              Param("speed", "Speed", "float", 1.0, minimum=0.1, maximum=10, tooltip="2 = twice as fast"))

    @classmethod
    def summary(cls, params):
        try:
            pts = parse_path(params.get("path", ""))
        except ValueError:
            return "invalid path"
        return f"{len(pts)} points, {sum(p[2] for p in pts) / 1000:.2f} s"

    def execute(self, ctx, p, node):
        for x, y, ms in parse_path(p["path"]):
            if ms:
                ctx.wait(ms / 1000 / p["speed"])
            ctx.input.move(*ctx.to_screen(x, y, p["space"]))
