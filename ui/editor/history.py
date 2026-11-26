"""Undo / redo for the canvas: snapshots of the workflow, taken after each edit settles.

Edits arrive as scene.changed_workflow (block moves, deletes, property typing…). A short debounce folds a
burst of changes (e.g. typing in a property field) into one undo step.
"""
from __future__ import annotations

import json
import zlib

from PySide6.QtCore import QObject, QTimer, Signal

from automation.workflow import Workflow

LIMIT = 100
MAX_BYTES = 48 * 1024 * 1024  # compressed; very large workflows keep fewer steps


class UndoHistory(QObject):
    changed = Signal()  # can_undo / can_redo may differ

    def __init__(self, scene, parent=None) -> None:
        super().__init__(parent)
        self._scene = scene
        self._undo: list[bytes] = []  # zlib-compressed Workflow.dumps() snapshots
        self._redo: list[bytes] = []
        self._current: str | None = None
        self._restoring = False
        self._timer = QTimer(self, singleShot=True, interval=350, timeout=self.commit)
        scene.changed_workflow.connect(self._on_change)

    def reset(self) -> None:
        """A different workflow was loaded: history starts over."""
        self._timer.stop()
        self._undo.clear()
        self._redo.clear()
        self._current = self._scene.wf.dumps()
        self.changed.emit()

    def _on_change(self) -> None:
        if not self._restoring:
            self._timer.start()

    def commit(self) -> None:
        self._timer.stop()
        snap = self._scene.wf.dumps()
        if self._current is None:
            self._current = snap
            return
        if snap == self._current:
            return
        self._undo.append(_pack(self._current))
        del self._undo[:-LIMIT]
        while len(self._undo) > 1 and sum(map(len, self._undo)) > MAX_BYTES:
            del self._undo[0]
        self._redo.clear()
        self._current = snap
        self.changed.emit()

    def can_undo(self) -> bool:
        return bool(self._undo) or self._timer.isActive()

    def can_redo(self) -> bool:
        return bool(self._redo)

    def undo(self) -> bool:
        self.commit()  # an edit still waiting for the debounce is the step to undo
        if not self._undo:
            return False
        self._redo.append(_pack(self._current))
        self._restore(self._undo.pop())
        return True

    def redo(self) -> bool:
        self.commit()
        if not self._redo:
            return False
        self._undo.append(_pack(self._current))
        self._restore(self._redo.pop())
        return True

    def _restore(self, packed: bytes) -> None:
        self._current = zlib.decompress(packed).decode()
        data = {"format": "lumencrest.workflow", "version": 1, **json.loads(self._current)}
        self._restoring = True
        try:
            self._scene.set_workflow(Workflow.from_dict(data))
        finally:
            self._restoring = False
        self.changed.emit()


def _pack(snapshot: str) -> bytes:
    return zlib.compress(snapshot.encode(), 1)  # level 1: fast; workflow JSON still shrinks ~10x
