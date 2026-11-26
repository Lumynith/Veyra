import os

import pytest
import win32con
import win32gui

from core.window_manager import ANCHOR, EMBED, GA_ROOT, WindowManager, list_windows, process_exe
from ui.viewport import Viewport


@pytest.fixture
def fake_roblox(qapp):
    hwnd = win32gui.CreateWindowEx(0, "STATIC", "Fake Roblox", win32con.WS_OVERLAPPEDWINDOW | win32con.WS_VISIBLE,
                                   100, 100, 500, 400, 0, 0, 0, None)
    yield hwnd
    if win32gui.IsWindow(hwnd):
        win32gui.DestroyWindow(hwnd)


@pytest.fixture
def setup(qapp, settings, bus):
    vp = Viewport(settings)
    vp.show()
    qapp.processEvents()
    wm = WindowManager(settings, bus)
    yield wm, vp
    wm.detach()
    vp.close()


def size(rect):
    return rect[2] - rect[0], rect[3] - rect[1]


def test_lists_windows_by_process(fake_roblox):
    found = list_windows([process_exe(os.getpid())])
    assert fake_roblox in [w.hwnd for w in found]


def test_embed_and_restore(fake_roblox, setup):
    wm, vp = setup
    orig_style = win32gui.GetWindowLong(fake_roblox, win32con.GWL_STYLE)
    orig_rect = win32gui.GetWindowRect(fake_roblox)
    assert wm.attach(fake_roblox, vp.host_hwnd(), EMBED) == EMBED
    assert win32gui.GetParent(fake_roblox) == vp.host_hwnd()
    rect = win32gui.GetWindowRect(fake_roblox)
    assert size(rect) == (841, 661)
    assert rect[:2] == win32gui.ClientToScreen(vp.host_hwnd(), (0, 0))
    wm.detach()
    assert win32gui.GetParent(fake_roblox) == 0
    assert win32gui.GetWindowLong(fake_roblox, win32con.GWL_STYLE) == orig_style
    assert win32gui.GetWindowRect(fake_roblox) == orig_rect


def test_anchor_client_mode_follows_host(fake_roblox, setup, settings, qapp):
    wm, vp = setup
    settings.set("viewport.size_mode", "client")
    assert wm.attach(fake_roblox, vp.host_hwnd(), ANCHOR) == ANCHOR
    assert win32gui.GetWindow(fake_roblox, win32con.GW_OWNER) == win32gui.GetAncestor(vp.host_hwnd(), GA_ROOT)
    assert size(win32gui.GetClientRect(fake_roblox)) == (841, 661)
    assert win32gui.ClientToScreen(fake_roblox, (0, 0)) == win32gui.ClientToScreen(vp.host_hwnd(), (0, 0))
    vp.move(vp.x() + 50, vp.y() + 30)
    qapp.processEvents()
    wm.sync()
    assert win32gui.ClientToScreen(fake_roblox, (0, 0)) == win32gui.ClientToScreen(vp.host_hwnd(), (0, 0))


def test_detects_closed_window(fake_roblox, setup, bus):
    wm, vp = setup
    closed = []
    bus.subscribe("roblox.closed", closed.append)
    wm.attach(fake_roblox, vp.host_hwnd(), EMBED)
    win32gui.DestroyWindow(fake_roblox)
    wm._poll()
    assert closed == [fake_roblox] and not wm.attached
