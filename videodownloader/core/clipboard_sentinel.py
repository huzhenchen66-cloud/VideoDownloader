"""3-Tier Non-Intrusive Clipboard Sentinel (`L0` Silent Pre-Probe / `L1` Non-Focus Bottom-Right Strip / `L2` Quick Capture):
- NEVER pops a modal dialog or steals focus when the user copies a link!
- 300ms debounce on `QClipboard.dataChanged`
- Win32 `SHQueryUserNotificationState` fullscreen/game/presentation Do-Not-Disturb detection
- 24-hour URL deduplication, 10s dismiss snooze, and domain blacklist.
"""

from __future__ import annotations

import ctypes
import sys
import time
from typing import Callable
from urllib.parse import urlparse

from PyQt6.QtCore import QEasingCurve, QObject, QPoint, QPropertyAnimation, QRect, Qt, QTimer, pyqtSignal
from PyQt6.QtGui import QColor, QCursor, QGuiApplication, QMouseEvent, QPainter, QPainterPath, QPen, QPixmap
from PyQt6.QtWidgets import QApplication, QHBoxLayout, QLabel, QPushButton, QWidget

from .db import Database
from .logging_setup import get_logger
from .paths import get_assets_dir
from .prefetch import SpeculativePrefetcher
from .url_cleaner import CleanedUrlResult, clean_share_input

logger = get_logger("clipboard")

# Win32 QUERY_USER_NOTIFICATION_STATE values that indicate fullscreen game / D3D video / presentation
QUNS_BUSY = 2
QUNS_RUNNING_D3D_FULL_SCREEN = 3
QUNS_PRESENTATION_MODE = 4


def is_fullscreen_busy() -> bool:
    """Return `True` if Windows reports an active fullscreen game, video, or presentation (`SHQueryUserNotificationState`)."""
    if not sys.platform.startswith("win"):
        return False
    try:
        state = ctypes.c_int(0)
        hr = ctypes.windll.shell32.SHQueryUserNotificationState(ctypes.byref(state))
        if hr == 0 and state.value in (QUNS_BUSY, QUNS_RUNNING_D3D_FULL_SCREEN, QUNS_PRESENTATION_MODE):
            return True
    except Exception:
        pass
    return False


class ClipboardToastStrip(QWidget):
    """L1 Non-Focus Bottom-Right Notification Pill (`WA_ShowWithoutActivating` + `WindowDoesNotAcceptFocus`)."""

    clicked = pyqtSignal(str)
    dismissed = pyqtSignal(str)

    def __init__(self) -> None:
        super().__init__(
            None,
            Qt.WindowType.Tool
            | Qt.WindowType.FramelessWindowHint
            | Qt.WindowType.WindowStaysOnTopHint
            | Qt.WindowType.WindowDoesNotAcceptFocus,
        )
        self.setAttribute(Qt.WidgetAttribute.WA_TranslucentBackground, True)
        self.setAttribute(Qt.WidgetAttribute.WA_ShowWithoutActivating, True)
        self.setFocusPolicy(Qt.FocusPolicy.NoFocus)
        self.setCursor(Qt.CursorShape.PointingHandCursor)
        self.setFixedSize(372, 58)

        self._current_url: str = ""
        self._auto_hide_timer = QTimer(self)
        self._auto_hide_timer.setSingleShot(True)
        self._auto_hide_timer.timeout.connect(self.hide)

        self._anim = QPropertyAnimation(self, b"pos", self)
        self._anim.setDuration(160)
        self._anim.setEasingCurve(QEasingCurve.Type.OutCubic)

        lay = QHBoxLayout(self)
        lay.setContentsMargins(14, 10, 12, 10)
        lay.setSpacing(10)

        icon_lbl = QLabel()
        icon_lbl.setFixedSize(28, 28)
        icon_path = get_assets_dir() / "icon.png"
        if icon_path.exists():
            pix = QPixmap(str(icon_path)).scaled(
                28, 28, Qt.AspectRatioMode.KeepAspectRatio, Qt.TransformationMode.SmoothTransformation
            )
            icon_lbl.setPixmap(pix)
        lay.addWidget(icon_lbl)

        self._text_lbl = QLabel("检测到视频链接")
        self._text_lbl.setStyleSheet(
            'color: #F8FAFC; font-family: "Segoe UI", "Microsoft YaHei UI"; font-size: 12px; font-weight: 600;'
        )
        lay.addWidget(self._text_lbl, 1)

        btn_close = QPushButton("×")
        btn_close.setFixedSize(24, 24)
        btn_close.setFocusPolicy(Qt.FocusPolicy.NoFocus)
        btn_close.setStyleSheet(
            "QPushButton { background: transparent; color: #94A3B8; border: none; font-size: 16px; font-weight: bold; }"
            "QPushButton:hover { color: #F8FAFC; background: rgba(255,255,255,0.12); border-radius: 12px; }"
        )
        btn_close.clicked.connect(self._on_close_clicked)
        lay.addWidget(btn_close)

    def paintEvent(self, _event) -> None:  # noqa: N802
        p = QPainter(self)
        p.setRenderHint(QPainter.RenderHint.Antialiasing, True)
        rect = self.rect().adjusted(1, 1, -1, -1)
        path = QPainterPath()
        path.addRoundedRect(float(rect.x()), float(rect.y()), float(rect.width()), float(rect.height()), 12.0, 12.0)
        p.fillPath(path, QColor(15, 23, 42, 240))
        p.setPen(QPen(QColor(56, 189, 248, 120), 1.2))
        p.drawPath(path)
        p.end()

    def mousePressEvent(self, event: QMouseEvent) -> None:  # noqa: N802
        if event.button() == Qt.MouseButton.LeftButton and self._current_url:
            url = self._current_url
            self.hide()
            self.clicked.emit(url)
            event.accept()
            return
        super().mousePressEvent(event)

    def _on_close_clicked(self) -> None:
        url = self._current_url
        self.hide()
        if url:
            self.dismissed.emit(url)

    def show_toast(self, url: str, platform_name: str, hotkey_chord: str) -> None:
        if (QGuiApplication.platformName() or "").lower() == "offscreen":
            return
        self._current_url = url
        hk = hotkey_chord or "Ctrl+Shift+D"
        self._text_lbl.setText(f"已预热 {platform_name} 链接 · 按 {hk} 或点击下载")

        screen = QGuiApplication.screenAt(QCursor.pos()) or QGuiApplication.primaryScreen()
        avail: QRect = screen.availableGeometry() if screen else QRect(0, 0, 1920, 1080)
        target_x = avail.right() - self.width() - 16
        target_y = avail.bottom() - self.height() - 16
        start_pos = QPoint(target_x, target_y + 22)
        end_pos = QPoint(target_x, target_y)

        self.move(start_pos)
        self.show()
        self._anim.stop()
        self._anim.setStartValue(start_pos)
        self._anim.setEndValue(end_pos)
        self._anim.start()
        self._auto_hide_timer.start(3500)


class ClipboardSentinel(QObject):
    """Non-intrusive clipboard watcher coordinating L0 speculative prefetch and L1 passive toast."""

    url_detected = pyqtSignal(str, str)  # (normalized_url, platform_name)
    open_capture_requested = pyqtSignal(str)

    def __init__(
        self,
        db: Database,
        prefetcher: SpeculativePrefetcher,
        is_app_foreground: Callable[[], bool],
        parent: QObject | None = None,
    ) -> None:
        super().__init__(parent)
        self.db = db
        self.prefetcher = prefetcher
        self._is_app_foreground = is_app_foreground

        self._last_raw_text: str = ""
        self._internal_ignore_text: str = ""
        self._snooze_until: float = 0.0
        self.last_candidate_url: str = ""

        self._debounce = QTimer(self)
        self._debounce.setSingleShot(True)
        self._debounce.setInterval(300)
        self._debounce.timeout.connect(self._evaluate_clipboard)

        self.toast = ClipboardToastStrip()
        self.toast.clicked.connect(self.open_capture_requested.emit)
        self.toast.dismissed.connect(self.suppress_url_24h)

        app = QApplication.instance()
        if app is not None:
            # Seed initial clipboard text so launching the app never pops a toast for stale clipboard data
            try:
                self._last_raw_text = (app.clipboard().text() or "").strip()
            except Exception:
                self._last_raw_text = ""
            app.clipboard().dataChanged.connect(self._on_clipboard_changed)

    def mark_internal_copy(self, text: str) -> None:
        """Prevent URLs copied inside VideoDownloader itself (e.g. LAN address) from triggering sentinel."""
        self._internal_ignore_text = (text or "").strip()

    def snooze(self, seconds: float = 10.0) -> None:
        self._snooze_until = max(self._snooze_until, time.time() + seconds)

    def suppress_url_24h(self, url: str) -> None:
        self.db.add_clipboard_suppression("url", url, ttl_seconds=86400.0)
        self.snooze(10.0)

    def suppress_domain_forever(self, url_or_domain: str) -> str:
        raw = (url_or_domain or "").strip().lower()
        host = urlparse(raw).hostname or raw if "://" in raw else raw
        host = host.removeprefix("www.")
        if host:
            self.db.add_clipboard_suppression("domain", host, ttl_seconds=0.0)
            self.snooze(10.0)
        return host

    def _on_clipboard_changed(self) -> None:
        self._debounce.start()

    def evaluate_text(self, raw_text: str) -> CleanedUrlResult | None:
        """Pure rule check returning a `CleanedUrlResult` if `raw_text` is an actionable, non-suppressed video link."""
        cfg = self.db.get_settings()
        if not cfg.get("clipboard_watch", True):
            return None

        text = (raw_text or "").strip()
        if not text or len(text) > 4096:
            return None
        if text == self._internal_ignore_text:
            return None
        if time.time() < self._snooze_until:
            return None

        cleaned = clean_share_input(text, resolve_short_links=False)
        if not cleaned:
            return None
        first = cleaned[0]

        # Only trigger automatically for known media platforms or direct media extensions (zero false positives on docs/github!)
        if first.platform_id == "generic":
            return None

        host = (urlparse(first.normalized_url).hostname or "").lower().removeprefix("www.")
        if host and (
            self.db.is_clipboard_suppressed("domain", host)
            or any(host.endswith(f".{d}") for d in self.db.list_suppressed_domains())
        ):
            return None

        if self.db.is_clipboard_suppressed("url", first.normalized_url):
            return None

        # Also skip if this exact URL is already active or completed in recent tasks
        for t in self.db.list_tasks(limit=80):
            if t.get("url") == first.normalized_url and t.get("status") in (
                "queued",
                "parsing",
                "downloading",
                "merging",
                "completed",
            ):
                return None

        return first

    def _evaluate_clipboard(self) -> None:
        app = QApplication.instance()
        if app is None:
            return
        try:
            text = (app.clipboard().text() or "").strip()
        except Exception:
            return

        if not text or text == self._last_raw_text:
            return
        self._last_raw_text = text

        candidate = self.evaluate_text(text)
        if candidate is None:
            return

        self.last_candidate_url = candidate.normalized_url
        self.db.add_clipboard_suppression("url", candidate.normalized_url, ttl_seconds=86400.0)
        self.url_detected.emit(candidate.normalized_url, candidate.platform_name)

        cfg = self.db.get_settings()
        # L0: Silent background pre-probe so pressing Ctrl+Shift+D opens in 0ms!
        if cfg.get("clipboard_prefetch", True):
            self.prefetcher.trigger_prefetch(candidate.normalized_url)

        # L1: Passive non-focus bottom-right toast (suppressed if app is foreground OR user is in fullscreen game/video)
        if (
            cfg.get("clipboard_toast", True)
            and not self._is_app_foreground()
            and not is_fullscreen_busy()
        ):
            hk = (cfg.get("hotkeys") or {}).get("capture", "Ctrl+Shift+D")
            self.toast.show_toast(candidate.normalized_url, candidate.platform_name, hk)

    def shutdown(self) -> None:
        self._debounce.stop()
        self.toast.hide()
