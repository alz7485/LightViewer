from __future__ import annotations

import re
import os
import shutil
import subprocess
import sys
import tarfile
import tempfile
import uuid
import zipfile
from io import BytesIO
from dataclasses import dataclass, field
from pathlib import Path, PurePosixPath
from typing import Iterable
from functools import lru_cache

from PySide6.QtCore import QBuffer, QByteArray, QIODevice, QSize
from PySide6.QtGui import QImage, QImageReader


IMAGE_EXTENSIONS = {
    ".jpg", ".jpeg", ".png", ".webp", ".bmp", ".gif", ".tif", ".tiff",
    ".ico", ".jfif", ".jpe", ".pbm", ".pgm", ".ppm", ".pnm", ".xbm", ".xpm",
    ".avif", ".heic", ".heif", ".jp2", ".j2k", ".jpf", ".jpx", ".jpm", ".mj2",
    ".psd", ".tga", ".targa", ".dds", ".pcx", ".msp", ".sgi", ".rgb", ".rgba",
    ".bw", ".icns", ".svg", ".svgz", ".qoi", ".jxl",
}
ARCHIVE_EXTENSIONS = {".zip", ".cbz", ".rar", ".cbr", ".7z", ".cb7", ".tar", ".tgz", ".gz", ".bz2", ".xz"}
ARCHIVE_SUFFIXES = (
    ".zip", ".cbz", ".rar", ".cbr", ".7z", ".cb7", ".tar",
    ".tar.gz", ".tgz", ".tar.bz2", ".tbz2", ".tar.xz", ".txz",
)
_SPLIT_NUMBER = re.compile(r"(\d+)")


def natural_key(text: str) -> list[object]:
    return [int(part) if part.isdigit() else part.casefold() for part in _SPLIT_NUMBER.split(text)]


def is_image_name(name: str) -> bool:
    return PurePosixPath(name).suffix.casefold() in IMAGE_EXTENSIONS


def is_archive_path(path: str | Path) -> bool:
    return str(path).casefold().endswith(ARCHIVE_SUFFIXES)


def safe_archive_parts(name: str) -> tuple[str, ...] | None:
    normalized = name.replace("\\", "/")
    path = PurePosixPath(normalized)
    if path.is_absolute() or not path.parts or any(part in ("", ".", "..") for part in path.parts):
        return None
    return path.parts


def archive_kind(path: str | Path) -> str:
    name = str(path).casefold()
    if name.endswith((".zip", ".cbz")):
        return "zip"
    if name.endswith((".rar", ".cbr")):
        return "rar"
    if name.endswith((".7z", ".cb7")):
        return "7z"
    if name.endswith((".tar", ".tar.gz", ".tgz", ".tar.bz2", ".tbz2", ".tar.xz", ".txz")):
        return "tar"
    return ""


def _app_folder() -> Path:
    return Path(sys.executable).resolve().parent if getattr(sys, "frozen", False) else Path(__file__).resolve().parent


def _first_tool(names: tuple[str, ...], candidates: list[Path]) -> str:
    for name in names:
        found = shutil.which(name)
        if found:
            return found
    return next((str(path) for path in candidates if path.exists()), "")


@lru_cache(maxsize=1)
def rar_tools() -> dict[str, str]:
    """Find common portable and installed RAR extraction backends."""
    app_folder = _app_folder()
    roots = [Path(value) for value in (
        os.environ.get("ProgramFiles", ""), os.environ.get("ProgramFiles(x86)", ""),
        os.environ.get("LOCALAPPDATA", ""),
    ) if value]
    seven_candidates = [app_folder / name for name in ("7z.exe", "7zz.exe", "7za.exe")]
    unrar_candidates = [app_folder / "UnRAR.exe"]
    winrar_candidates = [app_folder / "WinRAR.exe"]
    for root in roots:
        seven_candidates.extend(root / "7-Zip" / name for name in ("7z.exe", "7zz.exe", "7za.exe"))
        seven_candidates.extend(root / "Programs" / "7-Zip" / name for name in ("7z.exe", "7zz.exe", "7za.exe"))
        unrar_candidates.append(root / "WinRAR" / "UnRAR.exe")
        unrar_candidates.append(root / "Programs" / "WinRAR" / "UnRAR.exe")
        winrar_candidates.append(root / "WinRAR" / "WinRAR.exe")
        winrar_candidates.append(root / "Programs" / "WinRAR" / "WinRAR.exe")
    return {
        "seven": _first_tool(("7z", "7zz", "7za"), seven_candidates),
        "unrar": _first_tool(("unrar",), unrar_candidates),
        "winrar": _first_tool(("winrar",), winrar_candidates),
        "unar": _first_tool(("unar",), [app_folder / "unar.exe"]),
        # Windows 10/11 includes tar.exe based on libarchive.  It can act as
        # the RAR reader without requiring a separate 7-Zip installation.
        "bsdtar": _first_tool(("bsdtar", "tar"), [app_folder / "bsdtar.exe", app_folder / "tar.exe"]),
    }


def format_diagnostics() -> str:
    """Return a compact, user-facing decoder/backend capability report."""
    qt_formats = {bytes(value).decode("ascii", errors="ignore").upper() for value in QImageReader.supportedImageFormats()}
    lines = ["画像デコーダー"]
    for label, candidates in (
        ("JPEG", {"JPG", "JPEG"}), ("PNG", {"PNG"}), ("WebP", {"WEBP"}),
        ("TIFF", {"TIF", "TIFF"}), ("GIF", {"GIF"}), ("SVG", {"SVG", "SVGZ"}),
    ):
        lines.append(f"  {'✓' if qt_formats & candidates else '△'} {label}")
    try:
        import pillow_heif  # noqa: F401
        heif = True
    except (ImportError, OSError):
        heif = False
    lines.append(f"  {'✓' if heif else '△'} HEIF / HEIC（pillow-heif）")
    try:
        from PIL import features
        jp2 = bool(features.check("jpg_2000"))
    except Exception:
        jp2 = False
    lines.append(f"  {'✓' if jp2 else '△'} JPEG 2000")
    lines.extend(["", "圧縮ファイル", "  ✓ ZIP / CBZ", "  ✓ TAR系"])
    try:
        import py7zr  # noqa: F401
        seven_python = True
    except ImportError:
        seven_python = False
    lines.append(f"  {'✓' if seven_python else '×'} 7Z / CB7（py7zr）")
    backends = rar_tools()
    active = [name for name, path in backends.items() if path]
    lines.append(f"  {'✓' if active else '×'} RAR / CBR" + (f"（{', '.join(active)}）" if active else "（外部ツールなし）"))
    lines.extend(["", "✓ 利用可能　△ 環境・画像による　× バックエンドなし"])
    return "\n".join(lines)


def prepare_rarfile():
    import rarfile
    tools = rar_tools()
    if tools["seven"]:
        rarfile.SEVENZIP_TOOL = tools["seven"]
        rarfile.SEVENZIP2_TOOL = tools["seven"]
    if tools["unrar"]:
        rarfile.UNRAR_TOOL = tools["unrar"]
    if tools["unar"]:
        rarfile.UNAR_TOOL = tools["unar"]
    if tools["bsdtar"]:
        rarfile.BSDTAR_TOOL = tools["bsdtar"]
    if any(tools.values()):
        rarfile.CURRENT_SETUP = None
    return rarfile


def _run_archive_tool(command: list[str]) -> bytes | None:
    try:
        creationflags = getattr(subprocess, "CREATE_NO_WINDOW", 0)
        result = subprocess.run(command, stdout=subprocess.PIPE, stderr=subprocess.PIPE, timeout=90, creationflags=creationflags)
        return result.stdout if result.returncode == 0 and result.stdout else None
    except (OSError, subprocess.SubprocessError):
        return None


def _decode_tool_output(data: bytes) -> str:
    for encoding in ("utf-8", "mbcs", "cp932", "latin-1"):
        try:
            return data.decode(encoding)
        except (LookupError, UnicodeDecodeError):
            continue
    return data.decode("utf-8", errors="replace")


def _list_rar_images(path: Path) -> list[str]:
    try:
        rarfile = prepare_rarfile()
        with rarfile.RarFile(path) as archive:
            return [info.filename for info in archive.infolist() if not info.isdir() and is_image_name(info.filename)]
    except Exception:
        pass

    tools = rar_tools()
    if tools["seven"]:
        data = _run_archive_tool([tools["seven"], "l", "-slt", "-ba", str(path)])
        if data:
            names, current, is_folder = [], "", False
            for line in _decode_tool_output(data).splitlines() + [""]:
                if line.startswith("Path = "):
                    current = line[7:].strip(); is_folder = False
                elif line.startswith("Folder = "):
                    is_folder = line[9:].strip() == "+"
                elif not line.strip() and current:
                    if not is_folder and is_image_name(current): names.append(current)
                    current = ""
            if names:
                return names

    for key in ("unrar", "winrar"):
        if tools[key]:
            data = _run_archive_tool([tools[key], "lb", "-c-", "-p-", str(path)])
            if data:
                names = [line.strip() for line in _decode_tool_output(data).splitlines() if is_image_name(line.strip())]
                if names:
                    return names

    if tools["bsdtar"]:
        data = _run_archive_tool([tools["bsdtar"], "-tf", str(path)])
        if data:
            return [line.strip() for line in _decode_tool_output(data).splitlines() if is_image_name(line.strip())]
    return []


def _read_rar_member(path: Path, name: str) -> bytes | None:
    tools = rar_tools()
    commands = []
    if tools["seven"]:
        commands.append([tools["seven"], "x", "-so", "-y", str(path), name])
    if tools["unrar"]:
        commands.append([tools["unrar"], "p", "-inul", "-p-", str(path), name])
    if tools["winrar"]:
        commands.append([tools["winrar"], "p", "-inul", "-p-", str(path), name])
    if tools["bsdtar"]:
        commands.append([tools["bsdtar"], "-xOf", str(path), name])
    for command in commands:
        data = _run_archive_tool(command)
        if data:
            return data
    try:
        rarfile = prepare_rarfile()
        with rarfile.RarFile(path) as archive:
            return archive.read(name)
    except Exception:
        return None


@dataclass
class BookEntry:
    path: str
    title: str = ""
    folder_id: str = ""
    id: str = field(default_factory=lambda: uuid.uuid4().hex)

    @property
    def source_path(self) -> Path:
        return Path(self.path)

    @property
    def display_title(self) -> str:
        return self.title.strip() or self.source_path.stem

    @property
    def kind(self) -> str:
        return "archive" if is_archive_path(self.source_path) else "folder"

    def to_dict(self) -> dict[str, str]:
        return {"id": self.id, "path": self.path, "title": self.title, "folder_id": self.folder_id}

    @classmethod
    def from_dict(cls, value: dict[str, str]) -> "BookEntry":
        return cls(
            path=value.get("path", ""), title=value.get("title", ""),
            folder_id=value.get("folder_id", ""), id=value.get("id") or uuid.uuid4().hex,
        )


@dataclass
class ShelfFolder:
    name: str
    parent_id: str = ""
    id: str = field(default_factory=lambda: uuid.uuid4().hex)

    def to_dict(self) -> dict[str, str]:
        return {"id": self.id, "name": self.name, "parent_id": self.parent_id}

    @classmethod
    def from_dict(cls, value: dict[str, str]) -> "ShelfFolder":
        return cls(name=value.get("name", "新しい本棚"), parent_id=value.get("parent_id", ""), id=value.get("id") or uuid.uuid4().hex)


def parse_bookshelf(value) -> tuple[list[ShelfFolder], list[BookEntry]]:
    """Read both the v0.1 flat list and the v0.2 folder-aware schema."""
    if isinstance(value, list):
        books = [BookEntry.from_dict(item) for item in value if isinstance(item, dict) and item.get("path")]
        if not books: return [], []
        folder = ShelfFolder(name="本棚")
        for book in books: book.folder_id = folder.id
        return [folder], books
    if isinstance(value, dict):
        folders = [ShelfFolder.from_dict(item) for item in value.get("folders", []) if isinstance(item, dict)]
        books = [BookEntry.from_dict(item) for item in value.get("books", []) if isinstance(item, dict) and item.get("path")]
        valid_ids = {folder.id for folder in folders}
        for folder in folders:
            if folder.parent_id not in valid_ids or folder.parent_id == folder.id:
                folder.parent_id = ""
        if books and any(book.folder_id not in valid_ids for book in books):
            migrated = ShelfFolder(name="本棚")
            folders.append(migrated); valid_ids.add(migrated.id)
        for book in books:
            if book.folder_id not in valid_ids:
                book.folder_id = migrated.id
        return folders, books
    return [], []


class ImageSource:
    """Lazy index for a folder, archive, or individual image."""

    def __init__(self, path: str | Path):
        self.path = Path(path)
        self._pages: list[str] | None = None
        self._cover_name: str | None = None

    @property
    def title(self) -> str:
        return self.path.stem if self.path.is_file() else self.path.name

    @property
    def is_archive(self) -> bool:
        return is_archive_path(self.path)

    def pages(self) -> list[str]:
        if self._pages is None:
            self._pages = self._scan_pages()
        return self._pages

    def _scan_pages(self) -> list[str]:
        if self.is_archive:
            try:
                kind = archive_kind(self.path)
                if kind == "zip":
                    with zipfile.ZipFile(self.path) as archive:
                        names = [info.filename for info in archive.infolist() if not info.is_dir() and is_image_name(info.filename)]
                elif kind == "tar":
                    with tarfile.open(self.path, "r:*") as archive:
                        names = [item.name for item in archive.getmembers() if item.isfile() and is_image_name(item.name)]
                elif kind == "7z":
                    import py7zr
                    with py7zr.SevenZipFile(self.path, "r") as archive:
                        names = [name for name in archive.getnames() if not name.endswith("/") and is_image_name(name)]
                elif kind == "rar":
                    names = _list_rar_images(self.path)
                else:
                    names = []
                # Cover is the first actual image in archive order. Non-image
                # metadata and text files never become the thumbnail.
                self._cover_name = names[0] if names else None
                return sorted(names, key=natural_key)
            except Exception:
                return []
        if self.path.is_dir():
            try:
                files = [item.name for item in self.path.iterdir() if item.is_file() and item.suffix.casefold() in IMAGE_EXTENSIONS]
                ordered = sorted(files, key=natural_key)
                self._cover_name = ordered[0] if ordered else None
                return ordered
            except OSError:
                return []
        if self.path.is_file() and self.path.suffix.casefold() in IMAGE_EXTENSIONS:
            self._cover_name = self.path.name
            return [self.path.name]
        return []

    def page_count(self) -> int:
        return len(self.pages())

    def page_label(self, index: int) -> str:
        pages = self.pages()
        return PurePosixPath(pages[index]).name if 0 <= index < len(pages) else ""

    def page_size(self, index: int) -> QSize:
        reader, buffer = self._reader_for_page(index)
        if reader is None: return QSize()
        size = reader.size()
        del buffer
        if not size.isValid():
            try:
                from PIL import Image
                payload = self._pillow_payload(index)
                if payload is not None:
                    with Image.open(payload) as opened:
                        width, height = opened.size
                        orientation = int(opened.getexif().get(274, 1))
                        if orientation in (5, 6, 7, 8): width, height = height, width
                        size = QSize(width, height)
            except Exception:
                pass
        return size

    def majority_landscape(self, fallback: QImage | None = None, sample_limit: int = 24) -> bool:
        """Use lightweight image headers to choose one thumbnail orientation per folder."""
        if self.path.is_dir():
            pages = self.pages()
            if pages:
                step = max(1, len(pages) // max(1, sample_limit))
                landscape = portrait = 0
                for name in pages[::step][:sample_limit]:
                    size = QImageReader(str(self.path / name)).size()
                    if not size.isValid():
                        continue
                    if size.width() > size.height(): landscape += 1
                    else: portrait += 1
                if landscape or portrait:
                    return landscape > portrait
        return bool(fallback is not None and not fallback.isNull() and fallback.width() > fallback.height())

    def _archive_read(self, name: str) -> bytes | None:
        try:
            safe_parts = safe_archive_parts(name)
            if safe_parts is None:
                return None
            kind = archive_kind(self.path)
            if kind == "zip":
                with zipfile.ZipFile(self.path) as archive:
                    return archive.read(name)
            if kind == "tar":
                with tarfile.open(self.path, "r:*") as archive:
                    handle = archive.extractfile(name)
                    return handle.read() if handle else None
            if kind == "rar":
                return _read_rar_member(self.path, name)
            if kind == "7z":
                import py7zr
                with py7zr.SevenZipFile(self.path, "r") as archive:
                    if hasattr(archive, "read"):
                        result = archive.read([name])
                        stream = result.get(name)
                        return stream.read() if stream else None
                    with tempfile.TemporaryDirectory(prefix="lightviewer_7z_") as temporary:
                        archive.extract(path=temporary, targets=[name])
                        return Path(temporary, *safe_parts).read_bytes()
        except Exception:
            return None
        return None

    def _reader_for_page(self, index: int) -> tuple[QImageReader | None, QBuffer | None]:
        pages = self.pages()
        if not 0 <= index < len(pages):
            return None, None
        if self.is_archive:
            data = self._archive_read(pages[index])
            if data is None:
                return None, None
            buffer = QBuffer()
            buffer.setData(QByteArray(data))
            buffer.open(QIODevice.OpenModeFlag.ReadOnly)
            reader = QImageReader(buffer)
            reader.setAutoTransform(True)
            return reader, buffer
        actual = self.path if self.path.is_file() else self.path / pages[index]
        reader = QImageReader(str(actual))
        reader.setAutoTransform(True)
        return reader, None

    def _pillow_payload(self, index: int):
        pages = self.pages()
        if not 0 <= index < len(pages):
            return None
        if self.is_archive:
            data = self._archive_read(pages[index])
            return BytesIO(data) if data is not None else None
        return str(self.path if self.path.is_file() else self.path / pages[index])

    def _read_with_pillow(self, index: int, target: QSize, max_megapixels: int) -> QImage:
        try:
            from PIL import Image, ImageOps
            try:
                import pillow_heif
                pillow_heif.register_heif_opener()
            except (ImportError, OSError):
                pass
            payload = self._pillow_payload(index)
            if payload is None:
                return QImage()
            with Image.open(payload) as opened:
                frame = ImageOps.exif_transpose(opened)
                width, height = frame.size
                max_pixels = max(max(1, max_megapixels) * 1_000_000, target.width() * target.height())
                # Keep ordinary images at their original resolution.  Only
                # images above the configured safety limit are downsampled.
                # The former display-size cap could make text and line art
                # look jagged even when the source image was not very large.
                scale = min(1.0, (max_pixels / max(1, width * height)) ** 0.5)
                wanted = (max(1, round(width * scale)), max(1, round(height * scale)))
                try:
                    frame.draft("RGB", wanted)
                except (AttributeError, ValueError):
                    pass
                if frame.size != wanted:
                    frame.thumbnail(wanted, Image.Resampling.LANCZOS, reducing_gap=3.0)
                rgba = frame.convert("RGBA")
                raw = rgba.tobytes("raw", "RGBA")
                return QImage(raw, rgba.width, rgba.height, rgba.width * 4, QImage.Format.Format_RGBA8888).copy()
        except Exception:
            return QImage()

    def read_page(self, index: int, target: QSize, max_megapixels: int = 16) -> QImage:
        reader, buffer = self._reader_for_page(index)
        if reader is None:
            return QImage()
        original = reader.size()
        if original.isValid():
            max_pixels = max(max(1, max_megapixels) * 1_000_000, target.width() * target.height())
            scale = min(1.0, (max_pixels / max(1, original.width() * original.height())) ** 0.5)
            if scale < 0.999:
                reader.setScaledSize(QSize(max(1, round(original.width() * scale)), max(1, round(original.height() * scale))))
        image = reader.read()
        del buffer
        if not image.isNull():
            return image
        return self._read_with_pillow(index, target, max_megapixels)

    def first_image(self, size: QSize) -> QImage:
        pages = self.pages()
        if not pages:
            return QImage()
        try:
            index = pages.index(self._cover_name) if self._cover_name else 0
        except ValueError:
            index = 0
        return self.read_page(index, size, max_megapixels=2)


def entries_from_paths(paths: Iterable[str], folder_id: str = "") -> list[BookEntry]:
    entries: list[BookEntry] = []
    for raw in paths:
        path = Path(raw)
        if path.is_dir() or is_archive_path(path):
            entries.append(BookEntry(path=str(path.resolve()), folder_id=folder_id))
    return entries
