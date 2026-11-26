"""Mouse and keyboard output through a replaceable backend (default: Win32 SendInput)."""
from __future__ import annotations

import ctypes
import logging
import threading
from ctypes import wintypes

log = logging.getLogger(__name__)

_VK = {
    "enter": 0x0D, "return": 0x0D, "esc": 0x1B, "escape": 0x1B, "space": 0x20, "tab": 0x09,
    "backspace": 0x08, "delete": 0x2E, "del": 0x2E, "insert": 0x2D, "home": 0x24, "end": 0x23,
    "pageup": 0x21, "pgup": 0x21, "pagedown": 0x22, "pgdn": 0x22,
    "up": 0x26, "down": 0x28, "left": 0x25, "right": 0x27,
    "shift": 0x10, "lshift": 0xA0, "rshift": 0xA1, "ctrl": 0x11, "control": 0x11, "lctrl": 0xA2,
    "rctrl": 0xA3, "alt": 0x12, "lalt": 0xA4, "ralt": 0xA5, "win": 0x5B, "capslock": 0x14,
    "printscreen": 0x2C, "numlock": 0x90, "scrolllock": 0x91, "pause": 0x13,
}
_VK.update({f"f{i}": 0x6F + i for i in range(1, 25)})
_VK.update({f"num{i}": 0x60 + i for i in range(10)})
_EXTENDED = {0x21, 0x22, 0x23, 0x24, 0x25, 0x26, 0x27, 0x28, 0x2D, 0x2E, 0x5B, 0xA3, 0xA5, 0x90, 0x2C}
KEY_NAMES = sorted(_VK)
BUTTONS = ("left", "right", "middle")

_user32 = ctypes.WinDLL("user32", use_last_error=True)


def parse_key(name: str) -> int:
    """'a', 'F5', 'enter', 'ctrl', ';' -> virtual-key code."""
    key = name.strip().lower()
    if key in _VK:
        return _VK[key]
    if key.startswith("vk") and len(key) > 2:  # raw code from a recording, e.g. vk0xbb
        try:
            return int(key[2:], 16)
        except ValueError:
            pass
    if len(key) == 1:
        if key.isalnum():
            return ord(key.upper())
        scan = _user32.VkKeyScanW(ord(key))
        if scan != -1 and scan & 0xFF != 0xFF:
            return scan & 0xFF
    raise ValueError(f"Unknown key {name!r}")


_NAME_OF = {vk: name for name, vk in sorted(_VK.items(), key=lambda kv: len(kv[0]), reverse=True)}
_NAME_OF.update({0xA0: "shift", 0xA1: "shift", 0xA2: "ctrl", 0xA3: "ctrl", 0xA4: "alt", 0xA5: "alt", 0x5C: "win",
                 0x0D: "enter", 0x1B: "esc", 0x2E: "delete", 0x21: "pageup", 0x22: "pagedown"})
MODIFIERS = {0x10, 0x11, 0x12, 0x5B, 0x5C, 0xA0, 0xA1, 0xA2, 0xA3, 0xA4, 0xA5}


def key_name(vk: int) -> str:
    """Inverse of parse_key: a name parse_key accepts (left/right modifiers become plain shift/ctrl/alt)."""
    if vk in _NAME_OF:
        return _NAME_OF[vk]
    if 0x30 <= vk <= 0x39 or 0x41 <= vk <= 0x5A:
        return chr(vk).lower()
    ch = _user32.MapVirtualKeyW(vk, 2) & 0xFFFF  # MAPVK_VK_TO_CHAR, for ; , . / etc. on this keyboard layout
    if ch and chr(ch).isprintable() and not chr(ch).isspace() and parse_key(chr(ch).lower()) == vk:
        return chr(ch).lower()
    return f"vk{vk:#04x}"


def parse_combo(text: str) -> list[int]:
    parts = [p for p in text.replace(" ", "").split("+") if p]
    if not parts:
        raise ValueError("Empty key combination")
    return [parse_key(p) for p in parts]


# -- SendInput structures -------------------------------------------------

class _MOUSEINPUT(ctypes.Structure):
    _fields_ = [("dx", wintypes.LONG), ("dy", wintypes.LONG), ("mouseData", wintypes.DWORD),
                ("dwFlags", wintypes.DWORD), ("time", wintypes.DWORD), ("dwExtraInfo", ctypes.c_size_t)]


class _KEYBDINPUT(ctypes.Structure):
    _fields_ = [("wVk", wintypes.WORD), ("wScan", wintypes.WORD), ("dwFlags", wintypes.DWORD),
                ("time", wintypes.DWORD), ("dwExtraInfo", ctypes.c_size_t)]


class _HARDWAREINPUT(ctypes.Structure):
    _fields_ = [("uMsg", wintypes.DWORD), ("wParamL", wintypes.WORD), ("wParamH", wintypes.WORD)]


class _INPUTUNION(ctypes.Union):
    _fields_ = [("mi", _MOUSEINPUT), ("ki", _KEYBDINPUT), ("hi", _HARDWAREINPUT)]


class _INPUT(ctypes.Structure):
    _fields_ = [("type", wintypes.DWORD), ("u", _INPUTUNION)]


_user32.SendInput.argtypes = (wintypes.UINT, ctypes.POINTER(_INPUT), ctypes.c_int)
_user32.MapVirtualKeyW.argtypes = (wintypes.UINT, wintypes.UINT)

MOUSEEVENTF_MOVE, MOUSEEVENTF_ABSOLUTE, MOUSEEVENTF_VIRTUALDESK = 0x0001, 0x8000, 0x4000
MOUSEEVENTF_WHEEL = 0x0800
_BUTTON_FLAGS = {"left": (0x0002, 0x0004), "right": (0x0008, 0x0010), "middle": (0x0020, 0x0040)}
KEYEVENTF_EXTENDEDKEY, KEYEVENTF_KEYUP, KEYEVENTF_UNICODE, KEYEVENTF_SCANCODE = 0x1, 0x2, 0x4, 0x8


class SendInputBackend:
    """Real input. Keys are sent as scan codes, which games such as Roblox read."""

    def _send(self, *inputs: _INPUT) -> None:
        arr = (_INPUT * len(inputs))(*inputs)
        if _user32.SendInput(len(inputs), arr, ctypes.sizeof(_INPUT)) != len(inputs):
            raise OSError(f"SendInput was blocked (error {ctypes.get_last_error()})")

    @staticmethod
    def _mouse(flags: int, dx: int = 0, dy: int = 0, data: int = 0) -> _INPUT:
        return _INPUT(type=0, u=_INPUTUNION(mi=_MOUSEINPUT(dx, dy, ctypes.c_ulong(data).value, flags, 0, 0)))

    def move(self, x: int, y: int) -> None:
        vx, vy = _user32.GetSystemMetrics(76), _user32.GetSystemMetrics(77)
        vw, vh = _user32.GetSystemMetrics(78), _user32.GetSystemMetrics(79)
        nx = round((x - vx) * 65535 / max(vw - 1, 1))
        ny = round((y - vy) * 65535 / max(vh - 1, 1))
        self._send(self._mouse(MOUSEEVENTF_MOVE | MOUSEEVENTF_ABSOLUTE | MOUSEEVENTF_VIRTUALDESK, nx, ny))

    def button(self, name: str, down: bool) -> None:
        self._send(self._mouse(_BUTTON_FLAGS[name][0 if down else 1]))

    def wheel(self, notches: int) -> None:
        self._send(self._mouse(MOUSEEVENTF_WHEEL, data=120 * notches))

    def key(self, vk: int, down: bool) -> None:
        scan = _user32.MapVirtualKeyW(vk, 0)
        flags = KEYEVENTF_SCANCODE | (KEYEVENTF_EXTENDEDKEY if vk in _EXTENDED else 0) \
            | (0 if down else KEYEVENTF_KEYUP)
        self._send(_INPUT(type=1, u=_INPUTUNION(ki=_KEYBDINPUT(vk, scan, flags, 0, 0))))

    def char(self, ch: str) -> None:
        units = ch.encode("utf-16-le")
        events = []
        for i in range(0, len(units), 2):  # surrogate pairs need one event per UTF-16 unit
            code = int.from_bytes(units[i:i + 2], "little")
            for up in (0, KEYEVENTF_KEYUP):
                events.append(_INPUT(type=1, u=_INPUTUNION(ki=_KEYBDINPUT(0, code, KEYEVENTF_UNICODE | up, 0, 0))))
        self._send(*events)

    def cursor(self) -> tuple[int, int]:
        pt = wintypes.POINT()
        _user32.GetCursorPos(ctypes.byref(pt))
        return pt.x, pt.y

    def key_is_down(self, vk: int) -> bool:
        return bool(_user32.GetAsyncKeyState(vk) & 0x8000)


class StopRequested(Exception):
    """The user pressed Stop (or the emergency stop): automation must end now."""


class InputController:
    """Tracks held keys and buttons so a stopped macro never leaves them pressed.

    cancel() (called from any thread, e.g. the emergency stop) releases everything and refuses further
    output until reset(), even while the automation thread is still busy inside OCR or image search."""

    def __init__(self, backend=None) -> None:
        self.backend = backend or SendInputBackend()
        self._held_keys: set[int] = set()
        self._held_buttons: set[str] = set()
        self._lock = threading.RLock()  # a press can't slip in between cancel() and its release
        self._cancelled = threading.Event()

    def _check(self) -> None:
        if self._cancelled.is_set():
            raise StopRequested

    def cancel(self) -> None:
        with self._lock:
            self._cancelled.set()
            self.release_all()

    def reset(self) -> None:
        self._cancelled.clear()

    @property
    def cancelled(self) -> bool:
        return self._cancelled.is_set()

    def move(self, x: int, y: int) -> None:
        with self._lock:
            self._check()
            self.backend.move(int(x), int(y))

    def cursor(self) -> tuple[int, int]:
        return self.backend.cursor()

    def button_down(self, name: str) -> None:
        with self._lock:
            self._check()
            self.backend.button(name, True)
            self._held_buttons.add(name)

    def button_up(self, name: str) -> None:
        with self._lock:
            self.backend.button(name, False)
            self._held_buttons.discard(name)

    def click(self, name: str = "left", count: int = 1) -> None:
        for _ in range(count):
            self.button_down(name)
            self.button_up(name)

    def scroll(self, notches: int) -> None:
        with self._lock:
            self._check()
            self.backend.wheel(int(notches))

    def key_down(self, vk: int) -> None:
        with self._lock:
            self._check()
            self.backend.key(vk, True)
            self._held_keys.add(vk)

    def key_up(self, vk: int) -> None:
        with self._lock:
            self.backend.key(vk, False)
            self._held_keys.discard(vk)

    def tap(self, vk: int) -> None:
        self.key_down(vk)
        self.key_up(vk)

    def type_char(self, ch: str) -> None:
        with self._lock:
            self._check()
            self.backend.char(ch)

    def key_is_down(self, vk: int) -> bool:
        return self.backend.key_is_down(vk)

    def release_all(self) -> None:
        with self._lock:
            for vk in list(self._held_keys):
                try:
                    self.key_up(vk)
                except OSError as e:
                    log.error("Could not release key %#x: %s", vk, e)
            for name in list(self._held_buttons):
                try:
                    self.button_up(name)
                except OSError as e:
                    log.error("Could not release mouse %s: %s", name, e)
