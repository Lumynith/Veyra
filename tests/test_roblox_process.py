"""Detection, docking, closing and reconnection against a stand-in window in ANOTHER process.

Real Roblox is a separate process, so this matters: cross-process SetParent behaves differently from the
in-process windows used in test_window_manager. The stand-in is a copy of python.exe named
RobloxPlayerBeta.exe (the default process name) showing one plain window; no Roblox code is involved.
"""
import os
import shutil
import subprocess
import sys
import time
from pathlib import Path

import pytest
import win32con
import win32gui

from blocks import registry
from core.window_manager import ANCHOR, GA_ROOT, list_windows
from tests.conftest import dispose

registry.load_builtin()

CHILD = r'''
import ctypes, ctypes.wintypes as w
u = ctypes.WinDLL("user32")
u.CreateWindowExW.restype = w.HWND
u.CreateWindowExW.argtypes = (w.DWORD, w.LPCWSTR, w.LPCWSTR, w.DWORD, ctypes.c_int, ctypes.c_int, ctypes.c_int,
                              ctypes.c_int, w.HWND, w.HMENU, w.HINSTANCE, w.LPVOID)
h = u.CreateWindowExW(0, "EDIT", "Fake Roblox", 0x10CF0000, 50, 50, 640, 480, None, None, None, None)
msg = w.MSG()
while u.GetMessageW(ctypes.byref(msg), None, 0, 0) > 0:
    u.TranslateMessage(ctypes.byref(msg)); u.DispatchMessageW(ctypes.byref(msg))
'''


@pytest.fixture(scope="module")
def fake_exe(tmp_path_factory):
    base = Path(sys._base_executable).parent
    folder = tmp_path_factory.mktemp("fakeroblox")
    for dll in base.glob("*.dll"):
        shutil.copy(dll, folder)
    exe = folder / "RobloxPlayerBeta.exe"
    shutil.copy(sys._base_executable, exe)
    return exe


def launch(exe) -> tuple[subprocess.Popen, int]:
    env = {**os.environ, "PYTHONHOME": sys.base_prefix}
    proc = subprocess.Popen([str(exe), "-S", "-c", CHILD], env=env)
    end = time.time() + 10
    while time.time() < end:
        mine = [w for w in list_windows(["RobloxPlayerBeta.exe"]) if w.pid == proc.pid]
        if mine:
            return proc, mine[0].hwnd
        time.sleep(0.05)
    proc.kill()
    pytest.fail("stand-in window did not appear")


def test_detect_dock_close_and_reconnect(qapp, settings, bus, fake_exe):
    from core.window_manager import WindowManager
    from ui.viewport import ViewportPanel
    if any(w.title != "Fake Roblox" for w in list_windows(["RobloxPlayerBeta.exe"])):
        pytest.skip("real Roblox is running")
    events = []
    for topic in ("roblox.attached", "roblox.closed"):
        bus.subscribe(topic, lambda p, t=topic: events.append(t))
    wm = WindowManager(settings, bus)
    panel = ViewportPanel(wm, settings, bus)
    panel.show()
    qapp.processEvents()
    proc, hwnd = launch(fake_exe)
    try:
        panel.toolbar.reconnect()  # finds the window by process name and docks it (anchored by default)
        root = win32gui.GetAncestor(panel.viewport.host_hwnd(), GA_ROOT)
        assert wm.hwnd == hwnd and wm.mode == ANCHOR
        assert win32gui.GetWindow(hwnd, win32con.GW_OWNER) == root
        left, top, right, bottom = win32gui.GetWindowRect(hwnd)
        assert (right - left, bottom - top) == (841, 661)
        assert (left, top) == win32gui.ClientToScreen(panel.viewport.host_hwnd(), (0, 0))

        proc.kill()  # Roblox crashes / is closed
        proc.wait(5)
        end = time.time() + 3
        while wm.attached and time.time() < end:
            wm._poll()
            time.sleep(0.05)
        qapp.processEvents()
        assert not wm.attached and "roblox.closed" in events

        proc, hwnd2 = launch(fake_exe)  # Roblox restarted
        panel.toolbar.reconnect()
        assert wm.hwnd == hwnd2 and win32gui.GetWindow(hwnd2, win32con.GW_OWNER) == root
        wm.detach()  # handed back as a normal top-level window
        assert win32gui.GetParent(hwnd2) == 0 and win32gui.GetWindow(hwnd2, win32con.GW_OWNER) == 0
        assert win32gui.GetWindowLong(hwnd2, win32con.GWL_STYLE) & win32con.WS_CAPTION
    finally:
        wm.detach()
        proc.kill()
        dispose(qapp, panel, wm)


def test_record_and_play_back_in_docked_window(qapp, settings, bus, fake_exe, tmp_path):
    """Clicks and keys aimed at the docked (other-process) window are recorded; clicks on our own UI are not.
    The converted macro then replays at the same viewport-relative spot."""
    from automation.engine import AutomationEngine, Services
    from automation.input_controller import InputController, SendInputBackend
    from automation.recorder import InputRecorder
    from automation.recording import Options, to_workflow
    from core.window_manager import WindowManager
    from ui.viewport import ViewportPanel
    if any(w.title != "Fake Roblox" for w in list_windows(["RobloxPlayerBeta.exe"])):
        pytest.skip("real Roblox is running")
    wm = WindowManager(settings, bus)
    panel = ViewportPanel(wm, settings, bus)
    panel.show()
    panel.activateWindow()
    qapp.processEvents()
    proc, hwnd = launch(fake_exe)
    out = SendInputBackend()
    home = out.cursor()
    try:
        panel.toolbar.reconnect()
        assert wm.hwnd == hwnd
        qapp.processEvents()
        ox, oy = win32gui.ClientToScreen(hwnd, (0, 0))
        _, _, w, h = win32gui.GetClientRect(hwnd)
        rec = InputRecorder(include_injected=True)  # our own pid is excluded, as in the app
        rec.start((ox, oy), (w, h))
        out.move(ox + 100, oy + 120)
        time.sleep(0.05)
        out.button("left", True)
        out.button("left", False)
        time.sleep(0.15)
        out.key(0x7C, True)   # F13: harmless, but goes to whatever has focus
        out.key(0x7C, False)
        bx, by = win32gui.ClientToScreen(panel.toolbar.winId(), (5, 5))  # our own toolbar
        out.move(bx, by)
        out.button("left", True)
        out.button("left", False)
        time.sleep(0.15)
        data = rec.stop()
        kinds = [(e["k"], e.get("x"), e.get("y"), e.get("vk")) for e in data["events"] if e["k"] != "move"]
        assert ("down", ox + 100, oy + 120, None) in kinds and ("up", ox + 100, oy + 120, None) in kinds
        assert not any(k[0] == "down" and k[1:3] == (bx, by) for k in kinds), "click on our own UI was recorded"
        assert ("kdown", None, None, 0x7C) in kinds, "key typed into the docked window was not recorded"

        wf, _ = to_workflow(data, Options(moves="clicks"))
        clicks = [n for n in wf.nodes() if n["type"] == "mouse.move"]
        assert (clicks[0]["params"]["x"], clicks[0]["params"]["y"]) == (100, 120)
        out.move(*home)
        engine = AutomationEngine(bus, Services(input=InputController(out), roblox_hwnd=lambda: hwnd,
                                                data_dir=tmp_path))
        engine.run(wf.to_dict())
        assert engine.wait_finished(10)
        assert out.cursor() == (ox + 100, oy + 120)  # replayed at the same spot inside the viewport
    finally:
        out.move(*home)
        wm.detach()
        proc.kill()
        dispose(qapp, panel, wm)


def _focus_of(hwnd) -> int:
    import ctypes
    import ctypes.wintypes as W

    import win32process

    class GTI(ctypes.Structure):
        _fields_ = [("cbSize", W.DWORD), ("flags", W.DWORD), ("hwndActive", W.HWND), ("hwndFocus", W.HWND),
                    ("hwndCapture", W.HWND), ("hwndMenuOwner", W.HWND), ("hwndMoveSize", W.HWND),
                    ("hwndCaret", W.HWND), ("rcCaret", W.RECT)]
    info = GTI(cbSize=ctypes.sizeof(GTI))
    ctypes.windll.user32.GetGUIThreadInfo(win32process.GetWindowThreadProcessId(hwnd)[0], ctypes.byref(info))
    return info.hwndFocus or 0


def _edit_text(hwnd) -> str:
    import ctypes
    buf = ctypes.create_unicode_buffer(256)
    ctypes.windll.user32.SendMessageW(hwnd, 0x000D, 256, buf)  # WM_GETTEXT (GetWindowText can't read it)
    return buf.value


@pytest.mark.parametrize("mode", ["anchor", "embed"])
def test_focus_roblox_block_then_typing_still_reaches_it(qapp, settings, bus, fake_exe, tmp_path, mode):
    """Focus Roblox hands keyboard focus to the other-process window in both dock modes, and leaves its input
    working: it once used AttachThreadInput, which can unlink a docked window's input for good."""
    from automation.engine import AutomationEngine, Services
    from automation.input_controller import InputController, SendInputBackend
    from automation.workflow import Workflow, new_node
    from core.window_manager import GA_ROOT, WindowManager
    from ui.viewport import ViewportPanel
    wm = WindowManager(settings, bus)
    panel = ViewportPanel(wm, settings, bus)
    panel.show()
    qapp.processEvents()
    proc, hwnd = launch(fake_exe)  # attached by handle below, so a running real Roblox doesn't matter
    out = SendInputBackend()
    home = out.cursor()
    engine = AutomationEngine(bus, Services(input=InputController(out), roblox_hwnd=lambda: hwnd,
                                            focus_roblox=wm.focus_from_any_thread, data_dir=tmp_path))
    wf = Workflow()
    wf.add_script([new_node("flow.start"), new_node("rbx.focus")])

    results = []
    bus.subscribe("engine.finished", results.append)

    def run_focus():
        results.clear()
        engine.run(wf.to_dict())
        end = time.time() + 10
        while not results and time.time() < end:
            qapp.processEvents()  # the block asks the GUI thread to do the focusing
            time.sleep(0.01)
        assert results and results[-1]["result"] == "completed", results
        time.sleep(0.1)
        qapp.processEvents()

    try:
        assert wm.attach(hwnd, panel.viewport.host_hwnd(), mode) == mode
        root = win32gui.GetAncestor(hwnd, GA_ROOT)
        if mode == "anchor":
            assert not win32gui.GetWindowLong(hwnd, win32con.GWL_STYLE) & win32con.WS_CAPTION  # looks docked
            assert win32gui.GetWindow(hwnd, win32con.GW_OWNER) == win32gui.GetAncestor(panel.winId(), GA_ROOT)
        bx, by = win32gui.ClientToScreen(int(panel.toolbar.winId()), (40, 10))  # our toolbar: focus leaves Roblox
        out.move(bx, by)
        out.button("left", True)
        out.button("left", False)
        end = time.time() + 0.3  # let Qt finish handling the click (it re-activates its window), as before a Run
        while time.time() < end:
            qapp.processEvents()
            time.sleep(0.01)
        assert _focus_of(hwnd) != hwnd
        for _ in range(2):  # twice: the first run must not leave anything broken
            run_focus()
            end = time.time() + 1  # the window takes focus once it has handled its activation
            while _focus_of(hwnd) != hwnd and time.time() < end:
                qapp.processEvents()
                time.sleep(0.02)
            f = _focus_of(hwnd)
            fg = win32gui.GetForegroundWindow()
            assert f == hwnd, (f"run {_ + 1}: focus on {f:#x} {win32gui.GetClassName(f) if f else ''} "
                               f"{win32gui.GetWindowText(f) if f else ''!r}, foreground {fg:#x} "
                               f"{win32gui.GetClassName(fg) if fg else ''}, fake {hwnd:#x}, root {root:#x}")
            assert win32gui.GetForegroundWindow() == root
        out.key(0x58, True)  # "x": only typed once the fake is known to have focus
        out.key(0x58, False)
        time.sleep(0.2)
        assert _edit_text(hwnd).endswith("x")
    finally:
        out.move(*home)
        wm.detach()
        proc.kill()
        dispose(qapp, panel, wm)
