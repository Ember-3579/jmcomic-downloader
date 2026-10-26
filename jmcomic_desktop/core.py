"""Core download logic for the local JM comic downloader.

The GUI stays deliberately thin: this module owns input parsing, persisted
settings, jmcomic option construction and the background workers.
"""

from __future__ import annotations

import functools
import json
import os
import re
import threading
import time
import traceback
from dataclasses import asdict, dataclass, fields
from pathlib import Path
from typing import Optional

import jmcomic
from jmcomic import (
    DownloadCancelledException,
    DownloadControl,
    JmDownloader,
    JmOption,
)

APP_NAME = "JMComicDownloader"
APP_VERSION = "1.0.0"
APP_DISPLAY_NAME = "JM 漫画下载器"

DEFAULT_RULE = "Bd / [JM{Aid}] {Atitle} / 第{Pindex}话 {Pname}"


# ---------------------------------------------------------------------------
# Paths and settings


def app_data_dir() -> Path:
    override = os.environ.get("JMCOMIC_HOME")
    if override:
        path = Path(override).expanduser()
    else:
        root = os.environ.get("LOCALAPPDATA") or str(Path.home())
        path = Path(root) / APP_NAME
    path.mkdir(parents=True, exist_ok=True)
    return path


def default_download_dir() -> Path:
    override = os.environ.get("JMCOMIC_DOWNLOAD_DIR")
    if override:
        return Path(override).expanduser()
    downloads = Path.home() / "Downloads"
    base = downloads if downloads.is_dir() else Path.home()
    return base / "JMComic"


def _clamp_int(value, low: int, high: int, fallback: int) -> int:
    try:
        number = int(value)
    except (TypeError, ValueError):
        return fallback
    return max(low, min(high, number))


@dataclass
class Settings:
    download_dir: str = ""
    proxy: str = "system"
    cookie: str = ""
    client_impl: str = "api"
    image_threads: int = 20
    photo_threads: int = 4
    last_input: str = ""
    zip_enabled: bool = False
    pdf_enabled: bool = False
    long_enabled: bool = False
    window_geometry: str = ""

    def __post_init__(self) -> None:
        if not self.download_dir:
            self.download_dir = str(default_download_dir())
        self.download_dir = str(Path(self.download_dir).expanduser())
        self.proxy = str(self.proxy or "system").strip()
        self.cookie = str(self.cookie or "").strip()
        self.client_impl = self.client_impl if self.client_impl in {"api", "html"} else "api"
        self.image_threads = _clamp_int(self.image_threads, 1, 50, 20)
        self.photo_threads = _clamp_int(self.photo_threads, 1, 20, 4)
        self.last_input = str(self.last_input or "")[:500]
        self.window_geometry = str(self.window_geometry or "")


def settings_path() -> Path:
    return app_data_dir() / "settings.json"


def load_settings() -> Settings:
    path = settings_path()
    data = {}
    if path.is_file():
        try:
            data = json.loads(path.read_text(encoding="utf-8"))
        except (OSError, ValueError):
            data = {}
    if not isinstance(data, dict):
        data = {}
    allowed = {f.name for f in fields(Settings)}
    clean = {key: value for key, value in data.items() if key in allowed}
    try:
        return Settings(**clean)
    except TypeError:
        return Settings()


def save_settings(settings: Settings) -> None:
    path = settings_path()
    tmp = path.with_suffix(".json.tmp")
    tmp.write_text(
        json.dumps(asdict(settings), ensure_ascii=False, indent=2),
        encoding="utf-8",
    )
    tmp.replace(path)


def history_path() -> Path:
    return app_data_dir() / "history.json"


def load_history() -> list[dict]:
    path = history_path()
    if not path.is_file():
        return []
    try:
        data = json.loads(path.read_text(encoding="utf-8"))
    except (OSError, ValueError):
        return []
    return [entry for entry in data if isinstance(entry, dict)][:20]


def add_history(entry: dict) -> list[dict]:
    history = load_history()
    history = [item for item in history if item.get("key") != entry.get("key")]
    history.insert(0, entry)
    history = history[:20]
    try:
        history_path().write_text(
            json.dumps(history, ensure_ascii=False, indent=2),
            encoding="utf-8",
        )
    except OSError:
        pass
    return history


def cleanup_empty_directories(root: Path) -> None:
    """Remove empty chapter folders left behind by export plugins.

    Some jmcomic export plugins ask the dir rule for every chapter of an
    album while building exports, which creates empty folders for chapters
    that were filtered out.  Keeping those folders is confusing in a GUI.
    """

    try:
        root = Path(root)
        if not root.is_dir():
            return
        for folder in sorted(
            (path for path in root.rglob("*") if path.is_dir()),
            key=lambda path: len(path.parts),
            reverse=True,
        ):
            if folder.name == "导出":
                continue
            try:
                if not any(folder.iterdir()):
                    folder.rmdir()
            except OSError:
                continue
    except OSError:
        pass


def write_error_log(detail: str) -> Path:
    path = app_data_dir() / "last_error.log"
    try:
        path.write_text(
            f"{time.strftime('%Y-%m-%d %H:%M:%S')}\n{detail}\n",
            encoding="utf-8",
        )
    except OSError:
        pass
    return path


# ---------------------------------------------------------------------------
# Input parsing


@dataclass(frozen=True)
class InputSpec:
    kind: str  # album | photo
    jm_id: str
    raw: str


def parse_input(text: str) -> InputSpec:
    raw = str(text or "").strip()
    if not raw:
        raise ValueError("请先粘贴作品链接或输入车号。")

    # URLs are unambiguous: /photo/<id> is a chapter, /album/<id> is a book.
    match = re.search(r"(?i)/photo/(\d{3,})", raw)
    if match:
        return InputSpec("photo", match.group(1), raw)
    match = re.search(r"(?i)/album/(\d{3,})", raw)
    if match:
        return InputSpec("album", match.group(1), raw)

    # Explicit chapter notation such as p123456 or P 123456.
    match = re.fullmatch(r"(?i)\s*p[\s:：\-_]*(\d{3,})\s*", raw)
    if match:
        return InputSpec("photo", match.group(1), raw)

    # JM123456 / jm-123456 / 禁漫123456
    match = re.search(r"(?i)(?:jm|禁漫)[\s:：#\-_]*(\d{3,})", raw)
    if match:
        return InputSpec("album", match.group(1), raw)

    # A bare number is the most common way to use the CLI.
    match = re.fullmatch(r"\s*(\d{3,})\s*", raw)
    if match:
        return InputSpec("album", match.group(1), raw)

    # Last resort: a car number embedded in arbitrary copied text.
    numbers = re.findall(r"\d{3,}", raw)
    if numbers:
        return InputSpec("album", max(numbers, key=len), raw)

    raise ValueError("没有识别出有效车号，请检查链接或数字。")


def resolve_selected_positions(selection: str, album) -> Optional[list[int]]:
    """Turn "1-3,5,p123" into zero-based positions inside *album*.

    ``None`` means "all chapters".  Raises ValueError with a user-readable
    message for impossible ranges/ids.
    """

    text = str(selection or "").strip()
    if not text or text.lower() in {"all", "*", "全部", "全本"}:
        return None

    tokens = [token for token in re.split(r"[,，、;；\s]+", text) if token]
    if not tokens:
        return None

    by_index = {int(photo.album_index): pos for pos, photo in enumerate(album)}
    by_id = {str(photo.id): pos for pos, photo in enumerate(album)}
    positions: set[int] = set()

    for token in tokens:
        cleaned = token.strip()
        if not cleaned:
            continue
        if cleaned.lower().startswith("p") and cleaned[1:].isdigit():
            photo_id = cleaned[1:]
            if photo_id not in by_id:
                raise ValueError(f"章节 ID p{photo_id} 不在本作品中。")
            positions.add(by_id[photo_id])
            continue
        range_match = re.fullmatch(r"(\d+)\s*[-~]\s*(\d+)", cleaned)
        if range_match:
            start, end = int(range_match.group(1)), int(range_match.group(2))
            if start > end:
                start, end = end, start
            for index in range(start, end + 1):
                if index not in by_index:
                    raise ValueError(
                        f"章节序号 {index} 超出范围（1-{len(album)}）。"
                    )
                positions.add(by_index[index])
            continue
        if cleaned.isdigit():
            index = int(cleaned)
            if index not in by_index:
                raise ValueError(
                    f"章节序号 {index} 超出范围（1-{len(album)}）。"
                )
            positions.add(by_index[index])
            continue
        raise ValueError(
            f"无法识别章节「{cleaned}」，请使用 1-3,5 或 p123456 这样的格式。"
        )

    if not positions:
        raise ValueError("章节范围为空，请留空以下载全部章节。")
    return sorted(positions)


def album_meta(album) -> dict:
    chapters = []
    for photo in album:
        chapters.append(
            {
                "index": int(photo.album_index),
                "id": str(photo.id),
                "name": str(photo.name),
            }
        )
    return {
        "kind": "album",
        "id": str(album.id),
        "title": str(album.name),
        "author": " / ".join(album.authors) if album.authors else "未知",
        "chapter_count": len(album),
        "page_count": int(album.page_count),
        "tags": list(album.tags or [])[:8],
        "description": str(album.description or "").strip(),
        "chapters": chapters,
    }


def photo_meta(photo) -> dict:
    album = getattr(photo, "from_album", None)
    tags = list(getattr(album, "tags", []) or [])[:8]
    authors = list(getattr(album, "authors", []) or [])
    return {
        "kind": "photo",
        "id": str(photo.id),
        "title": str(photo.name),
        "author": " / ".join(authors) if authors else "未知",
        "album_id": str(getattr(album, "id", "") or ""),
        "album_title": str(getattr(album, "name", "") or ""),
        "chapter_count": 1,
        "page_count": len(photo),
        "tags": tags,
        "description": str(getattr(album, "description", "") or "").strip(),
        "chapters": [
            {
                "index": int(getattr(photo, "album_index", 1) or 1),
                "id": str(photo.id),
                "name": str(photo.name),
            }
        ],
    }


# ---------------------------------------------------------------------------
# jmcomic options


def _proxy_value(proxy: str):
    value = str(proxy or "").strip()
    if not value or value.lower() in {"system", "系统", "系统代理"}:
        return "system"
    if value.lower() in {"none", "off", "直连", "不使用"}:
        return None
    return value


def _cookie_dict(cookie: str) -> Optional[dict]:
    raw = str(cookie or "").strip()
    if not raw:
        return None
    if "=" not in raw:
        return {"AVS": raw}
    result = {}
    for part in raw.split(";"):
        if "=" in part:
            key, value = part.split("=", 1)
            key, value = key.strip(), value.strip()
            if key:
                result[key] = value
    return result or None


def build_option(settings: Settings, base_dir: str, kind: str, exports: dict) -> JmOption:
    base = Path(base_dir).expanduser().resolve()
    base.mkdir(parents=True, exist_ok=True)

    meta_data = {"proxies": _proxy_value(settings.proxy)}
    cookies = _cookie_dict(settings.cookie)
    if cookies:
        meta_data["cookies"] = cookies

    plugins: dict = {"dependencies_strategy": "failed-fast"}
    export_dir = base / "导出"
    hooks = []
    if any(exports.get(name) for name in ("zip", "pdf", "long")):
        export_dir.mkdir(parents=True, exist_ok=True)

    if exports.get("zip"):
        hooks.append(
            {
                "plugin": "zip",
                "kwargs": {
                    "zip_dir": str(export_dir),
                    "filename_rule": "JM{Aid}-{Atitle}",
                    "suffix": "zip",
                    "delete_original_file": False,
                },
            }
        )
    if exports.get("pdf"):
        hooks.append(
            {
                "plugin": "img2pdf",
                "kwargs": {
                    "pdf_dir": str(export_dir),
                    "filename_rule": "JM{Aid}-{Atitle}",
                },
            }
        )
    if exports.get("long"):
        hooks.append(
            {
                "plugin": "long_img",
                "kwargs": {
                    "img_dir": str(export_dir),
                    "filename_rule": "JM{Aid}-{Atitle}",
                },
            }
        )

    if hooks:
        key = "after_album" if kind == "album" else "after_photo"
        if kind == "photo":
            for hook in hooks:
                hook["kwargs"]["filename_rule"] = "JM{Aid}-{Pindex}-{Pname}"
        plugins[key] = hooks

    data = {
        "log": False,
        "dir_rule": {
            "base_dir": str(base),
            "rule": DEFAULT_RULE,
        },
        "download": {
            "cache": True,
            "image": {"decode": True, "suffix": None},
            "threading": {
                "image": settings.image_threads,
                "photo": settings.photo_threads,
            },
        },
        "client": {
            "impl": settings.client_impl,
            "retry_times": 5,
            "postman": {"meta_data": meta_data},
        },
        "plugins": plugins,
    }
    return JmOption.construct(data)


# ---------------------------------------------------------------------------
# Workers


class MetadataWorker(threading.Thread):
    def __init__(self, spec: InputSpec, settings: Settings, bus, cancel_event):
        super().__init__(daemon=True, name="jm-metadata")
        self.spec = spec
        self.settings = settings
        self.bus = bus
        self.cancel_event = cancel_event

    def run(self) -> None:
        try:
            option = build_option(
                self.settings, self.settings.download_dir, self.spec.kind, {}
            )
            client = option.new_jm_client()
            if self.cancel_event.is_set():
                return
            if self.spec.kind == "album":
                detail = client.get_album_detail(self.spec.jm_id)
                meta = album_meta(detail)
            else:
                detail = client.get_photo_detail(
                    self.spec.jm_id, fetch_album=True, fetch_scramble_id=True
                )
                meta = photo_meta(detail)
            self.bus.put(("meta", meta))
        except Exception as error:  # noqa: BLE001 - surfaced to the GUI
            detail = "".join(traceback.format_exception(error))
            log_path = write_error_log(detail)
            self.bus.put(
                (
                    "meta_error",
                    {
                        "message": friendly_error(error),
                        "log_path": str(log_path),
                    },
                )
            )


class GuiDownloader(JmDownloader):
    """JmDownloader with queue-based progress callbacks."""

    def __init__(self, option, bus, selection: str = "", mode: str = "album"):
        super().__init__(option)
        self.bus = bus
        self.selection = selection
        self.mode = mode
        self.selected_positions: Optional[list[int]] = None
        self.total_photos = 1
        self.done_photos = 0
        self.done_images = 0
        self.started_at = time.monotonic()
        self._lock = threading.Lock()
        self._last_progress = 0.0

    def before_album(self, album):
        if self.mode == "album":
            self.selected_positions = resolve_selected_positions(self.selection, album)
            self.total_photos = (
                len(self.selected_positions)
                if self.selected_positions is not None
                else len(album)
            )
        self.bus.put(("meta", album_meta(album)))
        self.bus.put(
            (
                "started",
                {
                    "title": str(album.name),
                    "total_photos": self.total_photos,
                    "save_path": str(getattr(album, "save_path", "") or ""),
                },
            )
        )
        super().before_album(album)

    def before_photo(self, photo):
        if self.mode == "photo":
            self.total_photos = 1
            self.bus.put(("meta", photo_meta(photo)))
            self.bus.put(
                (
                    "started",
                    {
                        "title": str(photo.name),
                        "total_photos": 1,
                        "save_path": str(getattr(photo, "save_path", "") or ""),
                    },
                )
            )
        super().before_photo(photo)

    def do_filter(self, detail):
        if detail.is_album() and self.selected_positions is not None:
            return [detail[position] for position in self.selected_positions]
        return detail

    def after_photo(self, photo):
        super().after_photo(photo)
        with self._lock:
            self.done_photos += 1
            done, total, images = self.done_photos, self.total_photos, self.done_images
        self.bus.put(
            (
                "chapter",
                {
                    "done": done,
                    "total": total,
                    "images": images,
                    "index": int(getattr(photo, "album_index", done) or done),
                    "name": str(photo.name),
                },
            )
        )

    def after_image(self, image, img_save_path):
        super().after_image(image, img_save_path)
        with self._lock:
            self.done_images += 1
            images, photos = self.done_images, self.done_photos
        now = time.monotonic()
        if now - self._last_progress >= 0.08:
            self._last_progress = now
            self.bus.put(
                (
                    "progress",
                    {
                        "photos": photos,
                        "total_photos": self.total_photos,
                        "images": images,
                    },
                )
            )


class DownloadWorker(threading.Thread):
    def __init__(
        self,
        spec: InputSpec,
        settings: Settings,
        selection: str,
        exports: dict,
        bus,
    ):
        super().__init__(daemon=True, name="jm-download")
        self.spec = spec
        self.settings = settings
        self.selection = selection
        self.exports = dict(exports)
        self.bus = bus
        self.control = DownloadControl()

    def cancel(self) -> None:
        self.control.cancel("用户取消了下载")

    def run(self) -> None:
        try:
            option = build_option(
                self.settings,
                self.settings.download_dir,
                self.spec.kind,
                self.exports,
            )
            downloader = functools.partial(
                GuiDownloader,
                bus=self.bus,
                selection=self.selection,
                mode=self.spec.kind,
            )
            if self.spec.kind == "album":
                result = jmcomic.download_album(
                    self.spec.jm_id,
                    option,
                    downloader=downloader,
                    control=self.control,
                )
            else:
                result = jmcomic.download_photo(
                    self.spec.jm_id,
                    option,
                    downloader=downloader,
                    control=self.control,
                )

            detail = result.detail
            save_path = str(getattr(detail, "save_path", "") or "")
            image_paths = list(result.manifest.image_filepath_list)
            if image_paths:
                # With the default rule the image sits in
                # <base>/<album>/<chapter>/<image>, so two parents up is the
                # album folder users actually want to open.
                first_image = Path(image_paths[0])
                album_folder = first_image.parent.parent
                if album_folder.is_dir():
                    save_path = str(album_folder)
                    cleanup_empty_directories(album_folder)
            elif self.spec.kind == "photo":
                album = getattr(detail, "from_album", None)
                save_path = str(
                    getattr(album, "save_path", "")
                    or Path(save_path).parent
                )
            exports = []
            try:
                for paths in result.manifest.export_filepath_dict.values():
                    exports.extend(str(path) for path in paths)
            except Exception:  # pragma: no cover - old jmcomic fallback
                exports = []

            self.bus.put(
                (
                    "done",
                    {
                        "title": str(getattr(detail, "name", "") or ""),
                        "save_path": save_path,
                        "exports": exports,
                        "images": len(image_paths),
                        "duration": result.duration or 0,
                    },
                )
            )
        except DownloadCancelledException:
            self.bus.put(("cancelled", {}))
        except Exception as error:  # noqa: BLE001 - surfaced to the GUI
            detail = "".join(traceback.format_exception(error))
            log_path = write_error_log(detail)
            self.bus.put(
                (
                    "error",
                    {
                        "message": friendly_error(error),
                        "log_path": str(log_path),
                    },
                )
            )


def friendly_error(error: BaseException) -> str:
    text = str(error).strip()
    name = type(error).__name__
    lowered = f"{name} {text}".lower()
    if any(
        word in lowered
        for word in ("proxy", "connection", "timeout", "timed out", "ssl", "curl")
    ):
        hint = "网络或代理连接失败，请在「设置」中检查代理，或稍后重试。"
    elif "cookie" in lowered or "login" in lowered or "avs" in lowered:
        hint = "该作品可能需要登录 Cookie，请在「设置」中填入 AVS Cookie。"
    elif "not found" in lowered or "404" in lowered:
        hint = "没有找到这个作品，请检查车号是否正确。"
    else:
        hint = "下载没有完成，请查看下方日志或错误记录。"
    if len(text) > 500:
        text = text[:500] + "…"
    return f"{hint}\n\n{name}: {text}" if text else hint
