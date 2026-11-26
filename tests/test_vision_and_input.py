import cv2
import numpy as np
import pytest

from automation.expressions import compare, evaluate, interpolate
from automation.input_controller import parse_combo, parse_key
from vision.image_detector import DetectOptions, find, find_all, similarity
from vision.text_detector import Word, find_text, full_text


def test_template_matching():
    rng = np.random.default_rng(0)
    hay = rng.integers(0, 255, (200, 300, 3), dtype=np.uint8)
    tpl = hay[50:80, 120:160].copy()
    m = find(hay, tpl, DetectOptions(multiscale=False))
    assert m.score > 0.99 and (m.x, m.y) == (120, 50)
    hay[150:180, 10:50] = tpl  # second copy
    found = find_all(hay, tpl, DetectOptions(confidence=0.95, multiscale=False))
    assert sorted((m.x, m.y) for m in found) == [(10, 150), (120, 50)]
    assert similarity(tpl, tpl) == pytest.approx(1.0)
    assert find(tpl, hay, DetectOptions(multiscale=False)) is None  # template larger than the search area


def test_text_search():
    words = [Word("Quest", 0, 0, 50, 10, 0), Word("Complete", 55, 0, 80, 10, 0), Word("Exit", 0, 30, 30, 10, 1)]
    m = find_text(words, "quest complete")
    assert (m.x, m.y, m.w, m.h) == (0, 0, 135, 10)
    m = find_text(words, "Exit", case_sensitive=True)
    assert (m.x, m.y, m.w, m.h) == (0, 30, 30, 10)
    assert find_text(words, "exit", case_sensitive=True) is None
    assert find_text(words, "Comp", mode="exact") is None
    assert full_text(words) == "Quest Complete\nExit"


def test_windows_ocr_reads_rendered_text():
    try:
        from vision.text_detector import WindowsOcr
        ocr = WindowsOcr()
    except Exception as e:
        pytest.skip(f"Windows OCR unavailable: {e}")
    img = np.full((60, 360, 3), 25, np.uint8)
    cv2.putText(img, "Start Game", (10, 40), cv2.FONT_HERSHEY_SIMPLEX, 1.0, (235, 235, 235), 2)
    box = find_text(ocr.words(img), "start game")
    assert box is not None and box.x < 30


def test_expressions():
    assert interpolate("{a} and {missing} {a}", {"a": 2.0}) == "2 and {missing} 2"
    assert evaluate("(1 + 2) * 3 - 4 / 2") == 7
    for bad in ("__import__('os')", "a + 1", "2 ** 1000"):
        with pytest.raises(ValueError):
            evaluate(bad)
    assert compare("10", ">", "9")  # numeric, not string comparison
    assert compare("true", "is true", "") and compare("0", "is false", "")
    assert compare("Hello", "==", "hello", case_sensitive=False)


def test_key_parsing():
    assert parse_key("A") == 0x41 and parse_key("f5") == 0x74 and parse_key("enter") == 0x0D
    assert parse_combo("ctrl + shift + s") == [0x11, 0x10, 0x53]
    with pytest.raises(ValueError):
        parse_key("notakey")
