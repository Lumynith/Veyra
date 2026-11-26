"""OpenCV template matching: modes, methods, multi-scale search, coarse-to-fine, and non-maximum suppression.

Inputs to best_match / all_matches are already prepared (see prepare) so callers can cache templates.
"""
from __future__ import annotations

from dataclasses import dataclass
from typing import Callable

import cv2
import numpy as np

METHODS = {"ccoeff": cv2.TM_CCOEFF_NORMED, "ccorr": cv2.TM_CCORR_NORMED, "sqdiff": cv2.TM_SQDIFF_NORMED}
MODES = ("color", "grayscale", "edges")
MIN_SIDE = 6           # templates scaled below this are skipped
PYRAMID_AREA = 250_000  # haystacks larger than this are searched at half size first


@dataclass(frozen=True)
class DetectOptions:
    confidence: float = 0.85
    mode: str = "color"
    method: str = "ccoeff"
    multiscale: bool = True
    scale_min: float = 0.85
    scale_max: float = 1.15
    scale_steps: int = 0      # 0 = automatic (about one step per 5 %)
    base_scale: float = 1.0   # e.g. current viewport height / height at capture time


@dataclass(frozen=True)
class Match:
    x: int        # top-left, relative to the searched image
    y: int
    w: int
    h: int
    score: float  # 0..1, higher is better for every method
    scale: float

    @property
    def center(self) -> tuple[int, int]:
        return self.x + self.w // 2, self.y + self.h // 2


def prepare(img: np.ndarray, mode: str) -> np.ndarray:
    if mode == "color":
        return img
    gray = cv2.cvtColor(img, cv2.COLOR_BGR2GRAY) if img.ndim == 3 else img
    if mode == "grayscale":
        return gray
    if mode == "edges":
        edges = cv2.Canny(gray, 60, 160)
        return cv2.dilate(edges, np.ones((3, 3), np.uint8))  # tolerate 1 px misalignment
    raise ValueError(f"Unknown detection mode {mode!r}")


def scales_for(opts: DetectOptions) -> list[float]:
    if not opts.multiscale:
        return [opts.base_scale]
    lo, hi = sorted((opts.scale_min, opts.scale_max))
    steps = opts.scale_steps or int(np.clip(round((hi - lo) / 0.05) + 1, 3, 15))
    values = np.linspace(lo, hi, steps) * opts.base_scale
    return sorted({round(float(v), 3) for v in values}, key=lambda s: abs(s - opts.base_scale))  # likeliest first


def resize(img: np.ndarray, scale: float) -> np.ndarray | None:
    if abs(scale - 1.0) < 1e-3:
        return img
    w, h = round(img.shape[1] * scale), round(img.shape[0] * scale)
    if min(w, h) < MIN_SIDE:
        return None
    return cv2.resize(img, (w, h), interpolation=cv2.INTER_AREA if scale < 1 else cv2.INTER_CUBIC)


def _scores(hay: np.ndarray, tpl: np.ndarray, method: str) -> np.ndarray:
    res = cv2.matchTemplate(hay, tpl, METHODS[method])
    if method == "sqdiff":
        res = 1.0 - res
    return np.nan_to_num(res, nan=0.0, posinf=0.0, neginf=0.0)  # flat templates give NaN


def _fits(hay: np.ndarray, tpl: np.ndarray) -> bool:
    return tpl.shape[0] <= hay.shape[0] and tpl.shape[1] <= hay.shape[1]


def _peaks(res: np.ndarray, count: int, w: int, h: int) -> list[tuple[float, int, int]]:
    res = res.copy()
    out = []
    for _ in range(count):
        _, score, _, (x, y) = cv2.minMaxLoc(res)
        out.append((float(score), x, y))
        res[max(0, y - h // 2):y + h // 2 + 1, max(0, x - w // 2):x + w // 2 + 1] = -1.0
    return out


def _best_single(hay: np.ndarray, tpl: np.ndarray, method: str, mode: str) -> tuple[float, int, int]:
    th, tw = tpl.shape[:2]
    big = hay.shape[0] * hay.shape[1] > PYRAMID_AREA and min(th, tw) >= 32 and mode != "edges"
    if not big:
        _, score, _, (x, y) = cv2.minMaxLoc(_scores(hay, tpl, method))
        return float(score), x, y
    # Coarse-to-fine: find candidates at half size, confirm each at full size in a small window.
    small_hay = cv2.resize(hay, None, fx=0.5, fy=0.5, interpolation=cv2.INTER_AREA)
    small_tpl = cv2.resize(tpl, None, fx=0.5, fy=0.5, interpolation=cv2.INTER_AREA)
    best = (-1.0, 0, 0)
    for _, cx, cy in _peaks(_scores(small_hay, small_tpl, method), 3, small_tpl.shape[1], small_tpl.shape[0]):
        x0, y0 = max(0, cx * 2 - 6), max(0, cy * 2 - 6)
        roi = hay[y0:y0 + th + 12, x0:x0 + tw + 12]
        if not _fits(roi, tpl):
            continue
        _, score, _, (x, y) = cv2.minMaxLoc(_scores(roi, tpl, method))
        if score > best[0]:
            best = (float(score), x0 + x, y0 + y)
    return best


def best_match(hay: np.ndarray, templates: list[tuple[float, np.ndarray]], method: str = "ccoeff",
               mode: str = "color", should_stop: Callable[[], None] | None = None,
               good_enough: float = 0.995) -> Match | None:
    """Best match over all (scale, template) pairs; None if no template fits inside hay."""
    best: Match | None = None
    for scale, tpl in templates:
        if should_stop:
            should_stop()  # raises when the workflow is stopped
        if tpl is None or not _fits(hay, tpl):
            continue
        score, x, y = _best_single(hay, tpl, method, mode)
        if best is None or score > best.score:
            best = Match(x, y, tpl.shape[1], tpl.shape[0], round(score, 4), scale)
            if score >= good_enough:
                break  # can't meaningfully improve; skip remaining scales
    return best


def all_matches(hay: np.ndarray, templates: list[tuple[float, np.ndarray]], confidence: float,
                method: str = "ccoeff", limit: int = 50,
                should_stop: Callable[[], None] | None = None) -> list[Match]:
    """Every distinct match at or above confidence, across scales, best first."""
    found: list[Match] = []
    for scale, tpl in templates:
        if should_stop:
            should_stop()
        if tpl is None or not _fits(hay, tpl):
            continue
        res = _scores(hay, tpl, method)
        th, tw = tpl.shape[:2]
        while len(found) < limit * 3:
            _, score, _, (x, y) = cv2.minMaxLoc(res)
            if score < confidence:
                break
            found.append(Match(x, y, tw, th, round(float(score), 4), scale))
            res[max(0, y - th // 2):y + th // 2 + 1, max(0, x - tw // 2):x + tw // 2 + 1] = -1.0
    return _nms(found, limit)


def _nms(matches: list[Match], limit: int, overlap: float = 0.3) -> list[Match]:
    kept: list[Match] = []
    for m in sorted(matches, key=lambda m: -m.score):
        if all(_iou(m, k) <= overlap for k in kept):
            kept.append(m)
            if len(kept) >= limit:
                break
    return kept


def _iou(a: Match, b: Match) -> float:
    ix = max(0, min(a.x + a.w, b.x + b.w) - max(a.x, b.x))
    iy = max(0, min(a.y + a.h, b.y + b.h) - max(a.y, b.y))
    inter = ix * iy
    return inter / float(a.w * a.h + b.w * b.h - inter)


def templates_for(tpl: np.ndarray, opts: DetectOptions) -> list[tuple[float, np.ndarray | None]]:
    """Resize first, then prepare: edges of a resized image are cleaner than resized edges."""
    return [(s, None if (r := resize(tpl, s)) is None else prepare(r, opts.mode)) for s in scales_for(opts)]


def find(hay: np.ndarray, tpl: np.ndarray, opts: DetectOptions = DetectOptions()) -> Match | None:
    """Convenience: prepare and search without caching."""
    return best_match(prepare(hay, opts.mode), templates_for(tpl, opts), opts.method, opts.mode)


def find_all(hay: np.ndarray, tpl: np.ndarray, opts: DetectOptions = DetectOptions(), limit: int = 50) -> list[Match]:
    return all_matches(prepare(hay, opts.mode), templates_for(tpl, opts), opts.confidence, opts.method, limit)


def similarity(a: np.ndarray, b: np.ndarray) -> float:
    """0..1 similarity of two images (b is resized to a)."""
    if b.shape[:2] != a.shape[:2]:
        b = cv2.resize(b, (a.shape[1], a.shape[0]), interpolation=cv2.INTER_AREA)
    if a.std() == 0 or b.std() == 0:  # correlation is undefined for flat images
        return 1.0 if np.allclose(a.mean(), b.mean(), atol=2) else 0.0
    return max(0.0, float(cv2.matchTemplate(a, b, cv2.TM_CCOEFF_NORMED)[0, 0]))
