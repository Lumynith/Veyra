import numpy as np
from PySide6.QtCore import QPointF, QRect

from automation.workflow import Workflow
from vision.assets import AssetLibrary
from vision.recognition_manager import RecognitionManager


class FakeWM:
    hwnd = None


def make_vision(tmp_path):
    return RecognitionManager(AssetLibrary(tmp_path), lambda rect: np.zeros((rect[3], rect[2], 3), np.uint8))


def test_capture_dialog_crops_selection(qapp):
    from ui.vision.capture_dialog import CaptureDialog
    frame = np.arange(100 * 80 * 3, dtype=np.uint32).reshape(80, 100, 3).astype(np.uint8)
    dlg = CaptureDialog(frame, "t", "asset", ["Menus"], "Menus", "Play")
    dlg.view.set_selection(QRect(10, 20, 30, 15))
    assert dlg.ok.isEnabled() and dlg.crop.shape == (15, 30, 3)
    assert (dlg.crop == frame[20:35, 10:40]).all()
    assert (dlg.asset_name(), dlg.asset_folder()) == ("Play", "Menus")
    dlg.view.set_selection(QRect(0, 0, 2, 2))  # too small
    assert not dlg.ok.isEnabled() and dlg.crop is None


def test_image_page_tree_and_search(qapp, tmp_path, settings):
    from ui.vision.image_page import ImageDetectionPage
    vision = make_vision(tmp_path)
    page = ImageDetectionPage(vision, FakeWM(), settings)
    assert page.lib_stack.currentIndex() == 0  # empty state
    a = vision.assets.add_image(np.full((20, 30, 3), 200, np.uint8), "Play", "Menus/Main")
    vision.assets.add_image(np.full((20, 30, 3), 50, np.uint8), "Close", "")
    page.refresh(("asset", a["id"]))
    assert page.lib_stack.currentIndex() == 1
    assert page._current() == ("asset", a["id"]) and page.test_btn.isEnabled()
    assert page._current_folder() == "Menus/Main"
    page.search.setText("clo")
    visible = [i.text(0) for i in page._items() if not i.isHidden()]
    assert visible == ["Close"]


def test_properties_use_image_and_region_pickers(qapp, tmp_path):
    from ui.editor.canvas import WorkflowScene
    from ui.editor.properties import PropertiesPanel
    from ui.vision.editors import ImagePicker, RegionPicker
    vision = make_vision(tmp_path)
    meta = vision.assets.add_image(np.zeros((10, 10, 3), np.uint8), "Btn")
    scene = WorkflowScene()
    scene.set_workflow(Workflow())
    node_id = scene.add_block("img.find", QPointF(0, 0), snap=False)
    from ui.editor.properties import EditorResources
    panel = PropertiesPanel(scene, EditorResources(vision=vision))
    panel.show_node(node_id)
    picker = panel.findChild(ImagePicker)
    assert picker is not None and panel.findChild(RegionPicker) is not None
    picker.combo.setCurrentIndex(picker.combo.findData(vision.assets.ref(meta["id"])))
    assert scene.wf.node(node_id)["params"]["image"] == vision.assets.ref(meta["id"])


def test_text_page_select_read_and_save_target(qapp, tmp_path, settings):
    from PySide6.QtCore import QThreadPool
    from ui.vision.text_page import TextDetectionPage
    from vision.text_detector import Word
    from vision.text_targets import TextTargets
    seen = []

    class FakeOcr:
        def words(self, img):
            seen.append(img.shape)
            return [Word("Play", 5, 4, 30, 10, 0, 0.9), Word("Now", 40, 4, 25, 10, 0, 0.8)]

    targets = TextTargets(tmp_path / "t.json")
    page = TextDetectionPage(make_vision(tmp_path), targets, lambda language="": FakeOcr(), settings)
    page.frame, page.source = np.zeros((200, 300, 3), np.uint8), "viewport"
    page._set_frame()
    page.view.set_selection(QRect(100, 50, 80, 30))
    page._read()
    QThreadPool.globalInstance().waitForDone(5000)
    for _ in range(5):
        qapp.processEvents()
    assert seen == [(30, 80, 3)] and page.table.rowCount() == 2  # OCR ran on the selection only
    assert page.table.item(0, 2).text() == "120, 59"  # word centre in viewport coordinates
    page.table.selectAll()
    assert page.text.text() == "Play Now"
    page.limit.setChecked(True)
    page._save_target()
    (t,) = targets.list()
    assert (t["name"], t["text"], t["match"], t["region"]) == ("Play Now", "Play Now", "exact", "100, 50, 80, 30")
    assert page.saved.count() == 1
