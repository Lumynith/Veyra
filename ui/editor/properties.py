"""Edits the selected block. Editors are generated from the block's Param list."""
from __future__ import annotations

from dataclasses import dataclass
from typing import Any

from PySide6.QtCore import Qt
from PySide6.QtWidgets import (QCheckBox, QComboBox, QCompleter, QDoubleSpinBox, QFileDialog, QFormLayout,
                               QHBoxLayout, QLabel, QLineEdit, QScrollArea, QSpinBox, QStackedWidget, QVBoxLayout,
                               QWidget)

from automation.input_controller import KEY_NAMES
from blocks import registry
from ui.vision.editors import ImagePicker, RegionPicker, TargetCombo
from ui.widgets import EmptyState, IconButton

IMAGE_FILTER = "Images (*.png *.jpg *.jpeg *.bmp)"


@dataclass
class EditorResources:
    """What rich property editors need. Anything left None falls back to a plain text field."""
    vision: Any = None    # RecognitionManager: image / region pickers
    targets: Any = None   # TextTargets: saved text target picker
    scripts: Any = None   # ScriptLibrary: saved script picker


class ScriptCombo(QComboBox):
    """Saved scripts; refreshed every time it opens."""

    def __init__(self, scripts, value: str, on_change) -> None:
        super().__init__(editable=True)
        self._scripts = scripts
        self.lineEdit().setPlaceholderText("Save a script in the Script Editor first")
        self._refresh(value)
        self.currentTextChanged.connect(on_change)

    def _refresh(self, value: str) -> None:
        self.blockSignals(True)
        self.clear()
        self.addItems(self._scripts.names())
        self.setCurrentText(value)
        self.blockSignals(False)

    def showPopup(self) -> None:
        self._refresh(self.currentText())
        super().showPopup()


def _editor(param, value, on_change, res: EditorResources, params=None) -> QWidget:
    kind, vision, targets = param.kind, res.vision, res.targets
    if kind == "script" and res.scripts is not None:
        w = ScriptCombo(res.scripts, str(value), on_change)
    elif kind == "text_target" and targets is not None:
        w = TargetCombo(targets, str(value))
        w.changed.connect(on_change)
    elif kind == "image" and vision is not None:
        w = ImagePicker(vision, str(value))
        w.changed.connect(on_change)
    elif kind == "region" and vision is not None:
        w = RegionPicker(vision, str(value), lambda: (params or {}).get("space", "viewport"))
        w.changed.connect(on_change)
    elif kind == "bool":
        w = QCheckBox(checked=bool(value))
        w.toggled.connect(on_change)
    elif kind == "int":
        w = QSpinBox(minimum=int(param.minimum if param.minimum is not None else -10**9),
                     maximum=int(param.maximum if param.maximum is not None else 10**9), value=int(value))
        w.valueChanged.connect(on_change)
    elif kind == "float":
        w = QDoubleSpinBox(decimals=3, singleStep=0.05 if (param.maximum or 2) <= 1 else 0.1,
                           minimum=param.minimum if param.minimum is not None else -1e9,
                           maximum=param.maximum if param.maximum is not None else 1e9, value=float(value))
        w.valueChanged.connect(on_change)
    elif kind == "choice":
        w = QComboBox()
        w.addItems([str(o) for o in param.options])
        w.setCurrentText(str(value))
        w.currentTextChanged.connect(on_change)
    elif kind == "file":
        w = QWidget()
        row = QHBoxLayout(w)
        row.setContentsMargins(0, 0, 0, 0)
        edit = QLineEdit(str(value), placeholderText="No file selected")
        edit.textChanged.connect(on_change)
        browse = IconButton("open", "Choose file…")
        browse.clicked.connect(lambda: (path := QFileDialog.getOpenFileName(w, "Choose image", edit.text(),
                                                                             IMAGE_FILTER)[0]) and edit.setText(path))
        row.addWidget(edit, 1)
        row.addWidget(browse)
    else:  # str, key, var (and region/image without a vision manager)
        w = QLineEdit(str(value))
        if kind == "region":
            w.setPlaceholderText("x, y, width, height — blank = everything")
        elif kind == "var":
            w.setPlaceholderText("variable name")
        elif kind == "key":
            completer = QCompleter(KEY_NAMES, w)
            completer.setCaseSensitivity(Qt.CaseSensitivity.CaseInsensitive)
            w.setCompleter(completer)
        w.textChanged.connect(on_change)
    if param.tooltip:
        w.setToolTip(param.tooltip)
    return w


class PropertiesPanel(QStackedWidget):
    def __init__(self, scene, resources: EditorResources | None = None, parent=None) -> None:
        super().__init__(parent)
        self._scene = scene
        self._res = resources or EditorResources()
        self.addWidget(EmptyState("settings", "Nothing selected", "Select a block to edit its properties."))
        self._scroll = QScrollArea(widgetResizable=True, frameShape=QScrollArea.Shape.NoFrame)
        self.addWidget(self._scroll)
        scene.selection_node.connect(self.show_node)
        scene.changed_workflow.connect(self._check_still_exists)
        self._node_id: str | None = None

    def _check_still_exists(self) -> None:
        if self._node_id and not any(n["id"] == self._node_id for n in self._scene.wf.nodes()):
            self.show_node(None)

    def _error_form(self, node: dict, node_id: str) -> QFormLayout:
        """Retries, retry interval, time limit and on-error action (stored on the node, not in params)."""
        form = QFormLayout()
        form.setFieldGrowthPolicy(QFormLayout.FieldGrowthPolicy.AllNonFixedFieldsGrow)

        def field(key):
            def changed(value):
                if value:
                    node[key] = value
                else:
                    node.pop(key, None)  # defaults are not stored
                self._scene.refresh_node(node_id)
                self._scene.changed_workflow.emit()
            return changed

        retries = QSpinBox(minimum=0, maximum=100, value=int(node.get("retries", 0)), specialValueText="None")
        retries.setToolTip("Try the block again this many times before the on-error action applies")
        retries.valueChanged.connect(field("retries"))
        delay = QDoubleSpinBox(minimum=0.0, maximum=3600.0, decimals=2, singleStep=0.5, suffix=" s",
                               value=float(node.get("retry_delay", 1.0)))
        delay.setToolTip("Wait between retries")
        delay.valueChanged.connect(lambda v: field("retry_delay")(v if v != 1.0 else 0))
        limit = QDoubleSpinBox(minimum=0.0, maximum=86400.0, decimals=1, singleStep=1.0, suffix=" s",
                               value=float(node.get("timeout", 0.0)), specialValueText="No limit")
        limit.setToolTip("If the block runs longer than this (e.g. waiting for an image), it counts as an error: "
                         "it is retried, then the on-error action applies")
        limit.valueChanged.connect(field("timeout"))
        on_error = QComboBox()
        for text, value in (("Stop the workflow", "stop"), ("Skip the block and continue", "continue"),
                            ("Restart the workflow", "restart")):
            on_error.addItem(text, value)
        on_error.setCurrentIndex(max(0, on_error.findData(node.get("on_error", "stop"))))
        on_error.currentIndexChanged.connect(lambda _: self._scene.set_on_error(node_id, on_error.currentData()))
        on_error.setToolTip("What happens when the block still fails after its retries. "
                            "The error text goes to {last_error}.")
        form.addRow("Retries", retries)
        form.addRow("Retry every", delay)
        form.addRow("Time limit", limit)
        form.addRow("On error", on_error)
        return form

    def show_node(self, node_id: str | None) -> None:
        self._node_id = node_id
        if node_id is None:
            self.setCurrentIndex(0)
            return
        node = self._scene.wf.node(node_id)
        block = registry.get(node["type"])
        body = QWidget()
        layout = QVBoxLayout(body)
        layout.setContentsMargins(10, 4, 10, 10)
        title = QLabel(f'<span style="color:{block.block_color()}">●</span>&nbsp; {block.title}',
                       objectName="emptyTitle")
        layout.addWidget(title)
        layout.addWidget(QLabel(block.description, objectName="muted", wordWrap=True))
        form = QFormLayout()
        form.setLabelAlignment(Qt.AlignmentFlag.AlignLeft)
        form.setFieldGrowthPolicy(QFormLayout.FieldGrowthPolicy.AllNonFixedFieldsGrow)

        def setter(name):
            def changed(value):
                node["params"][name] = value
                self._scene.refresh_node(node_id)
                self._scene.changed_workflow.emit()  # unsaved-changes tracking
            return changed

        for p in block.params:
            if p.kind == "hidden":
                continue
            form.addRow(p.label, _editor(p, node["params"].get(p.name, p.default), setter(p.name),
                                          self._res, node["params"]))
        layout.addLayout(form)
        if not block.hat and node["type"] != "flow.comment":
            layout.addWidget(QLabel("Error handling", objectName="panelTitle"))
            layout.addLayout(self._error_form(node, node_id))
        if not block.params:
            layout.addWidget(QLabel("This block has no settings.", objectName="muted"))
        layout.addStretch(1)
        self._scroll.setWidget(body)
        self.setCurrentIndex(1)
