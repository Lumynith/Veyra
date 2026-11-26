"""Recordings: raw input events → compressed actions → editable workflow blocks.

Raw events (what the recorder captured), times in seconds from the start:
  {"t", "k": "move", "x", "y"}             {"t", "k": "down"|"up", "x", "y", "b": "left"|"right"|"middle"}
  {"t", "k": "wheel", "x", "y", "d": notches} {"t", "k": "kdown"|"kup", "vk"}
Coordinates are screen pixels; the recording stores the Roblox viewport origin so blocks can use viewport space.
"""
from __future__ import annotations

import json
import math
import os
import time
from dataclasses import asdict, dataclass, fields
from pathlib import Path

from automation.input_controller import MODIFIERS, key_name
from automation.workflow import Workflow, new_node

FORMAT = "lumencrest.recording"
VERSION = 1
KINDS = {"move", "down", "up", "wheel", "kdown", "kup"}
MAX_EVENTS = 500_000


@dataclass
class Options:
    moves: str = "path"         # "path": keep filtered pointer paths · "clicks": only positions of clicks/scrolls
    min_move_px: int = 8        # drop pointer samples closer than this to the previous kept one
    min_move_ms: int = 40       # …or sooner than this after it
    timing_ms: int = 20         # round every delay to this many milliseconds
    min_wait: float = 0.05      # shorter pauses between actions are dropped
    speed: float = 1.0          # playback speed (2 = twice as fast)
    double_click_ms: int = 500
    drag_px: int = 6            # a press that moves further than this is a drag, not a click
    repeat: int = 1

    @classmethod
    def from_dict(cls, d: dict) -> "Options":
        names = {f.name for f in fields(cls)}
        return cls(**{k: v for k, v in (d or {}).items() if k in names})


PRESETS = {  # recording precision
    "low": dict(moves="clicks", min_move_px=20, min_move_ms=100, timing_ms=100, min_wait=0.1),
    "medium": dict(moves="path", min_move_px=8, min_move_ms=40, timing_ms=20, min_wait=0.05),
    "high": dict(moves="path", min_move_px=3, min_move_ms=15, timing_ms=5, min_wait=0.02),
    "exact": dict(moves="path", min_move_px=0, min_move_ms=0, timing_ms=1, min_wait=0.001),
}


# -- storage ---------------------------------------------------------------------------------------

def new_recording(origin: tuple[int, int] | None, size: tuple[int, int] | None) -> dict:
    return {"format": FORMAT, "version": VERSION, "created": time.strftime("%Y-%m-%d %H:%M:%S"),
            "space": "viewport" if origin else "screen", "origin": list(origin or (0, 0)),
            "size": list(size) if size else None, "events": []}


def validate_recording(data: dict) -> dict:
    """Check an imported recording; returns it with clean events. Raises ValueError."""
    if not isinstance(data, dict) or data.get("format") != FORMAT:
        raise ValueError("Not a Veyra recording file")
    if int(data.get("version", 0)) > VERSION:
        raise ValueError(f"Recording version {data['version']} is newer than this app supports ({VERSION})")
    events = data.get("events")
    if not isinstance(events, list) or len(events) > MAX_EVENTS:
        raise ValueError("Recording has no valid event list")
    clean, last = [], 0.0
    for i, e in enumerate(events):
        try:
            k, t = e["k"], float(e["t"])
            if k not in KINDS or t < last - 1e-6 or not math.isfinite(t):
                raise ValueError
            c = {"t": t, "k": k}
            if k in ("kdown", "kup"):
                c["vk"] = int(e["vk"]) & 0xFF
            else:
                c["x"], c["y"] = int(e["x"]), int(e["y"])
                if k in ("down", "up"):
                    if e["b"] not in ("left", "right", "middle"):
                        raise ValueError
                    c["b"] = e["b"]
                if k == "wheel":
                    c["d"] = float(e["d"])
        except (KeyError, TypeError, ValueError):
            raise ValueError(f"Recording event {i + 1} is invalid: {e!r}") from None
        clean.append(c)
        last = t
    origin = data.get("origin") or [0, 0]
    return {**data, "space": "viewport" if data.get("space") == "viewport" else "screen",
            "origin": [int(origin[0]), int(origin[1])], "events": clean}


def save_recording(rec: dict, path: Path) -> None:
    path = Path(path)
    path.parent.mkdir(parents=True, exist_ok=True)
    tmp = path.with_suffix(".tmp")
    tmp.write_text(json.dumps(rec, separators=(",", ":")), "utf-8")
    os.replace(tmp, path)


def load_recording(path: Path) -> dict:
    try:
        return validate_recording(json.loads(Path(path).read_text("utf-8")))
    except OSError as e:
        raise ValueError(f"Cannot read {path}: {e}") from None
    except json.JSONDecodeError as e:
        raise ValueError(f"{Path(path).name} is not valid JSON: {e}") from None


# -- compression -----------------------------------------------------------------------------------

def clean_events(events: list[dict]) -> list[dict]:
    """Safe duplicate removal: key auto-repeat, unchanged pointer samples, trailing pointer moves."""
    out, held, last_xy = [], set(), None
    for e in events:
        if e["k"] == "kdown":
            if e["vk"] in held:
                continue  # Windows auto-repeat while a key is held
            held.add(e["vk"])
        elif e["k"] == "kup":
            if e["vk"] not in held:
                continue  # released a key that was down before recording started
            held.discard(e["vk"])
        elif e["k"] == "move":
            if (e["x"], e["y"]) == last_xy:
                continue
        if "x" in e:
            last_xy = (e["x"], e["y"])
        out.append(e)
    while out and out[-1]["k"] == "move":  # moving to the Stop button is not part of the macro
        out.pop()
    return out


def _filter_path(moves: list[dict], o: Options) -> list[dict]:
    kept = [moves[0]]
    for m in moves[1:]:
        k = kept[-1]
        if math.dist((m["x"], m["y"]), (k["x"], k["y"])) >= o.min_move_px and (m["t"] - k["t"]) * 1000 >= o.min_move_ms:
            kept.append(m)
    if kept[-1] is not moves[-1]:
        kept.append(moves[-1])  # always end where the pointer really stopped
    return kept


def to_actions(events: list[dict], o: Options) -> list[dict]:
    """Group raw events into actions, each with start "t" and "end" times."""
    ev = clean_events(events)
    actions: list[dict] = []
    used = [False] * len(ev)

    def find(i: int, kind: str, key: str, value) -> int | None:
        for j in range(i + 1, len(ev)):
            if not used[j] and ev[j]["k"] == kind and ev[j].get(key) == value:
                return j
        return None

    def only_moves(i: int, j: int) -> bool:
        return all(ev[k]["k"] == "move" or used[k] for k in range(i + 1, j))

    def consume(i: int, j: int) -> None:
        for k in range(i, j + 1):
            used[k] = True

    def as_click(i: int) -> int | None:
        """Index of the matching release when ev[i] is a plain click, else None."""
        j = find(i, "up", "b", ev[i]["b"])
        if j is None or not only_moves(i, j):
            return None
        if math.dist((ev[i]["x"], ev[i]["y"]), (ev[j]["x"], ev[j]["y"])) > o.drag_px or ev[j]["t"] - ev[i]["t"] > 0.8:
            return None
        return j

    i = 0
    while i < len(ev):
        if used[i]:
            i += 1
            continue
        e = ev[i]
        k = e["k"]
        if k == "move":
            j = i
            while j + 1 < len(ev) and ev[j + 1]["k"] == "move" and not used[j + 1]:
                j += 1
            if o.moves == "path":
                pts = _filter_path(ev[i:j + 1], o)
                actions.append({"a": "path", "t": pts[0]["t"], "end": pts[-1]["t"],
                                "points": [(p["x"], p["y"], p["t"]) for p in pts]})
            consume(i, j)
        elif k == "down":
            j = as_click(i)
            if j is not None:
                count, end = 1, ev[j]["t"]
                nxt = next((n for n in range(j + 1, len(ev)) if not used[n] and ev[n]["k"] != "move"), None)
                if (nxt is not None and ev[nxt]["k"] == "down" and ev[nxt]["b"] == e["b"]
                        and (ev[nxt]["t"] - e["t"]) * 1000 <= o.double_click_ms
                        and math.dist((e["x"], e["y"]), (ev[nxt]["x"], ev[nxt]["y"])) <= o.drag_px):
                    j2 = as_click(nxt)
                    if j2 is not None:
                        count, end, j = 2, ev[j2]["t"], j2
                actions.append({"a": "click", "t": e["t"], "end": end, "b": e["b"], "x": e["x"], "y": e["y"],
                                "count": count})
                consume(i, j)
            else:
                j = find(i, "up", "b", e["b"])
                if j is not None and only_moves(i, j):
                    actions.append({"a": "drag", "t": e["t"], "end": ev[j]["t"], "b": e["b"], "x1": e["x"],
                                    "y1": e["y"], "x2": ev[j]["x"], "y2": ev[j]["y"]})
                    consume(i, j)
                else:
                    actions.append({"a": "mouse_down", "t": e["t"], "end": e["t"], "b": e["b"],
                                    "x": e["x"], "y": e["y"]})
                    used[i] = True
        elif k == "up":
            actions.append({"a": "mouse_up", "t": e["t"], "end": e["t"], "b": e["b"], "x": e["x"], "y": e["y"]})
            used[i] = True
        elif k == "wheel":
            total, j = e["d"], i
            for n in range(i + 1, len(ev)):
                if ev[n]["k"] == "move" or used[n]:
                    continue
                if ev[n]["k"] == "wheel" and ev[n]["t"] - ev[j]["t"] <= 0.3 and (ev[n]["d"] > 0) == (e["d"] > 0):
                    consume(j + 1, n)  # pointer jitter between notches goes with the scroll
                    total, j = total + ev[n]["d"], n
                else:
                    break
            used[i] = True
            if round(total):
                actions.append({"a": "scroll", "t": e["t"], "end": ev[j]["t"], "x": e["x"], "y": e["y"],
                                "amount": round(total)})
        elif k == "kdown":
            combo = _combo(ev, used, i) if e["vk"] in MODIFIERS else None
            j = find(i, "kup", "vk", e["vk"])
            if combo:
                names, last = combo
                actions.append({"a": "combo", "t": e["t"], "end": ev[last]["t"], "keys": "+".join(names)})
                consume(i, last)
            elif j is not None and all(used[n] or (o.moves == "clicks" and ev[n]["k"] == "move")
                                       for n in range(i + 1, j)):
                actions.append({"a": "key", "t": e["t"], "end": ev[j]["t"], "key": key_name(e["vk"]),
                                "hold": ev[j]["t"] - e["t"]})
                consume(i, j)
            else:  # held while other things happen (e.g. W held while clicking)
                actions.append({"a": "key_down", "t": e["t"], "end": e["t"], "key": key_name(e["vk"])})
                used[i] = True
        elif k == "kup":
            actions.append({"a": "key_up", "t": e["t"], "end": e["t"], "key": key_name(e["vk"])})
            used[i] = True
        i += 1
    return actions


def _combo(ev: list[dict], used: list[bool], i: int):
    """modifier(s) down, one key down+up, modifier(s) up, nothing else → (names, index of last event)."""
    mods, n = [], i
    while n < len(ev) and ev[n]["k"] == "kdown" and ev[n]["vk"] in MODIFIERS and not used[n]:
        mods.append(ev[n]["vk"])
        n += 1
    rest = [m for m in range(n, len(ev)) if ev[m]["k"] != "move" and not used[m]]
    if len(rest) < 2 + len(mods):
        return None
    down, up = ev[rest[0]], ev[rest[1]]
    if not (down["k"] == "kdown" and up["k"] == "kup" and down["vk"] == up["vk"] and down["vk"] not in MODIFIERS):
        return None
    releases = rest[2:2 + len(mods)]
    if sorted(ev[m]["vk"] for m in releases) != sorted(mods) or any(ev[m]["k"] != "kup" for m in releases):
        return None
    names = list(dict.fromkeys(key_name(vk) for vk in mods)) + [key_name(down["vk"])]
    return names, releases[-1]


# -- blocks ------------------------------------------------------------------------------------------

def _round(seconds: float, o: Options) -> float:
    step = max(1, o.timing_ms) / 1000
    return round(round(seconds / max(o.speed, 0.01) / step) * step, 3)


def to_workflow(rec: dict, o: Options) -> tuple[Workflow, dict]:
    """Editable workflow for a recording, plus stats."""
    space = rec.get("space", "screen")
    ox, oy = rec.get("origin", (0, 0)) if space == "viewport" else (0, 0)
    actions = to_actions(rec["events"], o)
    blocks, cursor, last_end = [], None, None

    def at(x, y):
        return x - ox, y - oy

    def move_to(x, y):
        nonlocal cursor
        if cursor is None or math.dist(cursor, (x, y)) > 2:
            vx, vy = at(x, y)
            blocks.append(new_node("mouse.move", {"x": vx, "y": vy, "space": space}))
        cursor = (x, y)

    for a in actions:
        if last_end is not None:
            gap = _round(a["t"] - last_end, o)
            if gap >= o.min_wait:
                blocks.append(new_node("flow.wait", {"seconds": gap}))
        last_end = a["end"]
        kind = a["a"]
        if kind == "path":
            pts, parts, prev = a["points"], [], None
            for x, y, t in pts:
                vx, vy = at(x, y)
                parts.append(f"{vx},{vy},{0 if prev is None else round(_round(t - prev, o) * 1000)}")
                prev = t
            blocks.append(new_node("mouse.path", {"path": "; ".join(parts), "space": space}))
            cursor = pts[-1][:2]
        elif kind == "click":
            move_to(a["x"], a["y"])
            plain = {("left", 1): "mouse.left_click", ("right", 1): "mouse.right_click",
                     ("left", 2): "mouse.double_click"}.get((a["b"], a["count"]))
            if plain:
                blocks.append(new_node(plain))
            else:
                vx, vy = at(a["x"], a["y"])
                blocks.append(new_node("mouse.click_at", {"x": vx, "y": vy, "space": space, "button": a["b"],
                                                          "clicks": a["count"]}))
        elif kind == "drag":
            (x1, y1), (x2, y2) = at(a["x1"], a["y1"]), at(a["x2"], a["y2"])
            blocks.append(new_node("mouse.drag", {"x1": x1, "y1": y1, "x2": x2, "y2": y2, "space": space,
                                                  "button": a["b"],
                                                  "duration": min(60.0, max(0.0, _round(a["end"] - a["t"], o)))}))
            cursor = (a["x2"], a["y2"])
        elif kind in ("mouse_down", "mouse_up"):
            move_to(a["x"], a["y"])
            blocks.append(new_node("mouse.down" if kind == "mouse_down" else "mouse.up", {"button": a["b"]}))
        elif kind == "scroll":
            move_to(a["x"], a["y"])
            blocks.append(new_node("mouse.scroll", {"amount": max(-100, min(100, a["amount"]))}))
        elif kind == "key":
            blocks.append(new_node("key.press", {"key": a["key"], "hold": min(60.0, max(0.0, _round(a["hold"], o)))}))
        elif kind == "combo":
            blocks.append(new_node("key.combo", {"keys": a["keys"]}))
        elif kind == "key_down":
            blocks.append(new_node("key.hold", {"key": a["key"]}))
        elif kind == "key_up":
            blocks.append(new_node("key.release", {"key": a["key"]}))

    duration = rec["events"][-1]["t"] if rec["events"] else 0.0
    comment = new_node("flow.comment", {"text": f"Recorded {rec.get('created', '')}: {len(rec['events'])} events, "
                                                f"{duration:.1f} s → {len(blocks)} blocks"})
    body = blocks
    if o.repeat > 1:
        loop = new_node("flow.repeat", {"times": int(o.repeat)})
        loop["body"] = blocks
        body = [loop]
    wf = Workflow()
    wf.add_script([new_node("flow.start"), comment, *body], (0, 0))
    stats = {"events": len(rec["events"]), "actions": len(actions), "blocks": len(blocks), "duration": duration}
    return wf, stats


def options_dict(o: Options) -> dict:
    return asdict(o)
