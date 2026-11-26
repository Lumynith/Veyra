"""Screen capture of physical-pixel screen rectangles, plus Unicode-safe image file I/O."""
from __future__ import annotations

from pathlib import Path

import cv2
import mss
import numpy as np

Rect = tuple[int, int, int, int]  # left, top, width, height (screen pixels)


def grab(rect: Rect) -> np.ndarray:
    """BGR image of the given screen rectangle."""
    left, top, width, height = rect
    if width <= 0 or height <= 0:
        raise ValueError(f"Empty capture area {rect}")
    with mss.mss() as sct:  # mss handles are per-thread, so open one per call
        shot = sct.grab({"left": left, "top": top, "width": width, "height": height})
    return cv2.cvtColor(np.asarray(shot), cv2.COLOR_BGRA2BGR)


def primary_screen() -> Rect:
    with mss.mss() as sct:
        m = sct.monitors[1]
    return m["left"], m["top"], m["width"], m["height"]


def virtual_screen() -> Rect:
    with mss.mss() as sct:
        m = sct.monitors[0]
    return m["left"], m["top"], m["width"], m["height"]


def read_image(path: str | Path) -> np.ndarray:
    data = np.fromfile(str(path), dtype=np.uint8)  # cv2.imread fails on non-ASCII paths
    img = cv2.imdecode(data, cv2.IMREAD_COLOR) if data.size else None
    if img is None:
        raise FileNotFoundError(f"Cannot read image {path}")
    return img


def save_image(img: np.ndarray, path: str | Path) -> Path:
    path = Path(path)
    path.parent.mkdir(parents=True, exist_ok=True)
    ok, buf = cv2.imencode(path.suffix or ".png", img)
    if not ok:
        raise ValueError(f"Cannot encode image as {path.suffix}")
    buf.tofile(str(path))
    return path


def capture_window(hwnd: int) -> np.ndarray:
    """BGR image of a window's client area via PrintWindow, so it works even when covered.

    Falls back to a screen grab when the window renders nothing through PrintWindow.
    """
    import ctypes

    import win32gui
    import win32ui

    _, _, w, h = win32gui.GetClientRect(hwnd)
    if w <= 0 or h <= 0:
        raise ValueError("Window has no visible area (minimized?)")
    hwnd_dc = win32gui.GetWindowDC(hwnd)
    mfc_dc = win32ui.CreateDCFromHandle(hwnd_dc)
    mem_dc = mfc_dc.CreateCompatibleDC()
    bmp = win32ui.CreateBitmap()
    try:
        bmp.CreateCompatibleBitmap(mfc_dc, w, h)
        mem_dc.SelectObject(bmp)
        # PW_CLIENTONLY | PW_RENDERFULLCONTENT: includes GPU-rendered content (DirectX) on Windows 8.1+
        ok = ctypes.windll.user32.PrintWindow(hwnd, mem_dc.GetSafeHdc(), 3)
        img = np.frombuffer(bmp.GetBitmapBits(True), np.uint8).reshape(h, w, 4)
        img = cv2.cvtColor(img, cv2.COLOR_BGRA2BGR)
    finally:
        win32gui.DeleteObject(bmp.GetHandle())
        mem_dc.DeleteDC()
        mfc_dc.DeleteDC()
        win32gui.ReleaseDC(hwnd, hwnd_dc)
    if not ok or not img.any():
        x, y = win32gui.ClientToScreen(hwnd, (0, 0))
        img = grab((x, y, w, h))
    return img
