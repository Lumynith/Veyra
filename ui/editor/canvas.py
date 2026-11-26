"""Block canvas: stacks of blocks that snap together. Edits the Workflow model; never executes anything.

The model is the source of truth. Every structural change edits the Workflow and calls relayout(),
which positions one BlockItem per node (children inside their container's C-shaped arm).
"""
from __future__ import annotations

import copy
import json

from PySide6.QtCore import QLineF, QMimeData, QPointF, QRectF, Qt, Signal
from PySide6.QtGui import QAction, QColor, QFont, QKeySequence, QPainter, QPainterPath, QPen
from PySide6.QtWidgets import (QApplication, QGraphicsItem, QGraphicsScene, QGraphicsView, QInputDialog, QMenu,
                               QMessageBox)

from automation.workflow import START, Workflow, _reid, new_node, walk
from blocks import registry
from blocks.base_block import CATEGORIES
from ui.theme import C, icon

MIME = "application/x-lumencrest-block"
CLIP_MIME = "application/x-lumencrest-blocks"  # copied blocks: JSON list of nodes
W, H, H_COLLAPSED, HAT = 228, 36, 26, 12   # block width / height / collapsed height / hat bump
ARM, FOOT, EMPTY = 16, 14, 22              # container arm width / footer / empty body height
NX, NW, ND = 14, 20, 5                     # notch x / width / depth
SNAP = 30
GROUP_PAD, GROUP_TITLE = 14, 22


def _blend(a: QColor, b: QColor, t: float) -> QColor:
    return QColor(round(a.red() * t + b.red() * (1 - t)), round(a.green() * t + b.green() * (1 - t)),
                  round(a.blue() * t + b.blue() * (1 - t)))


def _act(menu: QMenu, text: str, slot, hint: str | None = None, icon_name: str | None = None,
         checked: bool | None = None) -> QAction:
    action = menu.addAction(icon(icon_name), text) if icon_name else menu.addAction(text)
    action.triggered.connect(slot)
    if hint:  # displayed only; the real shortcut lives on the view
        action.setShortcut(QKeySequence(hint))
        action.setShortcutContext(Qt.ShortcutContext.WidgetShortcut)
    if checked is not None:
        action.setCheckable(True)
        action.setChecked(checked)
    return action


def error_note(node: dict) -> str:
    """Short canvas hint for non-default error handling."""
    notes = []
    if node.get("retries"):
        notes.append(f"retry ×{node['retries']}")
    if node.get("timeout"):
        notes.append(f"limit {node['timeout']:g} s")
    action = {"continue": "skip on error", "restart": "restart on error"}.get(node.get("on_error", "stop"))
    if action:
        notes.append(action)
    return "".join(f"  · {n}" for n in notes)


class BlockItem(QGraphicsItem):
    def __init__(self, node_id: str) -> None:
        super().__init__()
        self.node_id = node_id
        self.node: dict = {}
        self.block = None
        self.script_id = ""
        self.h, self.header = H, H
        self.running = False
        self.setFlag(QGraphicsItem.GraphicsItemFlag.ItemIsSelectable)
        self.setAcceptHoverEvents(True)
        self._hover = False

    def bind(self, node: dict, script_id: str, h: float, header: float) -> None:
        if (h, header) != (self.h, self.header):
            self.prepareGeometryChange()
        self.node, self.script_id, self.h, self.header = node, script_id, h, header
        self.block = registry.get(node["type"])
        self.setToolTip(self.block.description)
        self.update()

    @property
    def top(self) -> float:
        return HAT if self.block.hat else 0

    @property
    def is_c(self) -> bool:
        return self.block.container and not self.node.get("collapsed")

    def boundingRect(self) -> QRectF:
        return QRectF(-4, -4, W + 8, self.h + ND + 8)

    def shape(self) -> QPainterPath:
        return self._outline()

    def _outline(self) -> QPainterPath:
        p = QPainterPath()
        top, h = self.top, self.h
        p.moveTo(0, top)
        if self.block.hat:
            p.quadTo(W * 0.22, -top * 0.9, W * 0.44, top)
        else:  # notch on top
            for x, y in ((NX, 0), (NX + ND, ND), (NX + NW - ND, ND), (NX + NW, 0)):
                p.lineTo(x, y)
        p.lineTo(W, top)
        if self.is_c:
            p.lineTo(W, self.header)
            for x, y in ((ARM + NX + NW, self.header), (ARM + NX + NW - ND, self.header + ND),
                         (ARM + NX + ND, self.header + ND), (ARM + NX, self.header), (ARM, self.header),
                         (ARM, h - FOOT), (W, h - FOOT)):
                p.lineTo(x, y)
        p.lineTo(W, h)
        if not self.block.cap:  # tab underneath for the next block
            for x, y in ((NX + NW, h), (NX + NW - ND, h + ND), (NX + ND, h + ND), (NX, h)):
                p.lineTo(x, y)
        p.lineTo(0, h)
        p.closeSubpath()
        return p

    def paint(self, p: QPainter, *_):
        p.setRenderHint(QPainter.RenderHint.Antialiasing)
        color = QColor(self.block.block_color())
        path = self._outline()
        if self.running:
            glow = QColor(C["accent_hi"])
            glow.setAlpha(70)
            p.setPen(QPen(glow, 7))
            p.drawPath(path)
        fill = _blend(color, QColor(C["bg2"]), 0.34 if self._hover else 0.27)
        edge = QColor(C["accent_hi"]) if self.isSelected() or self.running else _blend(color, QColor(C["bg2"]), 0.7)
        p.setPen(QPen(edge, 1.6 if self.isSelected() or self.running else 1.0))
        p.setBrush(fill)
        p.drawPath(path)

        row_h = (H_COLLAPSED if self.node.get("collapsed") and not self.block.container else H)
        y0 = self.top
        p.drawPixmap(10, int(y0 + (row_h - 16) / 2), icon(self.block.block_icon(), color.lighter(130).name())
                     .pixmap(16, 16))
        f = QFont(p.font())
        f.setPointSizeF(9)
        f.setWeight(QFont.Weight.DemiBold)
        p.setFont(f)
        p.setPen(QColor(C["text"]))
        x = 34
        title = self.block.title
        if self.block.container and self.node.get("collapsed"):
            title += f"  ({len(self.node.get('body', []))})"
        p.drawText(QRectF(x, y0, W - x - 8, row_h), Qt.AlignmentFlag.AlignVCenter, title)
        if self.node.get("collapsed") and not self.block.container:
            return
        summary = self.block.summary(self.node.get("params", {}))
        summary = (summary + error_note(self.node)).strip(" ·")
        if summary:
            x += p.fontMetrics().horizontalAdvance(title) + 8
            f.setWeight(QFont.Weight.Normal)
            p.setFont(f)
            p.setPen(QColor(C["muted"]))
            width = W - x - 8
            p.drawText(QRectF(x, y0, width, row_h), Qt.AlignmentFlag.AlignVCenter,
                       p.fontMetrics().elidedText(summary, Qt.TextElideMode.ElideRight, int(width)))

    def hoverEnterEvent(self, e) -> None:
        self._hover = True
        self.update()

    def hoverLeaveEvent(self, e) -> None:
        self._hover = False
        self.update()

    def itemChange(self, change, value):
        if change == QGraphicsItem.GraphicsItemChange.ItemSelectedHasChanged:
            self.update()
        return super().itemChange(change, value)


class GroupItem(QGraphicsItem):
    """Frame around a set of stacks. Drag the title bar to move them all."""

    def __init__(self, group: dict) -> None:
        super().__init__()
        self.group = group
        self.rect = QRectF()
        self.setZValue(-1000)

    def set_rect(self, rect: QRectF) -> None:
        self.prepareGeometryChange()
        self.rect = rect

    def title_rect(self) -> QRectF:
        return QRectF(self.rect.left(), self.rect.top(), self.rect.width(), GROUP_TITLE)

    def boundingRect(self) -> QRectF:
        return self.rect.adjusted(-2, -2, 2, 2)

    def paint(self, p: QPainter, *_):
        p.setRenderHint(QPainter.RenderHint.Antialiasing)
        frame = QColor(C["accent"])
        frame.setAlpha(110)
        bg = QColor(C["accent"])
        bg.setAlpha(16)
        p.setPen(QPen(frame, 1, Qt.PenStyle.DashLine))
        p.setBrush(bg)
        p.drawRoundedRect(self.rect, 6, 6)
        p.setPen(QColor(C["accent_hi"]))
        p.drawText(self.title_rect().adjusted(10, 0, -10, 0), Qt.AlignmentFlag.AlignVCenter, self.group["title"])


class WorkflowScene(QGraphicsScene):
    selection_node = Signal(object)  # node id or None
    changed_workflow = Signal()
    run_requested = Signal(str, str)  # mode ("block" | "from"), node id

    def __init__(self, parent=None) -> None:
        super().__init__(parent)
        self.wf = Workflow()
        self.items_by_id: dict[str, BlockItem] = {}
        self.groups_by_id: dict[str, GroupItem] = {}
        self._press: tuple[str, QPointF] | None = None
        self._drag: dict | None = None
        self._drag_offset = QPointF()
        self._target = None
        self._group_drag: tuple[GroupItem, QPointF, dict] | None = None
        self._running_id: str | None = None
        self._indicator = self.addPath(QPainterPath(), QPen(QColor(C["accent_hi"]), 3))
        self._indicator.setZValue(10**6)
        self.setSceneRect(-5000, -5000, 10000, 10000)
        self.selectionChanged.connect(self._on_selection)

    # -- model <-> items -------------------------------------------------

    def set_workflow(self, wf: Workflow) -> None:
        self.clearSelection()
        for item in list(self.items_by_id.values()) + list(self.groups_by_id.values()):
            self.removeItem(item)
        self.items_by_id.clear()
        self.groups_by_id.clear()
        self.wf = wf
        self.relayout()
        self.changed_workflow.emit()

    def relayout(self) -> None:
        seen: set[str] = set()
        for si, script in enumerate(self.wf.scripts):
            z = 10**5 if script is self._drag else si * 100
            self._layout_list(script["blocks"], script["pos"][0], script["pos"][1], z, True, script["id"], seen)
        for node_id in set(self.items_by_id) - seen:
            self.removeItem(self.items_by_id.pop(node_id))
        self._layout_groups()

    def _layout_list(self, blocks, x, y, z, visible, script_id, seen) -> float:
        start = y
        for node in blocks:
            y += self._layout_node(node, x, y, z, visible, script_id, seen)
        return y - start

    def _layout_node(self, node, x, y, z, visible, script_id, seen) -> float:
        item = self.items_by_id.get(node["id"])
        new = item is None
        if new:
            item = self.items_by_id[node["id"]] = BlockItem(node["id"])
        seen.add(node["id"])
        block = registry.get(node["type"])
        top = HAT if block.hat else 0
        header = H + top
        if block.container:
            body_visible = visible and not node.get("collapsed")
            body = self._layout_list(node["body"], x + ARM, y + header, z + 1, body_visible, script_id, seen)
            h = header if node.get("collapsed") else header + max(EMPTY, body) + FOOT
        else:
            h = (H_COLLAPSED if node.get("collapsed") else H) + top
        item.running = node["id"] == self._running_id
        item.bind(node, script_id, h, header)  # before setPos: Qt may query the shape on move
        item.setPos(x, y)
        item.setZValue(z)
        item.setVisible(visible)
        if new:
            self.addItem(item)
        return h

    def _layout_groups(self) -> None:
        live = {g["id"] for g in self.wf.groups}
        for gid in set(self.groups_by_id) - live:
            self.removeItem(self.groups_by_id.pop(gid))
        for g in self.wf.groups:
            item = self.groups_by_id.get(g["id"])
            if item is None:
                item = self.groups_by_id[g["id"]] = GroupItem(g)
                self.addItem(item)
            item.group = g
            rect = QRectF()
            for b in self.items_by_id.values():
                if b.script_id in g["scripts"] and b.isVisible():
                    rect = rect.united(b.sceneBoundingRect())
            item.set_rect(rect.adjusted(-GROUP_PAD, -GROUP_PAD - GROUP_TITLE, GROUP_PAD, GROUP_PAD))
            item.update()

    def stack_height(self, script: dict) -> float:
        items = [self.items_by_id[n["id"]] for n in script["blocks"]]
        return sum(i.h for i in items)

    # -- editing operations ----------------------------------------------

    def add_block(self, type_id: str, pos: QPointF, snap: bool = True) -> str:
        node = new_node(type_id)
        script = self.wf.add_script([node], (pos.x(), pos.y()))
        self.relayout()
        if snap:
            self._drag = script
            self._finish_drag()
        self.changed_workflow.emit()
        return node["id"]

    def append_block(self, type_id: str, fallback: QPointF) -> str:
        """Add to the end of the Start stack (before a final End), or as a new stack."""
        starts = self.wf.start_scripts()
        block = registry.get(type_id)
        if not starts or block.hat:
            return self.add_block(type_id, fallback, snap=False)
        blocks = starts[0]["blocks"]
        node = new_node(type_id)
        index = len(blocks) - 1 if registry.get(blocks[-1]["type"]).cap else len(blocks)
        if block.cap and index < len(blocks):
            return self.add_block(type_id, fallback, snap=False)
        blocks.insert(index, node)
        self.relayout()
        self.changed_workflow.emit()
        return node["id"]

    def selected_blocks(self) -> list[BlockItem]:
        return [i for i in self.selectedItems() if isinstance(i, BlockItem)]

    def remove_selected(self) -> None:
        ids = [i.node_id for i in self.selected_blocks()]
        self.clearSelection()
        for node_id in ids:
            try:
                self.wf.remove(node_id)
            except KeyError:
                pass  # already removed with its container
        self.relayout()
        self.changed_workflow.emit()

    def selected_nodes(self) -> list[dict]:
        """Selected blocks in workflow order, leaving out blocks inside a selected container."""
        chosen = {i.node_id for i in self.selected_blocks()}
        out = []

        def visit(blocks):
            for node in blocks:
                if node["id"] in chosen:
                    out.append(node)
                elif "body" in node:
                    visit(node["body"])
        for script in self.wf.scripts:
            visit(script["blocks"])
        return out

    def paste_nodes(self, nodes: list[dict], pos: QPointF) -> int:
        """Pasted blocks become one new stack (fresh ids). A second Start is left out."""
        has_start = bool(self.wf.start_scripts())
        clones = []
        for node in nodes:
            if not isinstance(node, dict):
                continue
            if node["type"] == START and (has_start or clones):
                continue
            clones.append(_reid(copy.deepcopy(node)))
        if not clones:
            return 0
        # normalise through the model so damaged clipboard data can't break the canvas
        wf = Workflow.from_dict({"format": "lumencrest.workflow", "scripts": [{"blocks": clones}]})
        blocks = wf.scripts[0]["blocks"] if wf.scripts else []
        if not blocks:
            return 0
        self.wf.add_script(blocks, (pos.x(), pos.y()))
        self.clearSelection()
        self.relayout()
        for node in blocks:
            self.items_by_id[node["id"]].setSelected(True)
        self.changed_workflow.emit()
        return len(blocks)

    def find(self, text: str) -> list[str]:
        """Ids of blocks whose title, summary or parameter values contain text (workflow order)."""
        needle = text.strip().lower()
        if not needle:
            return []
        hits = []
        for script in self.wf.scripts:
            for node, _, _ in walk(script["blocks"]):
                block = registry.get(node["type"])
                hay = " ".join([block.title, block.summary(node["params"])]
                               + [str(v) for v in node["params"].values()]).lower()
                if needle in hay:
                    hits.append(node["id"])
        return hits

    def duplicate_selected(self) -> None:
        ids = [i.node_id for i in self.selected_blocks()]
        self.clearSelection()
        for node_id in ids:
            clone = self.wf.duplicate(node_id)
            self.relayout()
            self.items_by_id[clone["id"]].setSelected(True)
        self.changed_workflow.emit()

    def toggle_collapse(self, node_id: str) -> None:
        node = self.wf.node(node_id)
        node["collapsed"] = not node.get("collapsed")
        self.relayout()
        self.changed_workflow.emit()

    def set_on_error(self, node_id: str, mode: str) -> None:
        self.wf.node(node_id)["on_error"] = mode
        self.refresh_node(node_id)
        self.changed_workflow.emit()

    def group_selected(self, title: str = "Group") -> dict | None:
        script_ids = list(dict.fromkeys(i.script_id for i in self.selected_blocks()))
        if not script_ids:
            return None
        group = self.wf.group(script_ids, title)
        self.relayout()
        self.changed_workflow.emit()
        return group

    def ungroup(self, group_id: str) -> None:
        self.wf.groups = [g for g in self.wf.groups if g["id"] != group_id]
        self.relayout()
        self.changed_workflow.emit()

    def rename_group(self, group_id: str, title: str) -> None:
        for g in self.wf.groups:
            if g["id"] == group_id:
                g["title"] = title
        self.relayout()
        self.changed_workflow.emit()

    def tidy(self) -> None:
        """Lay stacks out left to right."""
        x = 0.0
        for script in self.wf.scripts:
            script["pos"] = [x, 0.0]
            x += W + ARM * 3 + 40
        self.relayout()
        self.changed_workflow.emit()

    def clear_workflow(self) -> None:
        self.set_workflow(Workflow(variables=dict(self.wf.variables)))

    def refresh_node(self, node_id: str) -> None:
        if node_id in self.items_by_id:
            self.items_by_id[node_id].update()

    def set_running(self, node_id: str | None) -> None:
        for nid in (self._running_id, node_id):
            if nid in self.items_by_id:
                self.items_by_id[nid].running = nid == node_id
                self.items_by_id[nid].update()
        self._running_id = node_id

    def _on_selection(self) -> None:
        blocks = self.selected_blocks()
        self.selection_node.emit(blocks[0].node_id if len(blocks) == 1 else None)

    # -- dragging stacks -------------------------------------------------

    def block_at(self, pos: QPointF) -> BlockItem | None:
        return next((i for i in self.items(pos) if isinstance(i, BlockItem) and i.isVisible()), None)

    def group_title_at(self, pos: QPointF) -> GroupItem | None:
        return next((g for g in self.groups_by_id.values() if g.title_rect().contains(pos)), None)

    def begin_drag(self, node_id: str, scene_pos: QPointF, single: bool = False) -> None:
        """Detach a block (and everything below it, unless single) into a floating stack."""
        item = self.items_by_id[node_id]
        origin = self.wf.script_of(node_id)
        if single:
            node = self.wf.remove(node_id)
            script = self.wf.add_script([node], (item.x(), item.y()))
        else:
            script = self.wf.detach_tail(node_id, (item.x(), item.y()))
        if script is not origin:  # a detached piece stays in its stack's group
            for g in self.wf.groups:
                if origin["id"] in g["scripts"]:
                    g["scripts"].append(script["id"])
        self._drag = script
        self._drag_offset = scene_pos - QPointF(*script["pos"])
        self.relayout()

    def drag_to(self, scene_pos: QPointF) -> None:
        pos = scene_pos - self._drag_offset
        self._drag["pos"] = [pos.x(), pos.y()]
        self.relayout()
        self._target = self._best_target()
        path = QPainterPath()
        if self._target:
            anchor = self._target[2]
            path.moveTo(anchor + QPointF(0, -1))
            path.lineTo(anchor + QPointF(W * 0.6, -1))
        self._indicator.setPath(path)

    def end_drag(self) -> None:
        self._finish_drag()
        self.changed_workflow.emit()

    def _finish_drag(self) -> None:
        script, self._drag = self._drag, None
        self._indicator.setPath(QPainterPath())
        target = self._best_target(script)
        self._target = None
        if target:
            lst, index, anchor, mode, other = target
            if mode == "above":
                other["pos"] = [anchor.x(), anchor.y() - self.stack_height(script)]
            lst[index:index] = script["blocks"]
            self.wf.remove_script(script)
        self.relayout()

    def _best_target(self, script: dict | None = None):
        script = script or self._drag
        if not script:
            return None
        top_left = QPointF(*script["pos"])
        bottom_left = top_left + QPointF(0, self.stack_height(script))
        best, best_d = None, SNAP
        for t in self._targets(script):
            probe = bottom_left if t[3] == "above" else top_left
            d = QLineF(probe, t[2]).length()
            if d < best_d:
                best, best_d = t, d
        return best

    def _targets(self, dragged: dict) -> list:
        blocks = dragged["blocks"]
        has_hat = registry.get(blocks[0]["type"]).hat
        has_cap = registry.get(blocks[-1]["type"]).cap
        out = []
        for script in self.wf.scripts:
            if script is dragged or not script["blocks"]:
                continue
            first = registry.get(script["blocks"][0]["type"])
            if not has_cap and not first.hat:
                out.append((script["blocks"], 0, QPointF(*script["pos"]), "above", script))
            if not has_hat:
                self._list_targets(script["blocks"], QPointF(*script["pos"]), has_cap, True, out)
        return out

    def _list_targets(self, blocks: list, origin: QPointF, has_cap: bool, top: bool, out: list) -> None:
        for i in range(len(blocks) + 1):
            if top and i == 0:
                continue  # covered by the "above" target
            if i > 0 and registry.get(blocks[i - 1]["type"]).cap:
                continue  # nothing may follow a cap
            if has_cap and i < len(blocks):
                continue  # a stack ending in a cap must go last
            if i < len(blocks):
                anchor = self.items_by_id[blocks[i]["id"]].pos()
            elif blocks:
                last = self.items_by_id[blocks[-1]["id"]]
                anchor = last.pos() + QPointF(0, last.h)
            else:
                anchor = origin
            out.append((blocks, i, anchor, "below", None))
        for node in blocks:
            if "body" in node and not node.get("collapsed"):
                item = self.items_by_id[node["id"]]
                self._list_targets(node["body"], item.pos() + QPointF(ARM, item.header), has_cap, False, out)

    # -- mouse -----------------------------------------------------------

    def mousePressEvent(self, e) -> None:
        if e.button() == Qt.MouseButton.LeftButton:
            if group := self.group_title_at(e.scenePos()):
                starts = {s["id"]: list(s["pos"]) for s in self.wf.scripts if s["id"] in group.group["scripts"]}
                self._group_drag = (group, e.scenePos(), starts)
                return
            if block := self.block_at(e.scenePos()):
                ctrl = e.modifiers() & Qt.KeyboardModifier.ControlModifier
                if ctrl:
                    block.setSelected(not block.isSelected())
                elif not block.isSelected():
                    self.clearSelection()
                    block.setSelected(True)
                self._press = (block.node_id, e.scenePos(), bool(e.modifiers() & Qt.KeyboardModifier.AltModifier))
                return
        super().mousePressEvent(e)

    def mouseMoveEvent(self, e) -> None:
        if self._group_drag:
            group, start, positions = self._group_drag
            delta = e.scenePos() - start
            for s in self.wf.scripts:
                if s["id"] in positions:
                    s["pos"] = [positions[s["id"]][0] + delta.x(), positions[s["id"]][1] + delta.y()]
            self.relayout()
            return
        if self._press and not self._drag:
            node_id, start, single = self._press
            if QLineF(start, e.scenePos()).length() > 5:
                self.begin_drag(node_id, start, single)
        if self._drag:
            self.drag_to(e.scenePos())
            return
        super().mouseMoveEvent(e)

    def mouseReleaseEvent(self, e) -> None:
        if self._group_drag:
            self._group_drag = None
            self.changed_workflow.emit()
            return
        self._press = None
        if self._drag:
            self.end_drag()
            return
        super().mouseReleaseEvent(e)

    def mouseDoubleClickEvent(self, e) -> None:
        if group := self.group_title_at(e.scenePos()):
            self.rename_group_dialog(group.group["id"])
            return
        if (block := self.block_at(e.scenePos())) and block.block.container:
            self.toggle_collapse(block.node_id)
            return
        super().mouseDoubleClickEvent(e)

    def rename_group_dialog(self, group_id: str) -> None:
        group = next(g for g in self.wf.groups if g["id"] == group_id)
        title, ok = QInputDialog.getText(self.views()[0] if self.views() else None, "Rename group", "Title:",
                                         text=group["title"])
        if ok and title.strip():
            self.rename_group(group_id, title.strip())


class WorkflowView(QGraphicsView):
    def __init__(self, scene: WorkflowScene, parent=None) -> None:
        super().__init__(scene, parent)
        self.wf_scene = scene
        self.setRenderHints(QPainter.RenderHint.Antialiasing | QPainter.RenderHint.TextAntialiasing)
        self.setDragMode(QGraphicsView.DragMode.RubberBandDrag)
        self.setTransformationAnchor(QGraphicsView.ViewportAnchor.AnchorUnderMouse)
        self.setViewportUpdateMode(QGraphicsView.ViewportUpdateMode.FullViewportUpdate)
        self.setAcceptDrops(True)
        self._pan: QPointF | None = None
        self.centerOn(W, 150)
        for text, keys, slot in (("Delete", QKeySequence.StandardKey.Delete, scene.remove_selected),
                                 ("Duplicate", "Ctrl+D", scene.duplicate_selected),
                                 ("Copy", QKeySequence.StandardKey.Copy, self.copy),
                                 ("Cut", QKeySequence.StandardKey.Cut, self.cut),
                                 ("Paste", QKeySequence.StandardKey.Paste, self.paste),
                                 ("Select all", QKeySequence.StandardKey.SelectAll, self._select_all),
                                 ("Group", "Ctrl+G", lambda: scene.group_selected()),
                                 ("Fit view", "Ctrl+0", self.fit)):
            act = QAction(text, self, shortcut=QKeySequence(keys),
                          shortcutContext=Qt.ShortcutContext.WidgetWithChildrenShortcut)
            act.triggered.connect(slot)
            self.addAction(act)

    # -- clipboard ------------------------------------------------------------

    def copy(self) -> int:
        nodes = self.wf_scene.selected_nodes()
        if nodes:
            data = QMimeData()
            payload = json.dumps(nodes)
            data.setData(CLIP_MIME, payload.encode())
            data.setText(payload)
            QApplication.clipboard().setMimeData(data)
        return len(nodes)

    def cut(self) -> None:
        if self.copy():
            self.wf_scene.remove_selected()

    def paste(self) -> None:
        data = QApplication.clipboard().mimeData()
        raw = bytes(data.data(CLIP_MIME)).decode() if data.hasFormat(CLIP_MIME) else data.text()
        try:
            nodes = json.loads(raw)
        except ValueError:
            return
        if not isinstance(nodes, list):
            return
        cursor = self.mapFromGlobal(self.cursor().pos())
        pos = self.mapToScene(cursor) if self.viewport().rect().contains(cursor) else self.center()
        try:
            self.wf_scene.paste_nodes(nodes, pos)
        except Exception as e:  # anything odd on the clipboard
            QMessageBox.information(self, "Paste", f"The clipboard does not hold usable blocks ({e}).")

    def can_paste(self) -> bool:
        data = QApplication.clipboard().mimeData()
        return data is not None and data.hasFormat(CLIP_MIME)

    def go_to(self, node_id: str) -> None:
        item = self.wf_scene.items_by_id.get(node_id)
        if item:
            self.wf_scene.clearSelection()
            item.setSelected(True)
            self.centerOn(item)

    def _confirm_clear(self) -> None:
        b = QMessageBox.StandardButton
        if QMessageBox.question(self, "Clear workflow", "Remove every block from the canvas?\n"
                                "(Ctrl+Z undoes it.)", b.Yes | b.No, b.No) == b.Yes:
            self.wf_scene.clear_workflow()

    def _select_all(self) -> None:
        for item in self.wf_scene.items_by_id.values():
            item.setSelected(item.isVisible())

    def center(self) -> QPointF:
        return self.mapToScene(self.viewport().rect().center()) - QPointF(W / 2, H / 2)

    def fit(self) -> None:
        items = [i for i in self.wf_scene.items_by_id.values() if i.isVisible()]
        if not items:
            return
        rect = QRectF()
        for i in items:
            rect = rect.united(i.sceneBoundingRect())
        self.fitInView(rect.adjusted(-40, -40, 40, 40), Qt.AspectRatioMode.KeepAspectRatio)
        scale = self.transform().m11()
        if scale > 1.0 or scale < 0.6:  # never magnify; below 60 % the text is unreadable
            self.resetTransform()
            self.scale(min(1.0, max(scale, 0.6)), min(1.0, max(scale, 0.6)))
            self.centerOn(rect.center())

    def show_top(self) -> None:
        """100 % zoom, scrolled to the top-left of the stacks."""
        items = [i for i in self.wf_scene.items_by_id.values() if i.isVisible()]
        self.resetTransform()
        if items:
            rect = QRectF()
            for i in items:
                rect = rect.united(i.sceneBoundingRect())
            view = self.viewport().rect()
            self.centerOn(rect.left() - 20 + view.width() / 2, rect.top() - 20 + view.height() / 2)

    # -- background & empty state ----------------------------------------

    def drawBackground(self, p: QPainter, rect: QRectF) -> None:
        p.fillRect(rect, QColor(C["bg1"]))
        step = 24
        p.setPen(QPen(QColor(C["line"]), 1.4))
        left, top = int(rect.left()) - int(rect.left()) % step, int(rect.top()) - int(rect.top()) % step
        for x in range(left, int(rect.right()), step):
            for y in range(top, int(rect.bottom()), step):
                p.drawPoint(x, y)

    def drawForeground(self, p: QPainter, rect: QRectF) -> None:
        if self.wf_scene.items_by_id:
            return
        p.save()
        p.resetTransform()
        p.setPen(QColor(C["muted"]))
        p.drawText(self.viewport().rect(), Qt.AlignmentFlag.AlignCenter,
                   "Drag blocks here from the library.\nBlocks snap together; Alt+drag moves a single block.")
        p.restore()

    # -- zoom & pan ------------------------------------------------------

    def wheelEvent(self, e) -> None:
        if e.modifiers() & Qt.KeyboardModifier.ControlModifier:
            factor = 1.15 if e.angleDelta().y() > 0 else 1 / 1.15
            if 0.3 <= self.transform().m11() * factor <= 2.5:
                self.scale(factor, factor)
            return
        super().wheelEvent(e)

    def mousePressEvent(self, e) -> None:
        if e.button() == Qt.MouseButton.MiddleButton:
            self._pan = e.position()
            self.setCursor(Qt.CursorShape.ClosedHandCursor)
            return
        super().mousePressEvent(e)

    def mouseMoveEvent(self, e) -> None:
        if self._pan is not None:
            delta = e.position() - self._pan
            self._pan = e.position()
            self.horizontalScrollBar().setValue(int(self.horizontalScrollBar().value() - delta.x()))
            self.verticalScrollBar().setValue(int(self.verticalScrollBar().value() - delta.y()))
            return
        super().mouseMoveEvent(e)

    def mouseReleaseEvent(self, e) -> None:
        if e.button() == Qt.MouseButton.MiddleButton and self._pan is not None:
            self._pan = None
            self.unsetCursor()
            return
        super().mouseReleaseEvent(e)

    # -- drag & drop from the library ------------------------------------

    def dragEnterEvent(self, e) -> None:
        if e.mimeData().hasFormat(MIME):
            e.acceptProposedAction()

    dragMoveEvent = dragEnterEvent

    def dropEvent(self, e) -> None:
        type_id = bytes(e.mimeData().data(MIME)).decode()
        pos = self.mapToScene(e.position().toPoint()) - QPointF(NX + NW / 2, H / 2)
        self.wf_scene.add_block(type_id, pos)
        e.acceptProposedAction()

    # -- context menu ----------------------------------------------------

    def contextMenuEvent(self, e) -> None:
        sc = self.wf_scene
        scene_pos = self.mapToScene(e.pos())
        menu = QMenu(self)
        block = sc.block_at(scene_pos)
        group = next((g for g in sc.groups_by_id.values() if g.title_rect().contains(scene_pos)), None)
        if block:
            if not block.isSelected():
                sc.clearSelection()
                block.setSelected(True)
            node = block.node
            single = len(sc.selected_blocks()) == 1
            if single and not block.block.hat:
                _act(menu, "Run this block", lambda: sc.run_requested.emit("block", block.node_id), icon_name="play")
                _act(menu, "Run from here", lambda: sc.run_requested.emit("from", block.node_id))
                menu.addSeparator()
            _act(menu, "Copy", self.copy, "Ctrl+C")
            _act(menu, "Cut", self.cut, "Ctrl+X")
            _act(menu, "Duplicate", sc.duplicate_selected, "Ctrl+D")
            _act(menu, "Expand" if node.get("collapsed") else "Collapse",
                 lambda: sc.toggle_collapse(block.node_id), icon_name="expand" if node.get("collapsed") else "collapse")
            errors = menu.addMenu("On error")
            _act(errors, "Stop the workflow", lambda: sc.set_on_error(block.node_id, "stop"),
                 checked=node.get("on_error", "stop") == "stop")
            _act(errors, "Skip it and continue", lambda: sc.set_on_error(block.node_id, "continue"),
                 checked=node.get("on_error") == "continue")
            _act(errors, "Restart the workflow", lambda: sc.set_on_error(block.node_id, "restart"),
                 checked=node.get("on_error") == "restart")
            menu.addSeparator()
            _act(menu, "Group stack", lambda: sc.group_selected(), "Ctrl+G")
            menu.addSeparator()
            _act(menu, "Delete", sc.remove_selected, "Del", "trash")
        elif group:
            gid = group.group["id"]
            _act(menu, "Rename group…", lambda: sc.rename_group_dialog(gid))
            _act(menu, "Ungroup", lambda: sc.ungroup(gid))
        else:
            add = menu.addMenu("Add block")
            for category in CATEGORIES:
                sub = add.addMenu(category)
                for b in sorted((b for b in registry.all_blocks() if b.category == category), key=lambda b: b.title):
                    _act(sub, b.title, lambda t=b.type_id: sc.add_block(t, scene_pos), icon_name=b.block_icon())
            menu.addSeparator()
            _act(menu, "Paste", self.paste, "Ctrl+V").setEnabled(self.can_paste())
            _act(menu, "Select all", self._select_all, "Ctrl+A")
            _act(menu, "Group selected", lambda: sc.group_selected(), "Ctrl+G").setEnabled(bool(sc.selected_blocks()))
            _act(menu, "Tidy stacks", sc.tidy)
            _act(menu, "Fit view", self.fit, "Ctrl+0")
            menu.addSeparator()
            _act(menu, "Clear workflow…", self._confirm_clear, icon_name="trash").setEnabled(bool(sc.items_by_id))
        menu.exec(e.globalPos())
