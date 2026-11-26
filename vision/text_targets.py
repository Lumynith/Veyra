"""Reusable text detection targets: what to look for and where. Blocks reference them as "text:<id>".

Stored in one JSON file. A target holds text, match, case_sensitive, region (viewport coordinates,
blank = whole viewport), language and confidence.
"""
from __future__ import annotations

import json
import logging
import os
import threading
import uuid
from pathlib import Path

log = logging.getLogger(__name__)
PREFIX = "text:"
FIELDS = {"text": "", "match": "partial", "case_sensitive": False, "region": "", "language": "", "confidence": 0.0}
_display: "TextTargets | None" = None


def set_display_targets(targets: "TextTargets") -> None:
    global _display
    _display = targets


def display_target(ref: str) -> str:
    if not ref:
        return ""
    return _display.label(ref) if _display else ref


class TextTargets:
    def __init__(self, path: Path) -> None:
        self.path = Path(path)
        self._lock = threading.RLock()
        self._items: dict[str, dict] = {}
        if self.path.exists():
            try:
                self._items = dict(json.loads(self.path.read_text("utf-8")).get("targets", {}))
            except (OSError, ValueError) as e:
                backup = self.path.with_suffix(".corrupt.json")
                os.replace(self.path, backup)
                log.error("Text targets unreadable (%s); moved to %s", e, backup)

    def _save(self) -> None:
        self.path.parent.mkdir(parents=True, exist_ok=True)
        tmp = self.path.with_suffix(".tmp")
        tmp.write_text(json.dumps({"targets": self._items}, indent=2, ensure_ascii=False), "utf-8")
        os.replace(tmp, self.path)

    @staticmethod
    def ref(target_id: str) -> str:
        return PREFIX + target_id

    def list(self) -> list[dict]:
        with self._lock:
            return sorted((dict(t) for t in self._items.values()), key=lambda t: t["name"].lower())

    def get(self, ref: str) -> dict:
        target_id = ref[len(PREFIX):] if ref.startswith(PREFIX) else ref
        with self._lock:
            if target_id not in self._items:
                raise KeyError(f"Text target {ref!r} not found (was it deleted?)")
            return dict(self._items[target_id])

    def label(self, ref: str) -> str:
        try:
            t = self.get(ref)
        except KeyError:
            return "(missing target)"
        return f"{t['name']} · “{t['text']}”"

    def add(self, name: str, **fields) -> dict:
        if not str(fields.get("text", "")).strip():
            raise ValueError("A text target needs some text to look for")
        target = {"id": uuid.uuid4().hex[:12], "name": name.strip() or fields["text"].strip(),
                  **{k: fields.get(k, default) for k, default in FIELDS.items()}}
        with self._lock:
            self._items[target["id"]] = target
            self._save()
        log.info("Saved text target %s", self.label(target["id"]))
        return dict(target)

    def update(self, ref: str, **fields) -> None:
        target = self.get(ref)
        target.update({k: v for k, v in fields.items() if k in FIELDS or k == "name"})
        if not target["name"].strip():
            raise ValueError("Name cannot be empty")
        with self._lock:
            self._items[target["id"]] = target
            self._save()

    def delete(self, ref: str) -> None:
        target = self.get(ref)
        with self._lock:
            del self._items[target["id"]]
            self._save()
