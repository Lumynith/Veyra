"""One entry point for capture + detection, shared by blocks (worker thread) and the UI.

Handles frame reuse (no duplicate captures within a few ms), template caching (decode once,
prepare each scale/mode once), and scale adaptation to the current viewport size.
"""
from __future__ import annotations

import threading
import time
from collections import OrderedDict
from dataclasses import replace
from typing import Any, Callable

import numpy as np
import win32gui

from vision import image_capture
from vision.assets import AssetLibrary
from vision.image_detector import DetectOptions, Match, all_matches, best_match, prepare, resize, scales_for

Rect = tuple[int, int, int, int]


class RecognitionManager:
    def __init__(self, assets: AssetLibrary, capture: Callable[[Rect], np.ndarray] = image_capture.grab,
                 roblox_hwnd: Callable[[], int | None] = lambda: None,
                 settings: Callable[[str, Any], Any] | None = None) -> None:
        self.assets = assets
        self._grab = capture
        self._roblox_hwnd = roblox_hwnd
        self._settings = settings or (lambda key, default: default)
        self._lock = threading.Lock()
        self._frame: tuple[float, Rect, np.ndarray] | None = None
        self._templates: dict[tuple, np.ndarray] = {}
        self._prepared: OrderedDict[tuple, np.ndarray | None] = OrderedDict()
        self.stats = {"captures": 0, "reused": 0, "decoded": 0}

    def setting(self, key: str, default: Any) -> Any:
        return self._settings(f"vision.{key}", default)

    @property
    def min_interval(self) -> float:
        return float(self.setting("min_interval", 0.05))

    # -- capture ---------------------------------------------------------

    def capture(self, rect: Rect) -> np.ndarray:
        """Screen capture; an identical rect captured within frame_reuse_ms is served from memory."""
        reuse = float(self.setting("frame_reuse_ms", 40)) / 1000
        now = time.perf_counter()
        with self._lock:
            if self._frame and self._frame[1] == tuple(rect) and now - self._frame[0] <= reuse:
                self.stats["reused"] += 1
                return self._frame[2]
        img = self._grab(tuple(rect))
        img.setflags(write=False)  # shared between callers; nobody may modify it
        with self._lock:
            self._frame = (time.perf_counter(), tuple(rect), img)
            self.stats["captures"] += 1
        return img

    def viewport_rect(self) -> Rect:
        hwnd = self._roblox_hwnd()
        if not hwnd or not win32gui.IsWindow(hwnd):
            raise RuntimeError("No Roblox window is attached")
        x, y = win32gui.ClientToScreen(hwnd, (0, 0))
        _, _, w, h = win32gui.GetClientRect(hwnd)
        return x, y, w, h

    def capture_viewport(self) -> tuple[np.ndarray, Rect]:
        rect = self.viewport_rect()
        return self._grab(rect), rect

    def capture_screen(self) -> tuple[np.ndarray, Rect]:
        rect = image_capture.virtual_screen()
        return self._grab(rect), rect

    def capture_window(self, hwnd: int) -> np.ndarray:
        return image_capture.capture_window(hwnd)

    # -- templates -------------------------------------------------------

    def template(self, ref: str) -> tuple[np.ndarray, dict, tuple]:
        """Decoded template, its metadata, and a cache key that changes when the file changes."""
        path, meta = self.assets.resolve(ref)
        try:
            key = (str(path), path.stat().st_mtime_ns)
        except OSError:
            raise FileNotFoundError(f"Image file not found: {path}") from None
        cache = bool(self.setting("cache", True))
        with self._lock:
            img = self._templates.get(key) if cache else None
        if img is None:
            img = image_capture.read_image(path)
            img.setflags(write=False)
            with self._lock:
                self.stats["decoded"] += 1
                if cache:
                    self._templates[key] = img
        return img, meta, key

    def _prepared_templates(self, img: np.ndarray, key: tuple, opts: DetectOptions) -> list:
        out = []
        cache = bool(self.setting("cache", True))
        for s in scales_for(opts):
            ck = (key, s, opts.mode)
            with self._lock:
                hit = ck in self._prepared
                tpl = self._prepared.get(ck)
                if hit:
                    self._prepared.move_to_end(ck)
            if not hit:
                scaled = resize(img, s)
                tpl = None if scaled is None else prepare(scaled, opts.mode)
                if cache:
                    with self._lock:
                        self._prepared[ck] = tpl
                        while len(self._prepared) > 512:
                            self._prepared.popitem(last=False)
            out.append((s, tpl))
        return out

    def clear_cache(self) -> None:
        with self._lock:
            self._templates.clear()
            self._prepared.clear()
            self._frame = None

    # -- detection -------------------------------------------------------

    @staticmethod
    def base_scale(meta: dict, space_size: tuple[int, int] | None) -> float:
        """Roblox UI scales with the viewport height; rescale templates captured at another size."""
        ref = meta.get("reference_size")
        if not ref or not space_size or not ref[1] or not space_size[1]:
            return 1.0
        return space_size[1] / ref[1]

    def _setup(self, ref: str, opts: DetectOptions, adapt_to: tuple[int, int] | None):
        img, meta, key = self.template(ref)
        if adapt_to:
            opts = replace(opts, base_scale=self.base_scale(meta, adapt_to))
        return self._prepared_templates(img, key, opts), opts

    def locate(self, ref: str, rect: Rect, opts: DetectOptions, adapt_to: tuple[int, int] | None = None,
               should_stop: Callable[[], None] | None = None, frame: np.ndarray | None = None) -> Match | None:
        """Best match inside rect (coordinates relative to rect), whatever its score."""
        templates, opts = self._setup(ref, opts, adapt_to)
        hay = prepare(frame if frame is not None else self.capture(rect), opts.mode)
        return best_match(hay, templates, opts.method, opts.mode, should_stop)

    def locate_all(self, ref: str, rect: Rect, opts: DetectOptions, adapt_to: tuple[int, int] | None = None,
                   limit: int = 50, should_stop: Callable[[], None] | None = None) -> list[Match]:
        templates, opts = self._setup(ref, opts, adapt_to)
        hay = prepare(self.capture(rect), opts.mode)
        return all_matches(hay, templates, opts.confidence, opts.method, limit, should_stop)
