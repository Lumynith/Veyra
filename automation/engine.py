"""Interprets serialized workflows on a background thread with pause / resume / stop."""
from __future__ import annotations

import ctypes
import logging
import threading
import time
from dataclasses import dataclass, field
from pathlib import Path
from typing import Any, Callable

import win32gui
from PySide6.QtCore import QObject

from automation.expressions import interpolate
from automation.input_controller import InputController, StopRequested  # noqa: F401 (re-exported)
from automation.workflow import Workflow, WorkflowError
from blocks import registry
from vision import image_capture

log = logging.getLogger("automation")
IDLE, RUNNING, PAUSED, STOPPING = "idle", "running", "paused", "stopping"
SPACES = ("viewport", "screen")
_INTERPOLATED = {"str", "file", "region", "key", "choice"}
TRACE_OK_PER_BATCH = 50


class BreakLoop(Exception):
    """Raised by Break Loop; caught by the nearest loop."""


class EndWorkflow(Exception):
    def __init__(self, stopped: bool = False, message: str = "") -> None:
        super().__init__(message)
        self.stopped = stopped


class RestartWorkflow(Exception):
    """A block's on-error action is "restart": run the workflow again from the top."""


class BlockTimeout(TimeoutError):
    """A block ran past its time limit (raised from ctx.checkpoint, so it interrupts waits and searches)."""

    def __init__(self, owner: str, title: str, seconds: float) -> None:
        super().__init__(f"{title} took longer than its {seconds:g} s time limit")
        self.owner = owner


class BlockFailed(Exception):
    def __init__(self, title: str, error: Exception) -> None:
        super().__init__(f"{title}: {error}")
        self.title = title


@dataclass
class Services:
    """Everything blocks may touch outside the engine. Tests swap these for fakes."""
    input: InputController = field(default_factory=InputController)
    roblox_hwnd: Callable[[], int | None] = lambda: None
    focus_roblox: Callable[[], bool] = lambda: False  # WindowManager.focus_from_any_thread in the app
    capture: Callable[[tuple], Any] = image_capture.grab
    ocr: Callable[[str], Any] = lambda language="": None  # language "" = the configured default
    data_dir: Path = Path("data")
    vision: Any = None  # RecognitionManager; built from capture/roblox_hwnd when not given
    text_targets: Any = None  # TextTargets; built from data_dir when not given

    def __post_init__(self) -> None:
        if self.vision is None:
            from vision.assets import AssetLibrary
            from vision.recognition_manager import RecognitionManager
            self.vision = RecognitionManager(AssetLibrary(self.data_dir / "assets" / "images"), self.capture,
                                             self.roblox_hwnd)
        if self.text_targets is None:
            from vision.text_targets import TextTargets
            self.text_targets = TextTargets(self.data_dir / "assets" / "text_targets.json")


def _short(value: Any) -> str:
    if value is None:
        return ""
    text = value if isinstance(value, str) else repr(value)
    return text if len(text) <= 80 else text[:77] + "..."


class Runtime:
    """The ctx passed to blocks."""

    def __init__(self, engine: "AutomationEngine", services: Services) -> None:
        self._engine = engine
        self.services = services
        self.input = services.input
        self.vars: dict[str, Any] = {}
        self._last_report = 0.0
        self._deadlines: list[list] = []  # [deadline, node id, title, seconds] for blocks with a time limit
        self._trace: list[dict] = []
        self._ok_in_batch = 0
        self._dropped = 0  # "ok" entries left out of this batch (tight loops would flood the UI)
        self._last_flush = 0.0
        self._current: dict | None = None

    # -- control ---------------------------------------------------------

    def checkpoint(self) -> None:
        stop, resume = self._engine._stop, self._engine._resume
        if not resume.is_set():
            paused_at = time.perf_counter()
            while not resume.wait(0.05):
                if stop.is_set():
                    raise StopRequested
            for d in self._deadlines:  # time spent paused doesn't count against time limits
                d[0] += time.perf_counter() - paused_at
        if stop.is_set():
            raise StopRequested
        self._check_deadlines()

    def _check_deadlines(self) -> None:
        if self._deadlines:
            now = time.perf_counter()
            for deadline, owner, title, seconds in self._deadlines:
                if now > deadline:
                    raise BlockTimeout(owner, title, seconds)

    def wait(self, seconds: float) -> None:
        stop, resume = self._engine._stop, self._engine._resume
        end = time.perf_counter() + max(0.0, seconds)
        while (left := end - time.perf_counter()) > 0:
            if not resume.is_set():
                paused_at = time.perf_counter()
                self.checkpoint()
                end += time.perf_counter() - paused_at  # paused time doesn't count
            elif stop.wait(min(left, 0.05)):
                raise StopRequested
            else:
                self._check_deadlines()
        self.checkpoint()

    def poll(self, fn: Callable[[], Any], timeout: float, interval: float) -> Any:
        """Call fn until it returns something truthy or timeout passes (timeout <= 0: forever)."""
        end = time.perf_counter() + timeout
        while True:
            result = fn()
            if result:
                return result
            if timeout > 0 and time.perf_counter() >= end:
                return None
            self.wait(max(interval, self.services.vision.min_interval))  # caps polling frequency / CPU

    # -- variables & logging ---------------------------------------------

    def set(self, name: str, value: Any) -> None:
        name = name.strip()
        if not name:
            raise ValueError("Variable name is empty")
        self.vars[name] = value

    def get(self, name: str) -> Any:
        if name.strip() not in self.vars:
            raise KeyError(f"Variable {name!r} is not set")
        return self.vars[name.strip()]

    def text(self, s: str) -> str:
        return interpolate(s, self.vars)

    def log(self, message: str, level: int = logging.INFO) -> None:
        log.log(level, message)

    def emit(self, topic: str, payload=None) -> None:
        """Publish an event to the UI (delivered on the GUI thread)."""
        self._engine._bus.publish(topic, payload)

    def trace(self, node: dict | None, title: str, status: str, detail: str = "", ms: float | None = None) -> None:
        """Run history entry (sent to the UI as engine.trace in batches).

        At most TRACE_OK_PER_BATCH successful blocks are kept per 100 ms batch; problems and detections always are."""
        if status == "ok":
            if self._ok_in_batch >= TRACE_OK_PER_BATCH:
                self._dropped += 1
                self.flush()
                return
            self._ok_in_batch += 1
        now = time.time()
        self._trace.append({"time": time.strftime("%H:%M:%S", time.localtime(now)) + f".{int(now * 1000) % 1000:03d}",
                            "id": node["id"] if node else None, "title": title, "status": status,
                            "detail": detail, "ms": None if ms is None else round(ms, 1)})
        self.flush()

    def detection(self, what: str, found: bool, detail: str = "") -> None:
        """Blocks report image / text search results here for the debugger."""
        self.trace(self._current, what, "found" if found else "not found", detail)

    def flush(self, force: bool = False) -> None:
        now = time.perf_counter()
        if force or (self._trace and now - self._last_flush >= 0.1):
            self._last_flush = now
            if self._dropped:
                self._trace.append({"time": time.strftime("%H:%M:%S"), "id": None, "status": "ok", "ms": None,
                                    "title": f"... {self._dropped} more blocks", "detail": "not listed individually"})
            batch, self._trace = self._trace, []
            self._ok_in_batch = self._dropped = 0
            if batch:
                self._engine._bus.publish("engine.trace", batch)
            self._engine._bus.publish("engine.vars", dict(self.vars))

    def path(self, p: str) -> Path:
        path = Path(p)
        return path if path.is_absolute() else self.services.data_dir / path

    # -- coordinates -----------------------------------------------------

    def roblox_hwnd(self) -> int:
        hwnd = self.services.roblox_hwnd()
        if not hwnd or not win32gui.IsWindow(hwnd):
            raise RuntimeError("No Roblox window is attached")
        return hwnd

    def focus_roblox(self) -> bool:
        return self.services.focus_roblox()

    def origin(self, space: str) -> tuple[int, int]:
        """Screen position of the coordinate space's (0, 0)."""
        if space == "screen":
            return 0, 0
        return win32gui.ClientToScreen(self.roblox_hwnd(), (0, 0))

    def to_screen(self, x: float, y: float, space: str) -> tuple[int, int]:
        ox, oy = self.origin(space)
        return int(round(ox + x)), int(round(oy + y))

    def area(self, region: str, space: str) -> tuple[int, int, int, int]:
        """Screen rect for a 'x, y, w, h' region (blank = the whole viewport / primary screen)."""
        if space == "screen":
            base = image_capture.primary_screen()
        else:
            hwnd = self.roblox_hwnd()
            ox, oy = win32gui.ClientToScreen(hwnd, (0, 0))
            _, _, w, h = win32gui.GetClientRect(hwnd)
            base = (ox, oy, w, h)
        if not region.strip():
            return base
        try:
            x, y, w, h = (int(float(v)) for v in region.replace(";", ",").split(","))
        except ValueError:
            raise ValueError(f"Region must be 'x, y, width, height', got {region!r}") from None
        if w <= 0 or h <= 0:
            raise ValueError(f"Region {region!r} has no area")
        ox, oy = (0, 0) if space == "screen" else base[:2]
        return ox + x, oy + y, w, h

    def capture(self, rect: tuple[int, int, int, int]):
        return self.services.vision.capture(rect)

    @property
    def vision(self):
        return self.services.vision

    def space_size(self, space: str) -> tuple[int, int]:
        """Size of the coordinate space: the viewport client area or the primary screen."""
        return self.area("", space)[2:]

    def ocr(self, language: str = ""):
        backend = self.services.ocr(language)
        if backend is None:
            raise RuntimeError("No OCR backend is available")
        return backend

    # -- interpreter -----------------------------------------------------

    def resolve(self, block, params: dict) -> dict:
        out = {}
        for p in block.params:
            v = params.get(p.name, p.default)
            if p.kind in _INTERPOLATED and isinstance(v, str):
                v = self.text(v)
            elif p.kind in ("int", "float"):
                v = float(self.text(str(v))) if isinstance(v, str) else v
                v = int(v) if p.kind == "int" else float(v)
            out[p.name] = v
        return out

    def run_blocks(self, blocks: list[dict]) -> None:
        prev_if: bool | None = None
        for node in blocks:
            self.checkpoint()
            block = registry.get(node["type"])
            if node["type"] == "flow.comment":
                continue
            self._engine._before_block(node, block)
            self.checkpoint()  # step mode waits here
            self._report(node, block)
            if node["type"] == "flow.else":
                if prev_if is None:
                    raise WorkflowError("Else must directly follow an If block")
                if not prev_if:
                    self.run_blocks(node["body"])
                prev_if = None
                continue
            ok, result = self._execute(node, block)
            prev_if = bool(result) if ok and node["type"] == "flow.if" else None

    def _execute(self, node: dict, block) -> tuple[bool, Any]:
        """Run one block with its retries, time limit and on-error action. (False, None) = skipped."""
        retries = int(node.get("retries", 0))
        limit = float(node.get("timeout", 0.0))
        attempt = 0
        while True:
            outer, self._current = self._current, node
            started = time.perf_counter()
            if limit > 0:
                self._deadlines.append([started + limit, node["id"], block.title, limit])
            try:
                result = block().execute(self, self.resolve(block, node["params"]), node)
                if not block.container:  # containers report through their children
                    self.trace(node, block.title, "ok", _short(result), (time.perf_counter() - started) * 1000)
                return True, result
            except (StopRequested, BreakLoop, EndWorkflow, RestartWorkflow):
                raise
            except BlockTimeout as e:
                if e.owner != node["id"]:
                    raise  # an enclosing block's time limit: that block handles it
                error = e
            except Exception as e:
                error = e
            finally:
                if limit > 0:
                    self._deadlines.pop()
                self._current = outer
            ms = (time.perf_counter() - started) * 1000
            message = str(error)
            if attempt < retries:
                attempt += 1
                delay = float(node.get("retry_delay", 1.0))
                log.warning("%s failed (%s); retry %d of %d in %g s", block.title, message, attempt, retries, delay)
                self.trace(node, block.title, "retry", f"{message} (retry {attempt}/{retries})", ms)
                self.wait(delay)
                continue
            self.vars["last_error"] = message
            action = node.get("on_error", "stop")
            if action == "continue":
                log.warning("%s failed, skipped: %s", block.title, message)
                self.trace(node, block.title, "skipped", message, ms)
                return False, None
            self.trace(node, block.title, "timeout" if isinstance(error, BlockTimeout) else "error", message, ms)
            if action == "restart":
                raise RestartWorkflow(f"{block.title}: {message}")
            raise error if isinstance(error, BlockFailed) else BlockFailed(block.title, error) from error

    def _report(self, node: dict, block) -> None:
        now = time.perf_counter()
        if now - self._last_report >= 0.1:  # throttle: tight loops must not flood or repaint the GUI
            self._last_report = now
            self._engine._bus.publish("engine.block", {"id": node["id"], "title": block.title})


class AutomationEngine(QObject):
    """Publishes engine.state, engine.block, engine.trace, engine.vars and engine.finished on the bus."""

    def __init__(self, bus, services: Services | None = None, parent=None) -> None:
        super().__init__(parent)
        self._bus = bus
        self.services = services or Services()
        self._stop = threading.Event()
        self._resume = threading.Event()
        self._thread: threading.Thread | None = None
        self._stepping = False
        self._pending: tuple | None = None  # restart request: run() arguments
        self._rt: Runtime | None = None
        self._last: tuple | None = None  # arguments of the last run, for restart
        self.state = IDLE

    def _set_state(self, state: str) -> None:
        self.state = state
        self._bus.publish("engine.state", state)

    @staticmethod
    def plan(workflow: dict, mode: str = "all", node_id: str | None = None) -> tuple[Workflow, list[dict]]:
        """Validate and pick the blocks to run: the Start stack, one block, or a block and those after it."""
        wf = Workflow.from_dict(workflow)
        if mode == "all":
            problems = wf.validate()
            if problems:
                raise WorkflowError("\n".join(problems))
            return wf, wf.start_scripts()[0]["blocks"][1:]
        problems = wf.check_params()
        if problems:
            raise WorkflowError("\n".join(problems))
        try:
            lst, i, _ = wf.locate(node_id)
        except KeyError:
            raise WorkflowError("That block is no longer in the workflow.") from None
        blocks = [lst[i]] if mode == "block" else lst[i:]
        return wf, [b for b in blocks if not registry.get(b["type"]).hat]

    def run(self, workflow: dict, mode: str = "all", node_id: str | None = None, step: bool = False) -> None:
        if self.state != IDLE:
            raise RuntimeError("A workflow is already running")
        wf, blocks = self.plan(workflow, mode, node_id)
        if not blocks:
            raise WorkflowError("There are no blocks to run.")
        self._stop.clear()
        self._resume.set()
        self._stepping = step
        self._last = (workflow, mode, node_id)
        self.services.input.reset()
        self._set_state(RUNNING)
        self._thread = threading.Thread(target=self._run, args=(blocks, dict(wf.variables)),
                                         daemon=True, name="automation")
        self._thread.start()

    def pause(self) -> None:
        if self.state == RUNNING:
            self._resume.clear()
            self._set_state(PAUSED)

    def resume(self) -> None:
        if self.state == PAUSED:
            self._stepping = False
            self._resume.set()
            self._set_state(RUNNING)

    def step(self) -> None:
        """Run the next block, then pause again. While running, pauses at the next block."""
        if self.state == PAUSED:
            self._stepping = True
            self._resume.set()
            self._set_state(RUNNING)
        elif self.state == RUNNING:
            self._stepping = True

    def stop(self) -> None:
        """Emergency-safe: input is released and blocked at once, even if a block is busy in OCR or a search."""
        if self.state in (RUNNING, PAUSED):
            self._stop.set()
            self._resume.set()
            self._set_state(STOPPING)
        if self.state == STOPPING:
            self.services.input.cancel()

    @property
    def restarting(self) -> bool:
        """A stop that is part of Restart: a new run starts as soon as this one ends."""
        return self._pending is not None

    def emergency_stop(self) -> None:
        self._pending = None
        self.stop()
        self.services.input.cancel()  # also when idle: nothing may stay held
        log.warning("Emergency stop")

    def restart(self, workflow: dict | None = None) -> None:
        """Stop (if running) and start again once the current run has ended; default: the last run."""
        args = (workflow, "all", None) if workflow else self._last
        if args is None:
            return
        if self.state == IDLE:
            self.run(*args)
        else:
            self._pending = args
            self.stop()

    def set_variable(self, name: str, value: Any) -> None:
        """From the debug console: change a variable of the running workflow."""
        rt = self._rt
        if rt is None or self.state == IDLE:
            raise RuntimeError("No workflow is running")
        rt.set(name, value)
        self._bus.publish("engine.vars", dict(rt.vars))

    def wait_finished(self, timeout: float | None = None) -> bool:
        if self._thread:
            self._thread.join(timeout)
            return not self._thread.is_alive()
        return True

    def _before_block(self, node: dict, block) -> None:
        """Step mode: pause before each block, showing which one is next."""
        if self._stepping and not self._stop.is_set():
            self._resume.clear()
            self._set_state(PAUSED)
            self._bus.publish("engine.block", {"id": node["id"], "title": block.title})

    def _run(self, blocks: list[dict], variables: dict) -> None:
        started = time.perf_counter()
        result, error, restarts = "completed", None, 0
        log.info("Workflow started")
        winmm = ctypes.WinDLL("winmm")
        winmm.timeBeginPeriod(1)  # 1 ms timer resolution so waits (e.g. recorded timing) are not rounded to ~15 ms
        try:
            while True:
                rt = self._rt = Runtime(self, self.services)
                rt.vars.update(variables)
                try:
                    rt.run_blocks(blocks)
                    break
                except RestartWorkflow as e:
                    restarts += 1
                    log.warning("Restarting the workflow after an error (%d): %s", restarts, e)
                    self.services.input.release_all()
                    rt.flush(force=True)
        except StopRequested:
            result = "stopped"
        except EndWorkflow as e:
            result = "stopped" if e.stopped else "completed"
            if str(e):
                log.info("%s", e)
        except BreakLoop:
            result, error = "error", "Break Loop was used outside a loop"
            log.error(error)
        except Exception as e:
            result, error = "error", str(e)
            log.error("Workflow failed: %s", e, exc_info=not isinstance(e, (BlockFailed, WorkflowError)))
        finally:
            winmm.timeEndPeriod(1)
            self.services.input.release_all()  # never leave keys or buttons held down
            self._stepping = False
        rt.flush(force=True)
        elapsed = time.perf_counter() - started
        log.info("Workflow %s after %.2f s", result, elapsed)
        self._rt = None
        self.state = IDLE
        self._bus.publish("engine.finished", {"result": result, "error": error, "elapsed": elapsed,
                                              "variables": dict(rt.vars), "restarts": restarts})
        self._bus.publish("engine.block", {"id": None, "title": ""})
        self._bus.publish("engine.state", IDLE)
        pending, self._pending = self._pending, None
        if pending:
            try:
                self.run(*pending)
            except (WorkflowError, RuntimeError) as e:
                log.error("Restart failed: %s", e)
