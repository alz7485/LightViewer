from __future__ import annotations

import json
import os
import shutil
import sys
from pathlib import Path
from typing import Any


APP_NAME = "LightViewer"


def app_dir() -> Path:
    """Return the executable/script directory (portable application layout)."""
    if getattr(sys, "frozen", False):
        return Path(sys.executable).resolve().parent
    return Path(__file__).resolve().parent


SETTINGS_PATH = app_dir() / "setting.json"
BOOKSHELF_PATH = app_dir() / "bookshelf.json"
CACHE_DIR = app_dir() / "cache"
OBJECT_PRESETS_PATH = app_dir() / "edit_object_presets.json"


DEFAULT_KEYS = {
    "next_page": ["Right", "Space", "PageDown"],
    "previous_page": ["Left", "Backspace", "PageUp"],
    "next_book": ["Down"],
    "previous_book": ["Up"],
    "show_bookshelf": ["B", "Escape"],
    "open_selected": ["Return", "Enter"],
    "toggle_fullscreen": ["F11"],
    "zoom_in": ["+", "="],
    "zoom_out": ["-"],
    "fit_image": ["0"],
    "actual_size": ["1"],
    "rotate_left": ["Ctrl+Left"],
    "rotate_right": ["Ctrl+Right"],
    "toggle_slideshow": ["S"],
    "toggle_preview_mode": ["T"],
    "open_print": ["Ctrl+P"],
    "open_editor": ["E"],
    "save_to_bookshelf": ["Ctrl+Shift+B"],
    "editor_undo": ["Ctrl+Z"],
    "editor_redo": ["Ctrl+Y", "Ctrl+Shift+Z"],
    "editor_save": ["Ctrl+S"],
    "editor_save_as": ["Ctrl+Shift+S"],
    "editor_select_all": ["Ctrl+A"],
    "editor_tool_select": ["V"],
    "editor_tool_ellipse": ["O"],
    "editor_tool_rect": ["R"],
    "editor_tool_line": ["L"],
    "editor_tool_arrow": ["A"],
    "editor_tool_text": ["T"],
    "editor_tool_text_fill": ["W"],
}


DEFAULT_SETTINGS: dict[str, Any] = {
    "window_height": 900,
    "window_width": 1280,
    "bookshelf_window_width": 1280,
    "bookshelf_window_height": 820,
    "preview_window_width": 1280,
    "preview_window_height": 900,
    "preview_thumbnail_width": 1400,
    "preview_thumbnail_height": 900,
    "window_resize_mode": "image_ratio",
    "auto_width_by_image": True,
    "max_decode_megapixels": 16,
    "bookshelf_mode": "thumbnail",
    "bookshelf_folder_id": "*",
    "thumbnail_size": 120,
    "preview_thumbnail_size": 120,
    "remember_last_book": True,
    "last_book_id": "",
    "last_page_by_book": {},
    "slideshow_seconds": 4,
    "background_color": "#17191c",
    "cache_limit_mb": 512,
    "bookshelf_sort": "name",
    "expanded_shelf_ids": [],
    "editor_resolution_mode": "full_save",
    "startup_debug": False,
    "key_bindings": DEFAULT_KEYS,
}


def _merge_defaults(value: dict[str, Any], default: dict[str, Any]) -> dict[str, Any]:
    merged = dict(default)
    for key, item in value.items():
        if key == "key_bindings" and isinstance(item, dict):
            merged[key] = {**DEFAULT_KEYS, **item}
        else:
            merged[key] = item
    # v0.1 compatibility: the old checkbox maps to the new explicit mode.
    if "window_resize_mode" in default and "window_resize_mode" not in value:
        merged["window_resize_mode"] = "image_ratio" if value.get("auto_width_by_image", True) else "fixed"
    # v0.2 compatibility: preview size inherits the former shared size once.
    if "preview_window_width" in default and "preview_window_width" not in value:
        merged["preview_window_width"] = int(value.get("window_width", default["preview_window_width"]))
    if "preview_window_height" in default and "preview_window_height" not in value:
        merged["preview_window_height"] = int(value.get("window_height", default["preview_window_height"]))
    return merged


def load_json(path: Path, default: Any) -> Any:
    try:
        with path.open("r", encoding="utf-8") as handle:
            value = json.load(handle)
        if isinstance(default, dict) and isinstance(value, dict):
            return _merge_defaults(value, default)
        return value
    except (OSError, json.JSONDecodeError, TypeError):
        backup = path.with_suffix(path.suffix + ".bak")
        try:
            with backup.open("r", encoding="utf-8") as handle:
                value = json.load(handle)
            if isinstance(default, dict) and isinstance(value, dict):
                return _merge_defaults(value, default)
            return value
        except (OSError, json.JSONDecodeError, TypeError):
            return dict(default) if isinstance(default, dict) else default


def save_json(path: Path, value: Any) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    temporary = path.with_suffix(path.suffix + ".tmp")
    with temporary.open("w", encoding="utf-8") as handle:
        json.dump(value, handle, ensure_ascii=False, indent=2)
    if path.exists():
        try: shutil.copy2(path, path.with_suffix(path.suffix + ".bak"))
        except OSError: pass
    os.replace(temporary, path)


def load_settings() -> dict[str, Any]:
    return load_json(SETTINGS_PATH, DEFAULT_SETTINGS)


def save_settings(settings: dict[str, Any]) -> None:
    save_json(SETTINGS_PATH, settings)
