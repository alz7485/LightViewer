from __future__ import annotations

import math

from PySide6.QtCore import QRectF, QSize, Qt
from PySide6.QtGui import QPageLayout, QPainter
from PySide6.QtPrintSupport import QPrintDialog, QPrintPreviewWidget, QPrinter
from PySide6.QtWidgets import (
    QApplication, QComboBox, QDialog, QHBoxLayout, QLabel, QMessageBox, QPushButton,
    QVBoxLayout,
)


def nup_grid(count: int) -> tuple[int, int]:
    mapping = {1: (1, 1), 2: (2, 1), 4: (2, 2), 6: (3, 2), 8: (4, 2), 9: (3, 3), 16: (4, 4)}
    if count in mapping:
        return mapping[count]
    columns = max(1, math.ceil(math.sqrt(count)))
    return columns, max(1, math.ceil(count / columns))


class ImagePrintPreviewDialog(QDialog):
    def __init__(self, source, current_index: int, selected_indices: list[int], max_megapixels: int, parent=None):
        super().__init__(parent)
        self.source = source
        self.current_index = current_index
        self.selected_indices = sorted(set(selected_indices))
        self.max_megapixels = max(8, int(max_megapixels))
        self._image_cache: dict[tuple[int, int, int], object] = {}
        self._painting = False
        self._cancel_requested = False
        self.setWindowTitle("印刷プレビュー")
        self.resize(1100, 820)

        self.printer = QPrinter(QPrinter.PrinterMode.HighResolution)
        self.printer.setFullPage(False)

        root = QVBoxLayout(self)
        controls = QHBoxLayout()
        controls.addWidget(QLabel("範囲"))
        self.scope = QComboBox()
        self.scope.addItem("すべて", "all")
        self.scope.addItem("現在のページ", "current")
        if self.selected_indices:
            self.scope.addItem(f"選択したページ（{len(self.selected_indices)}）", "selected")
            self.scope.setCurrentIndex(self.scope.count() - 1)
        controls.addWidget(self.scope)
        controls.addWidget(QLabel("Nアップ"))
        self.nup = QComboBox()
        for value in (1, 2, 4, 6, 8, 9, 16):
            self.nup.addItem(f"{value}ページ／枚", value)
        controls.addWidget(self.nup)
        controls.addWidget(QLabel("向き"))
        self.orientation = QComboBox()
        self.orientation.addItem("自動", "auto")
        self.orientation.addItem("縦", "portrait")
        self.orientation.addItem("横", "landscape")
        controls.addWidget(self.orientation)
        self.progress_label = QLabel("")
        controls.addWidget(self.progress_label)
        self.cancel_button = QPushButton("中止")
        self.cancel_button.setToolTip("プレビュー生成または印刷を中止")
        self.cancel_button.setEnabled(False)
        self.cancel_button.clicked.connect(self._request_cancel)
        controls.addWidget(self.cancel_button)
        controls.addStretch()
        print_button = QPushButton("🖨️ 印刷")
        print_button.setToolTip("プリンターと部数を選択して印刷")
        print_button.clicked.connect(self._print)
        close_button = QPushButton("閉じる")
        close_button.clicked.connect(self.reject)
        controls.addWidget(print_button); controls.addWidget(close_button)
        root.addLayout(controls)

        self.preview = QPrintPreviewWidget(self.printer, self)
        self.preview.setZoomMode(QPrintPreviewWidget.ZoomMode.FitInView)
        self.preview.paintRequested.connect(self._paint_pages)
        root.addWidget(self.preview)
        self.scope.currentIndexChanged.connect(self.preview.updatePreview)
        self.nup.currentIndexChanged.connect(self.preview.updatePreview)
        self.nup.currentIndexChanged.connect(self._orientation_changed)
        self.orientation.currentIndexChanged.connect(self._orientation_changed)

    def page_indices(self) -> list[int]:
        mode = self.scope.currentData()
        if mode == "current":
            return [self.current_index]
        if mode == "selected" and self.selected_indices:
            return self.selected_indices
        return list(range(self.source.page_count()))

    def _orientation_changed(self) -> None:
        mode = self.orientation.currentData()
        if mode == "portrait":
            self.printer.setPageOrientation(QPageLayout.Orientation.Portrait)
        elif mode == "landscape":
            self.printer.setPageOrientation(QPageLayout.Orientation.Landscape)
        else:
            self.printer.setPageOrientation(QPageLayout.Orientation.Landscape if int(self.nup.currentData()) in (2, 6, 8) else QPageLayout.Orientation.Portrait)
        self.preview.updatePreview()

    def _paint_pages(self, printer: QPrinter) -> None:
        if self._painting:
            return
        indices = self.page_indices()
        if not indices:
            return
        self._painting = True; self._cancel_requested = False; self.cancel_button.setEnabled(True)
        nup = int(self.nup.currentData())
        columns, rows = nup_grid(nup)
        painter = QPainter(printer)
        painter.setRenderHint(QPainter.RenderHint.SmoothPixmapTransform, True)
        page = QRectF(printer.pageRect(QPrinter.Unit.DevicePixel))
        gap = max(8.0, min(page.width(), page.height()) * 0.012)
        cell_width = (page.width() - gap * (columns - 1)) / columns
        cell_height = (page.height() - gap * (rows - 1)) / rows
        try:
            for offset, index in enumerate(indices):
                self.progress_label.setText(f"{offset + 1} / {len(indices)}")
                QApplication.processEvents()
                if self._cancel_requested: break
                slot = offset % nup
                if offset and slot == 0: printer.newPage()
                column = slot % columns; row = slot // columns
                cell = QRectF(page.left() + column * (cell_width + gap), page.top() + row * (cell_height + gap), cell_width, cell_height)
                target = QSize(min(5000, max(800, int(cell.width()))), min(5000, max(800, int(cell.height()))))
                cache_key = (index, target.width(), target.height())
                image = self._image_cache.get(cache_key)
                if image is None:
                    image = self.source.read_page(index, target, min(40, max(16, self.max_megapixels)))
                    self._image_cache[cache_key] = image
                    while len(self._image_cache) > 8: self._image_cache.pop(next(iter(self._image_cache)))
                if image.isNull():
                    painter.drawText(cell, Qt.AlignmentFlag.AlignCenter, f"{index + 1}ページを読み込めません")
                    continue
                scale = min(cell.width() / image.width(), cell.height() / image.height())
                width, height = image.width() * scale, image.height() * scale
                destination = QRectF(cell.center().x() - width / 2, cell.center().y() - height / 2, width, height)
                painter.drawImage(destination, image)
        finally:
            painter.end(); self._painting = False; self.cancel_button.setEnabled(False)
            self.progress_label.setText("中止しました" if self._cancel_requested else "準備完了")

    def _request_cancel(self) -> None:
        self._cancel_requested = True

    def _print(self) -> None:
        dialog = QPrintDialog(self.printer, self)
        dialog.setWindowTitle("印刷")
        if dialog.exec() != QDialog.DialogCode.Accepted:
            return
        try:
            self._paint_pages(self.printer)
            if not self._cancel_requested: self.accept()
        except Exception as error:
            QMessageBox.warning(self, "印刷できません", str(error))
