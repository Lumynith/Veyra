
import cv2
import numpy as np
import pytest

from vision.assets import AssetLibrary
from vision.image_detector import DetectOptions, best_match, find, find_all, prepare
from vision.recognition_manager import RecognitionManager


def ui_screen(w=640, h=480, seed=3) -> np.ndarray:
    """A synthetic 'game UI': gradient background with buttons, text and icons."""
    rng = np.random.default_rng(seed)
    img = np.zeros((h, w, 3), np.uint8)
    img[:] = np.linspace(30, 90, w, dtype=np.uint8)[None, :, None]
    for _ in range(25):
        x, y = int(rng.integers(0, w - 60)), int(rng.integers(0, h - 30))
        color = tuple(int(c) for c in rng.integers(40, 255, 3))
        cv2.rectangle(img, (x, y), (x + int(rng.integers(20, 60)), y + int(rng.integers(10, 30))), color, -1)
    cv2.rectangle(img, (300, 200), (390, 240), (40, 160, 230), -1)  # the "Play" button
    cv2.putText(img, "PLAY", (312, 230), cv2.FONT_HERSHEY_SIMPLEX, 0.8, (255, 255, 255), 2)
    cv2.circle(img, (120, 380), 18, (60, 220, 90), 3)
    return img


SCREEN = ui_screen()
BUTTON = SCREEN[195:245, 295:395].copy()  # 100×50, centre (345, 220)


def scaled(img, f):
    return cv2.resize(img, None, fx=f, fy=f, interpolation=cv2.INTER_LINEAR)


def test_exact_match_and_methods():
    for method in ("ccoeff", "ccorr", "sqdiff"):
        m = find(SCREEN, BUTTON, DetectOptions(method=method, multiscale=False))
        assert m.center == (345, 220) and m.score > 0.98, method


def test_multiscale_handles_ui_scaling():
    big = scaled(SCREEN, 1.2)  # Roblox UI drawn 20 % larger
    plain = find(big, BUTTON, DetectOptions(multiscale=False))
    multi = find(big, BUTTON, DetectOptions(scale_min=0.8, scale_max=1.3))
    assert multi.score > 0.85 and abs(multi.scale - 1.2) <= 0.06
    assert abs(multi.center[0] - 345 * 1.2) <= 3 and abs(multi.center[1] - 220 * 1.2) <= 3
    assert multi.score > plain.score + 0.1


def test_modes_tolerate_colour_and_lighting_changes():
    shifted = SCREEN.copy()
    shifted[..., 0] = np.clip(shifted[..., 0].astype(int) + 60, 0, 255)  # strong blue tint
    m = find(shifted, BUTTON, DetectOptions(mode="grayscale", multiscale=False))
    assert m.center == (345, 220)
    darker = (SCREEN * 0.55).astype(np.uint8)
    m = find(darker, BUTTON, DetectOptions(mode="edges", multiscale=False))
    assert m.center == (345, 220) and m.score > 0.6


def test_coarse_to_fine_on_large_capture():
    big = cv2.resize(ui_screen(seed=5), (1280, 960))  # > PYRAMID_AREA
    tpl = big[400:480, 600:720].copy()
    m = find(big, tpl, DetectOptions(multiscale=False))
    assert (m.x, m.y) == (600, 400) and m.score > 0.99


def test_find_all_dedupes_across_scales():
    screen = SCREEN.copy()
    screen[30:80, 20:120] = BUTTON
    found = find_all(screen, BUTTON, DetectOptions(confidence=0.9))
    assert sorted(m.center for m in found) == [(70, 55), (345, 220)]


def test_stop_callback_cancels_search():
    class Stop(Exception):
        pass

    def stop():
        raise Stop

    with pytest.raises(Stop):
        best_match(prepare(SCREEN, "color"), [(1.0, BUTTON)], should_stop=stop)


def test_manager_adapts_reuses_and_caches(tmp_path):
    lib = AssetLibrary(tmp_path)
    meta = lib.add_image(BUTTON, "Play", "Menus", reference_size=(640, 480))
    calls = []
    big = scaled(SCREEN, 1.25)  # the Roblox window is now 800×600

    def grab(rect):
        calls.append(rect)
        return big

    vm = RecognitionManager(lib, grab)
    opts = DetectOptions(multiscale=False)
    rect = (0, 0, 800, 600)
    m = vm.locate(lib.ref(meta["id"]), rect, opts, adapt_to=(800, 600))  # base scale 600/480 = 1.25
    assert m.score > 0.9 and m.scale == 1.25 and abs(m.center[0] - 431) <= 2
    vm.locate(lib.ref(meta["id"]), rect, opts, adapt_to=(800, 600))
    assert len(calls) == 1 and vm.stats["reused"] == 1  # second search reused the frame
    assert vm.stats["decoded"] == 1  # template decoded once


def test_asset_library_organises_and_persists(tmp_path):
    lib = AssetLibrary(tmp_path)
    a = lib.add_image(BUTTON, "Play button", "Menus")
    cv2.imwrite(str(tmp_path / "icon.png"), BUTTON[:20, :20])
    b = lib.import_file(tmp_path / "icon.png", "Icons")
    lib.rename(a["id"], "Play")
    lib.move(b["id"], "Icons/Small")
    lib.add_folder("Empty")
    assert lib.label(lib.ref(a["id"])) == "Menus/Play"
    assert lib.folders() == ["Empty", "Icons/Small", "Menus"]
    lib.rename_folder("Icons", "UI")  # subfolders follow their parent
    assert lib.get(b["id"])["folder"] == "UI/Small"
    lib.remove_folder("UI")
    assert lib.get(b["id"])["folder"] == "" and lib.folders() == ["Empty", "Menus"]
    lib.move(b["id"], "Icons/Small")

    again = AssetLibrary(tmp_path)  # reload from disk
    assert [x["name"] for x in again.list()] == ["icon", "Play"]
    path, meta = again.resolve(again.ref(a["id"]))
    assert path.exists() and meta["size"] == [100, 50]

    again.delete(a["id"])
    with pytest.raises(FileNotFoundError):
        again.resolve(again.ref(a["id"]))
    assert not path.exists()

    (tmp_path / "index.json").write_text("{broken")
    assert AssetLibrary(tmp_path).list() == [] and (tmp_path / "index.corrupt.json").exists()


def test_capture_window_reads_a_real_window(qapp):
    from PySide6.QtWidgets import QWidget
    from vision.image_capture import capture_window
    w = QWidget()
    w.setStyleSheet("background: rgb(10, 200, 30);")
    w.resize(120, 80)
    w.show()
    for _ in range(5):
        qapp.processEvents()
    img = capture_window(int(w.winId()))
    w.close()
    b, g, r = img[img.shape[0] // 2, img.shape[1] // 2]
    assert g > 150 and r < 60 and b < 60
