"""Macro recorder page: record mouse/keyboard input, tune compression, convert to editable blocks."""
from __future__ import annotations

import logging
from pathlib import Path
from typing import Callable

from PySide6.QtCore import Qt, QTimer
from PySide6.QtWidgets import (QComboBox, QDoubleSpinBox, QFileDialog, QFormLayout, QHBoxLayout, QLabel,
                               QListWidget, QMessageBox, QPushButton, QSpinBox, QSplitter, QVBoxLayout, QWidget)

from automation.engine import IDLE
from automation.input_controller import MODIFIERS
from automation.recorder import InputRecorder
from automation.recording import (PRESETS, Options, load_recording, options_dict, save_recording, to_workflow)
from automation.workflow import WorkflowError, walk
from blocks import registry
from ui.widgets import AdaptiveSplitter, Panel

log = logging.getLogger(__name__)
FILTER = "Veyra recording (*.lcr.json);;All JSON (*.json)"
PREVIEW_LIMIT = 500


class RecorderPage(QWidget):
    def __init__(self, settings, bus, engine, vision, open_workflow: Callable, recorder=None, parent=None) -> None:
        super().__init__(parent)
        self._settings, self._engine, self._vision, self._open_workflow = settings, engine, vision, open_workflow
        self.recorder = recorder or InputRecorder(on_hotkey=lambda name: bus.publish("recorder.hotkey", name))
        self.recording: dict | None = None
        self._dirty = False  # recorded but not saved
        bus.subscribe("recorder.hotkey", self._hotkey)
        bus.subscribe("engine.state", lambda _: self._update_buttons())

        # -- recording controls ---------------------------------------------------------------
        self.space = QComboBox()
        self.space.addItem("Roblox viewport", "viewport")
        self.space.addItem("Whole screen", "screen")
        self.space.setCurrentIndex(max(0, self.space.findData(settings.get("recorder.space", "viewport"))))
        self.space.setToolTip("Viewport: coordinates are relative to the Roblox window, so the macro still works "
                              "after the window moves.")
        self.record_btn = QPushButton("Record", objectName="primary")
        self.record_btn.clicked.connect(self.start)
        self.pause_btn = QPushButton("Pause")
        self.pause_btn.clicked.connect(self.toggle_pause)
        self.stop_btn = QPushButton("Stop")
        self.stop_btn.clicked.connect(self.stop)
        self.clear_btn = QPushButton("Clear")
        self.clear_btn.clicked.connect(self.clear)
        self.save_btn = QPushButton("Save…")
        self.save_btn.clicked.connect(self.save)
        self.import_btn = QPushButton("Import…")
        self.import_btn.clicked.connect(self.import_recording)
        self.status = QLabel("Not recording", objectName="emptyTitle")
        self.counts = QLabel("", objectName="muted")
        hint = QLabel("F9 pauses / resumes, F10 stops (also while Roblox has focus). "
                      "Input into this app itself is not recorded.",
                      objectName="muted", wordWrap=True)
        hint.setMinimumHeight(hint.fontMetrics().lineSpacing() * 2 + 4)  # splitters ignore heightForWidth

        row1 = QHBoxLayout()
        for b in (self.record_btn, self.pause_btn, self.stop_btn, self.clear_btn):
            row1.addWidget(b)
        row2 = QHBoxLayout()
        row2.addWidget(self.save_btn)
        row2.addWidget(self.import_btn)
        row2.addStretch(1)
        rec_body = QWidget()
        rec_layout = QVBoxLayout(rec_body)
        form = QFormLayout()
        form.addRow("Record in:", self.space)
        rec_layout.addLayout(form)
        rec_layout.addLayout(row1)
        rec_layout.addLayout(row2)
        rec_layout.addSpacing(8)
        rec_layout.addWidget(self.status)
        rec_layout.addWidget(self.counts)
        rec_layout.addWidget(hint)
        rec_layout.addStretch(1)

        # -- conversion options -------------------------------------------------------------------------
        o = Options.from_dict(settings.get("recorder.options", {}))
        self.preset = QComboBox()
        for key in PRESETS:
            self.preset.addItem(key.capitalize(), key)
        self.preset.addItem("Custom", "custom")
        self.moves = QComboBox()
        self.moves.addItem("Pointer paths (filtered)", "path")
        self.moves.addItem("Only where clicks happen", "clicks")
        self.min_px = QSpinBox(minimum=0, maximum=500, suffix=" px",
                               toolTip="Pointer samples closer than this to the last kept one are dropped")
        self.min_ms = QSpinBox(minimum=0, maximum=2000, suffix=" ms",
                               toolTip="Pointer samples sooner than this after the last kept one are dropped")
        self.timing = QSpinBox(minimum=1, maximum=1000, suffix=" ms", toolTip="Every delay is rounded to this step")
        self.min_wait = QDoubleSpinBox(minimum=0.0, maximum=10.0, decimals=3, singleStep=0.01, suffix=" s",
                                       toolTip="Pauses shorter than this are left out")
        self.speed = QDoubleSpinBox(minimum=0.1, maximum=10.0, decimals=2, singleStep=0.25, suffix=" ×",
                                    toolTip="Playback speed: 2 = twice as fast")
        self.repeat = QSpinBox(minimum=1, maximum=100000, toolTip="Wrap the macro in a Repeat block")
        self._set_options(o)
        self.preset.setCurrentIndex(max(0, self.preset.findData(settings.get("recorder.preset", "medium"))))
        self.preset.currentIndexChanged.connect(self._preset_chosen)
        for w in (self.moves,):
            w.currentIndexChanged.connect(self._option_edited)
        for w in (self.min_px, self.min_ms, self.timing, self.min_wait, self.speed, self.repeat):
            w.valueChanged.connect(self._option_edited)

        opts = QFormLayout()
        opts.addRow("Precision:", self.preset)
        opts.addRow("Mouse movement:", self.moves)
        opts.addRow("Min. move distance:", self.min_px)
        opts.addRow("Min. move interval:", self.min_ms)
        opts.addRow("Timing accuracy:", self.timing)
        opts.addRow("Skip pauses under:", self.min_wait)
        opts.addRow("Playback speed:", self.speed)
        opts.addRow("Repeat:", self.repeat)
        opt_body = QWidget()
        QVBoxLayout(opt_body).addLayout(opts)
        opt_body.layout().addStretch(1)

        # -- preview -----------------------------------------------------------------------------------
        self.stats = QLabel("Record or import something to preview the blocks.", objectName="muted", wordWrap=True)
        self.preview = QListWidget()
        self.play_btn = QPushButton("Play")
        self.play_btn.setToolTip("Convert with these settings and run it now (Shift+F5 stops)")
        self.play_btn.clicked.connect(self.play)
        self.convert_btn = QPushButton("Convert to blocks", objectName="primary")
        self.convert_btn.setToolTip("Open the recording in the editor as an editable workflow")
        self.convert_btn.clicked.connect(self.convert)
        actions = QHBoxLayout()
        actions.addStretch(1)
        actions.addWidget(self.play_btn)
        actions.addWidget(self.convert_btn)
        prev_body = QWidget()
        prev_layout = QVBoxLayout(prev_body)
        prev_layout.addWidget(self.stats)
        prev_layout.addWidget(self.preview, 1)
        prev_layout.addLayout(actions)

        top = AdaptiveSplitter(objectName="recorderTop", childrenCollapsible=False)
        top.addWidget(Panel("Recorder", rec_body))
        top.addWidget(Panel("Optimisation", opt_body))
        main = QSplitter(Qt.Orientation.Vertical, objectName="recorderMain", childrenCollapsible=False)
        main.addWidget(top)
        main.addWidget(Panel("Blocks preview", prev_body))
        main.setSizes([300, 400])
        layout = QVBoxLayout(self)
        layout.setContentsMargins(0, 0, 0, 0)
        layout.addWidget(main)

        self._timer = QTimer(self, interval=200, timeout=self._tick)
        self._update_buttons()

    # -- options -----------------------------------------------------------------------------------

    def _set_options(self, o: Options) -> None:
        self._loading = True
        self.moves.setCurrentIndex(max(0, self.moves.findData(o.moves)))
        self.min_px.setValue(o.min_move_px)
        self.min_ms.setValue(o.min_move_ms)
        self.timing.setValue(o.timing_ms)
        self.min_wait.setValue(o.min_wait)
        self.speed.setValue(o.speed)
        self.repeat.setValue(o.repeat)
        self._loading = False

    def options(self) -> Options:
        return Options(moves=self.moves.currentData(), min_move_px=self.min_px.value(),
                       min_move_ms=self.min_ms.value(), timing_ms=self.timing.value(),
                       min_wait=self.min_wait.value(), speed=self.speed.value(), repeat=self.repeat.value())

    def _preset_chosen(self, _=None) -> None:
        key = self.preset.currentData()
        if key in PRESETS:
            o = self.options()
            self._set_options(Options(**{**options_dict(o), **PRESETS[key]}))
        self._options_changed()

    def _option_edited(self, _=None) -> None:
        if self._loading:
            return
        key = self.preset.currentData()
        if key in PRESETS:  # a hand-edited filter value no longer matches the preset
            current = options_dict(self.options())
            if any(current[k] != v for k, v in PRESETS[key].items()):
                self.preset.blockSignals(True)
                self.preset.setCurrentIndex(self.preset.findData("custom"))
                self.preset.blockSignals(False)
        self._options_changed()

    def _options_changed(self) -> None:
        self._settings.set("recorder.preset", self.preset.currentData())
        self._settings.set("recorder.options", options_dict(self.options()))
        self._refresh_preview()

    # -- recording -----------------------------------------------------------------------------------

    def _viewport(self) -> tuple[tuple[int, int] | None, tuple[int, int] | None]:
        if self.space.currentData() != "viewport":
            return None, None
        x, y, w, h = self._vision.viewport_rect()  # raises if no Roblox window is attached
        return (x, y), (w, h)

    def start(self) -> None:
        if self.recorder.state != "idle" or self._engine.state != IDLE:
            return
        if self._dirty and not self._confirm_replace():
            return
        try:
            origin, size = self._viewport()
        except RuntimeError as e:
            QMessageBox.information(self, "Recorder", f"{e}. Attach Roblox first, or choose “Whole screen”.")
            return
        self._settings.set("recorder.space", self.space.currentData())
        try:
            self.recorder.start(origin, size)
        except OSError as e:
            log.error("Recording failed to start: %s", e)
            QMessageBox.warning(self, "Recorder", str(e))
            return
        self.recording = self.recorder.recording
        self._dirty = True
        self._timer.start()
        self._tick()
        self._update_buttons()

    def toggle_pause(self) -> None:
        if self.recorder.state == "recording":
            self.recorder.pause()
        elif self.recorder.state == "paused":
            self.recorder.resume()
        self._tick()
        self._update_buttons()

    def stop(self) -> None:
        if self.recorder.state == "idle":
            return
        self.recording = self.recorder.stop()
        ev = self.recording["events"]
        while ev and ev[-1]["k"] in ("kdown", "kup") and ev[-1]["vk"] in MODIFIERS:
            ev.pop()  # Ctrl/Alt of the hotkey that stopped the recording
        self._timer.stop()
        self._tick()
        self._update_buttons()
        self._refresh_preview()

    def _hotkey(self, name: str) -> None:
        (self.toggle_pause if name == "pause" else self.stop)()

    def clear(self) -> None:
        if self.recorder.state != "idle":
            self.stop()
        if self._dirty and not self._confirm_replace("Clear the recording?"):
            return
        self.recording, self._dirty = None, False
        self._tick()
        self._update_buttons()
        self._refresh_preview()

    def _confirm_replace(self, question: str = "Replace the current recording?") -> bool:
        b = QMessageBox.StandardButton
        return QMessageBox.question(self, "Recorder", f"{question} It has not been saved.",
                                    b.Yes | b.No, b.No) == b.Yes

    def _tick(self) -> None:
        state = self.recorder.state
        if self.recording is None:
            self.status.setText("Not recording")
            self.counts.setText("")
            return
        label = {"recording": "● Recording", "paused": "Paused"}.get(state, "Recorded")
        self.status.setText(label)
        self.counts.setText(f"{self.recorder.count if state != 'idle' else len(self.recording['events'])} events · "
                            f"{self.recorder.elapsed if state != 'idle' else self._duration():.1f} s · "
                            f"{'viewport' if self.recording.get('space') == 'viewport' else 'screen'} coordinates")

    def _duration(self) -> float:
        ev = (self.recording or {}).get("events") or []
        return ev[-1]["t"] if ev else 0.0

    def _update_buttons(self) -> None:
        state = self.recorder.state
        idle, has = state == "idle", bool(self.recording and self.recording["events"])
        engine_idle = self._engine.state == IDLE
        self.record_btn.setEnabled(idle and engine_idle)
        self.pause_btn.setEnabled(not idle)
        self.pause_btn.setText("Resume" if state == "paused" else "Pause")
        self.stop_btn.setEnabled(not idle)
        self.clear_btn.setEnabled(self.recording is not None)
        self.save_btn.setEnabled(idle and has)
        self.import_btn.setEnabled(idle)
        self.space.setEnabled(idle)
        self.play_btn.setEnabled(idle and has and engine_idle)
        self.convert_btn.setEnabled(idle and has)

    # -- files -----------------------------------------------------------------------------------------

    def save(self) -> None:
        if not self.recording:
            return
        path, _ = QFileDialog.getSaveFileName(self, "Save recording", "recording.lcr.json", FILTER)
        if not path:
            return
        try:
            save_recording(self.recording, Path(path))
        except OSError as e:
            QMessageBox.warning(self, "Save recording", str(e))
            return
        self._dirty = False
        log.info("Saved recording to %s (%d events)", path, len(self.recording["events"]))

    def import_recording(self) -> None:
        if self._dirty and not self._confirm_replace():
            return
        path, _ = QFileDialog.getOpenFileName(self, "Import recording", "", FILTER)
        if path:
            self.load(Path(path))

    def load(self, path: Path) -> bool:
        try:
            self.recording = load_recording(path)
        except ValueError as e:
            log.error("Import failed: %s", e)
            QMessageBox.warning(self, "Import recording", str(e))
            return False
        self._dirty = False
        log.info("Imported recording %s (%d events)", path.name, len(self.recording["events"]))
        self._tick()
        self._update_buttons()
        self._refresh_preview()
        return True

    # -- conversion ------------------------------------------------------------------------------------

    def workflow(self):
        return to_workflow(self.recording, self.options())

    def _refresh_preview(self) -> None:
        self.preview.clear()
        if not self.recording or not self.recording["events"] or self.recorder.state != "idle":
            self.stats.setText("Record or import something to preview the blocks.")
            return
        wf, st = self.workflow()
        self.stats.setText(f"{st['events']} recorded events → {st['actions']} actions → {st['blocks']} blocks · "
                           f"{st['duration']:.1f} s recorded")
        for i, (node, _, _) in enumerate(walk(wf.scripts[0]["blocks"])):
            if i >= PREVIEW_LIMIT:
                self.preview.addItem(f"… {st['blocks'] - PREVIEW_LIMIT} more")
                break
            block = registry.get(node["type"])
            summary = block.summary(node["params"])
            self.preview.addItem(f"{block.title}  {summary}".strip())

    def convert(self) -> None:
        if not self.recording or not self.recording["events"]:
            return
        wf, st = self.workflow()
        name = f"Recording {self.recording.get('created', '')[:16].replace(':', '.')}".strip()
        if self._open_workflow(wf, name):
            log.info("Converted recording to %d blocks", st["blocks"])

    def play(self) -> None:
        if not self.recording or self._engine.state != IDLE:
            return
        wf, _ = self.workflow()
        try:
            self._engine.run(wf.to_dict())
        except WorkflowError as e:
            QMessageBox.warning(self, "Cannot play recording", str(e))

    def shutdown(self) -> None:
        self.recorder.stop()
