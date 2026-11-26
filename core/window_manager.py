"""Find Roblox windows and dock one into the viewport.

Two modes:
  anchor - (default) Roblox stays a borderless top-level window, owned by our main window and kept aligned
           over the viewport. It can be the foreground window, which Roblox needs before it takes input.
  embed  - Roblox becomes a child window of the viewport host (SetParent). Looks the same, but Roblox ignores
           keyboard input while docked like this, so macros' key presses do nothing.
All coordinates are physical pixels (Qt 6 makes the process per-monitor DPI aware).
"""
from __future__ import annotations

import ctypes
import logging
import os
import threading
from ctypes import wintypes
from dataclasses import dataclass

import pywintypes
import win32con
import win32gui
import win32process
from PySide6.QtCore import QObject, QThread, QTimer, Signal

log = logging.getLogger(__name__)

EMBED, ANCHOR = "embed", "anchor"
GA_ROOT = 2
GWLP_HWNDPARENT = -8
PROCESS_QUERY_LIMITED_INFORMATION = 0x1000
_FRAME_BITS = (win32con.WS_POPUP | win32con.WS_CAPTION | win32con.WS_THICKFRAME
               | win32con.WS_SYSMENU | win32con.WS_MINIMIZEBOX | win32con.WS_MAXIMIZEBOX)

_k32 = ctypes.WinDLL("kernel32", use_last_error=True)
_k32.OpenProcess.restype = wintypes.HANDLE
_k32.OpenProcess.argtypes = (wintypes.DWORD, wintypes.BOOL, wintypes.DWORD)
_k32.QueryFullProcessImageNameW.argtypes = (wintypes.HANDLE, wintypes.DWORD, wintypes.LPWSTR,
                                             ctypes.POINTER(wintypes.DWORD))
_k32.CloseHandle.argtypes = (wintypes.HANDLE,)
_u32 = ctypes.WinDLL("user32")
_u32.IsHungAppWindow.argtypes = (wintypes.HWND,)


@dataclass(frozen=True)
class WindowInfo:
    hwnd: int
    title: str
    pid: int
    exe: str

    def label(self) -> str:
        return f"{self.title or '(untitled)'} — PID {self.pid}"


def process_exe(pid: int) -> str:
    handle = _k32.OpenProcess(PROCESS_QUERY_LIMITED_INFORMATION, False, pid)
    if not handle:
        return ""
    try:
        buf = ctypes.create_unicode_buffer(32768)
        size = wintypes.DWORD(len(buf))
        ok = _k32.QueryFullProcessImageNameW(handle, 0, buf, ctypes.byref(size))
        return os.path.basename(buf.value) if ok else ""
    finally:
        _k32.CloseHandle(handle)


def list_windows(process_names: list[str]) -> list[WindowInfo]:
    """Visible, unowned top-level windows belonging to the given executables."""
    names = {n.lower() for n in process_names}
    found: list[WindowInfo] = []

    def visit(hwnd, _):
        if win32gui.IsWindowVisible(hwnd) and not win32gui.GetWindow(hwnd, win32con.GW_OWNER):
            _, pid = win32process.GetWindowThreadProcessId(hwnd)
            exe = process_exe(pid)
            if exe.lower() in names:
                found.append(WindowInfo(hwnd, win32gui.GetWindowText(hwnd), pid, exe))
        return True

    win32gui.EnumWindows(visit, None)
    return found


@dataclass
class _Original:
    style: int
    owner: int
    rect: tuple[int, int, int, int]


def _frame_changed(hwnd: int) -> None:
    """Make Windows apply a style change to the frame now, so client-area sizes are measured correctly."""
    win32gui.SetWindowPos(hwnd, 0, 0, 0, 0, 0, win32con.SWP_NOMOVE | win32con.SWP_NOSIZE | win32con.SWP_NOZORDER
                          | win32con.SWP_NOACTIVATE | win32con.SWP_FRAMECHANGED)


class WindowManager(QObject):
    """Owns the attached Roblox window. Publishes roblox.* events on the bus."""

    _focus_requested = Signal(object)  # from the engine thread: {"ok": bool, "done": Event}

    def __init__(self, settings, bus, parent=None) -> None:
        super().__init__(parent)
        self._focus_requested.connect(self._focus_reply)
        self._settings = settings
        self._bus = bus
        self.hwnd: int | None = None
        self.mode: str | None = None
        self._host: int | None = None
        self._orig: _Original | None = None
        self._hung = False
        self._geometry = None
        self.fill_host = False  # True: size to the whole host (maximized viewport) instead of the configured size
        self._timer = QTimer(self)
        self._timer.timeout.connect(self._poll)
        self._timer.start(int(settings.get("viewport.poll_interval_ms", 500)))

    @property
    def attached(self) -> bool:
        return self.hwnd is not None

    def refresh(self) -> list[WindowInfo]:
        windows = list_windows(self._settings.get("roblox.process_names", []))
        self._bus.publish("roblox.windows", windows)
        return windows

    def attach(self, hwnd: int, host: int, mode: str = EMBED) -> str:
        """Dock hwnd into the host window. Returns the mode actually used."""
        if not win32gui.IsWindow(hwnd):
            raise ValueError("That window no longer exists")
        self.detach()
        if win32gui.IsIconic(hwnd) or _u32.IsZoomed(hwnd):
            win32gui.ShowWindow(hwnd, win32con.SW_RESTORE)
        self.hwnd, self._host = hwnd, host
        self._orig = _Original(
            style=win32gui.GetWindowLong(hwnd, win32con.GWL_STYLE),
            owner=win32gui.GetWindow(hwnd, win32con.GW_OWNER),
            rect=win32gui.GetWindowRect(hwnd),
        )
        modes = [mode] + ([ANCHOR] if mode == EMBED and self._settings.get("viewport.auto_fallback", True) else [])
        error: Exception | None = None
        for m in modes:
            try:
                self._embed() if m == EMBED else self._anchor()
                break
            except (pywintypes.error, OSError) as e:
                error = e
                log.warning("Docking in %s mode failed: %s", m, e)
                self._restore()
        else:
            self._clear()
            raise RuntimeError(f"Could not dock window: {error}")
        log.info("Attached window %#x in %s mode", hwnd, self.mode)
        self._bus.publish("roblox.attached", {"hwnd": hwnd, "mode": self.mode})
        return self.mode

    def detach(self) -> None:
        if self.hwnd is None:
            return
        hwnd = self.hwnd
        self._restore()
        self._clear()
        log.info("Detached window %#x", hwnd)
        self._bus.publish("roblox.detached", hwnd)

    def sync(self, force: bool = False) -> None:
        """Re-align and resize the attached window to the viewport."""
        hwnd, host = self.hwnd, self._host
        if hwnd is None or host is None or self._hung:
            return
        try:
            if win32gui.IsIconic(win32gui.GetAncestor(host, GA_ROOT)):
                return  # minimized: host coordinates are meaningless
            ox, oy = win32gui.ClientToScreen(host, (0, 0))
            x, y, w, h = self._target(hwnd, ox, oy)
            if force or win32gui.GetWindowRect(hwnd) != (x, y, x + w, y + h):
                if self.mode == EMBED:  # child windows are positioned in parent-client coordinates
                    x, y = x - ox, y - oy
                flags = win32con.SWP_NOZORDER | win32con.SWP_NOACTIVATE
                if force:
                    flags |= win32con.SWP_FRAMECHANGED | win32con.SWP_SHOWWINDOW
                win32gui.SetWindowPos(hwnd, 0, x, y, w, h, flags)
            self._report(hwnd)
        except pywintypes.error as e:
            log.debug("sync failed: %s", e)

    # -- keyboard focus ------------------------------------------------------

    def focus(self) -> bool:
        """Bring Roblox to the front with keyboard focus. GUI thread only; True when Roblox is in front.

        Docked (child) or anchored (owned), Roblox shares our GUI thread's input queue (Windows links them
        across processes), so SetFocus works from here. AttachThreadInput is never used:
        undoing it can also undo that link and leave Roblox deaf to all input."""
        hwnd = self.hwnd
        if hwnd is None or not win32gui.IsWindow(hwnd):
            return False
        root = win32gui.GetAncestor(hwnd, GA_ROOT)
        try:
            if win32gui.IsIconic(root):
                win32gui.ShowWindow(root, win32con.SW_RESTORE)
            win32gui.SetForegroundWindow(root)
            # Owned (anchor) and child (embed) windows share our input queue, so focus can stay on our side
            # unless it is handed over explicitly.
            win32gui.SetFocus(hwnd)
        except pywintypes.error as e:
            log.debug("focus failed: %s", e)
        return win32gui.GetForegroundWindow() == root

    def focus_from_any_thread(self, timeout: float = 2.0) -> bool:
        """focus(), run on the GUI thread; for blocks on the engine thread."""
        if QThread.currentThread() is self.thread():
            return self.focus()
        reply = {"ok": False, "done": threading.Event()}
        self._focus_requested.emit(reply)  # queued to the GUI thread
        reply["done"].wait(timeout)
        return reply["ok"]

    def _focus_reply(self, reply: dict) -> None:
        try:
            reply["ok"] = self.focus()
        finally:
            reply["done"].set()

    # -- internals -------------------------------------------------------

    def _target(self, hwnd: int, ox: int, oy: int) -> tuple[int, int, int, int]:
        if self.fill_host:
            _, _, w, h = win32gui.GetClientRect(self._host)
        else:
            w = int(self._settings.get("viewport.width", 841))
            h = int(self._settings.get("viewport.height", 661))
        if self._settings.get("viewport.size_mode") == "client":
            # Grow the outer rect by the measured frame so the rendering area is exactly w x h.
            left, top, right, bottom = win32gui.GetWindowRect(hwnd)
            cx, cy = win32gui.ClientToScreen(hwnd, (0, 0))
            _, _, cw, ch = win32gui.GetClientRect(hwnd)
            return ox - (cx - left), oy - (cy - top), w + (right - left - cw), h + (bottom - top - ch)
        return ox, oy, w, h

    def _embed(self) -> None:
        hwnd = self.hwnd
        win32gui.SetWindowLong(hwnd, win32con.GWL_STYLE, (self._orig.style & ~_FRAME_BITS) | win32con.WS_CHILD)
        win32gui.SetParent(hwnd, self._host)
        _frame_changed(hwnd)
        if win32gui.GetParent(hwnd) != self._host:
            raise OSError("SetParent did not take effect")
        self.mode = EMBED
        self.sync(force=True)

    def _anchor(self) -> None:
        # Borderless so it looks docked; owned, so it stays above our window and minimizes with it.
        win32gui.SetWindowLong(self.hwnd, win32con.GWL_STYLE, (self._orig.style & ~_FRAME_BITS) | win32con.WS_POPUP)
        win32gui.SetWindowLong(self.hwnd, GWLP_HWNDPARENT, win32gui.GetAncestor(self._host, GA_ROOT))
        _frame_changed(self.hwnd)  # before sync measures the frame for "client" sizing
        self.mode = ANCHOR
        self.sync(force=True)

    def _restore(self) -> None:
        hwnd, orig = self.hwnd, self._orig
        if hwnd is None or orig is None or not win32gui.IsWindow(hwnd):
            return
        try:
            if win32gui.GetWindowLong(hwnd, win32con.GWL_STYLE) & win32con.WS_CHILD:
                win32gui.SetParent(hwnd, 0)
            win32gui.SetWindowLong(hwnd, win32con.GWL_STYLE, orig.style)
            win32gui.SetWindowLong(hwnd, GWLP_HWNDPARENT, orig.owner)
            left, top, right, bottom = orig.rect
            win32gui.SetWindowPos(hwnd, 0, left, top, right - left, bottom - top,
                                  win32con.SWP_NOZORDER | win32con.SWP_NOACTIVATE
                                  | win32con.SWP_FRAMECHANGED | win32con.SWP_SHOWWINDOW)
        except pywintypes.error as e:
            log.error("Could not fully restore window %#x: %s", hwnd, e)

    def _clear(self) -> None:
        self.hwnd = self.mode = self._host = self._orig = self._geometry = None
        self._hung = False

    def _report(self, hwnd: int) -> None:
        left, top, right, bottom = win32gui.GetWindowRect(hwnd)
        _, _, cw, ch = win32gui.GetClientRect(hwnd)
        geometry = {"outer": (right - left, bottom - top), "client": (cw, ch)}
        if geometry != self._geometry:
            self._geometry = geometry
            self._bus.publish("roblox.geometry", geometry)

    def _poll(self) -> None:
        if self.hwnd is None:
            return
        if not win32gui.IsWindow(self.hwnd):
            hwnd = self.hwnd
            self._clear()
            log.warning("Roblox window %#x closed", hwnd)
            self._bus.publish("roblox.closed", hwnd)
            return
        hung = bool(_u32.IsHungAppWindow(self.hwnd))
        if hung != self._hung:
            self._hung = hung
            (log.warning if hung else log.info)("Roblox %s", "is not responding" if hung else "is responding again")
            self._bus.publish("roblox.hung", hung)
        self.sync()
