"""Image asset library: named, foldered images with stable ids.

Blocks reference images as "asset:<id>", so renaming or moving an asset never breaks a workflow.
Layout: <root>/index.json and <root>/<id>.png
"""
from __future__ import annotations

import json
import logging
import os
import threading
import time
import uuid
from pathlib import Path

import numpy as np

from vision.image_capture import read_image, save_image

log = logging.getLogger(__name__)
PREFIX = "asset:"
_display: "AssetLibrary | None" = None  # used only to show friendly names on canvas blocks


def set_display_library(lib: "AssetLibrary") -> None:
    global _display
    _display = lib


def display_label(ref: str) -> str:
    if ref.startswith(PREFIX):
        return _display.label(ref) if _display else ref
    return Path(ref).name if ref else ""


class AssetLibrary:
    def __init__(self, root: Path) -> None:
        self.root = Path(root)
        self._lock = threading.RLock()
        self._index = {"assets": {}, "folders": [], "recent": []}
        self._load()

    # -- persistence -----------------------------------------------------

    @property
    def _index_path(self) -> Path:
        return self.root / "index.json"

    def _load(self) -> None:
        if not self._index_path.exists():
            return
        try:
            data = json.loads(self._index_path.read_text("utf-8"))
            self._index = {"assets": dict(data.get("assets", {})), "folders": list(data.get("folders", [])),
                           "recent": [r for r in data.get("recent", []) if isinstance(r, str)]}
        except (OSError, ValueError) as e:
            backup = self._index_path.with_suffix(".corrupt.json")
            os.replace(self._index_path, backup)
            log.error("Image asset index unreadable (%s); moved to %s", e, backup)

    def _save(self) -> None:
        self.root.mkdir(parents=True, exist_ok=True)
        tmp = self._index_path.with_suffix(".tmp")
        tmp.write_text(json.dumps(self._index, indent=2), "utf-8")
        os.replace(tmp, self._index_path)

    # -- recently used ---------------------------------------------------

    RECENT = 8

    def touch(self, ref: str) -> None:
        """Remember that an asset was just picked (shown first in pickers)."""
        asset_id = ref.removeprefix("asset:")
        with self._lock:
            if asset_id not in self._index["assets"]:
                return
            recent = [asset_id] + [r for r in self._index["recent"] if r != asset_id]
            if recent != self._index["recent"]:
                self._index["recent"] = recent[:self.RECENT]
                self._save()

    def recent(self) -> list[dict]:
        with self._lock:
            return [dict(self._index["assets"][r]) for r in self._index["recent"] if r in self._index["assets"]]

    # -- queries ---------------------------------------------------------

    def list(self) -> list[dict]:
        with self._lock:
            return sorted((dict(a) for a in self._index["assets"].values()),
                          key=lambda a: (a["folder"].lower(), a["name"].lower()))

    def folders(self) -> list[str]:
        with self._lock:
            names = set(self._index["folders"]) | {a["folder"] for a in self._index["assets"].values()}
        return sorted((f for f in names if f), key=str.lower)

    def get(self, asset_id: str) -> dict:
        with self._lock:
            if asset_id not in self._index["assets"]:
                raise KeyError(f"Image asset {asset_id!r} not found (was it deleted?)")
            return dict(self._index["assets"][asset_id])

    def path(self, asset_id: str) -> Path:
        return self.root / self.get(asset_id)["file"]

    @staticmethod
    def ref(asset_id: str) -> str:
        return PREFIX + asset_id

    def resolve(self, ref: str) -> tuple[Path, dict]:
        """File path and metadata for an 'asset:<id>' reference or a plain file path."""
        if not ref:
            raise ValueError("No image selected")
        if ref.startswith(PREFIX):
            try:
                meta = self.get(ref[len(PREFIX):])
            except KeyError as e:
                raise FileNotFoundError(str(e)) from None
            return self.root / meta["file"], meta
        return Path(ref), {}

    def label(self, ref: str) -> str:
        try:
            _, meta = self.resolve(ref)
        except (FileNotFoundError, ValueError):
            return "(missing image)" if ref else ""
        if not meta:
            return Path(ref).name
        return f"{meta['folder']}/{meta['name']}" if meta["folder"] else meta["name"]

    # -- changes ---------------------------------------------------------

    def add_image(self, img: np.ndarray, name: str, folder: str = "",
                  reference_size: tuple[int, int] | None = None) -> dict:
        name = name.strip() or "image"
        asset_id = uuid.uuid4().hex[:12]
        save_image(img, self.root / f"{asset_id}.png")
        meta = {"id": asset_id, "name": name, "folder": folder.strip("/ "), "file": f"{asset_id}.png",
                "size": [int(img.shape[1]), int(img.shape[0])],
                "reference_size": list(reference_size) if reference_size else None,
                "created": time.strftime("%Y-%m-%d %H:%M:%S")}
        with self._lock:
            self._index["assets"][asset_id] = meta
            self._save()
        log.info("Saved image asset %s (%d×%d)", self.label(self.ref(asset_id)), *meta["size"])
        return dict(meta)

    def import_file(self, path: str | Path, folder: str = "", name: str | None = None) -> dict:
        img = read_image(path)  # validates and normalises to PNG
        return self.add_image(img, name or Path(path).stem, folder)

    def _update(self, asset_id: str, **fields) -> None:
        with self._lock:
            if asset_id not in self._index["assets"]:
                raise KeyError(asset_id)
            self._index["assets"][asset_id].update(fields)
            self._save()

    def rename(self, asset_id: str, name: str) -> None:
        if not name.strip():
            raise ValueError("Name cannot be empty")
        self._update(asset_id, name=name.strip())

    def move(self, asset_id: str, folder: str) -> None:
        self._update(asset_id, folder=folder.strip("/ "))

    def delete(self, asset_id: str) -> None:
        with self._lock:
            meta = self._index["assets"].pop(asset_id)
            self._save()
        try:
            (self.root / meta["file"]).unlink()
        except OSError as e:
            log.warning("Could not delete %s: %s", meta["file"], e)

    def add_folder(self, folder: str) -> None:
        folder = folder.strip("/ ")
        if not folder:
            raise ValueError("Folder name cannot be empty")
        with self._lock:
            if folder not in self._index["folders"]:
                self._index["folders"].append(folder)
                self._save()

    @staticmethod
    def _under(f: str, folder: str) -> bool:
        return f == folder or f.startswith(folder + "/")

    def rename_folder(self, old: str, new: str) -> None:
        """Rename a folder and its subfolders ("Icons" → "UI" also turns "Icons/Small" into "UI/Small")."""
        new = new.strip("/ ")
        if not new:
            raise ValueError("Folder name cannot be empty")
        swap = lambda f: new + f[len(old):] if self._under(f, old) else f  # noqa: E731
        with self._lock:
            for a in self._index["assets"].values():
                a["folder"] = swap(a["folder"])
            self._index["folders"] = list(dict.fromkeys(swap(f) for f in self._index["folders"]))
            self._save()

    def remove_folder(self, folder: str) -> None:
        """Remove a folder and its subfolders; their images move to the parent folder."""
        parent = folder.rpartition("/")[0]
        with self._lock:
            for a in self._index["assets"].values():
                if self._under(a["folder"], folder):
                    a["folder"] = parent
            self._index["folders"] = [f for f in self._index["folders"] if not self._under(f, folder)]
            self._save()
