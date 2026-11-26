"""Palette, stylesheet and tinted SVG icons."""
from __future__ import annotations

from functools import lru_cache
from string import Template

from PySide6.QtCore import QByteArray, Qt
from PySide6.QtGui import QIcon, QPainter, QPixmap
from PySide6.QtSvg import QSvgRenderer

from core.startup import ROOT

RES = ROOT / "resources"

C = {
    "bg0": "#0b0a0f",      # sidebar, status bar
    "bg1": "#111016",      # window
    "bg2": "#17151e",      # panels
    "bg3": "#1f1c29",      # inputs, hover
    "line": "#26222f",
    "text": "#e7e4ee",
    "muted": "#8a8598",
    "faint": "#57536a",
    "accent": "#9061f9",
    "accent_hi": "#b18cff",
    "accent_bg": "#2a1f45",
    "ok": "#4ade80",
    "warn": "#f5b14c",
    "err": "#f0616d",
}


PALETTES = {
    "dark": dict(C),
    "midnight": {**C, "bg0": "#070b14", "bg1": "#0c1220", "bg2": "#111a2c", "bg3": "#18243a", "line": "#1f2c45",
                 "text": "#e3e9f5", "muted": "#8391ab", "faint": "#4d5a73", "accent": "#3b82f6",
                 "accent_hi": "#7aa9ff", "accent_bg": "#16294d"},
    "light": {**C, "bg0": "#e6e4ec", "bg1": "#f4f3f7", "bg2": "#ffffff", "bg3": "#ebe9f1", "line": "#d6d3df",
              "text": "#1d1a24", "muted": "#615c70", "faint": "#a19cb0", "accent": "#7c4ddb",
              "accent_hi": "#6a3cc8", "accent_bg": "#e7defb", "ok": "#16a34a", "warn": "#c27a0e", "err": "#d33a48"},
}
ACCENTS = {"default": None, "violet": ("#9061f9", "#b18cff", "#2a1f45"), "blue": ("#3b82f6", "#7aa9ff", "#16294d"),
           "green": ("#22a565", "#4ade80", "#12331f"), "orange": ("#ea7a1a", "#f5a24c", "#3a2412"),
           "pink": ("#e0458f", "#f47cb4", "#3d1729")}


def set_palette(name: str = "dark", accent: str = "default") -> None:
    """Pick the colours before any widget is created (changed in place: modules hold a reference to C)."""
    C.update(PALETTES.get(name, PALETTES["dark"]))
    if ACCENTS.get(accent):
        C["accent"], C["accent_hi"], C["accent_bg"] = ACCENTS[accent]
    icon.cache_clear()


def stylesheet() -> str:
    return Template((RES / "themes" / "dark.qss").read_text("utf-8")).substitute(C)


def icon_names() -> list[str]:
    return sorted(p.stem for p in (RES / "icons").glob("*.svg"))


def _pixmap(name: str, color: str, size: int = 48) -> QPixmap:
    path = RES / "icons" / f"{name}.svg"
    if not path.exists():  # user-made blocks and plugins may name an icon that does not exist
        path = RES / "icons" / "blocks.svg"
    svg = path.read_text("utf-8").replace("#000", color)
    pm = QPixmap(size, size)
    pm.fill(Qt.GlobalColor.transparent)
    painter = QPainter(pm)
    QSvgRenderer(QByteArray(svg.encode())).render(painter)
    painter.end()
    return pm


@lru_cache(maxsize=None)
def icon(name: str, color: str | None = None, on: str | None = None, hover: str | None = None) -> QIcon:
    color, on, hover = color or C["muted"], on or C["accent_hi"], hover or C["text"]
    ic = QIcon()
    ic.addPixmap(_pixmap(name, color), QIcon.Mode.Normal, QIcon.State.Off)
    ic.addPixmap(_pixmap(name, hover), QIcon.Mode.Active, QIcon.State.Off)
    ic.addPixmap(_pixmap(name, on), QIcon.Mode.Normal, QIcon.State.On)
    ic.addPixmap(_pixmap(name, on), QIcon.Mode.Active, QIcon.State.On)
    ic.addPixmap(_pixmap(name, C["faint"]), QIcon.Mode.Disabled, QIcon.State.Off)
    return ic
