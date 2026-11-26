"""Image Detection blocks (OpenCV template matching through the RecognitionManager).

Results go to variables: {out} = found (true/false), {out}_x / {out}_y = match centre in the chosen
coordinate space, {out}_score = confidence, {out}_w / {out}_h = matched size, {out}_scale = scale used.
"""
import math

from automation.engine import SPACES
from blocks.base_block import Block, Param
from blocks.registry import register
from vision import image_capture
from vision.assets import display_label
from vision.image_detector import METHODS, MODES, DetectOptions, Match, similarity

CAT = "Image Detection"
IMAGE = Param("image", "Image", "image", "", tooltip="Pick an image asset, capture one, or import a file")
DETECT = (
    IMAGE,
    Param("confidence", "Confidence threshold", "float", 0.85, minimum=0.1, maximum=1.0,
          tooltip="0.80–0.90 suits most UI; lower it if the image is slightly different on screen"),
    Param("region", "Search region", "region", "", tooltip="x, y, width, height — blank searches everything"),
    Param("space", "Relative to", "choice", "viewport", options=SPACES),
    Param("mode", "Detection mode", "choice", "color", options=MODES,
          tooltip="grayscale ignores colour shifts; edges ignores colour and lighting (good for outlines/icons)"),
    Param("method", "Matching method", "choice", "ccoeff", options=tuple(METHODS),
          tooltip="ccoeff: robust default · ccorr: bright templates · sqdiff: exact pixel differences"),
    Param("multiscale", "Multi-scale matching", "bool", True, tooltip="Also try slightly smaller/larger sizes"),
    Param("scale_min", "Min scale", "float", 0.85, minimum=0.2, maximum=1.0),
    Param("scale_max", "Max scale", "float", 1.15, minimum=1.0, maximum=5.0),
    Param("adapt", "Adapt to viewport size", "bool", True,
          tooltip="Rescale images captured at a different Roblox window size"),
)
TIMING = (Param("timeout", "Max detection time (s)", "float", 10.0, minimum=0, maximum=86400,
                tooltip="0 waits forever"),
          Param("interval", "Retry interval (s)", "float", 0.25, minimum=0.02, maximum=60,
                tooltip="Time between attempts; higher uses less CPU"))
OUT = Param("output", "Output variable", "var", "image")
ACTIONS = ("left", "right", "middle", "double")
OFFSETS = (Param("offset_x", "Offset X", "int", 0, minimum=-10000, maximum=10000),
           Param("offset_y", "Offset Y", "int", 0, minimum=-10000, maximum=10000))


def options(p) -> DetectOptions:
    return DetectOptions(confidence=p["confidence"], mode=p["mode"], method=p["method"],
                         multiscale=p["multiscale"], scale_min=p["scale_min"], scale_max=p["scale_max"])


def detect(ctx, p) -> Match | None:
    """Best match above the threshold, with coordinates in p['space']."""
    left, top, w, h = ctx.area(p["region"], p["space"])
    adapt = ctx.space_size(p["space"]) if p["adapt"] and p["space"] == "viewport" else None
    m = ctx.vision.locate(p["image"], (left, top, w, h), options(p), adapt, should_stop=ctx.checkpoint)
    name = display_label(p["image"]) or "image"
    if m is None or m.score < p["confidence"]:
        best = f"best score {m.score:.2f}" if m is not None else "no candidate"
        ctx.detection(f"Image {name}", False, f"{best} < {p['confidence']:.2f}")
        return None
    ox, oy = ctx.origin(p["space"])
    found = Match(left - ox + m.x, top - oy + m.y, m.w, m.h, m.score, m.scale)
    ctx.detection(f"Image {name}", True, f"at {found.center[0]}, {found.center[1]} ({p['space']}) "
                                         f"score {m.score:.2f} scale {m.scale:.2f}")
    return found


def store(ctx, out: str, m: Match | None) -> None:
    if not out:
        return
    ctx.set(out, m is not None)
    if m:
        cx, cy = m.center
        for key, value in (("x", cx), ("y", cy), ("score", m.score), ("w", m.w), ("h", m.h),
                           ("scale", round(m.scale, 3)), ("left", m.x), ("top", m.y)):
            ctx.set(f"{out}_{key}", value)


def wait_for(ctx, p) -> Match:
    m = ctx.poll(lambda: detect(ctx, p), p["timeout"], p["interval"])
    store(ctx, p.get("output", ""), m)
    if not m:
        raise TimeoutError(f"{display_label(p['image'])} not found within {p['timeout']:g} s")
    return m


def click_at(ctx, x: int, y: int, space: str, action: str) -> None:
    ctx.input.move(*ctx.to_screen(x, y, space))
    ctx.wait(0.02)  # let the target register the hover first
    if action == "double":
        ctx.input.click("left", 2)
    else:
        ctx.input.click(action)


class _ImageBlock(Block):
    category = CAT

    @classmethod
    def summary(cls, params):
        return display_label(params.get("image", "")) or "no image"


@register
class FindImage(_ImageBlock):
    type_id, title = "img.find", "Find Image"
    description = "Look for an image once. Sets {output} (true/false), {output}_x/_y (centre) and {output}_score."
    params = DETECT + (OUT,)

    def execute(self, ctx, p, node):
        m = detect(ctx, p)
        store(ctx, p["output"], m)
        return m is not None


@register
class WaitForImage(_ImageBlock):
    type_id, title = "img.wait", "Wait For Image"
    description = "Keep searching until the image appears. Fails after the max detection time."
    params = DETECT + TIMING + (OUT,)

    def execute(self, ctx, p, node):
        wait_for(ctx, p)


@register
class ClickImage(_ImageBlock):
    type_id, title = "img.click", "Click Image"
    description = "Wait for an image, move to its centre (plus offset) and click."
    params = DETECT + TIMING + (Param("action", "Click", "choice", "left", options=ACTIONS),) + OFFSETS + (OUT,)

    def execute(self, ctx, p, node):
        x, y = wait_for(ctx, p).center
        click_at(ctx, x + p["offset_x"], y + p["offset_y"], p["space"], p["action"])


@register
class MoveToImage(_ImageBlock):
    type_id, title = "img.move", "Move To Image"
    description = "Wait for an image, then move the pointer to its centre (plus offset)."
    params = DETECT + TIMING + OFFSETS + (OUT,)

    def execute(self, ctx, p, node):
        x, y = wait_for(ctx, p).center
        ctx.input.move(*ctx.to_screen(x + p["offset_x"], y + p["offset_y"], p["space"]))


@register
class ImageExists(_ImageBlock):
    type_id, title = "img.exists", "Image Exists"
    description = "Check once whether an image is visible; stores true/false."
    params = DETECT + (Param("output", "Output variable", "var", "exists"),)

    def execute(self, ctx, p, node):
        found = detect(ctx, p) is not None
        ctx.set(p["output"], found)
        return found


@register
class ImageDisappears(_ImageBlock):
    type_id, title = "img.disappears", "Image Disappears"
    description = "Wait until an image is no longer visible. Fails after the max detection time."
    params = DETECT + TIMING

    def execute(self, ctx, p, node):
        if not ctx.poll(lambda: detect(ctx, p) is None, p["timeout"], p["interval"]):
            raise TimeoutError(f"{display_label(p['image'])} still visible after {p['timeout']:g} s")


@register
class FindMultipleImages(_ImageBlock):
    type_id, title = "img.find_all", "Find Multiple Images"
    description = ("Find every occurrence. {output} = count, {output}_list = [(x, y, score), …], "
                   "{output}_1_x/_1_y … per match, and {output}_x/_y = the selected match.")
    params = DETECT + (
        Param("limit", "Max results", "int", 20, minimum=1, maximum=500),
        Param("select", "Select", "choice", "best", options=("best", "closest to mouse", "closest to point",
                                                              "top-left")),
        Param("point_x", "Point X", "int", 0, minimum=-100000, maximum=100000, tooltip="For 'closest to point'"),
        Param("point_y", "Point Y", "int", 0, minimum=-100000, maximum=100000),
        Param("output", "Output variable", "var", "matches"),
    )

    def execute(self, ctx, p, node):
        left, top, w, h = ctx.area(p["region"], p["space"])
        adapt = ctx.space_size(p["space"]) if p["adapt"] and p["space"] == "viewport" else None
        found = ctx.vision.locate_all(p["image"], (left, top, w, h), options(p), adapt, p["limit"], ctx.checkpoint)
        ox, oy = ctx.origin(p["space"])
        matches = [Match(left - ox + m.x, top - oy + m.y, m.w, m.h, m.score, m.scale) for m in found]
        ctx.detection(f"Image {display_label(p['image']) or 'image'}", bool(matches), f"{len(matches)} match(es)")
        out = p["output"]
        ctx.set(out, len(matches))
        ctx.set(f"{out}_list", [(*m.center, m.score) for m in matches])
        for i, m in enumerate(matches, 1):
            ctx.set(f"{out}_{i}_x", m.center[0])
            ctx.set(f"{out}_{i}_y", m.center[1])
        chosen = self._select(ctx, p, matches)
        ctx.set(f"{out}_found", chosen is not None)
        if chosen:
            ctx.set(f"{out}_x", chosen.center[0])
            ctx.set(f"{out}_y", chosen.center[1])
            ctx.set(f"{out}_score", chosen.score)
        return len(matches)

    @staticmethod
    def _select(ctx, p, matches: list[Match]) -> Match | None:
        if not matches:
            return None
        if p["select"] == "top-left":
            return min(matches, key=lambda m: (m.y, m.x))
        if p["select"] in ("closest to mouse", "closest to point"):
            if p["select"] == "closest to mouse":
                sx, sy = ctx.input.cursor()
                ox, oy = ctx.origin(p["space"])
                px, py = sx - ox, sy - oy
            else:
                px, py = p["point_x"], p["point_y"]
            return min(matches, key=lambda m: math.dist(m.center, (px, py)))
        return matches[0]  # best score


@register
class CompareImages(_ImageBlock):
    type_id, title = "img.compare", "Compare Images"
    description = "Similarity (0–1) between an image and another image, or the current screen region."
    params = (IMAGE,
              Param("other", "Compare with", "image", "", tooltip="Blank compares with the search region"),
              Param("region", "Search region", "region", ""),
              Param("space", "Relative to", "choice", "viewport", options=SPACES),
              Param("output", "Output variable", "var", "similarity"))

    def execute(self, ctx, p, node):
        a = ctx.vision.template(p["image"])[0]
        b = ctx.vision.template(p["other"])[0] if p["other"] else ctx.capture(ctx.area(p["region"], p["space"]))
        score = round(similarity(a, b), 4)
        ctx.set(p["output"], score)
        return score


@register
class CaptureScreenshot(Block):
    type_id, title, category = "img.capture", "Capture Screenshot", CAT
    icon = "camera"
    description = "Save a region to a PNG file, and optionally add it to the image library."
    params = (Param("region", "Region", "region", ""),
              Param("space", "Relative to", "choice", "viewport", options=SPACES),
              Param("file", "Save as", "str", "captures/capture_{timestamp}.png"),
              Param("asset_name", "Add to library as", "str", "", tooltip="Blank: file only"),
              Param("output", "Output variable", "var", "capture_path"))
    summary_fmt = "{file}"

    def execute(self, ctx, p, node):
        img = ctx.capture(ctx.area(p["region"], p["space"]))
        path = image_capture.save_image(img, ctx.path(p["file"]))
        ctx.set(p["output"], str(path))
        if p["asset_name"]:
            ref_size = ctx.space_size("viewport") if p["space"] == "viewport" else None
            meta = ctx.vision.assets.add_image(img, p["asset_name"], "Captures", ref_size)
            ctx.set(f"{p['output']}_asset", ctx.vision.assets.ref(meta["id"]))
        ctx.log(f"Saved {path}")
