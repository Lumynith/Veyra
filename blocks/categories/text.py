"""Text Detection blocks (OCR on screen captures).

Text results: {out} = found (true/false), {out}_x / {out}_y = centre of the first match, {out}_text = what was
read, {out}_conf = OCR confidence, {out}_count = number of matches, {out}_list = [(x, y, text), …],
{out}_1_x / {out}_1_y / {out}_1_text … per match.
Number results: {out} = the number ("" when none), {out}_found, {out}_x / {out}_y, {out}_list = all numbers.
"""
import difflib
import re

from automation.engine import SPACES
from automation.expressions import coerce, compare
from blocks.base_block import Block, Param
from blocks.registry import register
from vision.text_detector import MATCH_MODES, TextMatch, find_all_text, find_numbers, full_text
from vision.text_targets import display_target

CAT = "Text Detection"
TARGET = Param("target", "Saved target", "text_target", "",
               tooltip="A target saved on the Text Detection page. When set it replaces Text, Match, "
                       "Case sensitive and Language below, and the search region if the target has one.")
AREA = (Param("region", "Search region", "region", "", tooltip="x, y, width, height — blank reads everything"),
        Param("space", "Relative to", "choice", "viewport", options=SPACES))
OCR = (Param("confidence", "Min. OCR confidence", "float", 0.0, minimum=0.0, maximum=1.0,
             tooltip="Ignore words the OCR is less sure about (0–1). Tesseract reports confidence; "
                     "Windows OCR does not, so its words always count as 1.0."),
       Param("language", "OCR language", "str", "",
             tooltip="Blank = the default from Settings. e.g. en-US (Windows OCR) or eng (Tesseract)"))
QUERY = (TARGET,
         Param("text", "Text", "str", "Play"),
         Param("match", "Match", "choice", "partial", options=MATCH_MODES,
               tooltip="partial: anywhere in a line · exact: whole word(s) only · regex: a regular expression"),
         Param("case_sensitive", "Case sensitive", "bool", False)) + OCR + AREA
TIMING = (Param("timeout", "Timeout (s)", "float", 10.0, minimum=0, maximum=86400, tooltip="0 waits forever"),
          Param("interval", "Retry interval (s)", "float", 0.5, minimum=0.05, maximum=60,
                tooltip="Time between OCR attempts; higher uses less CPU"))
OUT = Param("output", "Output variable", "var", "text")
ACTIONS = ("left", "right", "middle", "double")
NUMBER_OPS = ("any", "==", "!=", ">", "<", ">=", "<=")
NUMBER = OCR + AREA + (
    Param("op", "Condition", "choice", "any", options=NUMBER_OPS, tooltip="Only accept numbers that pass this test"),
    Param("value", "Compare to", "str", "0", tooltip="A number or {variable}"),
    Param("select", "Select", "choice", "first", options=("first", "last", "largest", "smallest")),
    Param("output", "Output variable", "var", "number"))


def query(ctx, p) -> dict:
    """Block params with a saved target applied."""
    if not p.get("target"):
        return p
    t = ctx.services.text_targets.get(p["target"])
    q = {**p, "text": t["text"], "match": t["match"], "case_sensitive": t["case_sensitive"],
         "language": t["language"] or p.get("language", ""),
         "confidence": max(float(t["confidence"]), p.get("confidence", 0.0))}
    if t["region"]:  # target regions are always viewport coordinates
        q["region"], q["space"] = t["region"], "viewport"
    return q


def read(ctx, p):
    left, top, w, h = ctx.area(p["region"], p["space"])
    words = ctx.ocr(p.get("language", "")).words(ctx.capture((left, top, w, h)))
    ox, oy = ctx.origin(p["space"])
    return words, (left - ox, top - oy)  # offset from capture to coordinate space


def shift(m: TextMatch, dx: int, dy: int) -> TextMatch:
    return TextMatch(m.x + dx, m.y + dy, m.w, m.h, m.text, m.conf)


def find_all(ctx, p) -> list[TextMatch]:
    words, (dx, dy) = read(ctx, p)
    found = [shift(m, dx, dy) for m in find_all_text(words, p["text"], p["case_sensitive"], p["match"],
                                                     p["confidence"])]
    detail = (f"{len(found)} match(es), first {found[0].text!r} at {found[0].center[0]}, {found[0].center[1]}"
              if found else f"read {len(words)} word(s)")
    ctx.detection(f"Text {p['text']!r}", bool(found), detail)
    return found


def store(ctx, out: str, matches: list[TextMatch]) -> None:
    if not out:
        return
    ctx.set(out, bool(matches))
    ctx.set(f"{out}_count", len(matches))
    ctx.set(f"{out}_list", [(*m.center, m.text) for m in matches])
    for i, m in enumerate(matches, 1):
        ctx.set(f"{out}_{i}_x", m.center[0])
        ctx.set(f"{out}_{i}_y", m.center[1])
        ctx.set(f"{out}_{i}_text", m.text)
    if matches:
        m = matches[0]
        for key, value in (("x", m.center[0]), ("y", m.center[1]), ("text", m.text), ("conf", round(m.conf, 3)),
                           ("w", m.w), ("h", m.h)):
            ctx.set(f"{out}_{key}", value)


def wait_for(ctx, p) -> list[TextMatch]:
    matches = ctx.poll(lambda: find_all(ctx, p), p["timeout"], p["interval"]) or []
    store(ctx, p.get("output", ""), matches)
    if not matches:
        raise TimeoutError(f"Text {p['text']!r} not found within {p['timeout']:g} s")
    return matches


class _TextBlock(Block):
    category = CAT

    @classmethod
    def summary(cls, params):
        if params.get("target"):
            return display_target(params["target"])
        return f"“{params.get('text', '')}”"


@register
class FindText(_TextBlock):
    type_id, title = "txt.find", "Find Text"
    description = ("Look for text once. Sets {output} (true/false), {output}_x/_y (centre), {output}_count "
                   "and {output}_list with every match.")
    params = QUERY + (OUT,)

    def execute(self, ctx, p, node):
        matches = find_all(ctx, query(ctx, p))
        store(ctx, p["output"], matches)
        return bool(matches)


@register
class WaitForText(_TextBlock):
    type_id, title = "txt.wait", "Wait For Text"
    description = "Keep reading until the text appears. Fails on timeout."
    params = QUERY + TIMING + (OUT,)

    def execute(self, ctx, p, node):
        wait_for(ctx, query(ctx, p))


@register
class ClickText(_TextBlock):
    type_id, title = "txt.click", "Click Text"
    description = "Wait for text, then click the centre of it (the Nth match when it appears more than once)."
    params = QUERY + TIMING + (Param("action", "Click", "choice", "left", options=ACTIONS),
                               Param("occurrence", "Occurrence", "int", 1, minimum=1, maximum=100,
                                     tooltip="Which match to click, in reading order"), OUT)

    def execute(self, ctx, p, node):
        q = query(ctx, p)
        matches = wait_for(ctx, q)
        if p["occurrence"] > len(matches):
            raise ValueError(f"Only {len(matches)} match(es) of {q['text']!r}; cannot click #{p['occurrence']}")
        x, y = matches[p["occurrence"] - 1].center
        ctx.input.move(*ctx.to_screen(x, y, q["space"]))
        ctx.wait(0.02)  # let the target register the hover first
        if p["action"] == "double":
            ctx.input.click("left", 2)
        else:
            ctx.input.click(p["action"])


@register
class ReadText(Block):
    type_id, title, category = "txt.read", "Read Text", CAT
    description = "Read all text in a region into a variable (lines separated by newlines). {output}_words = word count."
    params = OCR + AREA + (OUT,)
    summary_fmt = "→ {output}"

    def execute(self, ctx, p, node):
        words, _ = read(ctx, p)
        text = full_text(words, p["confidence"])
        ctx.set(p["output"], text)
        ctx.set(f"{p['output']}_words", sum(w.conf >= p["confidence"] for w in words))
        return text


@register
class TextExists(_TextBlock):
    type_id, title = "txt.exists", "Text Exists"
    description = "Check once whether text is visible; stores true/false."
    params = QUERY + (Param("output", "Output variable", "var", "exists"),)

    def execute(self, ctx, p, node):
        found = bool(find_all(ctx, query(ctx, p)))
        ctx.set(p["output"], found)
        return found


@register
class TextDisappears(_TextBlock):
    type_id, title = "txt.disappears", "Text Disappears"
    description = "Wait until text is no longer visible. Fails on timeout."
    params = QUERY + TIMING

    def execute(self, ctx, p, node):
        q = query(ctx, p)
        if not ctx.poll(lambda: not find_all(ctx, q), p["timeout"], p["interval"]):
            raise TimeoutError(f"Text {q['text']!r} still visible after {p['timeout']:g} s")


@register
class CompareText(Block):
    type_id, title, category = "txt.compare", "Compare Text", CAT
    description = "Compare two texts. 'similarity' stores a 0–1 score; the others store true/false."
    params = (Param("a", "Text A", "str", "{text}"),
              Param("b", "Text B", "str", ""),
              Param("mode", "Mode", "choice", "equals", options=("equals", "contains", "regex", "similarity"),
                    tooltip="regex: does Text A match the pattern in Text B"),
              Param("case_sensitive", "Case sensitive", "bool", False),
              Param("output", "Output variable", "var", "same"))
    summary_fmt = "{mode}"

    def execute(self, ctx, p, node):
        a, b = p["a"].strip(), p["b"].strip()
        if p["mode"] == "regex":
            result = re.search(b, a, 0 if p["case_sensitive"] else re.IGNORECASE) is not None
        else:
            if not p["case_sensitive"]:
                a, b = a.lower(), b.lower()
            if p["mode"] == "similarity":
                result = round(difflib.SequenceMatcher(None, a, b).ratio(), 4)
            else:
                result = (b in a) if p["mode"] == "contains" else (a == b)
        ctx.set(p["output"], result)
        return result


@register
class ExtractText(Block):
    type_id, title, category = "txt.extract", "Extract Text From Region", CAT
    description = ("Read a region and pull out part of it with a pattern, e.g. \\d+ for a number. "
                   "{output}_list holds every match.")
    params = OCR + AREA + (Param("pattern", "Pattern (regex)", "str", r"\d+", tooltip="Blank keeps all text"),
                           Param("output", "Output variable", "var", "value"))
    summary_fmt = "{pattern} → {output}"

    def execute(self, ctx, p, node):
        words, _ = read(ctx, p)
        text = full_text(words, p["confidence"])
        if p["pattern"]:
            found = [m.group(1) if m.groups() else m.group(0) for m in re.finditer(p["pattern"], text)]
            if not found:
                raise ValueError(f"Pattern {p['pattern']!r} not found in {text!r}")
            ctx.set(f"{p['output']}_list", found)
            text = found[0]
        ctx.set(p["output"], text)
        return text


def numbers(ctx, p):
    """Numbers passing the condition, plus the selected one (or None)."""
    words, (dx, dy) = read(ctx, p)
    found = [(v, shift(m, dx, dy)) for v, m in find_numbers(words, p["confidence"])]
    if p["op"] != "any":
        limit = coerce(p["value"])
        found = [(v, m) for v, m in found if compare(v, p["op"], limit)]
    ctx.detection("Number", bool(found), f"{len(found)} number(s): " + ", ".join(str(v) for v, _ in found[:5]))
    if not found:
        return found, None
    pick = {"first": lambda: found[0], "last": lambda: found[-1],
            "largest": lambda: max(found, key=lambda f: f[0]), "smallest": lambda: min(found, key=lambda f: f[0])}
    return found, pick[p["select"]]()


def store_number(ctx, out: str, found, chosen) -> None:
    ctx.set(out, chosen[0] if chosen else "")
    ctx.set(f"{out}_found", chosen is not None)
    ctx.set(f"{out}_count", len(found))
    ctx.set(f"{out}_list", [v for v, _ in found])
    if chosen:
        ctx.set(f"{out}_x", chosen[1].center[0])
        ctx.set(f"{out}_y", chosen[1].center[1])
        ctx.set(f"{out}_text", chosen[1].text)


def _number_summary(params) -> str:
    cond = "" if params.get("op", "any") == "any" else f" {params['op']} {params.get('value', '')}"
    return f"{params.get('select', 'first')}{cond} → {params.get('output', '')}"


@register
class FindNumber(Block):
    type_id, title, category = "txt.find_number", "Find Number", CAT
    description = ("Read numbers in a region (1,250 · 3.5 · 2.4K). {output} = the selected number, "
                   "{output}_found, {output}_list = all numbers that pass the condition.")
    params = NUMBER

    @classmethod
    def summary(cls, params):
        return _number_summary(params)

    def execute(self, ctx, p, node):
        found, chosen = numbers(ctx, p)
        store_number(ctx, p["output"], found, chosen)
        return chosen is not None


@register
class WaitForNumber(Block):
    type_id, title, category = "txt.wait_number", "Wait For Number", CAT
    description = "Keep reading until a number that passes the condition appears (e.g. ≥ 100). Fails on timeout."
    params = NUMBER + TIMING

    @classmethod
    def summary(cls, params):
        return _number_summary(params)

    def execute(self, ctx, p, node):
        result = ctx.poll(lambda: (r := numbers(ctx, p))[1] and r, p["timeout"], p["interval"])
        found, chosen = result or ([], None)
        store_number(ctx, p["output"], found, chosen)
        if chosen is None:
            cond = "" if p["op"] == "any" else f" {p['op']} {p['value']}"
            raise TimeoutError(f"No number{cond} appeared within {p['timeout']:g} s")
        return chosen[0]
