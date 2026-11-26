"""OCR with replaceable backends (Windows OCR, Tesseract), a result cache, and text/number search.

Any object with words(img) -> list[Word] can be a backend.
"""
from __future__ import annotations

import asyncio
import hashlib
import logging
import re
import threading
from collections import OrderedDict
from dataclasses import dataclass

import cv2
import numpy as np

log = logging.getLogger(__name__)
MATCH_MODES = ("partial", "exact", "regex")


@dataclass(frozen=True)
class Word:
    text: str
    x: int
    y: int
    w: int
    h: int
    line: int
    conf: float = 1.0  # 0–1. Windows OCR reports no confidence, so its words are always 1.0


@dataclass(frozen=True)
class TextMatch:
    x: int
    y: int
    w: int
    h: int
    text: str
    conf: float

    @property
    def center(self) -> tuple[int, int]:
        return self.x + self.w // 2, self.y + self.h // 2


class OcrBackend:
    name = ""

    def __init__(self, scale: float = 2.0) -> None:
        self.scale = max(1.0, float(scale))  # game UI text is small; upscaling helps both engines

    def words(self, img: np.ndarray) -> list[Word]:
        big = cv2.resize(img, None, fx=self.scale, fy=self.scale, interpolation=cv2.INTER_CUBIC) \
            if self.scale != 1 else img
        s = self.scale
        return [Word(w.text, round(w.x / s), round(w.y / s), round(w.w / s), round(w.h / s), w.line, w.conf)
                for w in self._recognize(big)]

    def _recognize(self, img: np.ndarray) -> list[Word]:
        raise NotImplementedError


class WindowsOcr(OcrBackend):
    name = "windows"

    def __init__(self, scale: float = 2.0, language: str = "") -> None:
        super().__init__(scale)
        from winrt.windows.media.ocr import OcrEngine
        if language:
            from winrt.windows.globalization import Language
            self._engine = OcrEngine.try_create_from_language(Language(language))
            if self._engine is None:
                raise RuntimeError(f"Windows OCR language {language!r} is not installed. "
                                   f"Installed: {', '.join(self.languages()) or 'none'}")
        else:
            self._engine = OcrEngine.try_create_from_user_profile_languages()
            if self._engine is None:
                raise RuntimeError("Windows OCR has no installed language. Add one in Windows language settings.")

    @staticmethod
    def languages() -> list[str]:
        from winrt.windows.media.ocr import OcrEngine
        return [lang.language_tag for lang in OcrEngine.available_recognizer_languages]

    def _recognize(self, img: np.ndarray) -> list[Word]:
        from winrt.windows.graphics.imaging import BitmapAlphaMode, BitmapPixelFormat, SoftwareBitmap
        from winrt.windows.storage.streams import DataWriter
        bgra = cv2.cvtColor(img, cv2.COLOR_BGR2BGRA)
        writer = DataWriter()
        writer.write_bytes(bgra.tobytes())
        bitmap = SoftwareBitmap(BitmapPixelFormat.BGRA8, bgra.shape[1], bgra.shape[0], BitmapAlphaMode.PREMULTIPLIED)
        bitmap.copy_from_buffer(writer.detach_buffer())

        async def run():
            return await self._engine.recognize_async(bitmap)

        result = asyncio.run(run())
        out = []
        for i, line in enumerate(result.lines):
            for w in line.words:
                r = w.bounding_rect
                out.append(Word(w.text, int(r.x), int(r.y), int(r.width), int(r.height), i))
        return out


class TesseractOcr(OcrBackend):
    name = "tesseract"

    def __init__(self, scale: float = 2.0, command: str = "", language: str = "") -> None:
        super().__init__(scale)
        import pytesseract
        if command:
            pytesseract.pytesseract.tesseract_cmd = command
        self._tess = pytesseract
        self._lang = language or "eng"

    @staticmethod
    def languages(command: str = "") -> list[str]:
        import pytesseract
        if command:
            pytesseract.pytesseract.tesseract_cmd = command
        return [lang for lang in pytesseract.get_languages(config="") if lang != "osd"]

    def _recognize(self, img: np.ndarray) -> list[Word]:
        d = self._tess.image_to_data(cv2.cvtColor(img, cv2.COLOR_BGR2RGB), lang=self._lang,
                                     output_type=self._tess.Output.DICT)
        out, lines = [], {}
        for i, text in enumerate(d["text"]):
            conf = float(d["conf"][i])
            if text.strip() and conf >= 0:
                key = (d["block_num"][i], d["par_num"][i], d["line_num"][i])
                line = lines.setdefault(key, len(lines))
                out.append(Word(text, d["left"][i], d["top"][i], d["width"][i], d["height"][i], line, conf / 100))
        return out


def make_backend(name: str, scale: float = 2.0, tesseract_cmd: str = "", language: str = "") -> OcrBackend:
    if name == "tesseract":
        return TesseractOcr(scale, tesseract_cmd, language)
    return WindowsOcr(scale, language)


def languages(name: str, tesseract_cmd: str = "") -> list[str]:
    return TesseractOcr.languages(tesseract_cmd) if name == "tesseract" else WindowsOcr.languages()


class CachedOcr:
    """Wraps a backend: identical images (same pixels) are recognised once. Serialises engine use."""

    def __init__(self, backend, size: int = 32) -> None:
        self.backend = backend
        self.name = getattr(backend, "name", "")
        self._size = size
        self._cache: OrderedDict[bytes, list[Word]] = OrderedDict()
        self._lock = threading.Lock()
        self.stats = {"hits": 0, "runs": 0}

    def words(self, img: np.ndarray) -> list[Word]:
        img = np.ascontiguousarray(img)
        key = hashlib.blake2b(img.data, digest_size=16, person=str(img.shape).encode()[:16]).digest()
        with self._lock:  # one recognition at a time; engines are not guaranteed thread-safe
            if key in self._cache:
                self._cache.move_to_end(key)
                self.stats["hits"] += 1
                return self._cache[key]
            words = self.backend.words(img)
            self.stats["runs"] += 1
            if self._size:
                self._cache[key] = words
                while len(self._cache) > self._size:
                    self._cache.popitem(last=False)
            return words


# -- searching ----------------------------------------------------------------

def _lines(words: list[Word]) -> list[list[Word]]:
    lines: dict[int, list[Word]] = {}
    for w in words:
        lines.setdefault(w.line, []).append(w)
    return [sorted(ws, key=lambda w: w.x) for _, ws in sorted(lines.items())]


def full_text(words: list[Word], min_conf: float = 0.0) -> str:
    return "\n".join(" ".join(w.text for w in line) for line in _lines(confident(words, min_conf)))


def confident(words: list[Word], min_conf: float) -> list[Word]:
    return [w for w in words if w.conf >= min_conf] if min_conf > 0 else list(words)


def _line_matches(words: list[Word], rx: re.Pattern):
    """(regex match, words it covers) for every match in every line."""
    for line in _lines(words):
        text, spans = "", []
        for w in line:  # remember each word's character span inside the joined line
            if text:
                text += " "
            spans.append((len(text), len(text) + len(w.text), w))
            text += w.text
        for m in rx.finditer(text):
            if m.end() > m.start():
                yield m, [w for start, end, w in spans if start < m.end() and end > m.start()] or line


def _box(m: re.Match, hit: list[Word], group: int = 0) -> TextMatch:
    x0, y0 = min(w.x for w in hit), min(w.y for w in hit)
    x1, y1 = max(w.x + w.w for w in hit), max(w.y + w.h for w in hit)
    return TextMatch(x0, y0, x1 - x0, y1 - y0, m.group(group), min(w.conf for w in hit))


def compile_query(query: str, case_sensitive: bool = False, mode: str = "partial") -> re.Pattern:
    """partial: anywhere in a line · exact: whole word(s) only · regex: a regular expression."""
    if not query:
        raise ValueError("No text to search for")
    flags = 0 if case_sensitive else re.IGNORECASE
    if mode == "regex":
        try:
            return re.compile(query, flags)
        except re.error as e:
            raise ValueError(f"Invalid regex {query!r}: {e}") from None
    pattern = re.escape(query.strip())
    if mode == "exact":
        pattern = rf"(?<!\w){pattern}(?!\w)"
    return re.compile(pattern, flags)


def find_all_text(words: list[Word], query: str, case_sensitive: bool = False, mode: str = "partial",
                  min_conf: float = 0.0) -> list[TextMatch]:
    rx = compile_query(query, case_sensitive, mode)
    return [_box(m, hit) for m, hit in _line_matches(confident(words, min_conf), rx)]


def find_text(words: list[Word], query: str, case_sensitive: bool = False,
              mode: str = "partial", min_conf: float = 0.0) -> TextMatch | None:
    found = find_all_text(words, query, case_sensitive, mode, min_conf)
    return found[0] if found else None


# -- numbers ------------------------------------------------------------------

_NUMBER = re.compile(r"(?<![\w.])([-+]?(?:\d{1,3}(?:,\d{3})+|\d+)(?:\.\d+)?)\s?([kKmMbB])?(?![\w])")
_SUFFIX = {"k": 1e3, "m": 1e6, "b": 1e9}


def parse_number(text: str) -> float | int | None:
    """'1,250' → 1250, '2.5k' → 2500, '-3' → -3. None when the text holds no number."""
    m = _NUMBER.search(text.strip())
    if not m:
        return None
    value = float(m.group(1).replace(",", "")) * _SUFFIX.get((m.group(2) or "").lower(), 1)
    return int(value) if value.is_integer() else value


def find_numbers(words: list[Word], min_conf: float = 0.0) -> list[tuple[float | int, TextMatch]]:
    """Every number on screen, in reading order, with where it is."""
    out = []
    for m, hit in _line_matches(confident(words, min_conf), _NUMBER):
        value = parse_number(m.group(0))
        if value is not None:
            out.append((value, _box(m, hit)))
    return out
