from __future__ import annotations

import hashlib
import base64
import math
import sys
import time
from datetime import datetime
from pathlib import Path

from PySide6.QtCore import QObject, QPoint, QRunnable, QSize, Qt, QThreadPool, QTimer, QUrl, Signal
from PySide6.QtGui import QAction, QDesktopServices, QFont, QIcon, QImage, QKeySequence, QPainter, QPixmap, QShortcut
from PySide6.QtNetwork import QLocalServer, QLocalSocket
from PySide6.QtWidgets import (
    QApplication, QFileDialog, QHBoxLayout, QInputDialog, QLabel, QListView,
    QListWidgetItem, QMainWindow, QMenu, QMessageBox, QPushButton, QSizePolicy, QComboBox, QLineEdit,
    QSplitter, QToolBar, QVBoxLayout, QWidget, QStackedWidget, QAbstractItemView,
    QTreeWidgetItem, QProgressDialog,
)

from app_config import BOOKSHELF_PATH, CACHE_DIR, app_dir, load_json, load_settings, save_json, save_settings
from icon_data import APP_ICON_B64
from sources import (
    IMAGE_EXTENSIONS, BookEntry, ImageSource, ShelfFolder, archive_kind,
    entries_from_paths, is_archive_path, parse_bookshelf,
)
from widgets import (
    ADD_SHELF_ITEM_ID, THUMBNAIL_LANDSCAPE_ROLE, FileDropThumbnailList,
    ImageView, PortraitThumbnailDelegate, ShelfTreeWidget,
    ZoomableThumbnailList,
)


APP_STYLE = """
QMainWindow, QWidget#BookshelfRoot { background: #f3f5f8; color: #273142; }
QWidget#FolderPanel, QWidget#ShelfPanel {
    background: #ffffff; border: 1px solid #d9dfe8; border-radius: 8px;
}
QLabel#SectionTitle { color: #182235; font-size: 14px; font-weight: 600; padding: 2px 4px; }
QPushButton {
    background: #ffffff; border: 1px solid #cbd3df; border-radius: 5px;
    min-height: 27px; padding: 1px 7px;
}
QPushButton:hover { background: #edf4ff; border-color: #82aee8; }
QPushButton:pressed { background: #dceaff; }
QTreeWidget, QListWidget {
    background: #ffffff; border: 1px solid #d9dfe8; border-radius: 6px;
    outline: 0; padding: 3px;
}
QTreeWidget::item { min-height: 27px; border-radius: 4px; }
QTreeWidget::item:hover, QListWidget::item:hover { background: #eef4fc; }
QTreeWidget::item:selected, QListWidget::item:selected { background: #d8e9ff; color: #17253b; }
QSplitter::handle { background: transparent; width: 8px; }
QToolBar { background: #f8fafc; border: 0; border-bottom: 1px solid #d7dde7; spacing: 4px; padding: 4px; }
QToolBar QToolButton {
    background: #ffffff; border: 1px solid #cbd3df; border-radius: 5px;
    padding: 3px 6px; margin: 0; min-width: 24px; min-height: 24px;
}
QToolBar QToolButton:hover { background: #edf4ff; border-color: #82aee8; }
QToolBar QToolButton:pressed { background: #dceaff; }
QToolBar QToolButton:checked { background: #d8e9ff; border-color: #397fc4; color: #17253b; }
QComboBox, QSpinBox, QLineEdit, QFontComboBox {
    background: #ffffff; border: 1px solid #cbd3df; border-radius: 4px;
    padding: 3px 6px; min-height: 23px; selection-background-color: #d8e9ff;
}
QComboBox:hover, QSpinBox:hover, QLineEdit:hover, QFontComboBox:hover { border-color: #82aee8; }
QTabWidget::pane { border: 1px solid #d9dfe8; background: #ffffff; border-radius: 5px; }
QTabBar::tab { background: #e9edf3; border: 1px solid #d3dae4; padding: 6px 14px; }
QTabBar::tab:selected { background: #ffffff; border-bottom-color: #ffffff; color: #17253b; }
QMenu { background: white; border: 1px solid #cbd3df; padding: 4px; }
QMenu::item { padding: 6px 26px 6px 10px; border-radius: 4px; }
QMenu::item:selected { background: #dceaff; color: #17253b; }
QToolTip { background: #263247; color: white; border: 0; padding: 4px; }
"""


_SYMBOL_ICONS: dict[tuple[str, int], QIcon] = {}


def symbol_icon(symbol: str, size: int = 22) -> QIcon:
    """Render an emoji as a real QIcon so tree indentation cannot hide it."""
    key = (symbol, size)
    if key in _SYMBOL_ICONS:
        return _SYMBOL_ICONS[key]
    canvas = max(24, size + 8)
    pixmap = QPixmap(canvas, canvas)
    pixmap.fill(Qt.GlobalColor.transparent)
    painter = QPainter(pixmap)
    painter.setRenderHint(QPainter.RenderHint.TextAntialiasing, True)
    font = QFont("Segoe UI Emoji")
    font.setPixelSize(size)
    painter.setFont(font)
    painter.drawText(pixmap.rect(), Qt.AlignmentFlag.AlignCenter, symbol)
    painter.end()
    icon = QIcon(pixmap)
    _SYMBOL_ICONS[key] = icon
    return icon


class WorkerSignals(QObject):
    ready = Signal(int, str, object, bool)


class ThumbnailTask(QRunnable):
    def __init__(self, token: int, key: str, path: str, size: int, cache: Path):
        super().__init__()
        self.token, self.key, self.path, self.size, self.cache = token, key, path, size, cache
        self.signals = WorkerSignals()

    def run(self) -> None:
        source = ImageSource(self.path)
        image = QImage(str(self.cache)) if self.cache.exists() else source.first_image(QSize(self.size * 2, round(self.size * 2.76)))
        self.signals.ready.emit(self.token, self.key, image, source.majority_landscape(image))


class PageSignals(QObject):
    ready = Signal(int, object)


class PageLoadTask(QRunnable):
    def __init__(self, token: int, source: ImageSource, index: int, target: QSize, max_megapixels: int):
        super().__init__()
        self.token, self.source, self.index = token, source, index
        self.target, self.max_megapixels = target, max_megapixels
        self.signals = PageSignals()

    def run(self) -> None:
        self.signals.ready.emit(self.token, self.source.read_page(self.index, self.target, self.max_megapixels))


class PrefetchSignals(QObject):
    ready = Signal(int, int, object)


class PrefetchTask(QRunnable):
    def __init__(self, token: int, source: ImageSource, index: int, target: QSize, max_megapixels: int):
        super().__init__(); self.token, self.source, self.index, self.target, self.max_megapixels = token, source, index, target, max_megapixels; self.signals = PrefetchSignals()

    def run(self) -> None:
        self.signals.ready.emit(self.token, self.index, self.source.read_page(self.index, self.target, self.max_megapixels))


class PageThumbnailSignals(QObject):
    ready = Signal(int, int, object)


class PageThumbnailTask(QRunnable):
    def __init__(self, token: int, source: ImageSource, index: int, size: int):
        super().__init__()
        self.token, self.source, self.index, self.size = token, source, index, size
        self.signals = PageThumbnailSignals()

    def run(self) -> None:
        target = QSize(self.size * 2, round(self.size * 2.76))
        self.signals.ready.emit(self.token, self.index, self.source.read_page(self.index, target, 3))


class EditorLoadTask(QRunnable):
    def __init__(self, token: int, source: ImageSource, index: int, target: QSize, max_megapixels: int):
        super().__init__()
        self.token, self.source, self.index = token, source, index
        self.target, self.max_megapixels = target, max_megapixels
        self.signals = PageSignals()

    def run(self) -> None:
        self.signals.ready.emit(self.token, self.source.read_page(self.index, self.target, self.max_megapixels))


class PreviewHost(QMainWindow):
    close_requested = Signal()
    paths_dropped = Signal(list)
    user_moved = Signal(object)

    def __init__(self):
        super().__init__()
        self.allow_close = False
        self.setAcceptDrops(True)

    def dragEnterEvent(self, event) -> None:
        if event.mimeData().hasUrls(): event.acceptProposedAction()
        else: super().dragEnterEvent(event)

    def dragMoveEvent(self, event) -> None:
        if event.mimeData().hasUrls(): event.acceptProposedAction()
        else: super().dragMoveEvent(event)

    def dropEvent(self, event) -> None:
        paths = [url.toLocalFile() for url in event.mimeData().urls() if url.isLocalFile()]
        if paths:
            self.paths_dropped.emit(paths); event.acceptProposedAction()
        else:
            super().dropEvent(event)

    def closeEvent(self, event) -> None:
        if self.allow_close:
            event.accept()
        else:
            self.close_requested.emit()
            event.accept() if self.allow_close else event.ignore()

    def moveEvent(self, event) -> None:
        super().moveEvent(event)
        if self.isVisible() and not self.property("programmatic_geometry"):
            self.user_moved.emit(self.geometry().center())


class ViewerWindow(QMainWindow):
    """Bookshelf window and controller for the independent preview window."""

    def __init__(self, restore_session: bool = True, fast_external_start: bool = False, startup_clock: float | None = None):
        super().__init__()
        self._startup_clock = startup_clock if startup_clock is not None else time.perf_counter()
        self.settings = load_settings()
        self._startup_debug = bool(self.settings.get("startup_debug", False))
        self._startup_log_finished = False
        self._bookshelf_loaded = not fast_external_start
        if self._bookshelf_loaded:
            raw_bookshelf = load_json(BOOKSHELF_PATH, {"folders": [], "books": []})
            self.shelf_folders, self.books = parse_bookshelf(raw_bookshelf)
        else:
            # Related-file launch: disk-heavy bookshelf restore is deliberately deferred.
            self.shelf_folders, self.books = [], []
        self.source: ImageSource | None = None
        self.current_book_id = ""
        self.page_index = 0
        self.source_landscape = False
        self.preview_from_bookshelf = False
        self.bookshelf_return_to_preview = False
        self._app_closing = False
        self._shortcuts: list[QShortcut] = []
        self._loading_token = 0
        self._thumbnail_token = 0
        self._page_thumbnail_token = 0
        self._prefetch_token = 0
        self._editor_loading_token = 0
        self._editor_pending: dict | None = None
        self._editor_progress: QProgressDialog | None = None
        self._requested_page_thumbnails: set[int] = set()
        self._requested_shelf_thumbnails: set[str] = set()
        self._thumbnail_tasks: set[ThumbnailTask] = set()
        self._page_cache: dict[int, QImage] = {}
        self._current_image = QImage()
        self._preview_center_anchor: QPoint | None = None
        self.thumbnail_pool = QThreadPool(self); self.thumbnail_pool.setMaxThreadCount(2)
        self.page_pool = QThreadPool(self); self.page_pool.setMaxThreadCount(1)
        self.page_thumbnail_pool = QThreadPool(self); self.page_thumbnail_pool.setMaxThreadCount(2)
        self.prefetch_pool = QThreadPool(self); self.prefetch_pool.setMaxThreadCount(1)
        self.editor_load_pool = QThreadPool(self); self.editor_load_pool.setMaxThreadCount(1)
        self.slideshow_timer = QTimer(self); self.slideshow_timer.timeout.connect(self.next_page)
        self.settings_save_timer = QTimer(self); self.settings_save_timer.setSingleShot(True); self.settings_save_timer.setInterval(600)
        self.settings_save_timer.timeout.connect(lambda: save_settings(self.settings))
        self.cache_trim_timer = QTimer(self); self.cache_trim_timer.setSingleShot(True); self.cache_trim_timer.setInterval(2000); self.cache_trim_timer.timeout.connect(self._trim_cache)
        self.setWindowTitle("LightViewer - 本棚")
        self.setObjectName("BookshelfWindow")
        self._build_bookshelf_ui()
        self._build_preview_ui()
        self._apply_settings(resize=True)
        if self._bookshelf_loaded:
            self._refresh_shelf_folders()
            self._refresh_bookshelf()
        self._install_shortcuts()
        self._startup_log("MainWindow/UI生成完了")

    def _startup_log(self, label: str, finish: bool = False) -> None:
        if not self._startup_debug or (self._startup_log_finished and not finish):
            return
        try:
            log_dir = app_dir() / "logs"
            log_dir.mkdir(parents=True, exist_ok=True)
            elapsed = round((time.perf_counter() - self._startup_clock) * 1000)
            path = log_dir / "startup.log"
            with path.open("a", encoding="utf-8") as handle:
                if label == "起動開始":
                    handle.write(f"\n{datetime.now():%Y-%m-%d %H:%M:%S}\n")
                handle.write(f"{label:<24} {elapsed:>6} ms\n")
                if finish:
                    handle.write("-" * 38 + "\n")
            if finish:
                self._startup_log_finished = True
        except OSError:
            pass

    def _ensure_bookshelf_loaded(self, refresh: bool = True) -> None:
        if self._bookshelf_loaded:
            return
        raw_bookshelf = load_json(BOOKSHELF_PATH, {"folders": [], "books": []})
        self.shelf_folders, self.books = parse_bookshelf(raw_bookshelf)
        self._bookshelf_loaded = True
        if refresh:
            self._refresh_shelf_folders()
            self._refresh_bookshelf()
        self._startup_log("本棚を遅延復元")

    def _build_bookshelf_ui(self) -> None:
        self.setAcceptDrops(True)
        root = QWidget(); root.setObjectName("BookshelfRoot"); layout = QVBoxLayout(root); layout.setContentsMargins(12, 12, 12, 12)
        add_folder = QPushButton("+📁"); add_folder.setToolTip("画像フォルダを本棚に追加"); add_folder.clicked.connect(self.add_folder)
        add_archive = QPushButton("+📙"); add_archive.setToolTip("ZIP・RAR・7Zなどの圧縮ファイルを本棚に追加"); add_archive.clicked.connect(self.add_archives)
        remove = QPushButton("🗑️"); remove.setToolTip("選択した項目を本棚から削除"); remove.clicked.connect(self.remove_selected_books)
        settings_button = QPushButton("⚙️"); settings_button.setToolTip("設定"); settings_button.clicked.connect(self.open_settings)
        self.mode_button = QPushButton(); self.mode_button.setFixedWidth(38); self.mode_button.clicked.connect(self.toggle_shelf_mode)
        for button in (add_folder, add_archive, remove, settings_button): button.setFixedWidth(46)
        splitter = QSplitter(Qt.Orientation.Horizontal)
        folder_panel = QWidget(); folder_panel.setObjectName("FolderPanel"); folder_layout = QVBoxLayout(folder_panel); folder_layout.setContentsMargins(8, 8, 8, 8)
        folder_buttons = QHBoxLayout()
        folder_title = QLabel("本棚"); folder_title.setObjectName("SectionTitle"); folder_buttons.addWidget(folder_title)
        for label, tip, callback in [("+📚", "選択中のフォルダ内に本棚を追加", self.add_child_shelf_folder), ("📝", "名前を変更", self.rename_shelf_folder), ("🗑️", "本棚フォルダを削除", self.delete_shelf_folder)]:
            button = QPushButton(label); button.setFixedWidth(46); button.setToolTip(tip); button.clicked.connect(callback); folder_buttons.addWidget(button)
        folder_layout.addLayout(folder_buttons)
        self.folder_list = ShelfTreeWidget(); self.folder_list.setColumnCount(1); self.folder_list.setHeaderHidden(True); self.folder_list.setIconSize(QSize(24, 24)); self.folder_list.setMinimumWidth(210); self.folder_list.setMaximumWidth(390)
        self.folder_list.setSortingEnabled(False)
        self.folder_list.setSelectionMode(QAbstractItemView.SelectionMode.ExtendedSelection)
        self.folder_list.setEditTriggers(QAbstractItemView.EditTrigger.SelectedClicked | QAbstractItemView.EditTrigger.EditKeyPressed)
        self.folder_list.currentItemChanged.connect(lambda _now, _old: self._folder_filter_changed())
        self.folder_list.itemChanged.connect(self._folder_item_changed)
        self.folder_list.itemExpanded.connect(self._folder_expansion_changed); self.folder_list.itemCollapsed.connect(self._folder_expansion_changed)
        self.folder_list.setContextMenuPolicy(Qt.ContextMenuPolicy.CustomContextMenu); self.folder_list.customContextMenuRequested.connect(self._folder_menu)
        self.folder_list.structure_changed.connect(self._folder_tree_moved)
        self.folder_list.books_dropped.connect(self._books_dropped_on_folder)
        self.folder_list.add_root_requested.connect(lambda: self.add_shelf_folder(""))
        folder_layout.addWidget(self.folder_list)
        self.shelf = FileDropThumbnailList()
        self.shelf.enable_book_drag()
        self.shelf_delegate = PortraitThumbnailDelegate(int(self.settings["thumbnail_size"]), self.shelf); self.shelf.setItemDelegate(self.shelf_delegate)
        self.shelf.setSelectionMode(ZoomableThumbnailList.SelectionMode.ExtendedSelection)
        self.shelf.itemDoubleClicked.connect(lambda _item: self.open_selected_book())
        self.shelf.setContextMenuPolicy(Qt.ContextMenuPolicy.CustomContextMenu); self.shelf.customContextMenuRequested.connect(self._shelf_menu)
        self.shelf.control_wheel.connect(self._zoom_shelf_thumbnails)
        self.shelf.paths_dropped.connect(self._shelf_paths_dropped)
        self.shelf.verticalScrollBar().valueChanged.connect(lambda _value: QTimer.singleShot(25, self._request_visible_shelf_thumbnails))
        shelf_panel = QWidget(); shelf_panel.setObjectName("ShelfPanel"); shelf_layout = QVBoxLayout(shelf_panel); shelf_layout.setContentsMargins(8, 8, 8, 8)
        shelf_buttons = QHBoxLayout(); self.shelf_title_label = QLabel("本棚"); self.shelf_title_label.setObjectName("SectionTitle"); self.shelf_title_label.setToolTip("現在表示している本棚")
        shelf_buttons.addWidget(self.shelf_title_label)
        shelf_buttons.addWidget(add_folder); shelf_buttons.addWidget(add_archive); shelf_buttons.addWidget(remove)
        shelf_buttons.addStretch(); shelf_buttons.addWidget(settings_button); shelf_buttons.addWidget(self.mode_button)
        shelf_layout.addLayout(shelf_buttons)
        filter_row = QHBoxLayout(); self.shelf_search = QLineEdit(); self.shelf_search.setPlaceholderText("🔎 本棚内を検索"); self.shelf_search.setClearButtonEnabled(True); self.shelf_search.textChanged.connect(self._refresh_bookshelf); filter_row.addWidget(self.shelf_search, 1)
        self.shelf_sort = QComboBox(); self.shelf_sort.setToolTip("本の並び順"); self.shelf_sort.addItem("登録順", "manual"); self.shelf_sort.addItem("名前順", "name"); self.shelf_sort.addItem("更新日時順", "updated"); self.shelf_sort.setCurrentIndex(max(0, self.shelf_sort.findData(self.settings.get("bookshelf_sort", "name")))); self.shelf_sort.currentIndexChanged.connect(self._shelf_sort_changed); filter_row.addWidget(self.shelf_sort)
        shelf_layout.addLayout(filter_row); shelf_layout.addWidget(self.shelf)
        splitter.addWidget(folder_panel); splitter.addWidget(shelf_panel); splitter.setStretchFactor(0, 0); splitter.setStretchFactor(1, 1); splitter.setSizes([230, 1000])
        layout.addWidget(splitter)
        self.shelf.setToolTip("ここへ画像フォルダ・ZIP・RAR・7Zなどをドロップして登録")
        self.setCentralWidget(root)

    def _build_preview_ui(self) -> None:
        self.preview_window = PreviewHost(); self.preview_window.setWindowTitle("LightViewer - プレビュー")
        self.preview_window.close_requested.connect(self._preview_close_requested)
        self.preview_window.paths_dropped.connect(self._preview_paths_dropped)
        self.preview_window.user_moved.connect(self._preview_user_moved)
        toolbar = QToolBar("操作"); toolbar.setMovable(False); toolbar.setIconSize(QSize(18, 18)); self.preview_window.addToolBar(toolbar)
        for text, tip, slot in [("📚", "本棚を表示", self.show_bookshelf), ("📚＋", "現在の画像フォルダ／圧縮ファイルを本棚へ登録", self.save_current_to_bookshelf), ("◀", "前のページ", self.previous_page), ("▶", "次のページ", self.next_page), ("⛶", "画像全体を表示", self.fit_image), ("100%", "原寸表示", self.actual_size), ("−", "縮小", lambda: self.view.zoom_by(1 / 1.15)), ("＋", "拡大", lambda: self.view.zoom_by(1.15)), ("🖨️", "印刷・印刷プレビュー", self.open_print_preview), ("✏️", "現在のページを編集", self.open_editor), ("⚙️", "設定", self.open_settings)]:
            action = QAction(text, self.preview_window); action.setToolTip(tip); action.triggered.connect(slot); toolbar.addAction(action)
        spacer = QWidget(); spacer.setSizePolicy(QSizePolicy.Policy.Expanding, QSizePolicy.Policy.Preferred); toolbar.addWidget(spacer)
        self.preview_mode_button = QPushButton("▦"); self.preview_mode_button.setFixedWidth(38); self.preview_mode_button.setToolTip("サムネイル表示へ切替"); self.preview_mode_button.clicked.connect(self.toggle_preview_mode); toolbar.addWidget(self.preview_mode_button)
        self.preview_stack = QStackedWidget()
        single_page = QWidget(); single_layout = QVBoxLayout(single_page); single_layout.setContentsMargins(0, 0, 0, 0); single_layout.setSpacing(0)
        self.view = ImageView(); self.view.next_requested.connect(self.next_page); self.view.previous_requested.connect(self.previous_page); self.view.paths_dropped.connect(self._preview_paths_dropped)
        self.status = QLabel(); self.status.setAlignment(Qt.AlignmentFlag.AlignCenter); self.status.setStyleSheet("padding:4px;background:#202328;color:#ddd")
        single_layout.addWidget(self.view); single_layout.addWidget(self.status)
        self.page_thumbnails = FileDropThumbnailList(); self.page_thumbnail_delegate = PortraitThumbnailDelegate(int(self.settings["preview_thumbnail_size"]), self.page_thumbnails)
        self.page_thumbnails.paths_dropped.connect(self._preview_paths_dropped)
        self.page_thumbnails.setItemDelegate(self.page_thumbnail_delegate); self.page_thumbnails.setViewMode(QListView.ViewMode.IconMode)
        self.page_thumbnails.setSelectionMode(QAbstractItemView.SelectionMode.ExtendedSelection)
        self.page_thumbnails.setResizeMode(QListView.ResizeMode.Adjust); self.page_thumbnails.setMovement(QListView.Movement.Static)
        self.page_thumbnails.setVerticalScrollMode(QAbstractItemView.ScrollMode.ScrollPerPixel)
        self.page_thumbnails.itemDoubleClicked.connect(self._open_page_thumbnail); self.page_thumbnails.control_wheel.connect(self._zoom_preview_thumbnails)
        self.page_thumbnails.verticalScrollBar().valueChanged.connect(lambda _value: QTimer.singleShot(30, self._request_visible_page_thumbnails))
        self.preview_stack.addWidget(single_page); self.preview_stack.addWidget(self.page_thumbnails); self.preview_window.setCentralWidget(self.preview_stack)
        # Heavy editor module/window is created only when the edit button is used.
        self.editor_window = None

    def _install_shortcuts(self) -> None:
        for shortcut in self._shortcuts: shortcut.setParent(None)
        self._shortcuts.clear()
        actions = {"next_page": self.next_page, "previous_page": self.previous_page, "next_book": self.next_book, "previous_book": self.previous_book, "show_bookshelf": self.show_bookshelf, "open_selected": self.open_selected_book, "toggle_fullscreen": self.toggle_fullscreen, "zoom_in": lambda: self.view.zoom_by(1.15), "zoom_out": lambda: self.view.zoom_by(1 / 1.15), "fit_image": self.fit_image, "actual_size": self.actual_size, "rotate_left": lambda: self.view.rotate_by(-90), "rotate_right": lambda: self.view.rotate_by(90), "toggle_slideshow": self.toggle_slideshow, "toggle_preview_mode": self.toggle_preview_mode, "open_print": self.open_print_preview, "open_editor": self.open_editor, "save_to_bookshelf": self.save_current_to_bookshelf}
        for parent in (self, self.preview_window):
            used: set[str] = set()
            for name, callback in actions.items():
                for text in self.settings["key_bindings"].get(name, []):
                    sequence = QKeySequence(text); canonical = sequence.toString()
                    if sequence.isEmpty() or canonical in used: continue
                    shortcut = QShortcut(sequence, parent); shortcut.setContext(Qt.ShortcutContext.WindowShortcut); shortcut.activated.connect(callback); self._shortcuts.append(shortcut); used.add(canonical)
        for widget, callback in ((self.folder_list, self.delete_shelf_folder), (self.shelf, self.remove_selected_books)):
            shortcut = QShortcut(QKeySequence("Delete"), widget); shortcut.setContext(Qt.ShortcutContext.WidgetWithChildrenShortcut); shortcut.activated.connect(callback); self._shortcuts.append(shortcut)

    def _apply_settings(self, resize: bool = False) -> None:
        self.view.set_background(self.settings["background_color"]); self.slideshow_timer.setInterval(int(self.settings["slideshow_seconds"]) * 1000)
        self.shelf_delegate.set_width(int(self.settings["thumbnail_size"])); self.page_thumbnail_delegate.set_width(int(self.settings["preview_thumbnail_size"]))
        if resize:
            self._resize_window_center(self, int(self.settings["bookshelf_window_width"]), int(self.settings["bookshelf_window_height"]))
            width_key, height_key = ("preview_window_width", "preview_window_height") if self.preview_stack.currentIndex() == 0 else ("preview_thumbnail_width", "preview_thumbnail_height")
            self._resize_window_center(self.preview_window, int(self.settings[width_key]), int(self.settings[height_key]))
        self._install_shortcuts()
        if self.editor_window is not None:
            self.editor_window.set_key_bindings(self.settings["key_bindings"])

    @staticmethod
    def _resize_window_center(window: QMainWindow, width: int, height: int, center: QPoint | None = None) -> None:
        screen = (window.screen() or QApplication.primaryScreen()).availableGeometry(); width = min(max(420, width), screen.width()); height = min(max(300, height), screen.height())
        if not window.isVisible(): window.resize(width, height); return
        anchor = QPoint(center) if center is not None else window.geometry().center(); x = max(screen.left(), min(anchor.x() - width // 2, screen.right() - width + 1)); y = max(screen.top(), min(anchor.y() - height // 2, screen.bottom() - height + 1))
        window.setProperty("programmatic_geometry", True)
        window.setGeometry(x, y, width, height)
        window.setProperty("programmatic_geometry", False)

    def _preview_user_moved(self, center) -> None:
        if isinstance(center, QPoint): self._preview_center_anchor = QPoint(center)

    def _remember_preview_size(self) -> None:
        if not self.preview_window.isVisible() or self.preview_window.isFullScreen(): return
        size = self.preview_window.size(); keys = ("preview_window_width", "preview_window_height") if self.preview_stack.currentIndex() == 0 else ("preview_thumbnail_width", "preview_thumbnail_height")
        self.settings[keys[0]] = size.width(); self.settings[keys[1]] = size.height()

    def open_settings(self) -> None:
        # Lazy import: settings UI is not needed during normal image startup.
        from dialogs import SettingsDialog
        dialog = SettingsDialog(self.settings, self.preview_window if self.preview_window.isVisible() else self)
        if dialog.exec():
            self.settings = dialog.result_settings(); save_settings(self.settings); self._apply_settings(resize=True); self._refresh_bookshelf()
            if self.source and self.preview_stack.currentIndex() == 0: self._load_current_page()

    def _save_books(self) -> None:
        save_json(BOOKSHELF_PATH, {"folders": [folder.to_dict() for folder in self.shelf_folders], "books": [book.to_dict() for book in self.books]})

    def _current_folder_filter(self) -> str:
        item = self.folder_list.currentItem()
        folder_id = item.data(0, Qt.ItemDataRole.UserRole) if item else "*"
        return "*" if folder_id == ADD_SHELF_ITEM_ID else folder_id

    def _current_folder_name(self) -> str:
        folder_id = self._current_folder_filter()
        if folder_id == "*": return "すべて"
        folder = next((item for item in self.shelf_folders if item.id == folder_id), None)
        return folder.name if folder else "本棚"

    def _descendant_folder_ids(self, folder_id: str) -> set[str]:
        result = {folder_id}; pending = [folder_id]
        while pending:
            parent_id = pending.pop()
            children = [folder.id for folder in self.shelf_folders if folder.parent_id == parent_id and folder.id not in result]
            result.update(children); pending.extend(children)
        return result

    def _filtered_books(self) -> list[BookEntry]:
        folder_id = self._current_folder_filter()
        books = list(self.books) if folder_id == "*" else [book for book in self.books if book.folder_id in self._descendant_folder_ids(folder_id)]
        query = self.shelf_search.text().strip().casefold() if hasattr(self, "shelf_search") else ""
        if query: books = [book for book in books if query in book.display_title.casefold() or query in book.path.casefold()]
        mode = self.settings.get("bookshelf_sort", "name")
        if mode == "name": books.sort(key=lambda book: book.display_title.casefold())
        elif mode == "updated":
            def modified(book):
                try: return Path(book.path).stat().st_mtime_ns
                except OSError: return 0
            books.sort(key=modified, reverse=True)
        return books

    def _shelf_sort_changed(self) -> None:
        self.settings["bookshelf_sort"] = self.shelf_sort.currentData(); self.settings_save_timer.start(); self._refresh_bookshelf()

    def _folder_icon(self, folder: ShelfFolder) -> str:
        has_children = any(item.parent_id == folder.id for item in self.shelf_folders)
        return "🗂️" if has_children else "📚"

    def _refresh_shelf_folders(self, select_id: str | None = None) -> None:
        wanted = select_id if select_id is not None else self.settings.get("bookshelf_folder_id", "*")
        valid = {folder.id for folder in self.shelf_folders}
        if wanted not in valid: wanted = self.shelf_folders[0].id if self.shelf_folders else "*"
        self.folder_list.blockSignals(True); self.folder_list.clear(); items: dict[str, QTreeWidgetItem] = {}
        remaining = list(self.shelf_folders)
        while remaining:
            progressed = False
            for folder in remaining[:]:
                if folder.parent_id and folder.parent_id not in items:
                    continue
                item = QTreeWidgetItem([folder.name]); item.setData(0, Qt.ItemDataRole.UserRole, folder.id); item.setIcon(0, symbol_icon(self._folder_icon(folder), 20))
                item.setFlags(item.flags() | Qt.ItemFlag.ItemIsEditable | Qt.ItemFlag.ItemIsDragEnabled | Qt.ItemFlag.ItemIsDropEnabled)
                if folder.parent_id: items[folder.parent_id].addChild(item)
                else: self.folder_list.addTopLevelItem(item)
                items[folder.id] = item; remaining.remove(folder); progressed = True
            if not progressed:
                for folder in remaining:
                    folder.parent_id = ""; item = QTreeWidgetItem([folder.name]); item.setData(0, Qt.ItemDataRole.UserRole, folder.id); item.setIcon(0, symbol_icon(self._folder_icon(folder), 20)); item.setFlags(item.flags() | Qt.ItemFlag.ItemIsEditable | Qt.ItemFlag.ItemIsDragEnabled | Qt.ItemFlag.ItemIsDropEnabled); self.folder_list.addTopLevelItem(item); items[folder.id] = item
                break
        add_item = QTreeWidgetItem(["本棚を追加"])
        add_item.setData(0, Qt.ItemDataRole.UserRole, ADD_SHELF_ITEM_ID)
        add_item.setIcon(0, symbol_icon("＋", 19))
        add_item.setToolTip(0, "最上位に新しい本棚を追加")
        add_item.setForeground(0, Qt.GlobalColor.darkGray)
        add_item.setFlags(Qt.ItemFlag.ItemIsEnabled)
        self.folder_list.addTopLevelItem(add_item)
        expanded = set(self.settings.get("expanded_shelf_ids", []))
        if expanded:
            for folder_id, item in items.items(): item.setExpanded(folder_id in expanded)
        else: self.folder_list.expandAll()
        if wanted in items: self.folder_list.setCurrentItem(items[wanted])
        self.folder_list.blockSignals(False); self.settings["bookshelf_folder_id"] = wanted
        self.shelf_title_label.setText(self._current_folder_name())

    def _folder_filter_changed(self) -> None:
        self.settings["bookshelf_folder_id"] = self._current_folder_filter(); self.shelf_title_label.setText(self._current_folder_name()); self.settings_save_timer.start(); self._refresh_bookshelf()

    def _folder_expansion_changed(self, _item: QTreeWidgetItem) -> None:
        expanded: list[str] = []
        def visit(item: QTreeWidgetItem) -> None:
            folder_id = item.data(0, Qt.ItemDataRole.UserRole)
            if folder_id and folder_id != ADD_SHELF_ITEM_ID and item.isExpanded(): expanded.append(str(folder_id))
            for index in range(item.childCount()): visit(item.child(index))
        for index in range(self.folder_list.topLevelItemCount()): visit(self.folder_list.topLevelItem(index))
        self.settings["expanded_shelf_ids"] = expanded; self.settings_save_timer.start()

    def _folder_item_changed(self, item: QTreeWidgetItem, column: int) -> None:
        if column != 0: return
        folder_id = item.data(0, Qt.ItemDataRole.UserRole)
        folder = next((entry for entry in self.shelf_folders if entry.id == folder_id), None)
        if not folder: return
        name = item.text(0).strip()
        if not name: name = folder.name
        folder.name = name; self.folder_list.blockSignals(True); item.setText(0, name); self.folder_list.blockSignals(False)
        self.shelf_title_label.setText(name); self._save_books()

    def _folder_tree_moved(self) -> None:
        by_id = {folder.id: folder for folder in self.shelf_folders}
        ordered: list[ShelfFolder] = []

        def visit(item: QTreeWidgetItem, parent_id: str) -> None:
            folder_id = item.data(0, Qt.ItemDataRole.UserRole)
            folder = by_id.get(folder_id)
            if not folder: return
            folder.parent_id = parent_id
            ordered.append(folder)
            for child_index in range(item.childCount()):
                visit(item.child(child_index), folder.id)

        for index in range(self.folder_list.topLevelItemCount()):
            visit(self.folder_list.topLevelItem(index), "")
        if len(ordered) == len(self.shelf_folders):
            selected_id = self._current_folder_filter()
            self.shelf_folders = ordered
            self._save_books()
            self._refresh_shelf_folders(selected_id)
            self._refresh_bookshelf()

    def _books_dropped_on_folder(self, book_ids: list[str], folder_id: str) -> None:
        if folder_id not in {folder.id for folder in self.shelf_folders}: return
        selected = set(book_ids); moved = False
        for book in self.books:
            if book.id in selected and book.folder_id != folder_id:
                book.folder_id = folder_id; moved = True
        if moved:
            self.settings["bookshelf_folder_id"] = folder_id
            self._save_books(); self._refresh_shelf_folders(folder_id); self._refresh_bookshelf(); self.statusBar().showMessage(f"{len(selected)}冊を移動しました", 2500)

    def _refresh_bookshelf(self) -> None:
        selected_ids = {item.data(Qt.ItemDataRole.UserRole) for item in self.shelf.selectedItems()}
        previous_visuals = ({item.data(Qt.ItemDataRole.UserRole): (item.icon(), item.data(THUMBNAIL_LANDSCAPE_ROLE)) for row in range(self.shelf.count()) for item in [self.shelf.item(row)]} if self.shelf_delegate.thumbnail_mode else {})
        self._thumbnail_token += 1; self.thumbnail_pool.clear(); self._thumbnail_tasks.clear(); self._requested_shelf_thumbnails.clear(); self.shelf.clear(); thumb = self.settings["bookshelf_mode"] == "thumbnail"; size = int(self.settings["thumbnail_size"])
        self.mode_button.setText("☷" if thumb else "▦"); self.mode_button.setToolTip("リスト表示へ切替" if thumb else "サムネイル表示へ切替")
        self.shelf_delegate.thumbnail_mode = thumb; self.shelf_delegate.set_landscape_mode(False); self.shelf_delegate.set_width(size); self.shelf.setViewMode(QListView.ViewMode.IconMode if thumb else QListView.ViewMode.ListMode); self.shelf.setResizeMode(QListView.ResizeMode.Adjust); self.shelf.setMovement(QListView.Movement.Static); self.shelf.setDragEnabled(True)
        self.shelf.setIconSize(QSize(round(size * 2.76), round(size * 2.76)) if thumb else QSize(28, 28)); self.shelf.setGridSize(QSize())
        for book in self._filtered_books():
            exists = Path(book.path).exists(); item = QListWidgetItem(book.display_title + ("\n見つかりません" if not exists else "")); item.setData(Qt.ItemDataRole.UserRole, book.id); item.setToolTip(book.path)
            if thumb and book.id in previous_visuals:
                icon, landscape = previous_visuals[book.id]; item.setIcon(icon); item.setData(THUMBNAIL_LANDSCAPE_ROLE, landscape)
            elif not thumb:
                item.setIcon(symbol_icon("📙" if book.kind == "archive" else "📁", 21))
            if not exists: item.setForeground(Qt.GlobalColor.gray)
            self.shelf.addItem(item)
            if book.id in selected_ids: item.setSelected(True)
        self.shelf.viewport().update(); QTimer.singleShot(0, self._request_visible_shelf_thumbnails)

    def _request_visible_shelf_thumbnails(self) -> None:
        if self.settings.get("bookshelf_mode") != "thumbnail" or not self.shelf.count(): return
        viewport = self.shelf.viewport().rect(); margin = max(240, int(self.settings["thumbnail_size"]) * 3); token = self._thumbnail_token; size = int(self.settings["thumbnail_size"])
        for row in range(self.shelf.count()):
            item = self.shelf.item(row); book_id = item.data(Qt.ItemDataRole.UserRole)
            if book_id in self._requested_shelf_thumbnails: continue
            rect = self.shelf.visualItemRect(item)
            if rect.bottom() < -margin or rect.top() > viewport.height() + margin: continue
            book = self._book_by_id(book_id)
            if not book or not Path(book.path).exists(): continue
            self._requested_shelf_thumbnails.add(book_id); self._request_thumbnail(book, size, token)

    def _thumbnail_cache_path(self, book: BookEntry, size: int) -> Path:
        try: stamp = Path(book.path).stat().st_mtime_ns
        except OSError: stamp = 0
        return CACHE_DIR / f"{hashlib.sha1(f'{book.path}|{stamp}|{size}'.encode('utf-8')).hexdigest()}.jpg"

    def _request_thumbnail(self, book: BookEntry, size: int, token: int) -> None:
        cache = self._thumbnail_cache_path(book, size)
        task = ThumbnailTask(token, book.id, book.path, size, cache)
        task.signals.ready.connect(lambda generation, key, image, landscape, path=cache, worker=task: self._thumbnail_ready(generation, key, image, landscape, path, worker))
        self._thumbnail_tasks.add(task); self.thumbnail_pool.start(task)

    def _set_shelf_icon(self, book_id: str, pixmap: QPixmap, landscape: bool) -> None:
        for row in range(self.shelf.count()):
            item = self.shelf.item(row)
            if item.data(Qt.ItemDataRole.UserRole) == book_id: item.setIcon(QIcon(pixmap)); item.setData(THUMBNAIL_LANDSCAPE_ROLE, landscape); break

    def _thumbnail_ready(self, token: int, key: str, image: QImage, landscape: bool, cache: Path, task: ThumbnailTask) -> None:
        self._thumbnail_tasks.discard(task)
        if token != self._thumbnail_token: return
        if image.isNull():
            for row in range(self.shelf.count()):
                item = self.shelf.item(row)
                if item.data(Qt.ItemDataRole.UserRole) == key and "読み込み失敗" not in item.text(): item.setText(item.text() + "\n⚠ 読み込み失敗")
            return
        if not cache.exists(): CACHE_DIR.mkdir(parents=True, exist_ok=True); image.save(str(cache), "JPG", 90)
        self._set_shelf_icon(key, QPixmap.fromImage(image), landscape); self.shelf.doItemsLayout(); self.shelf.viewport().update(); self.cache_trim_timer.start()

    def _trim_cache(self) -> None:
        limit = max(64, int(self.settings.get("cache_limit_mb", 512))) * 1024 * 1024
        try: files = [path for path in CACHE_DIR.glob("*") if path.is_file()]
        except OSError: return
        total = sum(path.stat().st_size for path in files)
        if total <= limit: return
        for path in sorted(files, key=lambda item: item.stat().st_mtime_ns):
            try: total -= path.stat().st_size; path.unlink()
            except OSError: pass
            if total <= limit: break

    def _zoom_shelf_thumbnails(self, direction: int) -> None:
        if self.settings["bookshelf_mode"] != "thumbnail": return
        self.settings["thumbnail_size"] = max(80, min(320, int(self.settings["thumbnail_size"]) + direction * 15)); self.settings_save_timer.start(); self._refresh_bookshelf()

    def add_folder(self) -> None:
        path = QFileDialog.getExistingDirectory(self, "本棚に登録する画像フォルダ")
        if path: self.add_paths([path])

    def add_archives(self) -> None:
        paths, _ = QFileDialog.getOpenFileNames(self, "本棚に登録する書庫", "", "対応書庫 (*.zip *.cbz *.rar *.cbr *.7z *.cb7 *.tar *.tar.gz *.tgz *.tar.bz2 *.tbz2 *.tar.xz *.txz)"); self.add_paths(paths)

    def add_paths(self, paths: list[str]) -> None:
        existing = {str(Path(book.path).resolve()).casefold() for book in self.books}; additions = []; target_folder = self._current_folder_filter()
        if target_folder == "*":
            folder = ShelfFolder(name="本棚"); self.shelf_folders.append(folder); target_folder = folder.id; self._refresh_shelf_folders(target_folder)
        for entry in entries_from_paths(paths, target_folder):
            if entry.path.casefold() not in existing: additions.append(entry); existing.add(entry.path.casefold())
        if additions: self.books.extend(additions); self._save_books(); self._refresh_bookshelf()

    def save_current_to_bookshelf(self) -> None:
        self._ensure_bookshelf_loaded()
        if not self.source:
            QMessageBox.information(self.preview_window, "本棚へ登録", "登録する画像が開かれていません。")
            return
        if not self.shelf_folders:
            QMessageBox.information(self.preview_window, "本棚へ登録", "先に本棚を作成してください。")
            return
        source_path = self.source.path
        if source_path.is_file() and not is_archive_path(source_path):
            source_path = source_path.parent
        if not source_path.is_dir() and not is_archive_path(source_path):
            QMessageBox.warning(self.preview_window, "本棚へ登録", "現在の画像フォルダまたは圧縮ファイルを確認できません。")
            return
        # Lazy import: shelf selection dialog is only needed on registration.
        from dialogs import ShelfSelectionDialog
        dialog = ShelfSelectionDialog(self.shelf_folders, self.preview_window)
        if not dialog.exec(): return
        folder_id = dialog.selected_folder_id()
        if folder_id not in {folder.id for folder in self.shelf_folders}: return
        resolved = str(source_path.resolve())
        existing = next((book for book in self.books if str(Path(book.path).resolve()).casefold() == resolved.casefold()), None)
        if existing:
            if existing.folder_id == folder_id:
                self._preview_notice("すでに選択した本棚へ登録されています。")
                return
            existing.folder_id = folder_id
            message = "登録済みの項目を選択した本棚へ移動しました。"
        else:
            self.books.append(BookEntry(path=resolved, folder_id=folder_id))
            message = "選択した本棚へ登録しました。"
        self.settings["bookshelf_folder_id"] = folder_id
        self._save_books(); self._refresh_shelf_folders(folder_id); self._refresh_bookshelf(); self.settings_save_timer.start()
        self._preview_notice(message)

    def _preview_notice(self, message: str, timeout: int = 3500) -> None:
        self.preview_window.statusBar().showMessage(message, timeout)

    def _shelf_paths_dropped(self, paths: list[str]) -> None:
        books = [path for path in paths if Path(path).is_dir() or is_archive_path(path)]
        images = [path for path in paths if Path(path).is_file() and Path(path).suffix.casefold() in IMAGE_EXTENSIONS]
        if books:
            self.add_paths(books)
        if images:
            self.open_external_path(images[0], from_bookshelf=True)

    def _preview_paths_dropped(self, paths: list[str]) -> None:
        supported = [path for path in paths if Path(path).is_dir() or is_archive_path(path) or (Path(path).is_file() and Path(path).suffix.casefold() in IMAGE_EXTENSIONS)]
        if supported:
            self.open_external_path(supported[0], from_bookshelf=False)

    def remove_selected_books(self) -> None:
        ids = {item.data(Qt.ItemDataRole.UserRole) for item in self.shelf.selectedItems()}
        if ids: self.books = [book for book in self.books if book.id not in ids]; self._save_books(); self._refresh_bookshelf()

    def _shelf_menu(self, pos) -> None:
        item = self.shelf.itemAt(pos); menu = QMenu(self); menu.addAction("開く", self.open_selected_book); menu.addAction("登録から削除", self.remove_selected_books)
        if item:
            move_menu = menu.addMenu("本棚へ移動"); self._populate_move_menu(move_menu)
            menu.addSeparator(); menu.addAction("保存場所を開く", lambda: self._open_book_location(item))
        menu.exec(self.shelf.mapToGlobal(pos))

    def _populate_move_menu(self, menu: QMenu, parent_id: str = "") -> None:
        for folder in [item for item in self.shelf_folders if item.parent_id == parent_id]:
            children = [item for item in self.shelf_folders if item.parent_id == folder.id]
            if children:
                child_menu = menu.addMenu(f"🗂️ {folder.name}")
                child_menu.addAction("ここへ移動", lambda checked=False, folder_id=folder.id: self.move_selected_books(folder_id))
                child_menu.addSeparator(); self._populate_move_menu(child_menu, folder.id)
            else:
                menu.addAction(f"📚 {folder.name}", lambda checked=False, folder_id=folder.id: self.move_selected_books(folder_id))

    def move_selected_books(self, folder_id: str) -> None:
        ids = {item.data(Qt.ItemDataRole.UserRole) for item in self.shelf.selectedItems()}
        for book in self.books:
            if book.id in ids: book.folder_id = folder_id
        if ids: self._save_books(); self._refresh_bookshelf()

    def add_shelf_folder(self, parent_id: str = "") -> None:
        if not isinstance(parent_id, str): parent_id = ""
        title = "子フォルダを追加" if parent_id else "本棚を追加"
        name, ok = QInputDialog.getText(self, title, "名前"); name = name.strip()
        if ok and name:
            folder = ShelfFolder(name=name, parent_id=parent_id); self.shelf_folders.append(folder); self._save_books(); self._refresh_shelf_folders(folder.id); self._refresh_bookshelf()

    def add_child_shelf_folder(self) -> None:
        folder = self._selected_real_folder()
        self.add_shelf_folder(folder.id if folder else "")

    def _selected_real_folder(self) -> ShelfFolder | None:
        folder_id = self._current_folder_filter(); return next((folder for folder in self.shelf_folders if folder.id == folder_id), None)

    def _selected_real_folders(self) -> list[ShelfFolder]:
        selected_ids = {item.data(0, Qt.ItemDataRole.UserRole) for item in self.folder_list.selectedItems()}
        return [folder for folder in self.shelf_folders if folder.id in selected_ids]

    def rename_shelf_folder(self) -> None:
        item = self.folder_list.currentItem()
        if item and item.data(0, Qt.ItemDataRole.UserRole) != ADD_SHELF_ITEM_ID: self.folder_list.editItem(item, 0)

    def delete_shelf_folder(self) -> None:
        folders = self._selected_real_folders()
        if not folders:
            folder = self._selected_real_folder()
            folders = [folder] if folder else []
        if not folders: return
        names = "、".join(f"「{folder.name}」" for folder in folders)
        if QMessageBox.question(self, "本棚を削除", f"{names}と配下のフォルダを削除しますか？\n登録だけを削除し、実ファイルは削除しません。") != QMessageBox.StandardButton.Yes: return
        folder_ids: set[str] = set()
        for folder in folders: folder_ids.update(self._descendant_folder_ids(folder.id))
        self.books = [book for book in self.books if book.folder_id not in folder_ids]
        self.shelf_folders = [item for item in self.shelf_folders if item.id not in folder_ids]; self._save_books(); self._refresh_shelf_folders(); self._refresh_bookshelf()

    def _folder_menu(self, pos) -> None:
        item = self.folder_list.itemAt(pos)
        if item and item.data(0, Qt.ItemDataRole.UserRole) == ADD_SHELF_ITEM_ID:
            menu = QMenu(self); menu.addAction("本棚を追加", lambda: self.add_shelf_folder("")); menu.exec(self.folder_list.mapToGlobal(pos)); return
        if item and item not in self.folder_list.selectedItems(): self.folder_list.setCurrentItem(item)
        folder = self._selected_real_folder(); menu = QMenu(self); menu.addAction("本棚を追加", lambda: self.add_shelf_folder(""))
        if folder:
            menu.addAction("子フォルダを追加", lambda: self.add_shelf_folder(folder.id)); menu.addAction("名前を変更", self.rename_shelf_folder); menu.addAction("削除", self.delete_shelf_folder)
        menu.exec(self.folder_list.mapToGlobal(pos))

    def _open_book_location(self, item: QListWidgetItem) -> None:
        book = self._book_by_id(item.data(Qt.ItemDataRole.UserRole))
        if book:
            target = Path(book.path) if Path(book.path).is_dir() else Path(book.path).parent; QDesktopServices.openUrl(QUrl.fromLocalFile(str(target)))

    def _book_by_id(self, book_id: str) -> BookEntry | None:
        return next((book for book in self.books if book.id == book_id), None)

    def open_selected_book(self) -> None:
        if not self.isVisible(): return
        item = self.shelf.currentItem()
        if item:
            book = self._book_by_id(item.data(Qt.ItemDataRole.UserRole))
            if book: self.open_source(book.path, book.id, from_bookshelf=True)

    def open_source(self, path: str, book_id: str = "", initial_page: int | None = None, from_bookshelf: bool = False) -> None:
        source = ImageSource(path); count = source.page_count()
        if count == 0:
            extra = "\nRARでは7-Zip、UnRAR、WinRAR、bsdtarのいずれかが必要な場合があります。" if archive_kind(path) == "rar" else ""; QMessageBox.warning(self, "開けません", "対応する画像が見つからないか、書庫を読み込めません。" + extra); return
        self.source, self.current_book_id, self.preview_from_bookshelf = source, book_id, from_bookshelf
        self.source_landscape = source.majority_landscape()
        self._prefetch_token += 1; self.prefetch_pool.clear(); self._page_cache.clear(); self._current_image = QImage()
        self._preview_center_anchor = None
        self.bookshelf_return_to_preview = False
        saved = initial_page if initial_page is not None else (self.settings.get("last_page_by_book", {}).get(book_id, 0) if book_id else 0); self.page_index = min(max(0, int(saved)), count - 1)
        self._page_thumbnail_token += 1; self._requested_page_thumbnails.clear(); self.hide(); self.preview_window.show(); self.preview_window.raise_(); self.preview_window.activateWindow()
        if self.preview_stack.currentIndex() == 0: self._load_current_page()
        else: self._populate_page_thumbnails()

    def open_external_path(self, path: str, from_bookshelf: bool = False) -> None:
        target = Path(path).resolve()
        if target.is_dir() or is_archive_path(target): self.open_source(str(target), from_bookshelf=from_bookshelf); return
        if target.is_file() and target.suffix.casefold() in IMAGE_EXTENSIONS:
            source = ImageSource(target.parent); names = source.pages(); wanted = target.name.casefold(); index = next((i for i, name in enumerate(names) if name.casefold() == wanted), 0); self.open_source(str(target.parent), initial_page=index, from_bookshelf=from_bookshelf); return
        QMessageBox.warning(self, "開けません", "対応する画像、書庫、フォルダではありません。")

    def _load_current_page(self) -> None:
        if not self.source: return
        self._loading_token += 1; token = self._loading_token; viewport = self.view.viewport().size(); screen_width = (self.preview_window.screen() or QApplication.primaryScreen()).availableGeometry().width()
        decode_width = screen_width * 2 if self.settings.get("window_resize_mode", "image_ratio") == "image_ratio" else viewport.width() * 2; target = QSize(max(1000, decode_width), max(1000, viewport.height() * 2))
        count = self.source.page_count(); self.status.setText(f"読み込み中…  {self.source.title}  |  {self.page_index + 1} / {count}")
        if self.page_index in self._page_cache:
            image = self._page_cache.pop(self.page_index); QTimer.singleShot(0, lambda current_token=token, cached=image: self._page_ready(current_token, cached)); return
        task = PageLoadTask(token, self.source, self.page_index, target, int(self.settings["max_decode_megapixels"])); task.signals.ready.connect(self._page_ready); self.page_pool.clear(); self.page_pool.start(task)

    def _page_ready(self, token: int, image: QImage) -> None:
        if token != self._loading_token or not self.source: return
        if image.isNull(): self.status.setText(f"画像を読み込めませんでした  |  {self.source.page_label(self.page_index)}"); return
        self._current_image = image.copy(); self.view.set_image(image); count = self.source.page_count(); self.status.setText(f"{self.source.title}  |  {self.page_index + 1} / {count}  |  {self.source.page_label(self.page_index)}"); self.preview_window.setWindowTitle(f"LightViewer - {self.source.page_label(self.page_index)}")
        if self.current_book_id and self.settings.get("remember_last_book"): self.settings["last_book_id"] = self.current_book_id; self.settings.setdefault("last_page_by_book", {})[self.current_book_id] = self.page_index; self.settings_save_timer.start()
        self._resize_width_for_image(image); self._prefetch_neighbors()
        if not self._startup_log_finished:
            self._startup_log("最初の画像表示", finish=True)
            if not self._bookshelf_loaded:
                QTimer.singleShot(250, self._ensure_bookshelf_loaded)

    def _prefetch_neighbors(self) -> None:
        if not self.source: return
        target = QSize(max(1000, self.view.viewport().width() * 2), max(1000, self.view.viewport().height() * 2)); token = self._prefetch_token; maximum = int(self.settings["max_decode_megapixels"])
        for index in (self.page_index + 1, self.page_index - 1):
            if not 0 <= index < self.source.page_count() or index in self._page_cache: continue
            task = PrefetchTask(token, self.source, index, target, maximum); task.signals.ready.connect(self._prefetch_ready); self.prefetch_pool.start(task, -1)

    def _prefetch_ready(self, token: int, index: int, image: QImage) -> None:
        if token != self._prefetch_token or image.isNull(): return
        self._page_cache[index] = image
        while len(self._page_cache) > 2: self._page_cache.pop(next(iter(self._page_cache)))

    def _resize_width_for_image(self, image: QImage) -> None:
        if self.settings.get("window_resize_mode", "image_ratio") != "image_ratio" or self.preview_window.isFullScreen() or image.height() <= 0: return
        if self._preview_center_anchor is None: self._preview_center_anchor = self.preview_window.geometry().center()
        screen = (self.preview_window.screen() or QApplication.primaryScreen()).availableGeometry(); target_height = min(int(self.settings["preview_window_height"]), screen.height()); chrome = max(70, self.preview_window.height() - self.view.viewport().height()); image_height = max(100, target_height - chrome); desired = min(max(420, round(image_height * image.width() / image.height()) + 20), screen.width()); self._resize_window_center(self.preview_window, desired, target_height, self._preview_center_anchor)

    def toggle_preview_mode(self) -> None:
        if not self.source: return
        self._remember_preview_size()
        if self.preview_stack.currentIndex() == 0:
            self.preview_stack.setCurrentIndex(1); self.preview_mode_button.setText("▣"); self.preview_mode_button.setToolTip("単一表示へ切替"); self._resize_window_center(self.preview_window, int(self.settings["preview_thumbnail_width"]), int(self.settings["preview_thumbnail_height"])); self._populate_page_thumbnails()
        else:
            self.preview_stack.setCurrentIndex(0); self.preview_mode_button.setText("▦"); self.preview_mode_button.setToolTip("サムネイル表示へ切替"); self._resize_window_center(self.preview_window, int(self.settings["preview_window_width"]), int(self.settings["preview_window_height"])); self._load_current_page()
        self.settings_save_timer.start()

    def _populate_page_thumbnails(self) -> None:
        if not self.source: return
        self._page_thumbnail_token += 1; self._requested_page_thumbnails.clear(); self.page_thumbnail_pool.clear(); self.page_thumbnails.clear(); size = int(self.settings["preview_thumbnail_size"]); self.page_thumbnail_delegate.set_landscape_mode(self.source_landscape); self.page_thumbnail_delegate.set_width(size); self.page_thumbnails.setIconSize(QSize(round(size * 2.76), round(size * 2.76))); self.page_thumbnails.setGridSize(self.page_thumbnail_delegate.cell_size)
        for index, name in enumerate(self.source.pages()):
            item = QListWidgetItem(f"{index + 1}\n{Path(name).name}"); item.setData(Qt.ItemDataRole.UserRole, index); item.setToolTip(name); self.page_thumbnails.addItem(item)
        self.page_thumbnails.setCurrentRow(self.page_index); self.page_thumbnails.scrollToItem(self.page_thumbnails.item(self.page_index)); QTimer.singleShot(0, self._request_visible_page_thumbnails)

    def _request_visible_page_thumbnails(self) -> None:
        if not self.source or self.preview_stack.currentIndex() != 1: return
        cell = self.page_thumbnail_delegate.cell_size; viewport = self.page_thumbnails.viewport().size()
        columns = max(1, viewport.width() // max(1, cell.width()))
        first_grid_row = max(0, self.page_thumbnails.verticalScrollBar().value() // max(1, cell.height()))
        visible_grid_rows = max(2, viewport.height() // max(1, cell.height()) + 2)
        start = max(0, (first_grid_row - 1) * columns)
        end = min(self.page_thumbnails.count(), (first_grid_row + visible_grid_rows + 2) * columns)
        token = self._page_thumbnail_token; size = int(self.settings["preview_thumbnail_size"])
        for index in range(start, end):
            if index in self._requested_page_thumbnails: continue
            self._requested_page_thumbnails.add(index); task = PageThumbnailTask(token, self.source, index, size); task.signals.ready.connect(self._page_thumbnail_ready); self.page_thumbnail_pool.start(task)

    def _page_thumbnail_ready(self, token: int, index: int, image: QImage) -> None:
        if token != self._page_thumbnail_token or image.isNull() or not 0 <= index < self.page_thumbnails.count(): return
        self.page_thumbnails.item(index).setIcon(QIcon(QPixmap.fromImage(image)))

    def _zoom_preview_thumbnails(self, direction: int) -> None:
        self.settings["preview_thumbnail_size"] = max(80, min(320, int(self.settings["preview_thumbnail_size"]) + direction * 15)); self.settings_save_timer.start(); self._populate_page_thumbnails()

    def _open_page_thumbnail(self, item: QListWidgetItem) -> None:
        self.page_index = int(item.data(Qt.ItemDataRole.UserRole)); self.toggle_preview_mode()

    def _move_preview_thumb_selection(self, delta: int) -> None:
        if not self.page_thumbnails.count(): return
        row = self.page_thumbnails.currentRow(); row = 0 if row < 0 else max(0, min(self.page_thumbnails.count() - 1, row + delta)); self.page_thumbnails.setCurrentRow(row); self.page_thumbnails.scrollToItem(self.page_thumbnails.item(row)); self.page_index = row

    def next_page(self) -> None:
        if not self.preview_window.isVisible(): self._move_shelf_selection(1); return
        if self.preview_stack.currentIndex() == 1: self._move_preview_thumb_selection(1); return
        if not self.source: return
        if self.page_index + 1 < self.source.page_count(): self.page_index += 1; self._load_current_page()
        elif self.current_book_id: self.next_book()

    def previous_page(self) -> None:
        if not self.preview_window.isVisible(): self._move_shelf_selection(-1); return
        if self.preview_stack.currentIndex() == 1: self._move_preview_thumb_selection(-1); return
        if not self.source: return
        if self.page_index > 0: self.page_index -= 1; self._load_current_page()
        elif self.current_book_id: self.previous_book(last_page=True)

    def _move_book(self, delta: int, last_page: bool = False) -> None:
        active_books = self._filtered_books(); ids = [book.id for book in active_books]
        try: index = ids.index(self.current_book_id)
        except ValueError: index = -1 if delta > 0 else 0
        target = index + delta
        if 0 <= target < len(active_books):
            book = active_books[target]; self.open_source(book.path, book.id, from_bookshelf=True)
            if last_page and self.source: self.page_index = max(0, self.source.page_count() - 1); self._load_current_page()

    def _move_shelf_selection(self, delta: int) -> None:
        if not self.shelf.count(): return
        row = self.shelf.currentRow(); row = 0 if row < 0 else max(0, min(self.shelf.count() - 1, row + delta)); self.shelf.setCurrentRow(row); self.shelf.scrollToItem(self.shelf.item(row))

    def next_book(self) -> None:
        self._move_shelf_selection(1) if not self.preview_window.isVisible() else self._move_book(1)

    def previous_book(self, last_page: bool = False) -> None:
        self._move_shelf_selection(-1) if not self.preview_window.isVisible() else self._move_book(-1, last_page)

    def fit_image(self) -> None:
        if self.preview_window.isVisible() and self.preview_stack.currentIndex() == 0: self.view.fit_image()

    def actual_size(self) -> None:
        if self.preview_window.isVisible() and self.preview_stack.currentIndex() == 0: self.view.actual_size()

    def _selected_preview_pages(self) -> list[int]:
        if self.preview_stack.currentIndex() != 1: return []
        return sorted({int(item.data(Qt.ItemDataRole.UserRole)) for item in self.page_thumbnails.selectedItems()})

    def open_print_preview(self) -> None:
        if not self.source: return
        # Printing pulls in Qt print support; defer it until the user asks for it.
        from printing import ImagePrintPreviewDialog
        dialog = ImagePrintPreviewDialog(self.source, self.page_index, self._selected_preview_pages(), int(self.settings["max_decode_megapixels"]), self.preview_window)
        dialog.exec()

    def open_editor(self) -> None:
        if not self.source: return
        selected = self._selected_preview_pages()
        if selected: self.page_index = selected[0]
        source, index = self.source, self.page_index
        mode = str(self.settings.get("editor_resolution_mode", "full_save"))
        if mode == "original":
            size = source.page_size(index)
            target = size if size.isValid() else QSize(12000, 12000)
            max_megapixels = max(1, math.ceil(target.width() * target.height() / 1_000_000))
        else:
            target = QSize(max(2000, self.preview_window.width() * 3), max(2000, self.preview_window.height() * 3))
            max_megapixels = max(24, int(self.settings["max_decode_megapixels"]))
        label = source.page_label(index)
        if source.is_archive: source_path = source.path
        elif source.path.is_dir(): source_path = source.path / source.pages()[index]
        else: source_path = source.path
        self._editor_loading_token += 1
        token = self._editor_loading_token
        self.editor_load_pool.clear()
        self._editor_pending = {
            "token": token, "source": source, "index": index, "label": label,
            "source_path": source_path, "full_save": mode == "full_save",
        }
        progress = QProgressDialog("編集用画像を読み込んでいます…", "キャンセル", 0, 0, self.preview_window)
        progress.setWindowTitle("編集を開く"); progress.setWindowModality(Qt.WindowModality.WindowModal)
        progress.setMinimumDuration(0); progress.canceled.connect(lambda: self._cancel_editor_load(token))
        self._editor_progress = progress; progress.show()
        task = EditorLoadTask(token, source, index, target, max_megapixels)
        task.signals.ready.connect(self._editor_image_ready)
        self.editor_load_pool.start(task)

    def _cancel_editor_load(self, token: int) -> None:
        if token != self._editor_loading_token: return
        self._editor_loading_token += 1; self._editor_pending = None; self.editor_load_pool.clear()
        if self._editor_progress: self._editor_progress.close(); self._editor_progress = None

    def _ensure_editor_window(self) -> None:
        if self.editor_window is not None:
            return
        # editor.py is one of the heaviest modules. Import/create it on first use.
        from editor import ImageEditorWindow
        self.editor_window = ImageEditorWindow()
        self.editor_window.return_requested.connect(self._return_from_editor)
        self.editor_window.set_key_bindings(self.settings["key_bindings"])

    def _editor_image_ready(self, token: int, image: QImage) -> None:
        if token != self._editor_loading_token or not self._editor_pending: return
        pending = self._editor_pending; self._editor_pending = None
        if self._editor_progress: self._editor_progress.close(); self._editor_progress = None
        if image.isNull(): QMessageBox.warning(self.preview_window, "編集できません", "画像を読み込めませんでした。"); return
        self._ensure_editor_window()
        self.editor_window.load_image(
            image, pending["label"], pending["source_path"],
            full_resolution_source=pending["source"] if pending["full_save"] else None,
            page_index=pending["index"], export_full_resolution=pending["full_save"],
        )
        self.editor_window.set_background(self.settings["background_color"])
        if self.editor_window is not None:
            self.editor_window.set_key_bindings(self.settings["key_bindings"])
        self.editor_window.prepare_for_preview(self.preview_window.geometry())
        self.preview_window.hide(); self.editor_window.show(); self.editor_window.raise_(); self.editor_window.activateWindow()
        QTimer.singleShot(0, self.editor_window.finalize_layout)

    def _return_from_editor(self) -> None:
        self.preview_window.show(); self.preview_window.raise_(); self.preview_window.activateWindow()

    def toggle_shelf_mode(self) -> None:
        self.settings["bookshelf_mode"] = "list" if self.settings["bookshelf_mode"] == "thumbnail" else "thumbnail"; save_settings(self.settings); self._refresh_bookshelf()

    def toggle_fullscreen(self) -> None:
        if self.preview_window.isVisible(): self.preview_window.showNormal() if self.preview_window.isFullScreen() else self.preview_window.showFullScreen()
        else: self.showNormal() if self.isFullScreen() else self.showFullScreen()

    def toggle_slideshow(self) -> None:
        if self.slideshow_timer.isActive(): self.slideshow_timer.stop()
        elif self.source and self.preview_stack.currentIndex() == 0: self.slideshow_timer.start()

    def show_bookshelf(self) -> None:
        if not self.preview_window.isVisible(): return
        self._ensure_bookshelf_loaded()
        self.bookshelf_return_to_preview = True
        self.slideshow_timer.stop(); self._remember_preview_size(); self._loading_token += 1; self._page_thumbnail_token += 1
        self.page_pool.clear(); self.page_thumbnail_pool.clear(); self.preview_window.hide(); self.show(); self.raise_(); self.activateWindow(); self.shelf.setFocus(); self.settings_save_timer.start()

    def start_preview(self, restore_session: bool = True) -> None:
        if restore_session:
            self._ensure_bookshelf_loaded()
        if restore_session and self.settings.get("remember_last_book"):
            book = self._book_by_id(self.settings.get("last_book_id", ""))
            if book and Path(book.path).exists():
                self.open_source(book.path, book.id, from_bookshelf=False)
                return
        self.preview_from_bookshelf = False
        self.bookshelf_return_to_preview = False
        self.preview_stack.setCurrentIndex(0)
        self.preview_mode_button.setText("▦"); self.preview_mode_button.setToolTip("サムネイル表示へ切替")
        self.view.clear_image(); self.status.setText("画像を開くには「本棚」を押すか、画像ファイルを関連付けて起動してください")
        self.hide(); self.preview_window.show(); self.preview_window.raise_(); self.preview_window.activateWindow()

    def _preview_close_requested(self) -> None:
        if self.preview_from_bookshelf: self.show_bookshelf()
        else:
            self._remember_preview_size(); self._app_closing = True; self.preview_window.allow_close = True; self.close(); QApplication.quit()

    def dragEnterEvent(self, event) -> None:
        if event.mimeData().hasUrls(): event.acceptProposedAction()
        else: super().dragEnterEvent(event)

    def dragMoveEvent(self, event) -> None:
        point = self.shelf.mapFrom(self, event.position().toPoint())
        if event.mimeData().hasUrls() and self.shelf.rect().contains(point): event.acceptProposedAction()
        else: event.ignore()

    def dropEvent(self, event) -> None:
        point = self.shelf.mapFrom(self, event.position().toPoint())
        paths = [url.toLocalFile() for url in event.mimeData().urls() if url.isLocalFile()]
        if paths and self.shelf.rect().contains(point):
            self._shelf_paths_dropped(paths); event.acceptProposedAction()
        else:
            event.ignore()

    def closeEvent(self, event) -> None:
        self.settings["bookshelf_window_width"] = self.width(); self.settings["bookshelf_window_height"] = self.height(); self._remember_preview_size(); self.slideshow_timer.stop(); self._loading_token += 1; self._page_thumbnail_token += 1; save_settings(self.settings)
        if not self._app_closing and self.bookshelf_return_to_preview:
            event.ignore()
            self.bookshelf_return_to_preview = False
            self.preview_from_bookshelf = False
            self.hide(); self.preview_window.show(); self.preview_window.raise_(); self.preview_window.activateWindow()
            return
        if self.editor_window is not None:
            self.editor_window.hide()
        self._editor_loading_token += 1
        if self._editor_progress: self._editor_progress.close(); self._editor_progress = None
        for pool in (self.thumbnail_pool, self.page_pool, self.page_thumbnail_pool, self.prefetch_pool, self.editor_load_pool): pool.clear()
        for pool in (self.thumbnail_pool, self.page_pool, self.page_thumbnail_pool, self.prefetch_pool, self.editor_load_pool): pool.waitForDone()
        if not self._app_closing: self._app_closing = True; self.preview_window.allow_close = True; self.preview_window.close()
        event.accept()

    def resizeEvent(self, event) -> None:
        super().resizeEvent(event)
        if hasattr(self, "shelf") and self.isVisible(): QTimer.singleShot(30, self._request_visible_shelf_thumbnails)



def _apply_windows_app_identity_and_icon(app: QApplication) -> None:
    """Apply the portable onedir icon to Windows taskbar/window/Alt+Tab."""
    try:
        if sys.platform == "win32":
            import ctypes
            ctypes.windll.shell32.SetCurrentProcessExplicitAppUserModelID(
                "LightViewer.LightViewer"
            )
    except Exception:
        pass

    try:
        # Runtime icon is embedded in the Python package, so icon.ico is not
        # required beside LightViewer.exe after an onedir build.
        icon_bytes = base64.b64decode(APP_ICON_B64)
        pixmap = QPixmap()
        if pixmap.loadFromData(icon_bytes, "ICO"):
            icon = QIcon(pixmap)
            if not icon.isNull():
                app.setWindowIcon(icon)
    except Exception:
        pass


def main() -> int:
    startup_clock = time.perf_counter()
    app = QApplication(sys.argv); app.setApplicationName("LightViewer"); _apply_windows_app_identity_and_icon(app); app.setStyle("Fusion"); app.setFont(QFont("Segoe UI", 10)); app.setStyleSheet(APP_STYLE)
    startup_path = sys.argv[1] if len(sys.argv) > 1 and Path(sys.argv[1]).exists() else ""
    server_name = "LightViewer-user-instance-v1"
    probe = QLocalSocket()
    probe.connectToServer(server_name)
    # Local IPC normally connects in a few ms. 350 ms made every cold start
    # pay a visible fixed delay when no previous instance existed.
    if probe.waitForConnected(60):
        payload = startup_path if startup_path else "__ACTIVATE__"
        probe.write(payload.encode("utf-8")); probe.flush(); probe.waitForBytesWritten(700); probe.disconnectFromServer()
        return 0
    QLocalServer.removeServer(server_name)
    server = QLocalServer(app)
    if not server.listen(server_name):
        QMessageBox.warning(None, "起動できません", "アプリの単一起動用通信を開始できませんでした。")
        return 1
    window = ViewerWindow(restore_session=False, fast_external_start=bool(startup_path), startup_clock=startup_clock)
    window._startup_log("起動開始")

    def accept_instance_request() -> None:
        while server.hasPendingConnections():
            socket = server.nextPendingConnection()

            def receive(sock=socket) -> None:
                payload = bytes(sock.readAll()).decode("utf-8", errors="replace")
                if payload and payload != "__ACTIVATE__" and Path(payload).exists():
                    window.open_external_path(payload, from_bookshelf=False)
                else:
                    window.hide(); window.preview_window.showNormal(); window.preview_window.show()
                    window.preview_window.raise_(); window.preview_window.activateWindow()
                sock.disconnectFromServer(); sock.deleteLater()

            socket.readyRead.connect(receive)
            if socket.bytesAvailable(): receive()

    server.newConnection.connect(accept_instance_request)
    if startup_path: QTimer.singleShot(0, lambda: window.open_external_path(startup_path, from_bookshelf=False))
    else: QTimer.singleShot(0, lambda: window.start_preview(restore_session=True))
    return app.exec()


if __name__ == "__main__":
    raise SystemExit(main())
