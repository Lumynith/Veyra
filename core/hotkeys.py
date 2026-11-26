"""System-wide hotkeys (RegisterHotKey) that work while Roblox has focus.

Only the exact registered combinations are taken from other apps; every other key reaches Roblox untouched.
The defaults use Ctrl+Alt+<key>, which Roblox does not use. Each press is published as "hotkey" on the bus.
"""
from __future__ import annotations

import ctypes
import logging
import threading
from ctypes import wintypes

from automation.input_controller import parse_key

log = logging.getLogger(__name__)
user32 = ctypes.WinDLL("user32", use_last_error=True)
kernel32 = ctypes.WinDLL("kernel32", use_last_error=True)
user32.RegisterHotKey.argtypes = (wintypes.HWND, ctypes.c_int, wintypes.UINT, wintypes.UINT)
user32.UnregisterHotKey.argtypes = (wintypes.HWND, ctypes.c_int)
user32.PostThreadMessageW.argtypes = (wintypes.DWORD, wintypes.UINT, wintypes.WPARAM, wintypes.LPARAM)
WM_HOTKEY, WM_QUIT = 0x0312, 0x0012
MOD = {"alt": 0x1, "ctrl": 0x2, "control": 0x2, "shift": 0x4, "win": 0x8, "meta": 0x8}
MOD_NOREPEAT = 0x4000

ACTIONS = {  # action -> (label, default)
    "start": ("Start macro", "Ctrl+Alt+F5"),
    "pause": ("Pause macro", "Ctrl+Alt+F6"),
    "resume": ("Resume macro", "Ctrl+Alt+F7"),
    "stop": ("Stop macro (emergency stop)", "Ctrl+Alt+F8"),
    "record": ("Start recording", "Ctrl+Alt+F9"),
    "record_stop": ("Stop recording", "Ctrl+Alt+F10"),
    "toggle_window": ("Show / hide the app", "Ctrl+Alt+H"),
}


def parse_hotkey(text: str) -> tuple[int, int]:
    """'Ctrl+Alt+F5' -> (modifier flags, virtual key). Needs exactly one non-modifier key."""
    parts = [p.strip().lower() for p in text.split("+") if p.strip()]
    mods, keys = 0, []
    for part in parts:
        if part in MOD:
            mods |= MOD[part]
        else:
            keys.append(part)
    if len(keys) != 1:
        raise ValueError(f"{text!r} needs exactly one key besides Ctrl/Alt/Shift/Win")
    return mods, parse_key(keys[0])


class GlobalHotkeys:
    def __init__(self, on_hotkey) -> None:
        self.on_hotkey = on_hotkey  # called on the hotkey thread with the action name: keep it quick
        self._thread: threading.Thread | None = None
        self._tid = 0
        self._ready = threading.Event()
        self.failed: dict[str, str] = {}  # action -> reason it could not be registered
        self.active: dict[str, str] = {}

    def apply(self, bindings: dict[str, str]) -> dict[str, str]:
        """(Re)register {action: "Ctrl+Alt+F5"}; blank disables. Returns the failures."""
        self.stop()
        self._bindings = {a: k for a, k in bindings.items() if a in ACTIONS and isinstance(k, str) and k.strip()}
        self.failed, self.active = {}, {}
        if not self._bindings:
            return self.failed
        self._ready.clear()
        self._thread = threading.Thread(target=self._loop, daemon=True, name="global-hotkeys")
        self._thread.start()
        self._ready.wait(2)
        for action, reason in self.failed.items():
            log.warning("Hotkey %s for %s not available: %s", self._bindings[action], ACTIONS[action][0], reason)
        return self.failed

    def stop(self) -> None:
        if self._thread and self._tid:
            user32.PostThreadMessageW(self._tid, WM_QUIT, 0, 0)
            self._thread.join(2)
        self._thread, self._tid = None, 0

    def _loop(self) -> None:
        self._tid = kernel32.GetCurrentThreadId()
        ids = {}
        seen = set()
        for i, (action, text) in enumerate(self._bindings.items(), 1):
            try:
                mods, vk = parse_hotkey(text)
            except ValueError as e:
                self.failed[action] = str(e)
                continue
            if (mods, vk) in seen:
                self.failed[action] = "already used by another action"
                continue
            if user32.RegisterHotKey(None, i, mods | MOD_NOREPEAT, vk):
                ids[i] = action
                seen.add((mods, vk))
                self.active[action] = text
            else:
                self.failed[action] = "already taken by another program"
        self._ready.set()
        msg = wintypes.MSG()
        try:
            while user32.GetMessageW(ctypes.byref(msg), None, 0, 0) > 0:
                if msg.message == WM_HOTKEY and msg.wParam in ids:
                    try:
                        self.on_hotkey(ids[msg.wParam])
                    except Exception:
                        log.exception("Hotkey handler failed")
        finally:
            for i in ids:
                user32.UnregisterHotKey(None, i)


def bindings_from(settings) -> dict[str, str]:
    stored = settings.get("hotkeys", {}) or {}
    return {a: str(stored.get(a, default)) for a, (_, default) in ACTIONS.items()}


def hotkey_vks(bindings: dict[str, str]) -> set[int]:
    """Main (non-modifier) keys of the bindings, so the recorder can leave them out."""
    out = set()
    for text in bindings.values():
        try:
            out.add(parse_hotkey(text)[1])
        except ValueError:
            pass
    return out
