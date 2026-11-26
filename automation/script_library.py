"""Saved user scripts: data/scripts/<name>.py"""
from __future__ import annotations

import os
import re
from pathlib import Path

NAME = re.compile(r"[\w\- ]{1,60}")


def check_name(name: str) -> str:
    name = name.strip()
    if not NAME.fullmatch(name):
        raise ValueError("Script names may use letters, digits, spaces, '-' and '_' (max 60)")
    return name


class ScriptLibrary:
    def __init__(self, folder: Path) -> None:
        self.folder = Path(folder)

    def names(self) -> list[str]:
        if not self.folder.exists():
            return []
        return sorted((p.stem for p in self.folder.glob("*.py")), key=str.lower)

    def path(self, name: str) -> Path:
        return self.folder / f"{check_name(name)}.py"

    def read(self, name: str) -> str:
        path = self.path(name)
        if not path.exists():
            raise FileNotFoundError(f"Script {name!r} not found in {self.folder}")
        return path.read_text("utf-8")

    def write(self, name: str, source: str) -> Path:
        path = self.path(name)
        path.parent.mkdir(parents=True, exist_ok=True)
        tmp = path.with_suffix(".tmp")
        tmp.write_text(source, "utf-8")
        os.replace(tmp, path)
        return path

    def delete(self, name: str) -> None:
        self.path(name).unlink(missing_ok=True)
