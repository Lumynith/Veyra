"""The Macro Library: saved macros as ordinary project files in data/macros/, one level of folders to organise
them. Everything is a plain file, so the library can also be managed in Explorer and survives app updates.
No Qt here; ui/library/library_page.py is the page."""
from __future__ import annotations

import logging
import re
import shutil
from dataclasses import dataclass
from datetime import datetime
from pathlib import Path

from automation import project
from automation.workflow import Workflow, WorkflowError

log = logging.getLogger(__name__)
_BAD = re.compile(r'[<>:"/\\|?*\x00-\x1f]')


def safe_name(name: str) -> str:
    """A name usable as a Windows file or folder name ('' if nothing usable is left)."""
    name = _BAD.sub("", str(name)).strip().rstrip(". ")  # Windows drops trailing dots and spaces
    return "" if name.upper() in {"CON", "PRN", "AUX", "NUL"} | {f"{d}{i}" for d in ("COM", "LPT") for i in range(10)} \
        else name[:80]


@dataclass(frozen=True)
class Entry:
    path: Path
    name: str
    folder: str          # "" = top level
    modified: datetime
    blocks: int
    error: str = ""      # set when the file cannot be read; the entry still shows so it can be deleted


class MacroLibrary:
    def __init__(self, root: str | Path) -> None:
        self.root = Path(root)
        self._cache: dict[Path, tuple[float, Entry]] = {}

    # -- reading ---------------------------------------------------------------------------------

    def folders(self) -> list[str]:
        return sorted((p.name for p in self.root.iterdir() if p.is_dir()), key=str.lower) if self.root.exists() else []

    def entries(self) -> list[Entry]:
        """Every macro, newest first. Files are re-read only when they change."""
        if not self.root.exists():
            return []
        found = []
        for path in [*self.root.glob("*" + project.EXT), *self.root.glob("*/*" + project.EXT)]:
            try:
                mtime = path.stat().st_mtime
            except OSError:
                continue
            cached = self._cache.get(path)
            if not cached or cached[0] != mtime:
                cached = (mtime, self._read(path, mtime))
                self._cache[path] = cached
            found.append(cached[1])
        self._cache = {e.path: self._cache[e.path] for e in found}
        return sorted(found, key=lambda e: e.modified, reverse=True)

    def _read(self, path: Path, mtime: float) -> Entry:
        folder = "" if path.parent == self.root else path.parent.name
        modified = datetime.fromtimestamp(mtime)
        try:
            wf, meta = project.read_document(path)
        except WorkflowError as e:
            return Entry(path, project.display_name(path), folder, modified, 0, str(e))
        return Entry(path, meta["name"], folder, modified, sum(1 for _ in wf.nodes()))

    # -- changing --------------------------------------------------------------------------------

    def _folder(self, folder: str) -> Path:
        folder = safe_name(folder)
        return self.root / folder if folder else self.root

    def _free_path(self, folder: str, name: str, keep: Path | None = None) -> Path:
        """data/macros/<folder>/<name>.lcp.json, numbered if that file already exists."""
        base = safe_name(name) or "Macro"
        target = self._folder(folder)
        path, n = target / (base + project.EXT), 2
        while path.exists() and path != keep:
            path, n = target / f"{base} ({n}){project.EXT}", n + 1
        return path

    def contains(self, path: Path) -> bool:
        """Whether path is a macro inside the library (top level or one folder down)."""
        root, parent = self.root.resolve(), Path(path).resolve().parent
        return parent == root or parent.parent == root

    def path_for(self, name: str, folder: str = "") -> Path:
        """Where a new macro called name would be saved."""
        return self._free_path(folder, name)

    def add(self, wf: Workflow, name: str, folder: str = "") -> Path:
        path = self._free_path(folder, name)
        project.write_project(path, wf, project.new_meta(name))
        return path

    def import_file(self, source: str | Path, folder: str = "") -> Path:
        """Copy a project or workflow file into the library. Raises WorkflowError if it is not a valid one."""
        wf, meta = project.read_document(source)
        path = self._free_path(folder, meta["name"])
        project.write_project(path, wf, meta)
        return path

    def rename(self, path: Path, name: str) -> Path:
        if not safe_name(name):
            raise ValueError("That name has no letters or numbers a file name can use.")
        wf, meta = project.read_document(path)
        new = self._free_path(path.parent.name if path.parent != self.root else "", name, keep=path)
        project.write_project(new, wf, {**meta, "name": name.strip()})
        if new != path:
            path.unlink()
        return new

    def duplicate(self, path: Path) -> Path:
        wf, meta = project.read_document(path)
        folder = path.parent.name if path.parent != self.root else ""
        new = self._free_path(folder, meta["name"] + " copy")
        project.write_project(new, wf, project.new_meta(meta["name"] + " copy"))
        return new

    def move(self, path: Path, folder: str) -> Path:
        new = self._free_path(folder, project.display_name(path))
        if new.parent == path.parent:
            return path
        new.parent.mkdir(parents=True, exist_ok=True)
        shutil.move(str(path), str(new))
        return new

    def new_folder(self, name: str) -> str:
        name = safe_name(name)
        if not name:
            raise ValueError("That folder name has no letters or numbers a file name can use.")
        (self.root / name).mkdir(parents=True, exist_ok=True)
        return name

    def delete(self, path: Path) -> None:
        """To the Recycle Bin, so a wrong click can be undone there."""
        _recycle(path)

    def delete_folder(self, name: str) -> list[tuple[Path, Path]]:
        """Moves the folder's macros to the top level, then removes the folder. Returns (old, new) per macro."""
        folder = self._folder(name)
        moved = [(path, self.move(path, "")) for path in list(folder.glob("*" + project.EXT))]
        if any(folder.iterdir()):  # other files the user put there: leave them, in the Recycle Bin
            _recycle(folder)
        else:
            folder.rmdir()
        return moved


def _recycle(path: Path) -> None:
    from win32com.shell import shell, shellcon
    flags = shellcon.FOF_ALLOWUNDO | shellcon.FOF_NOCONFIRMATION | shellcon.FOF_SILENT | shellcon.FOF_NOERRORUI
    result, aborted = shell.SHFileOperation((0, shellcon.FO_DELETE, str(path.resolve()), None, flags))
    if result or aborted or path.exists():
        raise OSError(f"Windows could not move {path.name} to the Recycle Bin (code {result}).")
    log.info("Moved %s to the Recycle Bin", path)
