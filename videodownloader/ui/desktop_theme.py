"""Shared Desktop UI constants, zero-emoji status/preset tables, formatting utilities & worker thread bridge."""

from __future__ import annotations

import base64
import threading
from typing import Any, Callable

from PyQt6.QtCore import QObject, pyqtSignal
from PyQt6.QtGui import QPixmap

from .tokens import QUALITY_PRESETS, STATUS_TEXT

QUALITY_ITEMS: list[tuple[str, str, bool, str]] = QUALITY_PRESETS

PROXY_MODE_ITEMS: list[tuple[str, str]] = [
    ("智能分流 (推荐: 境外走代理 / 国内B站抖音直连)", "auto"),
    ("全局跟随系统代理", "system"),
    ("全局使用自定义代理", "custom"),
    ("强制全部直连 (不走代理)", "direct"),
]

COOKIE_ITEMS: list[tuple[str, str]] = [
    ("不使用浏览器 Cookie", "none"),
    ("从 Edge 浏览器读取", "edge"),
    ("从 Chrome 浏览器读取", "chrome"),
    ("从 Firefox 浏览器读取", "firefox"),
    ("从 Brave 浏览器读取", "brave"),
]

STATUS_LABELS: dict[str, str] = STATUS_TEXT


def format_size(num_bytes: int | float | None) -> str:
    b = float(num_bytes or 0)
    if b <= 0:
        return "-"
    if b >= 1024**3:
        return f"{b / (1024**3):.2f} GB"
    if b >= 1024**2:
        return f"{b / (1024**2):.1f} MB"
    return f"{int(b / 1024)} KB"


def format_mmss(ms_or_sec: float, is_ms: bool = False) -> str:
    sec = max(0, int(ms_or_sec / 1000.0 if is_ms else ms_or_sec))
    if sec >= 3600:
        return f"{sec // 3600}:{(sec % 3600) // 60:02d}:{sec % 60:02d}"
    return f"{sec // 60:02d}:{sec % 60:02d}"


def data_uri_to_pixmap(data_uri: str) -> QPixmap:
    pix = QPixmap()
    if not data_uri or "," not in data_uri:
        return pix
    try:
        raw = base64.b64decode(data_uri.split(",", 1)[1])
        pix.loadFromData(raw)
    except Exception:
        pass
    return pix


class AsyncBridge(QObject):
    """Thread-to-Qt-Main-Thread signal bridge."""

    done = pyqtSignal(object)
    failed = pyqtSignal(str)


def run_in_thread(
    fn: Callable[[], Any],
    on_done: Callable[[Any], None],
    on_error: Callable[[str], None],
    parent: QObject | None = None,
) -> AsyncBridge:
    bridge = AsyncBridge(parent)
    bridge.done.connect(on_done)
    bridge.failed.connect(on_error)

    def _target() -> None:
        try:
            res = fn()
            bridge.done.emit(res)
        except Exception as exc:
            bridge.failed.emit(str(exc))

    threading.Thread(target=_target, daemon=True).start()
    return bridge
