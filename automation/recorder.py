"""Records global mouse and keyboard input with Windows low-level hooks.

Read-only observation of the standard Windows input stream (nothing is injected into or read from Roblox).
Skipped: input synthesised by software (including our own playback), clicks and keys aimed at this app's
own windows, and the control hotkeys (F9 pause/resume, F10 stop) so they work while Roblox has focus.
Hook callbacks must return within a few hundred ms, so they only append to a list.
"""
from __future__ import annotations

import ctypes
import logging
import os
import threading
import time
from ctypes import wintypes
from typing import Callable

from automation.recording import new_recording

log = logging.getLogger(__name__)
user32 = ctypes.WinDLL("user32", use_last_error=True)
kernel32 = ctypes.WinDLL("kernel32", use_last_error=True)

WH_KEYBOARD_LL, WH_MOUSE_LL, WM_QUIT = 13, 14, 0x0012
LLMHF_INJECTED, LLKHF_INJECTED = 0x01, 0x10
MOUSE_MESSAGES = {0x0200: ("move", ""), 0x0201: ("down", "left"), 0x0202: ("up", "left"),
                  0x0204: ("down", "right"), 0x0205: ("up", "right"), 0x0207: ("down", "middle"),
                  0x0208: ("up", "middle"), 0x020A: ("wheel", "")}
KEY_MESSAGES = {0x0100: "kdown", 0x0104: "kdown", 0x0101: "kup", 0x0105: "kup"}
VK_F9, VK_F10 = 0x78, 0x79
HOTKEYS = {VK_F9: "pause", VK_F10: "stop"}

LRESULT = ctypes.c_ssize_t
HOOKPROC = ctypes.WINFUNCTYPE(LRESULT, ctypes.c_int, wintypes.WPARAM, wintypes.LPARAM)


class MSLLHOOKSTRUCT(ctypes.Structure):
    _fields_ = [("pt", wintypes.POINT), ("mouseData", wintypes.DWORD), ("flags", wintypes.DWORD),
                ("time", wintypes.DWORD), ("dwExtraInfo", ctypes.c_size_t)]


class KBDLLHOOKSTRUCT(ctypes.Structure):
    _fields_ = [("vkCode", wintypes.DWORD), ("scanCode", wintypes.DWORD), ("flags", wintypes.DWORD),
                ("time", wintypes.DWORD), ("dwExtraInfo", ctypes.c_size_t)]


class GUITHREADINFO(ctypes.Structure):
    _fields_ = [("cbSize", wintypes.DWORD), ("flags", wintypes.DWORD), ("hwndActive", wintypes.HWND),
                ("hwndFocus", wintypes.HWND), ("hwndCapture", wintypes.HWND), ("hwndMenuOwner", wintypes.HWND),
                ("hwndMoveSize", wintypes.HWND), ("hwndCaret", wintypes.HWND), ("rcCaret", wintypes.RECT)]


user32.SetWindowsHookExW.argtypes = (ctypes.c_int, HOOKPROC, wintypes.HINSTANCE, wintypes.DWORD)
user32.SetWindowsHookExW.restype = wintypes.HHOOK
user32.CallNextHookEx.argtypes = (wintypes.HHOOK, ctypes.c_int, wintypes.WPARAM, wintypes.LPARAM)
user32.CallNextHookEx.restype = LRESULT
user32.UnhookWindowsHookEx.argtypes = (wintypes.HHOOK,)
user32.WindowFromPoint.argtypes = (wintypes.POINT,)
user32.WindowFromPoint.restype = wintypes.HWND
user32.GetWindowThreadProcessId.argtypes = (wintypes.HWND, ctypes.POINTER(wintypes.DWORD))
user32.GetGUIThreadInfo.argtypes = (wintypes.DWORD, ctypes.POINTER(GUITHREADINFO))
user32.GetForegroundWindow.restype = wintypes.HWND
user32.PostThreadMessageW.argtypes = (wintypes.DWORD, wintypes.UINT, wintypes.WPARAM, wintypes.LPARAM)
WNDENUMPROC = ctypes.WINFUNCTYPE(wintypes.BOOL, wintypes.HWND, wintypes.LPARAM)
user32.EnumWindows.argtypes = (WNDENUMPROC, wintypes.LPARAM)
user32.EnumChildWindows.argtypes = (wintypes.HWND, WNDENUMPROC, wintypes.LPARAM)
user32.GetWindowRect.argtypes = (wintypes.HWND, ctypes.POINTER(wintypes.RECT))
user32.IsWindowVisible.argtypes = (wintypes.HWND,)
user32.IsIconic.argtypes = (wintypes.HWND,)
user32.GetWindowLongW.argtypes = (wintypes.HWND, ctypes.c_int)
GWL_EXSTYLE, WS_EX_TRANSPARENT = -20, 0x20
kernel32.GetModuleHandleW.restype = wintypes.HMODULE


def _pid(hwnd) -> int:
    pid = wintypes.DWORD()
    user32.GetWindowThreadProcessId(hwnd, ctypes.byref(pid))
    return pid.value


def _contains(hwnd, x: int, y: int) -> bool:
    r = wintypes.RECT()
    return bool(user32.GetWindowRect(hwnd, ctypes.byref(r))) and r.left <= x < r.right and r.top <= y < r.bottom


def owner_pid_at(x: int, y: int) -> int:
    """Process of the window under a screen point, decided from window rectangles only.

    WindowFromPoint is not used: it hit-tests by sending WM_NCHITTEST, and when the owning GUI thread is busy
    Windows skips that window and reports the one underneath. A visible child window of another process
    (the docked Roblox window) inside the top window counts as that process."""
    found = []

    def top(hwnd, _):
        if (user32.IsWindowVisible(hwnd) and not user32.IsIconic(hwnd)
                and not user32.GetWindowLongW(hwnd, GWL_EXSTYLE) & WS_EX_TRANSPARENT and _contains(hwnd, x, y)):
            found.append(hwnd)
            return False  # EnumWindows goes front to back: the first hit is on top
        return True

    user32.EnumWindows(WNDENUMPROC(top), 0)
    if not found:
        return 0
    owner = _pid(found[0])
    foreign = []

    def child(hwnd, _):
        if user32.IsWindowVisible(hwnd) and _pid(hwnd) != owner and _contains(hwnd, x, y):
            foreign.append(hwnd)
            return False
        return True

    user32.EnumChildWindows(found[0], WNDENUMPROC(child), 0)
    return _pid(foreign[0]) if foreign else owner


class InputRecorder:
    def __init__(self, on_hotkey: Callable[[str], None] | None = None, exclude_pid: int | None = None,
                 include_injected: bool = False) -> None:
        self._injected_ok = include_injected  # tests only: lets SendInput stand in for a person
        self.on_hotkey = on_hotkey  # called on the hook thread: keep it quick (e.g. publish on the bus)
        self._own_pid = os.getpid() if exclude_pid is None else exclude_pid
        self.state = "idle"
        self.recording: dict | None = None
        self._events: list[dict] = []
        self._thread: threading.Thread | None = None
        self._tid = 0
        self._ready = threading.Event()
        self._error: str | None = None
        self._t0 = 0.0
        self._paused_at = 0.0
        self._paused_total = 0.0
        self._skip_up: set[int] = set()
        self.ignore_vks: set[int] = set()  # main keys of the app's global hotkeys: never part of a macro

    # -- control ------------------------------------------------------------------------------

    def start(self, origin: tuple[int, int] | None = None, size: tuple[int, int] | None = None) -> None:
        if self.state != "idle":
            raise RuntimeError("Already recording")
        self.recording = new_recording(origin, size)
        self._events = self.recording["events"]
        self._t0, self._paused_total = time.perf_counter(), 0.0
        self._ready.clear()
        self._error = None
        self.state = "recording"
        self._thread = threading.Thread(target=self._loop, daemon=True, name="input-recorder")
        self._thread.start()
        self._ready.wait(2)
        if self._error:
            self.state = "idle"
            raise OSError(self._error)
        log.info("Recording started (%s)", "viewport" if origin else "screen")

    def pause(self) -> None:
        if self.state == "recording":
            self._paused_at = time.perf_counter()
            self.state = "paused"

    def resume(self) -> None:
        if self.state == "paused":
            self._paused_total += time.perf_counter() - self._paused_at  # the pause leaves no gap
            self.state = "recording"

    def stop(self) -> dict | None:
        if self.state == "idle":
            return self.recording
        self.state = "idle"
        if self._tid:
            user32.PostThreadMessageW(self._tid, WM_QUIT, 0, 0)
        if self._thread:
            self._thread.join(2)
        log.info("Recording stopped: %d events", len(self._events))
        return self.recording

    @property
    def count(self) -> int:
        return len(self._events)

    @property
    def elapsed(self) -> float:
        if self.state == "idle":
            return self._events[-1]["t"] if self._events else 0.0
        now = self._paused_at if self.state == "paused" else time.perf_counter()
        return now - self._t0 - self._paused_total

    # -- hook thread ----------------------------------------------------------------------------

    def _loop(self) -> None:
        self._tid = kernel32.GetCurrentThreadId()
        mouse_proc, key_proc = HOOKPROC(self._on_mouse), HOOKPROC(self._on_key)  # keep references alive
        module = kernel32.GetModuleHandleW(None)
        hooks = [user32.SetWindowsHookExW(WH_MOUSE_LL, mouse_proc, module, 0),
                 user32.SetWindowsHookExW(WH_KEYBOARD_LL, key_proc, module, 0)]
        if not all(hooks):
            self._error = f"Could not install input hooks: {ctypes.WinError(ctypes.get_last_error())}"
            for h in hooks:
                if h:
                    user32.UnhookWindowsHookEx(h)
            self._ready.set()
            return
        self._ready.set()
        msg = wintypes.MSG()
        try:
            while user32.GetMessageW(ctypes.byref(msg), None, 0, 0) > 0:
                user32.TranslateMessage(ctypes.byref(msg))
                user32.DispatchMessageW(ctypes.byref(msg))
        finally:
            for h in hooks:
                user32.UnhookWindowsHookEx(h)
            self._tid = 0

    def _now(self) -> float:
        return round(time.perf_counter() - self._t0 - self._paused_total, 4)

    def _on_mouse(self, code, wparam, lparam):
        try:
            if code == 0 and self.state == "recording" and wparam in MOUSE_MESSAGES:
                info = MSLLHOOKSTRUCT.from_address(lparam)
                if self._injected_ok or not info.flags & LLMHF_INJECTED:
                    kind, button = MOUSE_MESSAGES[wparam]
                    x, y = info.pt.x, info.pt.y
                    # Clicks on this app (e.g. its Stop button) are not part of the macro. The embedded Roblox
                    # window belongs to another process, so clicks on it are kept.
                    if kind == "move" or owner_pid_at(x, y) != self._own_pid:
                        e = {"t": self._now(), "k": kind, "x": x, "y": y}
                        if button:
                            e["b"] = button
                        if kind == "wheel":
                            e["d"] = ctypes.c_short(info.mouseData >> 16).value / 120
                        self._events.append(e)
        except Exception:  # never let an error escape into the Windows input chain
            log.debug("mouse hook error", exc_info=True)
        return user32.CallNextHookEx(None, code, wparam, lparam)

    def _on_key(self, code, wparam, lparam):
        try:
            if code == 0 and self.state != "idle" and wparam in KEY_MESSAGES:
                info = KBDLLHOOKSTRUCT.from_address(lparam)
                kind, vk = KEY_MESSAGES[wparam], info.vkCode
                if info.flags & LLKHF_INJECTED and not self._injected_ok:
                    pass
                elif vk in HOTKEYS:
                    if kind == "kdown" and vk not in self._skip_up and self.on_hotkey:
                        self.on_hotkey(HOTKEYS[vk])
                    (self._skip_up.add if kind == "kdown" else self._skip_up.discard)(vk)
                elif vk in self.ignore_vks:
                    pass
                elif self.state == "recording" and not self._keyboard_is_ours():
                    self._events.append({"t": self._now(), "k": kind, "vk": vk})
        except Exception:
            log.debug("keyboard hook error", exc_info=True)
        return user32.CallNextHookEx(None, code, wparam, lparam)

    def _keyboard_is_ours(self) -> bool:
        fg = user32.GetForegroundWindow()
        info = GUITHREADINFO(cbSize=ctypes.sizeof(GUITHREADINFO))
        tid = user32.GetWindowThreadProcessId(fg, None)
        focus = info.hwndFocus if user32.GetGUIThreadInfo(tid, ctypes.byref(info)) and info.hwndFocus else fg
        return _pid(focus) == self._own_pid
