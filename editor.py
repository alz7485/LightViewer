from __future__ import annotations

import math
from pathlib import Path

from PySide6.QtCore import QPointF, QRect, QRectF, QSize, Qt, Signal
from PySide6.QtGui import (
    QAction, QColor, QFont, QFontDatabase, QImage, QKeySequence, QPainter,
    QPainterPath, QPen, QPixmap, QPolygonF, QShortcut,
)
from PySide6.QtWidgets import (
    QApplication, QColorDialog, QComboBox, QFileDialog, QFontComboBox,
    QGraphicsItem, QGraphicsPixmapItem, QGraphicsScene, QGraphicsView,
    QInputDialog, QMainWindow, QMessageBox, QProgressDialog, QPushButton,
    QSizePolicy, QSpinBox, QToolBar, QWidget,
)

from app_config import OBJECT_PRESETS_PATH, load_json, save_json


class AnnotationItem(QGraphicsItem):
    HANDLE = 20.0
    ROTATE_OFFSET = 38.0

    def __init__(self, kind: str, rect: QRectF, color: QColor, text: str = "", font: QFont | None = None):
        super().__init__()
        normalized = rect.normalized()
        self.kind = kind
        self.width = max(20.0, normalized.width())
        self.height = max(20.0, normalized.height())
        self.color = QColor(color)
        self.text = text
        self.font = QFont(font or QFont("Yu Gothic UI", 18))
        self.setPos(normalized.topLeft())
        self.setFlags(QGraphicsItem.GraphicsItemFlag.ItemIsSelectable | QGraphicsItem.GraphicsItemFlag.ItemIsMovable | QGraphicsItem.GraphicsItemFlag.ItemIsFocusable)
        self.setTransformOriginPoint(self.width / 2, self.height / 2)
        self._operation = ""
        self._creating = False
        self.changed_callback = None
        self._drag_scene_start = QPointF()
        self._drag_start_pos = QPointF()
        self._peer_starts: list[tuple[AnnotationItem, QPointF]] = []

    def rect(self) -> QRectF:
        return QRectF(0, 0, self.width, self.height)

    def boundingRect(self) -> QRectF:
        margin = self.HANDLE / 2 + 4
        return self.rect().adjusted(-margin, -(self.ROTATE_OFFSET + margin), margin, margin)

    def shape(self) -> QPainterPath:
        # Include controls in the clickable shape.  Previously the rotation
        # handle was painted outside the item's shape, so it could not receive
        # mouse events.
        path = QPainterPath()
        path.addRect(self.rect().adjusted(-5, -5, 5, 5))
        path.addRect(self._resize_handle().adjusted(-3, -3, 3, 3))
        path.addEllipse(self._rotate_handle().adjusted(-3, -3, 3, 3))
        return path

    def _resize_handle(self) -> QRectF:
        return QRectF(self.width - self.HANDLE / 2, self.height - self.HANDLE / 2, self.HANDLE, self.HANDLE)

    def _rotate_handle(self) -> QRectF:
        return QRectF(
            self.width / 2 - self.HANDLE / 2,
            -self.ROTATE_OFFSET - self.HANDLE / 2,
            self.HANDLE,
            self.HANDLE,
        )

    def paint(self, painter: QPainter, option, widget=None) -> None:
        # The real annotation is always drawn, including while it is being
        # created or selected.  Controls are a separate, translucent overlay.
        painter.save()
        painter.setRenderHint(QPainter.RenderHint.Antialiasing, True)
        pen = QPen(self.color, 4.0, Qt.PenStyle.SolidLine, Qt.PenCapStyle.RoundCap, Qt.PenJoinStyle.RoundJoin)
        painter.setPen(pen)
        rect = self.rect().adjusted(2, 2, -2, -2)
        if self.kind == "ellipse":
            painter.drawEllipse(rect)
        elif self.kind == "rect":
            painter.drawRect(rect)
        elif self.kind in ("line", "arrow"):
            start, end = QPointF(2, 2), QPointF(self.width - 2, self.height - 2)
            painter.drawLine(start, end)
            if self.kind == "arrow":
                angle = math.atan2(end.y() - start.y(), end.x() - start.x())
                length = min(28.0, max(14.0, min(self.width, self.height) * 0.3))
                left = end - QPointF(math.cos(angle - 0.55) * length, math.sin(angle - 0.55) * length)
                right = end - QPointF(math.cos(angle + 0.55) * length, math.sin(angle + 0.55) * length)
                painter.setBrush(self.color); painter.drawPolygon(QPolygonF([end, left, right]))
        elif self.kind in ("text", "text_fill"):
            painter.setBrush(Qt.GlobalColor.white if self.kind == "text_fill" else Qt.BrushStyle.NoBrush)
            painter.drawRect(rect)
            painter.setFont(self.font)
            painter.drawText(rect.adjusted(8, 6, -8, -6), Qt.AlignmentFlag.AlignLeft | Qt.AlignmentFlag.AlignVCenter | Qt.TextFlag.TextWordWrap, self.text)
        painter.restore()
        if self.isSelected() and not self._creating:
            painter.save()
            guide = QColor("#1976d2"); guide.setAlpha(185)
            painter.setPen(QPen(guide, 1.5, Qt.PenStyle.DashLine)); painter.setBrush(QColor(255, 255, 255, 235))
            painter.drawRect(self.rect())
            painter.drawLine(QPointF(self.width / 2, 0), QPointF(self.width / 2, -self.ROTATE_OFFSET + self.HANDLE / 2))
            painter.drawRect(self._resize_handle()); painter.drawEllipse(self._rotate_handle())
            painter.restore()

    def mousePressEvent(self, event) -> None:
        if self.isSelected() and self._resize_handle().contains(event.pos()):
            self._operation = "resize"; event.accept(); return
        if self.isSelected() and self._rotate_handle().contains(event.pos()):
            self._operation = "rotate"; event.accept(); return
        self._operation = "move"
        super().mousePressEvent(event)
        self._drag_scene_start = event.scenePos()
        self._drag_start_pos = QPointF(self.pos())
        self._peer_starts = [
            (item, QPointF(item.pos())) for item in self.scene().selectedItems()
            if isinstance(item, AnnotationItem) and item is not self
        ] if self.scene() else []

    def mouseMoveEvent(self, event) -> None:
        if self._operation == "resize":
            self.prepareGeometryChange()
            self.width = max(20.0, event.pos().x())
            self.height = max(20.0, event.pos().y())
            self.setTransformOriginPoint(self.width / 2, self.height / 2)
            self.update(); event.accept(); return
        if self._operation == "rotate":
            center = self.mapToScene(self.rect().center())
            delta = event.scenePos() - center
            self.setRotation(math.degrees(math.atan2(delta.y(), delta.x())) + 90.0)
            event.accept(); return
        if self._operation == "move":
            delta = event.scenePos() - self._drag_scene_start
            self.setPos(self._drag_start_pos + delta)
            for item, start in self._peer_starts:
                item.setPos(start + delta)
            event.accept(); return
        super().mouseMoveEvent(event)

    def mouseReleaseEvent(self, event) -> None:
        changed = bool(self._operation)
        self._operation = ""
        super().mouseReleaseEvent(event)
        if changed and self.changed_callback:
            self.changed_callback()

    def to_state(self) -> dict:
        return {
            "kind": self.kind, "x": self.pos().x(), "y": self.pos().y(),
            "width": self.width, "height": self.height, "rotation": self.rotation(),
            "color": self.color.name(QColor.NameFormat.HexArgb), "text": self.text,
            "font_family": self.font.family(), "font_size": self.font.pointSize(),
            "font_weight": self.font.weight(), "font_italic": self.font.italic(),
            "z": self.zValue(),
        }

    @classmethod
    def from_state(cls, state: dict) -> "AnnotationItem":
        font = QFont(str(state.get("font_family", "Yu Gothic UI")), int(state.get("font_size", 18)))
        font.setWeight(QFont.Weight(int(state.get("font_weight", int(QFont.Weight.Normal)))))
        font.setItalic(bool(state.get("font_italic", False)))
        item = cls(
            str(state.get("kind", "rect")),
            QRectF(float(state.get("x", 0)), float(state.get("y", 0)), float(state.get("width", 80)), float(state.get("height", 60))),
            QColor(str(state.get("color", "#e00000"))), str(state.get("text", "")), font,
        )
        item.setRotation(float(state.get("rotation", 0))); item.setZValue(float(state.get("z", 0)))
        return item

    def set_color(self, color: QColor) -> None:
        self.color = QColor(color); self.update()

    def set_text_font(self, family: str, size: int) -> None:
        self.font.setFamily(family); self.font.setPointSize(size); self.update()


class EditorCanvas(QGraphicsView):
    item_created = Signal(object)
    content_changed = Signal()

    def __init__(self, parent=None):
        super().__init__(parent)
        self.setScene(QGraphicsScene(self))
        self.setRenderHints(QPainter.RenderHint.Antialiasing | QPainter.RenderHint.SmoothPixmapTransform)
        self.setDragMode(QGraphicsView.DragMode.RubberBandDrag)
        self.setRubberBandSelectionMode(Qt.ItemSelectionMode.IntersectsItemShape)
        self.setBackgroundBrush(QColor("#30343a"))
        self.tool = "select"
        self.color = QColor("#e00000")
        self.text_font = QFont("Yu Gothic UI", 18)
        self._base_item: QGraphicsPixmapItem | None = None
        self._creating: AnnotationItem | None = None
        self._start = QPointF()

    def load_image(self, image) -> None:
        self.scene().clear()
        pixmap = QPixmap.fromImage(image)
        self._base_item = self.scene().addPixmap(pixmap)
        self._base_item.setZValue(-1000)
        self._base_item.setAcceptedMouseButtons(Qt.MouseButton.NoButton)
        self.scene().setSceneRect(QRectF(pixmap.rect()))
        self.fitInView(self.scene().sceneRect(), Qt.AspectRatioMode.KeepAspectRatio)

    def set_tool(self, tool: str) -> None:
        self.tool = tool
        self.setDragMode(QGraphicsView.DragMode.RubberBandDrag if tool == "select" else QGraphicsView.DragMode.NoDrag)
        self.viewport().setCursor(Qt.CursorShape.ArrowCursor if tool == "select" else Qt.CursorShape.CrossCursor)

    def mousePressEvent(self, event) -> None:
        scene_pos = self.mapToScene(event.position().toPoint())
        hit = self.scene().itemAt(scene_pos, self.transform())
        if self.tool == "select" or isinstance(hit, AnnotationItem):
            super().mousePressEvent(event); return
        if event.button() != Qt.MouseButton.LeftButton or not self.scene().sceneRect().contains(scene_pos):
            super().mousePressEvent(event); return
        self.scene().clearSelection()
        self._start = scene_pos
        self._creating = AnnotationItem(self.tool, QRectF(scene_pos, scene_pos + QPointF(20, 20)), self.color, "", self.text_font)
        self._creating._creating = True
        self._creating.changed_callback = self.content_changed.emit
        self.scene().addItem(self._creating); self._creating.setSelected(True)
        event.accept()

    def mouseMoveEvent(self, event) -> None:
        if self._creating:
            end = self.mapToScene(event.position().toPoint())
            rect = QRectF(self._start, end).normalized()
            self._creating.prepareGeometryChange(); self._creating.setPos(rect.topLeft()); self._creating.width = max(20.0, rect.width()); self._creating.height = max(20.0, rect.height()); self._creating.setTransformOriginPoint(self._creating.width / 2, self._creating.height / 2); self._creating.update(); event.accept(); return
        super().mouseMoveEvent(event)

    def mouseReleaseEvent(self, event) -> None:
        if self._creating:
            item = self._creating; self._creating = None
            if item.kind in ("text", "text_fill"):
                text, ok = QInputDialog.getMultiLineText(self, "テキスト", "入力する文字")
                if not ok or not text:
                    self.scene().removeItem(item); event.accept(); return
                item.text = text; item.update()
            item._creating = False; item.update()
            self.item_created.emit(item); event.accept(); return
        super().mouseReleaseEvent(event)

    def wheelEvent(self, event) -> None:
        if event.modifiers() & Qt.KeyboardModifier.ControlModifier:
            factor = 1.15 if event.angleDelta().y() > 0 else 1 / 1.15
            self.scale(factor, factor); event.accept(); return
        super().wheelEvent(event)

    def selected_annotations(self) -> list[AnnotationItem]:
        return [item for item in self.scene().selectedItems() if isinstance(item, AnnotationItem)]

    def annotations(self) -> list[AnnotationItem]:
        return sorted(
            [item for item in self.scene().items() if isinstance(item, AnnotationItem)],
            key=lambda item: item.zValue(),
        )

    def annotation_states(self) -> list[dict]:
        return [item.to_state() for item in self.annotations()]

    def restore_annotation_states(self, states: list[dict]) -> None:
        for item in self.annotations(): self.scene().removeItem(item)
        for state in states:
            item = AnnotationItem.from_state(state); item.changed_callback = self.content_changed.emit
            self.scene().addItem(item)

    def select_all_annotations(self) -> None:
        for item in self.annotations(): item.setSelected(True)

    def add_annotation_states(self, states: list[dict], center_in_view: bool = True) -> list[AnnotationItem]:
        if not states: return []
        min_x = min(float(state.get("x", 0)) for state in states)
        min_y = min(float(state.get("y", 0)) for state in states)
        max_x = max(float(state.get("x", 0)) + float(state.get("width", 20)) for state in states)
        max_y = max(float(state.get("y", 0)) + float(state.get("height", 20)) for state in states)
        if center_in_view:
            center = self.mapToScene(self.viewport().rect().center())
            offset = QPointF(center.x() - (max_x - min_x) / 2 - min_x, center.y() - (max_y - min_y) / 2 - min_y)
        else:
            offset = QPointF()
        self.scene().clearSelection(); created = []
        top_z = max([item.zValue() for item in self.annotations()] + [0]) + 1
        for index, source in enumerate(states):
            state = dict(source); state["x"] = float(state.get("x", 0)) + offset.x(); state["y"] = float(state.get("y", 0)) + offset.y(); state["z"] = top_z + index
            item = AnnotationItem.from_state(state); item.changed_callback = self.content_changed.emit
            self.scene().addItem(item); item.setSelected(True); created.append(item)
        self.content_changed.emit()
        return created

    def delete_selected(self) -> None:
        selected = self.selected_annotations()
        for item in selected: self.scene().removeItem(item)
        if selected: self.content_changed.emit()

    def render_image(self, base_override: QImage | None = None):
        bounds = self.scene().sceneRect()
        if base_override is not None and not base_override.isNull():
            output = base_override.convertToFormat(QImage.Format.Format_ARGB32)
        else:
            output = QImage(max(1, int(bounds.width())), max(1, int(bounds.height())), QImage.Format.Format_ARGB32)
            output.fill(Qt.GlobalColor.white)
        selected = self.scene().selectedItems(); self.scene().clearSelection()
        base_visible = self._base_item.isVisible() if self._base_item else False
        if base_override is not None and self._base_item: self._base_item.setVisible(False)
        painter = QPainter(output); painter.setRenderHint(QPainter.RenderHint.Antialiasing, True); painter.setRenderHint(QPainter.RenderHint.SmoothPixmapTransform, True)
        self.scene().render(painter, QRectF(output.rect()), bounds); painter.end()
        if self._base_item: self._base_item.setVisible(base_visible)
        for item in selected: item.setSelected(True)
        return output


class ImageEditorWindow(QMainWindow):
    return_requested = Signal()

    def __init__(self, parent=None):
        super().__init__(parent)
        self.setWindowTitle("LightViewer - 編集")
        self._suggested_path = str(Path.cwd() / "edited.png")
        self._saved_path: str | None = None
        self._dirty = False
        self._history: list[list[dict]] = []
        self._history_index = -1
        self._saved_history_index = -1
        self._restoring_history = False
        self._full_resolution_source = None
        self._full_resolution_index = 0
        self._export_full_resolution = False
        self._shortcuts: list[QShortcut] = []
        raw_presets = load_json(OBJECT_PRESETS_PATH, {"presets": []})
        self.object_presets: list[dict] = list(raw_presets.get("presets", [])) if isinstance(raw_presets, dict) else []
        self.canvas = EditorCanvas()
        self.setCentralWidget(self.canvas)
        toolbar = QToolBar("編集"); toolbar.setMovable(False); toolbar.setFloatable(False); toolbar.setIconSize(QSize(18, 18))
        self.toolbar = toolbar; self.addToolBar(toolbar)

        back = QAction("↩", self); back.setToolTip("編集を閉じてプレビューへ戻る"); back.triggered.connect(self._return); toolbar.addAction(back)
        self.undo_action = QAction("↶", self); self.undo_action.setToolTip("元に戻す（Ctrl+Z）"); self.undo_action.triggered.connect(self.undo); toolbar.addAction(self.undo_action)
        self.redo_action = QAction("↷", self); self.redo_action.setToolTip("やり直し（Ctrl+Y）"); self.redo_action.triggered.connect(self.redo); toolbar.addAction(self.redo_action)
        tools = [("🖱", "select", "選択・移動"), ("◯", "ellipse", "赤い線の丸"), ("□", "rect", "赤い線の四角"), ("╱", "line", "赤い直線"), ("➜", "arrow", "赤い矢印線"), ("T□", "text", "枠線だけのテキストボックス"), ("T▣", "text_fill", "白塗りつぶしのテキストボックス")]
        self.tool_actions: dict[str, QAction] = {}
        for label, key, tip in tools:
            action = QAction(label, self); action.setCheckable(True); action.setToolTip(tip); action.triggered.connect(lambda checked=False, value=key: self._set_tool(value)); toolbar.addAction(action); self.tool_actions[key] = action
        self.tool_actions["select"].setChecked(True)
        toolbar.addSeparator()
        color = QAction("🎨", self); color.setToolTip("選択オブジェクトと次に追加するオブジェクトの色"); color.triggered.connect(self._pick_color); toolbar.addAction(color)
        self.font_combo = QFontComboBox(); self.font_combo.setFixedWidth(170); self.font_combo.setToolTip("テキストのフォント"); self.font_combo.setCurrentFont(QFont("Yu Gothic UI")); self.font_combo.currentFontChanged.connect(self._font_changed); toolbar.addWidget(self.font_combo)
        self.font_size = QSpinBox(); self.font_size.setFixedWidth(78); self.font_size.setRange(6, 144); self.font_size.setValue(18); self.font_size.setSuffix(" pt"); self.font_size.setToolTip("テキストのフォントサイズ"); self.font_size.valueChanged.connect(self._font_changed); toolbar.addWidget(self.font_size)
        toolbar.addSeparator()
        self.preset_combo = QComboBox(); self.preset_combo.setFixedWidth(140); self.preset_combo.setToolTip("登録済み編集オブジェクト"); toolbar.addWidget(self.preset_combo)
        register_preset = QAction("★＋", self); register_preset.setToolTip("選択オブジェクトを登録"); register_preset.triggered.connect(self.register_selected_preset); toolbar.addAction(register_preset)
        recall_preset = QAction("★", self); recall_preset.setToolTip("選択中の登録オブジェクトを呼び出す"); recall_preset.triggered.connect(self.recall_selected_preset); toolbar.addAction(recall_preset)
        delete_preset = QAction("★−", self); delete_preset.setToolTip("選択中の登録オブジェクトを削除"); delete_preset.triggered.connect(self.delete_selected_preset); toolbar.addAction(delete_preset)
        spacer = QWidget(); spacer.setSizePolicy(QSizePolicy.Policy.Expanding, QSizePolicy.Policy.Preferred); toolbar.addWidget(spacer)
        delete = QAction("🗑️", self); delete.setToolTip("選択オブジェクトを削除（Delete）"); delete.triggered.connect(self.canvas.delete_selected); toolbar.addAction(delete)
        save = QAction("💾", self); save.setToolTip("保存（初回は保存先を選択）"); save.triggered.connect(self.save); toolbar.addAction(save)
        save_as = QAction("💾＋", self); save_as.setToolTip("名前を付けて保存"); save_as.triggered.connect(self.save_as); toolbar.addAction(save_as)
        self.canvas.scene().selectionChanged.connect(self._selection_changed)
        self.canvas.item_created.connect(lambda _item: self._record_history())
        self.canvas.content_changed.connect(self._record_history)
        self._refresh_preset_combo(); self._update_history_actions()

    def load_image(self, image, title: str, source_path: str | Path | None = None, full_resolution_source=None, page_index: int = 0, export_full_resolution: bool = False) -> None:
        self.canvas.load_image(image)
        stem = Path(title).stem or "image"
        base = Path(source_path).parent if source_path else Path.cwd()
        self._suggested_path = str(base / f"{stem}_edited.png")
        self._saved_path: str | None = None
        self._full_resolution_source = full_resolution_source
        self._full_resolution_index = int(page_index)
        self._export_full_resolution = bool(export_full_resolution)
        self._history = [[]]; self._history_index = 0; self._saved_history_index = 0; self._dirty = False
        self._update_history_actions()
        self.setWindowTitle(f"LightViewer - 編集 - {title}")

    def fit_image(self) -> None:
        self.canvas.fitInView(self.canvas.scene().sceneRect(), Qt.AspectRatioMode.KeepAspectRatio)

    def set_background(self, color: str) -> None:
        self.canvas.setBackgroundBrush(QColor(color))

    def _required_toolbar_width(self) -> int:
        widths = []
        for action in self.toolbar.actions():
            widget = self.toolbar.widgetForAction(action)
            if widget and action.isVisible(): widths.append(max(0, widget.sizeHint().width()))
        return sum(widths) + max(0, len(widths) - 1) * self.toolbar.layout().spacing() + 24

    def prepare_for_preview(self, preview_geometry: QRect) -> None:
        target_screen = QApplication.screenAt(preview_geometry.center()) or self.screen() or QApplication.primaryScreen()
        screen = target_screen.availableGeometry()
        width = min(screen.width(), max(preview_geometry.width(), self._required_toolbar_width()))
        height = min(screen.height(), preview_geometry.height())
        center = preview_geometry.center()
        x = max(screen.left(), min(center.x() - width // 2, screen.right() - width + 1))
        y = max(screen.top(), min(preview_geometry.top(), screen.bottom() - height + 1))
        self.setGeometry(x, y, width, height)

    def finalize_layout(self) -> None:
        # A second measurement after native widgets are polished catches font
        # and DPI differences on Windows without changing the preview height.
        geometry = self.geometry()
        self.prepare_for_preview(geometry)
        self.fit_image()

    def _mark_dirty(self) -> None:
        self._dirty = True

    def _record_history(self) -> None:
        if self._restoring_history: return
        state = self.canvas.annotation_states()
        if 0 <= self._history_index < len(self._history) and state == self._history[self._history_index]: return
        if self._history_index + 1 < len(self._history):
            if self._saved_history_index > self._history_index: self._saved_history_index = -1
            self._history = self._history[:self._history_index + 1]
        self._history.append(state); self._history_index += 1
        if len(self._history) > 100:
            self._history.pop(0); self._history_index -= 1; self._saved_history_index -= 1
        self._dirty = self._history_index != self._saved_history_index
        self._update_history_actions()

    def _restore_history(self) -> None:
        if not 0 <= self._history_index < len(self._history): return
        self._restoring_history = True
        try: self.canvas.restore_annotation_states(self._history[self._history_index])
        finally: self._restoring_history = False
        self._dirty = self._history_index != self._saved_history_index
        self._update_history_actions()

    def _update_history_actions(self) -> None:
        self.undo_action.setEnabled(self._history_index > 0)
        self.redo_action.setEnabled(self._history_index + 1 < len(self._history))

    def undo(self) -> None:
        if self._history_index > 0: self._history_index -= 1; self._restore_history()

    def redo(self) -> None:
        if self._history_index + 1 < len(self._history): self._history_index += 1; self._restore_history()

    def set_key_bindings(self, bindings: dict) -> None:
        for shortcut in self._shortcuts: shortcut.setParent(None)
        self._shortcuts.clear()
        callbacks = {
            "editor_undo": self.undo, "editor_redo": self.redo,
            "editor_save": self.save, "editor_save_as": self.save_as,
            "editor_select_all": self.canvas.select_all_annotations,
            "editor_tool_select": lambda: self._set_tool("select"),
            "editor_tool_ellipse": lambda: self._set_tool("ellipse"),
            "editor_tool_rect": lambda: self._set_tool("rect"),
            "editor_tool_line": lambda: self._set_tool("line"),
            "editor_tool_arrow": lambda: self._set_tool("arrow"),
            "editor_tool_text": lambda: self._set_tool("text"),
            "editor_tool_text_fill": lambda: self._set_tool("text_fill"),
        }
        used = set()
        for name, callback in callbacks.items():
            for value in bindings.get(name, []):
                sequence = QKeySequence(value); canonical = sequence.toString()
                if sequence.isEmpty() or canonical in used: continue
                shortcut = QShortcut(sequence, self); shortcut.setContext(Qt.ShortcutContext.WindowShortcut); shortcut.activated.connect(callback)
                self._shortcuts.append(shortcut); used.add(canonical)

    def _set_tool(self, tool: str) -> None:
        for key, action in self.tool_actions.items(): action.setChecked(key == tool)
        self.canvas.set_tool(tool)

    def _selection_changed(self) -> None:
        selected = self.canvas.selected_annotations()
        if selected:
            first = selected[0]
            self.font_combo.blockSignals(True); self.font_combo.setCurrentFont(first.font); self.font_combo.blockSignals(False)
            self.font_size.blockSignals(True); self.font_size.setValue(first.font.pointSize()); self.font_size.blockSignals(False)

    def _pick_color(self) -> None:
        color = QColorDialog.getColor(self.canvas.color, self, "色を選択")
        if not color.isValid(): return
        self.canvas.color = color
        selected = self.canvas.selected_annotations()
        for item in selected: item.set_color(color)
        if selected: self._record_history()

    def _font_changed(self, *_args) -> None:
        family = self.font_combo.currentFont().family(); size = self.font_size.value()
        self.canvas.text_font = QFont(family, size)
        selected = self.canvas.selected_annotations()
        for item in selected: item.set_text_font(family, size)
        if selected: self._record_history()

    def keyPressEvent(self, event) -> None:
        if event.key() == Qt.Key.Key_Delete: self.canvas.delete_selected(); event.accept(); return
        if event.matches(QKeySequence.StandardKey.SelectAll): self.canvas.select_all_annotations(); event.accept(); return
        super().keyPressEvent(event)

    def _refresh_preset_combo(self, selected_name: str = "") -> None:
        self.preset_combo.blockSignals(True); self.preset_combo.clear()
        for preset in self.object_presets: self.preset_combo.addItem(str(preset.get("name", "登録オブジェクト")))
        if selected_name:
            index = self.preset_combo.findText(selected_name)
            if index >= 0: self.preset_combo.setCurrentIndex(index)
        self.preset_combo.blockSignals(False)

    def _save_presets(self) -> None:
        save_json(OBJECT_PRESETS_PATH, {"presets": self.object_presets})

    def register_selected_preset(self) -> None:
        selected = self.canvas.selected_annotations()
        if not selected:
            QMessageBox.information(self, "オブジェクト登録", "登録するオブジェクトを選択してください。")
            return
        name, ok = QInputDialog.getText(self, "オブジェクト登録", "登録名")
        name = name.strip()
        if not ok or not name: return
        states = [item.to_state() for item in sorted(selected, key=lambda value: value.zValue())]
        min_x = min(float(state["x"]) for state in states); min_y = min(float(state["y"]) for state in states)
        for state in states: state["x"] = float(state["x"]) - min_x; state["y"] = float(state["y"]) - min_y
        existing = next((preset for preset in self.object_presets if str(preset.get("name", "")).casefold() == name.casefold()), None)
        if existing:
            if QMessageBox.question(self, "オブジェクト登録", f"「{name}」を上書きしますか？") != QMessageBox.StandardButton.Yes: return
            existing["name"] = name; existing["states"] = states
        else:
            self.object_presets.append({"name": name, "states": states})
        self._save_presets(); self._refresh_preset_combo(name); self.statusBar().showMessage(f"「{name}」を登録しました", 3000)

    def recall_selected_preset(self) -> None:
        index = self.preset_combo.currentIndex()
        if not 0 <= index < len(self.object_presets): return
        preset = self.object_presets[index]; states = preset.get("states", [])
        if isinstance(states, list):
            self.canvas.add_annotation_states(states, center_in_view=True)
            self.statusBar().showMessage(f"「{preset.get('name', '')}」を呼び出しました", 2500)

    def delete_selected_preset(self) -> None:
        index = self.preset_combo.currentIndex()
        if not 0 <= index < len(self.object_presets): return
        name = str(self.object_presets[index].get("name", "登録オブジェクト"))
        if QMessageBox.question(self, "登録オブジェクトを削除", f"「{name}」を削除しますか？") != QMessageBox.StandardButton.Yes: return
        self.object_presets.pop(index); self._save_presets(); self._refresh_preset_combo()

    def _save_to_path(self, path: str) -> bool:
        suffix = Path(path).suffix.casefold()
        fmt = {".jpg": "JPG", ".jpeg": "JPG", ".webp": "WEBP"}.get(suffix, "PNG")
        base_override = None
        if self._export_full_resolution and self._full_resolution_source is not None:
            progress = QProgressDialog("元解像度の画像を読み込んでいます…", "キャンセル", 0, 0, self)
            progress.setWindowTitle("高解像度で保存"); progress.setWindowModality(Qt.WindowModality.WindowModal); progress.show(); QApplication.processEvents()
            size = self._full_resolution_source.page_size(self._full_resolution_index)
            megapixels = max(1, math.ceil(size.width() * size.height() / 1_000_000)) if size.isValid() else 100
            base_override = self._full_resolution_source.read_page(self._full_resolution_index, size if size.isValid() else QSize(12000, 12000), megapixels)
            QApplication.processEvents(); canceled = progress.wasCanceled(); progress.close()
            if canceled: return False
            if base_override.isNull():
                QMessageBox.warning(self, "保存できません", "元解像度の画像を読み込めませんでした。")
                return False
        output = self.canvas.render_image(base_override)
        if not output.save(path, fmt, 95):
            QMessageBox.warning(self, "保存できません", "画像を保存できませんでした。")
            return False
        self._saved_path = path
        self._suggested_path = path
        self._saved_history_index = self._history_index; self._dirty = False
        self.statusBar().showMessage(f"保存しました: {Path(path).name}", 3000)
        return True

    def save(self) -> bool:
        return self._save_to_path(self._saved_path) if self._saved_path else self.save_as()

    def save_as(self) -> bool:
        path, selected_filter = QFileDialog.getSaveFileName(self, "編集画像を保存", self._suggested_path, "PNG (*.png);;JPEG (*.jpg *.jpeg);;WebP (*.webp)")
        if not path: return False
        suffix = Path(path).suffix.casefold()
        if not suffix:
            path += ".png"; suffix = ".png"
        return self._save_to_path(path)

    def _confirm_return(self) -> bool:
        if not self._dirty:
            return True
        box = QMessageBox(self)
        box.setWindowTitle("未保存の編集があります")
        box.setIcon(QMessageBox.Icon.Warning)
        box.setText("編集内容が保存されていません。")
        box.setInformativeText("保存してからビューワーに戻りますか？")
        save_button = box.addButton("保存", QMessageBox.ButtonRole.AcceptRole)
        discard_button = box.addButton("保存せず戻る", QMessageBox.ButtonRole.DestructiveRole)
        box.addButton("キャンセル", QMessageBox.ButtonRole.RejectRole)
        box.setDefaultButton(save_button)
        box.exec()
        clicked = box.clickedButton()
        if clicked is save_button:
            return self.save()
        return clicked is discard_button

    def _return(self) -> None:
        if not self._confirm_return(): return
        self.hide(); self.return_requested.emit()

    def closeEvent(self, event) -> None:
        if not self._confirm_return():
            event.ignore(); return
        event.accept(); self.return_requested.emit()
