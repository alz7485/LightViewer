from __future__ import annotations

from PySide6.QtCore import QMimeData, QPoint, QRect, QRectF, QSize, Qt, Signal
from PySide6.QtGui import QColor, QDrag, QPainter, QPixmap, QWheelEvent
from PySide6.QtWidgets import (
    QAbstractItemView,
    QFrame, QGraphicsPixmapItem, QGraphicsScene, QGraphicsView, QListWidget,
    QStyledItemDelegate, QStyle, QTreeWidget,
)


THUMBNAIL_LANDSCAPE_ROLE = int(Qt.ItemDataRole.UserRole) + 1
BOOK_IDS_MIME = "application/x-light-image-viewer-book-ids"
ADD_SHELF_ITEM_ID = "__add_shelf__"


class ImageView(QGraphicsView):
    next_requested = Signal()
    previous_requested = Signal()
    paths_dropped = Signal(list)

    def __init__(self, parent=None):
        super().__init__(parent)
        self.setScene(QGraphicsScene(self))
        self._item = QGraphicsPixmapItem()
        self._item.setTransformationMode(Qt.TransformationMode.SmoothTransformation)
        self.scene().addItem(self._item)
        self._fit_mode = True
        self._zoom = 1.0
        self._drag_origin = QPoint()
        self.setAlignment(Qt.AlignmentFlag.AlignCenter)
        self.setDragMode(QGraphicsView.DragMode.ScrollHandDrag)
        self.setFrameShape(QFrame.Shape.NoFrame)
        self.setBackgroundBrush(QColor("#17191c"))
        self.setTransformationAnchor(QGraphicsView.ViewportAnchor.AnchorUnderMouse)
        self.setResizeAnchor(QGraphicsView.ViewportAnchor.AnchorViewCenter)
        self.setRenderHints(QPainter.RenderHint.Antialiasing | QPainter.RenderHint.SmoothPixmapTransform)
        self.setAcceptDrops(True)
        self.viewport().setAcceptDrops(True)

    def dragEnterEvent(self, event) -> None:
        if event.mimeData().hasUrls():
            event.acceptProposedAction()
        else:
            super().dragEnterEvent(event)

    def dragMoveEvent(self, event) -> None:
        if event.mimeData().hasUrls():
            event.acceptProposedAction()
        else:
            super().dragMoveEvent(event)

    def dropEvent(self, event) -> None:
        paths = [url.toLocalFile() for url in event.mimeData().urls() if url.isLocalFile()]
        if paths:
            self.paths_dropped.emit(paths)
            event.acceptProposedAction()
        else:
            super().dropEvent(event)

    def set_background(self, color: str) -> None:
        self.setBackgroundBrush(QColor(color))

    def set_image(self, image) -> None:
        pixmap = QPixmap.fromImage(image)
        self._item.setPixmap(pixmap)
        self._item.setTransformOriginPoint(self._item.boundingRect().center())
        self.scene().setSceneRect(QRectF(pixmap.rect()))
        self._fit_mode = True
        self.fit_image()

    def clear_image(self) -> None:
        self._item.setPixmap(QPixmap())

    def fit_image(self) -> None:
        if self._item.pixmap().isNull():
            return
        self.resetTransform()
        self.fitInView(self._item, Qt.AspectRatioMode.KeepAspectRatio)
        self._fit_mode = True
        self._zoom = self.transform().m11()

    def actual_size(self) -> None:
        self.resetTransform()
        self._fit_mode = False
        self._zoom = 1.0

    def zoom_by(self, factor: float) -> None:
        if self._item.pixmap().isNull():
            return
        next_zoom = self.transform().m11() * factor
        if 0.03 <= next_zoom <= 20.0:
            self.scale(factor, factor)
            self._zoom = next_zoom
            self._fit_mode = False

    def rotate_by(self, degrees: int) -> None:
        self._item.setRotation((self._item.rotation() + degrees) % 360)
        bounds = self._item.mapRectToScene(self._item.boundingRect())
        self.scene().setSceneRect(bounds)
        if self._fit_mode:
            self.fitInView(bounds, Qt.AspectRatioMode.KeepAspectRatio)

    def resizeEvent(self, event) -> None:
        super().resizeEvent(event)
        if self._fit_mode:
            self.fit_image()

    def wheelEvent(self, event: QWheelEvent) -> None:
        if event.modifiers() & Qt.KeyboardModifier.ControlModifier:
            self.zoom_by(1.15 if event.angleDelta().y() > 0 else 1 / 1.15)
            event.accept()
            return
        if event.angleDelta().y() < 0:
            self.next_requested.emit()
        elif event.angleDelta().y() > 0:
            self.previous_requested.emit()
        event.accept()


class ZoomableThumbnailList(QListWidget):
    control_wheel = Signal(int)

    def wheelEvent(self, event: QWheelEvent) -> None:
        if event.modifiers() & Qt.KeyboardModifier.ControlModifier:
            self.control_wheel.emit(1 if event.angleDelta().y() > 0 else -1)
            event.accept()
            return
        super().wheelEvent(event)


class FileDropThumbnailList(ZoomableThumbnailList):
    paths_dropped = Signal(list)

    def __init__(self, parent=None):
        super().__init__(parent)
        self._book_drag_enabled = False
        self.setAcceptDrops(True)
        self.viewport().setAcceptDrops(True)
        self.setDragDropMode(QAbstractItemView.DragDropMode.DropOnly)

    def enable_book_drag(self) -> None:
        self._book_drag_enabled = True
        self.setDragEnabled(True)
        self.setDragDropMode(QAbstractItemView.DragDropMode.DragDrop)

    def startDrag(self, supported_actions) -> None:
        if not self._book_drag_enabled:
            super().startDrag(supported_actions)
            return
        ids = [str(item.data(Qt.ItemDataRole.UserRole) or "") for item in self.selectedItems()]
        ids = [book_id for book_id in ids if book_id]
        if not ids:
            return
        mime = QMimeData()
        mime.setData(BOOK_IDS_MIME, "\n".join(ids).encode("utf-8"))
        drag = QDrag(self)
        drag.setMimeData(mime)
        current = self.currentItem()
        if current and not current.icon().isNull():
            drag.setPixmap(current.icon().pixmap(QSize(72, 72)))
        drag.exec(Qt.DropAction.MoveAction)

    def dragEnterEvent(self, event) -> None:
        if event.mimeData().hasUrls():
            event.acceptProposedAction()
        else:
            super().dragEnterEvent(event)

    def dragMoveEvent(self, event) -> None:
        if event.mimeData().hasUrls():
            event.acceptProposedAction()
        else:
            super().dragMoveEvent(event)

    def dropEvent(self, event) -> None:
        paths = [url.toLocalFile() for url in event.mimeData().urls() if url.isLocalFile()]
        if paths:
            self.paths_dropped.emit(paths)
            event.acceptProposedAction()
        else:
            super().dropEvent(event)


class ShelfTreeWidget(QTreeWidget):
    """Bookshelf hierarchy with internal moves and book drop targets."""

    structure_changed = Signal()
    books_dropped = Signal(list, str)
    add_root_requested = Signal()

    def __init__(self, parent=None):
        super().__init__(parent)
        self.setAcceptDrops(True)
        self.viewport().setAcceptDrops(True)
        self.setDragEnabled(True)
        self.setDragDropMode(QAbstractItemView.DragDropMode.InternalMove)
        self.setDefaultDropAction(Qt.DropAction.MoveAction)
        self.setDropIndicatorShown(True)

    def mousePressEvent(self, event) -> None:
        item = self.itemAt(event.position().toPoint())
        if item and item.data(0, Qt.ItemDataRole.UserRole) == ADD_SHELF_ITEM_ID:
            self.add_root_requested.emit()
            event.accept()
            return
        super().mousePressEvent(event)

    def dragEnterEvent(self, event) -> None:
        if event.mimeData().hasFormat(BOOK_IDS_MIME):
            event.acceptProposedAction()
            return
        super().dragEnterEvent(event)

    def dragMoveEvent(self, event) -> None:
        if event.mimeData().hasFormat(BOOK_IDS_MIME):
            item = self.itemAt(event.position().toPoint())
            folder_id = item.data(0, Qt.ItemDataRole.UserRole) if item else ""
            if folder_id and folder_id != ADD_SHELF_ITEM_ID:
                event.acceptProposedAction()
            else:
                event.ignore()
            return
        item = self.itemAt(event.position().toPoint())
        if item and item.data(0, Qt.ItemDataRole.UserRole) == ADD_SHELF_ITEM_ID:
            event.ignore()
            return
        super().dragMoveEvent(event)

    def dropEvent(self, event) -> None:
        if event.mimeData().hasFormat(BOOK_IDS_MIME):
            item = self.itemAt(event.position().toPoint())
            folder_id = item.data(0, Qt.ItemDataRole.UserRole) if item else ""
            if folder_id and folder_id != ADD_SHELF_ITEM_ID:
                raw = bytes(event.mimeData().data(BOOK_IDS_MIME)).decode("utf-8", "ignore")
                ids = [value for value in raw.splitlines() if value]
                if ids:
                    self.books_dropped.emit(ids, str(folder_id))
                    event.setDropAction(Qt.DropAction.MoveAction)
                    event.accept()
                    return
            event.ignore()
            return
        before = self._folder_signature()
        super().dropEvent(event)
        if event.isAccepted() and self._folder_signature() != before:
            self.structure_changed.emit()

    def _folder_signature(self) -> tuple:
        result = []

        def visit(item, parent_id: str) -> None:
            folder_id = item.data(0, Qt.ItemDataRole.UserRole)
            if folder_id and folder_id != ADD_SHELF_ITEM_ID:
                result.append((str(folder_id), parent_id))
                for index in range(item.childCount()):
                    visit(item.child(index), str(folder_id))

        for index in range(self.topLevelItemCount()):
            visit(self.topLevelItem(index), "")
        return tuple(result)


class PortraitThumbnailDelegate(QStyledItemDelegate):
    """Uniform portrait cells; landscape artwork is fitted to the cell width."""

    def __init__(self, width: int = 170, parent=None):
        super().__init__(parent)
        self.thumbnail_mode = True
        self.force_landscape = False
        self.set_width(width)

    def set_width(self, width: int) -> None:
        self.width = max(80, int(width))
        self.portrait_image_size = QSize(self.width, round(self.width * 1.38))
        self.landscape_image_size = QSize(round(self.width * 1.38), self.width)
        self.cell_size = self._cell_size(self.force_landscape)

    def set_landscape_mode(self, landscape: bool) -> None:
        self.force_landscape = bool(landscape)
        self.cell_size = self._cell_size(self.force_landscape)

    def _is_landscape(self, index) -> bool:
        value = index.data(THUMBNAIL_LANDSCAPE_ROLE)
        return self.force_landscape if value is None else bool(value)

    def _image_size(self, landscape: bool) -> QSize:
        return self.landscape_image_size if landscape else self.portrait_image_size

    def _cell_size(self, landscape: bool) -> QSize:
        image_size = self._image_size(landscape)
        return QSize(image_size.width() + 26, image_size.height() + 54)

    def sizeHint(self, option, index) -> QSize:
        if not self.thumbnail_mode:
            return super().sizeHint(option, index)
        return self._cell_size(self._is_landscape(index))

    def paint(self, painter: QPainter, option, index) -> None:
        if not self.thumbnail_mode:
            super().paint(painter, option, index)
            return
        painter.save()
        selected = bool(option.state & QStyle.StateFlag.State_Selected)
        painter.fillRect(option.rect, option.palette.highlight() if selected else option.palette.base())
        margin = 10
        image_size = self._image_size(self._is_landscape(index))
        image_rect = QRect(option.rect.left() + margin, option.rect.top() + 7, image_size.width(), image_size.height())
        icon = index.data(Qt.ItemDataRole.DecorationRole)
        if icon:
            pixmap = icon.pixmap(QSize(image_size.width() * 2, image_size.height() * 2))
            if not pixmap.isNull():
                scaled = pixmap.scaled(image_rect.size(), Qt.AspectRatioMode.KeepAspectRatio, Qt.TransformationMode.SmoothTransformation)
                x = image_rect.left() + (image_rect.width() - scaled.width()) // 2
                y = image_rect.top() + (image_rect.height() - scaled.height()) // 2
                painter.drawPixmap(x, y, scaled)
        painter.setPen(option.palette.mid().color())
        painter.drawRect(image_rect.adjusted(0, 0, -1, -1))
        text_rect = QRect(option.rect.left() + 5, image_rect.bottom() + 7, option.rect.width() - 10, 38)
        painter.setPen(option.palette.highlightedText().color() if selected else option.palette.text().color())
        painter.drawText(text_rect, Qt.AlignmentFlag.AlignHCenter | Qt.AlignmentFlag.AlignTop | Qt.TextFlag.TextWordWrap, str(index.data(Qt.ItemDataRole.DisplayRole) or ""))
        painter.restore()
