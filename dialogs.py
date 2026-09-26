from __future__ import annotations

from copy import deepcopy
import shutil

from PySide6.QtCore import Qt
from PySide6.QtGui import QKeySequence
from PySide6.QtWidgets import (
    QCheckBox, QColorDialog, QComboBox, QDialog, QDialogButtonBox, QFormLayout,
    QAbstractItemView, QFrame, QHBoxLayout, QLabel, QLineEdit, QMessageBox, QPushButton,
    QScrollArea, QSpinBox, QTabWidget, QTreeWidget, QTreeWidgetItem, QVBoxLayout, QWidget,
)

from app_config import CACHE_DIR
from sources import format_diagnostics


class ShelfSelectionDialog(QDialog):
    """Select exactly one user-ordered bookshelf folder."""

    def __init__(self, folders, parent=None):
        super().__init__(parent)
        self.setWindowTitle("登録先の本棚を選択")
        self.resize(430, 520)
        root = QVBoxLayout(self)
        note = QLabel("現在表示している画像フォルダ／圧縮ファイルの登録先を選択してください。")
        note.setWordWrap(True); root.addWidget(note)
        self.tree = QTreeWidget(); self.tree.setHeaderHidden(True)
        self.tree.setSelectionMode(QAbstractItemView.SelectionMode.SingleSelection)
        self.tree.setSortingEnabled(False)
        root.addWidget(self.tree, 1)
        items = {}
        remaining = list(folders)
        while remaining:
            progressed = False
            for folder in remaining[:]:
                if folder.parent_id and folder.parent_id not in items:
                    continue
                has_children = any(child.parent_id == folder.id for child in folders)
                item = QTreeWidgetItem([f"{'🗂️' if has_children else '📚'}  {folder.name}"])
                item.setData(0, Qt.ItemDataRole.UserRole, folder.id)
                if folder.parent_id: items[folder.parent_id].addChild(item)
                else: self.tree.addTopLevelItem(item)
                items[folder.id] = item; remaining.remove(folder); progressed = True
            if not progressed:
                break
        self.tree.expandAll()
        if self.tree.topLevelItemCount(): self.tree.setCurrentItem(self.tree.topLevelItem(0))
        self.tree.itemDoubleClicked.connect(lambda _item, _column: self.accept())
        buttons = QDialogButtonBox(QDialogButtonBox.StandardButton.Ok | QDialogButtonBox.StandardButton.Cancel)
        buttons.accepted.connect(self.accept); buttons.rejected.connect(self.reject); root.addWidget(buttons)

    def selected_folder_id(self) -> str:
        item = self.tree.currentItem()
        return str(item.data(0, Qt.ItemDataRole.UserRole)) if item else ""


ACTION_NAMES = {
    "next_page": "次のページ",
    "previous_page": "前のページ",
    "next_book": "次の本",
    "previous_book": "前の本",
    "show_bookshelf": "本棚を表示",
    "open_selected": "選択した本を開く",
    "toggle_fullscreen": "全画面表示",
    "zoom_in": "拡大",
    "zoom_out": "縮小",
    "fit_image": "全体表示",
    "actual_size": "100%表示",
    "rotate_left": "左回転",
    "rotate_right": "右回転",
    "toggle_slideshow": "スライドショー",
    "toggle_preview_mode": "単一／サムネイル切替",
    "open_print": "印刷",
    "open_editor": "編集画面を開く",
    "save_to_bookshelf": "現在の本を本棚へ登録",
    "editor_undo": "編集：元に戻す",
    "editor_redo": "編集：やり直し",
    "editor_save": "編集：保存",
    "editor_save_as": "編集：名前を付けて保存",
    "editor_select_all": "編集：すべて選択",
    "editor_tool_select": "編集：選択ツール",
    "editor_tool_ellipse": "編集：丸ツール",
    "editor_tool_rect": "編集：四角ツール",
    "editor_tool_line": "編集：線ツール",
    "editor_tool_arrow": "編集：矢印ツール",
    "editor_tool_text": "編集：枠テキスト",
    "editor_tool_text_fill": "編集：白背景テキスト",
}


class KeySequenceEdit(QLineEdit):
    def keyPressEvent(self, event) -> None:
        if event.key() in (Qt.Key.Key_Backspace, Qt.Key.Key_Delete):
            self.clear()
            return
        sequence = QKeySequence(event.keyCombination()).toString(QKeySequence.SequenceFormat.PortableText)
        if sequence:
            self.setText(sequence)


class SettingsDialog(QDialog):
    def __init__(self, settings: dict, parent=None):
        super().__init__(parent)
        self.setWindowTitle("設定")
        self.resize(620, 580)
        self.setMinimumSize(560, 500)
        self._settings = deepcopy(settings)
        root = QVBoxLayout(self)
        root.setContentsMargins(10, 10, 10, 10); root.setSpacing(8)
        tabs = QTabWidget()
        root.addWidget(tabs)

        general = QWidget()
        form = QFormLayout(general)
        form.setVerticalSpacing(6); form.setHorizontalSpacing(12)
        self.preview_height = QSpinBox(); self.preview_height.setRange(400, 2160); self.preview_height.setValue(int(settings["preview_window_height"]))
        self.preview_width = QSpinBox(); self.preview_width.setRange(500, 3840); self.preview_width.setValue(int(settings["preview_window_width"]))
        self.shelf_height = QSpinBox(); self.shelf_height.setRange(400, 2160); self.shelf_height.setValue(int(settings["bookshelf_window_height"]))
        self.shelf_width = QSpinBox(); self.shelf_width.setRange(600, 3840); self.shelf_width.setValue(int(settings["bookshelf_window_width"]))
        self.preview_thumb_height = QSpinBox(); self.preview_thumb_height.setRange(500, 2160); self.preview_thumb_height.setValue(int(settings["preview_thumbnail_height"]))
        self.preview_thumb_width = QSpinBox(); self.preview_thumb_width.setRange(700, 3840); self.preview_thumb_width.setValue(int(settings["preview_thumbnail_width"]))
        self.window_mode = QComboBox()
        self.window_mode.addItem("画像の縦横比に合わせて横幅を変更", "image_ratio")
        self.window_mode.addItem("ウィンドウサイズを固定して画像を合わせる", "fixed")
        self.window_mode.setCurrentIndex(max(0, self.window_mode.findData(settings.get("window_resize_mode", "image_ratio"))))
        self.megapixels = QSpinBox(); self.megapixels.setRange(2, 100); self.megapixels.setSuffix(" MP"); self.megapixels.setValue(int(settings["max_decode_megapixels"]))
        self.thumb_size = QSpinBox(); self.thumb_size.setRange(96, 320); self.thumb_size.setSuffix(" px"); self.thumb_size.setValue(int(settings["thumbnail_size"]))
        self.preview_thumb_size = QSpinBox(); self.preview_thumb_size.setRange(80, 320); self.preview_thumb_size.setSuffix(" px"); self.preview_thumb_size.setValue(int(settings["preview_thumbnail_size"]))
        self.mode = QComboBox(); self.mode.addItem("サムネイル", "thumbnail"); self.mode.addItem("リスト", "list"); self.mode.setCurrentIndex(max(0, self.mode.findData(settings["bookshelf_mode"])))
        self.remember = QCheckBox("最後に開いた本とページを記憶"); self.remember.setChecked(bool(settings["remember_last_book"]))
        self.startup_debug = QCheckBox("起動時間を計測する（デバッグ）"); self.startup_debug.setChecked(bool(settings.get("startup_debug", False)))
        self.slideshow = QSpinBox(); self.slideshow.setRange(1, 60); self.slideshow.setSuffix(" 秒"); self.slideshow.setValue(int(settings["slideshow_seconds"]))
        self.color_button = QPushButton(settings["background_color"]); self.color_button.clicked.connect(self._pick_color)
        self.cache_limit = QSpinBox(); self.cache_limit.setRange(64, 8192); self.cache_limit.setSuffix(" MB"); self.cache_limit.setValue(int(settings.get("cache_limit_mb", 512)))
        self.editor_resolution = QComboBox()
        self.editor_resolution.addItem("軽量表示の解像度で編集・保存", "display")
        self.editor_resolution.addItem("元解像度を読み込んで編集", "original")
        self.editor_resolution.addItem("軽量編集・保存時だけ元解像度", "full_save")
        self.editor_resolution.setCurrentIndex(max(0, self.editor_resolution.findData(settings.get("editor_resolution_mode", "full_save"))))
        self.cache_label = QLabel(); self._update_cache_label()
        cache_row = QWidget(); cache_layout = QHBoxLayout(cache_row); cache_layout.setContentsMargins(0, 0, 0, 0); cache_layout.addWidget(self.cache_label); clear_cache = QPushButton("キャッシュを削除"); clear_cache.clicked.connect(self._clear_cache); cache_layout.addWidget(clear_cache)
        form.addRow("プレビュー・単一 横幅", self.preview_width)
        form.addRow("プレビュー・単一 高さ", self.preview_height)
        form.addRow("プレビュー・サムネ 横幅", self.preview_thumb_width)
        form.addRow("プレビュー・サムネ 高さ", self.preview_thumb_height)
        form.addRow("本棚 横幅", self.shelf_width)
        form.addRow("本棚 高さ", self.shelf_height)
        form.addRow("表示サイズ方式", self.window_mode)
        form.addRow("最大デコード解像度", self.megapixels)
        form.addRow("本棚のサムネイル", self.thumb_size)
        form.addRow("プレビューのサムネイル", self.preview_thumb_size)
        form.addRow("本棚の初期表示", self.mode)
        form.addRow("履歴", self.remember)
        form.addRow("デバッグ", self.startup_debug)
        form.addRow("スライドショー間隔", self.slideshow)
        form.addRow("背景色", self.color_button)
        form.addRow("キャッシュ上限", self.cache_limit)
        form.addRow("キャッシュ管理", cache_row)
        form.addRow("編集解像度", self.editor_resolution)
        diagnostics = QPushButton("対応形式を確認"); diagnostics.clicked.connect(self._show_diagnostics)
        form.addRow("環境診断", diagnostics)
        general_scroll = QScrollArea(); general_scroll.setWidgetResizable(True); general_scroll.setFrameShape(QFrame.Shape.NoFrame); general_scroll.setWidget(general)
        tabs.addTab(general_scroll, "表示・動作")

        keys = QWidget()
        key_form = QFormLayout(keys)
        key_form.setVerticalSpacing(6); key_form.setHorizontalSpacing(12)
        self.key_edits: dict[str, QLineEdit] = {}
        for action, label in ACTION_NAMES.items():
            edit = KeySequenceEdit()
            edit.setText(", ".join(settings["key_bindings"].get(action, [])))
            edit.setPlaceholderText("例: Right, Space")
            self.key_edits[action] = edit
            key_form.addRow(label, edit)
        note = QLabel("複数のキーはカンマで区切ります。Delete または Backspace で消去できます。")
        note.setWordWrap(True)
        key_form.addRow(note)
        key_scroll = QScrollArea(); key_scroll.setWidgetResizable(True); key_scroll.setFrameShape(QFrame.Shape.NoFrame); key_scroll.setWidget(keys)
        tabs.addTab(key_scroll, "キー設定")

        buttons = QDialogButtonBox(QDialogButtonBox.StandardButton.Ok | QDialogButtonBox.StandardButton.Cancel)
        buttons.accepted.connect(self.accept); buttons.rejected.connect(self.reject)
        root.addWidget(buttons)

    def _pick_color(self) -> None:
        color = QColorDialog.getColor(parent=self)
        if color.isValid():
            self.color_button.setText(color.name())

    def _update_cache_label(self) -> None:
        total = 0
        try: total = sum(path.stat().st_size for path in CACHE_DIR.glob("*") if path.is_file())
        except OSError: pass
        self.cache_label.setText(f"{total / 1024 / 1024:.1f} MB")

    def _clear_cache(self) -> None:
        if QMessageBox.question(self, "キャッシュを削除", "サムネイルキャッシュを削除しますか？\n必要な画像は次回表示時に再生成されます。") != QMessageBox.StandardButton.Yes: return
        try:
            if CACHE_DIR.exists(): shutil.rmtree(CACHE_DIR)
        except OSError:
            pass
        self._update_cache_label()

    def _show_diagnostics(self) -> None:
        QMessageBox.information(self, "対応形式の確認", format_diagnostics())

    def result_settings(self) -> dict:
        result = deepcopy(self._settings)
        result.update({
            "window_height": self.preview_height.value(),
            "window_width": self.preview_width.value(),
            "preview_window_height": self.preview_height.value(),
            "preview_window_width": self.preview_width.value(),
            "bookshelf_window_height": self.shelf_height.value(),
            "bookshelf_window_width": self.shelf_width.value(),
            "preview_thumbnail_height": self.preview_thumb_height.value(),
            "preview_thumbnail_width": self.preview_thumb_width.value(),
            "window_resize_mode": self.window_mode.currentData(),
            "auto_width_by_image": self.window_mode.currentData() == "image_ratio",
            "max_decode_megapixels": self.megapixels.value(),
            "thumbnail_size": self.thumb_size.value(),
            "preview_thumbnail_size": self.preview_thumb_size.value(),
            "bookshelf_mode": self.mode.currentData(),
            "remember_last_book": self.remember.isChecked(),
            "startup_debug": self.startup_debug.isChecked(),
            "slideshow_seconds": self.slideshow.value(),
            "background_color": self.color_button.text(),
            "cache_limit_mb": self.cache_limit.value(),
            "editor_resolution_mode": self.editor_resolution.currentData(),
        })
        result["key_bindings"] = {
            action: [part.strip() for part in edit.text().split(",") if part.strip()]
            for action, edit in self.key_edits.items()
        }
        return result
