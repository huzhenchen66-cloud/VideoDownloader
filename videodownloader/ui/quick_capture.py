"""Raycast-Style Quick Capture Floating Panel (`ui/quick_capture.py`):
- Invoked anywhere via global hotkey (`Ctrl+Shift+D`) or clicking the L1 passive toast strip.
- Win11 Mica backdrop (`DWMWA_SYSTEMBACKDROP_TYPE = 38, mode = 2`) with rounded dark/light translucent fallback.
- Instant 0ms rendering when `SpeculativePrefetcher` has pre-warmed the clipboard URL, or smooth skeleton shimmer while probing.
- Keyboard-first flow: `Enter` download now, `Ctrl+Enter` queue paused, `Tab`/`Left`/`Right` switch quality, `Esc` dismiss & suppress.
"""

from __future__ import annotations

import ctypes
import sys
from typing import Any, Callable

from PyQt6.QtCore import QEvent, QRect, QRectF, Qt, QTimer
from PyQt6.QtGui import (
    QColor,
    QCursor,
    QGuiApplication,
    QKeyEvent,
    QLinearGradient,
    QPainter,
    QPainterPath,
    QPen,
    QPixmap,
)
from PyQt6.QtWidgets import (
    QApplication,
    QFrame,
    QGraphicsDropShadowEffect,
    QHBoxLayout,
    QLabel,
    QLineEdit,
    QPushButton,
    QStackedWidget,
    QVBoxLayout,
    QWidget,
)

from ..core.clipboard_sentinel import ClipboardSentinel
from ..core.db import Database
from ..core.prefetch import PrefetchEntry, SpeculativePrefetcher
from ..core.task_queue import TaskQueueManager
from ..core.url_cleaner import clean_share_input
from .desktop_theme import format_mmss
from .tokens import QUICK_QUALITY_CHIPS, get_anime_theme


def _try_enable_win11_mica(hwnd: int, dark: bool = True) -> bool:
    """Attempt Win11 `DWMWA_SYSTEMBACKDROP_TYPE = 38` (`2 = Mica`) + `DWMWA_USE_IMMERSIVE_DARK_MODE = 20`."""
    if not sys.platform.startswith("win") or not hwnd:
        return False
    try:
        dwmapi = ctypes.windll.dwmapi
        dark_val = ctypes.c_int(1 if dark else 0)
        dwmapi.DwmSetWindowAttribute(ctypes.c_void_p(hwnd), 20, ctypes.byref(dark_val), 4)
        # DWMWA_WINDOW_CORNER_PREFERENCE = 33, DWMWCP_ROUND = 2
        corner_val = ctypes.c_int(2)
        dwmapi.DwmSetWindowAttribute(ctypes.c_void_p(hwnd), 33, ctypes.byref(corner_val), 4)
        # DWMWA_SYSTEMBACKDROP_TYPE = 38, DWMSBT_MAINWINDOW (Mica) = 2
        backdrop_val = ctypes.c_int(2)
        hr = dwmapi.DwmSetWindowAttribute(ctypes.c_void_p(hwnd), 38, ctypes.byref(backdrop_val), 4)
        return hr == 0
    except Exception:
        return False


class _SkeletonShimmerCard(QWidget):
    """Animated skeleton loading card shown during `probing` state (zero spinner text)."""

    def __init__(self, parent: QWidget | None = None) -> None:
        super().__init__(parent)
        self.setFixedHeight(106)
        self._phase: float = 0.0
        self._theme_style: str = "miku"
        self._timer = QTimer(self)
        self._timer.setInterval(40)
        self._timer.timeout.connect(self._tick)

    def set_theme_style(self, theme_style: str) -> None:
        self._theme_style = theme_style
        self.update()

    def start(self) -> None:
        if not self._timer.isActive():
            self._timer.start()

    def stop(self) -> None:
        self._timer.stop()

    def _tick(self) -> None:
        self._phase = (self._phase + 0.045) % 1.0
        self.update()

    def paintEvent(self, _event) -> None:  # noqa: N802
        p = QPainter(self)
        p.setRenderHint(QPainter.RenderHint.Antialiasing, True)
        w = float(self.width())

        theme = get_anime_theme(self._theme_style)
        c1, c2 = theme["gradient"]

        # Animated top 2.5px anime progress sweep
        bar_grad = QLinearGradient(w * (self._phase - 0.35), 0, w * (self._phase + 0.35), 0)
        bar_grad.setColorAt(0.0, QColor(c1).lighter(120))
        c_mid = QColor(c1)
        c_mid.setAlpha(240)
        bar_grad.setColorAt(0.5, c_mid)
        bar_grad.setColorAt(1.0, QColor(c2).lighter(120))
        p.fillRect(QRectF(0, 0, w, 2.5), bar_grad)

        base = QColor(148, 163, 184, 38)
        shimmer_x = w * self._phase
        shim = QLinearGradient(shimmer_x - 140, 0, shimmer_x + 140, 0)
        shim.setColorAt(0.0, base)
        shim.setColorAt(0.5, QColor(148, 163, 184, 82))
        shim.setColorAt(1.0, base)

        for rx, ry, rw, rh, rad in (
            (0.0, 12.0, 160.0, 90.0, 10.0),
            (176.0, 18.0, w - 200.0, 20.0, 6.0),
            (176.0, 48.0, (w - 200.0) * 0.65, 16.0, 6.0),
            (176.0, 78.0, 220.0, 22.0, 11.0),
        ):
            path = QPainterPath()
            path.addRoundedRect(QRectF(rx, ry, max(40.0, rw), rh), rad, rad)
            p.fillPath(path, shim)

        p.end()


class QuickCaptureDialog(QWidget):
    """Frameless floating Quick Capture palette (`720px` wide, 5 states: `empty | probing | ready | cached | failed`)."""

    def __init__(
        self,
        db: Database,
        queue_mgr: TaskQueueManager,
        prefetcher: SpeculativePrefetcher,
        sentinel: ClipboardSentinel,
        on_open_cookie_settings: Callable[[], None],
        on_notify: Callable[[str, str], None],
        parent: QWidget | None = None,
    ) -> None:
        super().__init__(
            None,
            Qt.WindowType.Tool
            | Qt.WindowType.FramelessWindowHint
            | Qt.WindowType.WindowStaysOnTopHint,
        )
        self.db = db
        self.queue_mgr = queue_mgr
        self.prefetcher = prefetcher
        self.sentinel = sentinel
        self._on_open_cookie_settings = on_open_cookie_settings
        self._on_notify = on_notify

        self.setAttribute(Qt.WidgetAttribute.WA_TranslucentBackground, True)
        self.setFixedWidth(720)

        self.state: str = "empty"  # empty | probing | ready | cached | failed
        self._current_norm_url: str = ""
        self._current_entry: PrefetchEntry | None = None
        self._selected_chip_idx: int = 0
        self._subtitle_enabled: bool = True

        self._input_debounce = QTimer(self)
        self._input_debounce.setSingleShot(True)
        self._input_debounce.setInterval(260)
        self._input_debounce.timeout.connect(self._on_debounced_url_edit)

        self._build_ui()
        self.prefetcher.probe_started.connect(self._on_probe_started)
        self.prefetcher.probe_finished.connect(self._on_probe_finished)

    def _build_ui(self) -> None:
        outer = QVBoxLayout(self)
        outer.setContentsMargins(18, 18, 18, 18)

        self.surface = QFrame()
        self.surface.setObjectName("qcSurface")
        shadow = QGraphicsDropShadowEffect(self.surface)
        shadow.setBlurRadius(36)
        shadow.setOffset(0, 12)
        shadow.setColor(QColor(0, 0, 0, 145))
        self.surface.setGraphicsEffect(shadow)
        outer.addWidget(self.surface)

        lay = QVBoxLayout(self.surface)
        lay.setContentsMargins(20, 18, 20, 14)
        lay.setSpacing(14)

        # Row 1: URL Bar + Instant Download Button
        top_row = QHBoxLayout()
        top_row.setSpacing(10)

        self.url_edit = QLineEdit()
        self.url_edit.setObjectName("qcUrlEdit")
        self.url_edit.setPlaceholderText("粘贴任意视频链接或分享口令，按 Enter 立即下载...")
        self.url_edit.setMinimumHeight(42)
        self.url_edit.textEdited.connect(lambda _: self._input_debounce.start())
        top_row.addWidget(self.url_edit, 1)

        self.btn_download = QPushButton("立即下载")
        self.btn_download.setObjectName("qcPrimaryBtn")
        self.btn_download.setMinimumHeight(42)
        self.btn_download.setCursor(Qt.CursorShape.PointingHandCursor)
        self.btn_download.clicked.connect(lambda: self.commit_download(start_paused=False))
        top_row.addWidget(self.btn_download)
        lay.addLayout(top_row)

        # Row 2: State Stack (`0=empty`, `1=probing`, `2=ready/cached`, `3=failed`)
        self.stack = QStackedWidget()

        # Page 0: Empty
        p_empty = QWidget()
        pe_lay = QVBoxLayout(p_empty)
        pe_lay.setContentsMargins(4, 10, 4, 10)
        self.lbl_empty = QLabel("支持 YouTube · B站 · 抖音 · 快手 · 小红书 · TikTok · X 等 1800+ 站点 · 复制链接即可后台秒级预热")
        self.lbl_empty.setObjectName("qcMutedText")
        pe_lay.addWidget(self.lbl_empty)
        self.stack.addWidget(p_empty)

        # Page 1: Probing Skeleton
        self.skeleton = _SkeletonShimmerCard()
        self.stack.addWidget(self.skeleton)

        # Page 2: Ready / Cached Media Card
        p_ready = QWidget()
        pr_lay = QVBoxLayout(p_ready)
        pr_lay.setContentsMargins(0, 2, 0, 2)
        pr_lay.setSpacing(12)

        info_row = QHBoxLayout()
        info_row.setSpacing(16)

        self.thumb_lbl = QLabel()
        self.thumb_lbl.setFixedSize(160, 90)
        self.thumb_lbl.setAlignment(Qt.AlignmentFlag.AlignCenter)
        self.thumb_lbl.setObjectName("qcThumb")
        info_row.addWidget(self.thumb_lbl)

        meta_col = QVBoxLayout()
        meta_col.setSpacing(6)

        badge_row = QHBoxLayout()
        badge_row.setSpacing(8)
        self.lbl_platform_badge = QLabel("平台")
        self.lbl_platform_badge.setObjectName("qcPlatformBadge")
        badge_row.addWidget(self.lbl_platform_badge)

        self.lbl_cache_badge = QLabel("极速预热 0ms")
        self.lbl_cache_badge.setObjectName("qcCacheBadge")
        badge_row.addWidget(self.lbl_cache_badge)
        badge_row.addStretch(1)
        meta_col.addLayout(badge_row)

        self.lbl_title = QLabel("视频标题")
        self.lbl_title.setObjectName("qcTitle")
        self.lbl_title.setWordWrap(True)
        meta_col.addWidget(self.lbl_title)

        self.lbl_submeta = QLabel("")
        self.lbl_submeta.setObjectName("qcMutedText")
        meta_col.addWidget(self.lbl_submeta)
        meta_col.addStretch(1)
        info_row.addLayout(meta_col, 1)
        pr_lay.addLayout(info_row)

        # Interactive Quality Chips + Subtitle toggle
        chips_row = QHBoxLayout()
        chips_row.setSpacing(8)
        self._chip_buttons: list[QPushButton] = []
        for idx, (label, _qk, _oa, _af) in enumerate(QUICK_QUALITY_CHIPS):
            btn = QPushButton(label)
            btn.setCheckable(True)
            btn.setObjectName("qcChip")
            btn.setCursor(Qt.CursorShape.PointingHandCursor)
            btn.clicked.connect(lambda _, i=idx: self._select_chip(i))
            chips_row.addWidget(btn)
            self._chip_buttons.append(btn)

        chips_row.addStretch(1)

        self.btn_sub_chip = QPushButton("字幕: 开")
        self.btn_sub_chip.setCheckable(True)
        self.btn_sub_chip.setChecked(True)
        self.btn_sub_chip.setObjectName("qcChip")
        self.btn_sub_chip.clicked.connect(self._toggle_subtitle_chip)
        chips_row.addWidget(self.btn_sub_chip)
        pr_lay.addLayout(chips_row)
        self.stack.addWidget(p_ready)

        # Page 3: Failed with structured ErrorCode & 1-click actions
        p_fail = QWidget()
        pf_lay = QVBoxLayout(p_fail)
        pf_lay.setContentsMargins(2, 6, 2, 6)
        pf_lay.setSpacing(10)

        self.lbl_fail_reason = QLabel("")
        self.lbl_fail_reason.setObjectName("qcErrorText")
        self.lbl_fail_reason.setWordWrap(True)
        pf_lay.addWidget(self.lbl_fail_reason)

        fail_btns = QHBoxLayout()
        fail_btns.setSpacing(8)
        self.btn_fail_cookie = QPushButton("打开 Cookie 设置")
        self.btn_fail_cookie.setObjectName("qcChip")
        self.btn_fail_cookie.clicked.connect(self._handle_open_cookie)
        fail_btns.addWidget(self.btn_fail_cookie)

        btn_force_dl = QPushButton("仍要尝试直接下载")
        btn_force_dl.setObjectName("qcChip")
        btn_force_dl.clicked.connect(lambda: self.commit_download(start_paused=False))
        fail_btns.addWidget(btn_force_dl)

        btn_ignore_domain = QPushButton("不再提示此域名")
        btn_ignore_domain.setObjectName("qcChip")
        btn_ignore_domain.clicked.connect(self._handle_ignore_domain)
        fail_btns.addWidget(btn_ignore_domain)

        fail_btns.addStretch(1)
        pf_lay.addLayout(fail_btns)
        self.stack.addWidget(p_fail)

        lay.addWidget(self.stack)

        # Row 3: Keyboard Shortcut Legend Footer
        footer = QHBoxLayout()
        lbl_keys = QLabel("Enter 立即下载   ·   Ctrl+Enter 加入暂停队列   ·   Tab 切换清晰度   ·   Esc 关闭并忽略")
        lbl_keys.setObjectName("qcFooterText")
        footer.addWidget(lbl_keys)
        footer.addStretch(1)
        lay.addLayout(footer)

        self._select_chip(0)
        cfg = self.db.get_settings()
        self.apply_theme(
            is_dark=(cfg.get("theme_mode") == "dark"),
            theme_style=cfg.get("theme_style", "miku"),
        )

    def apply_theme(self, is_dark: bool, theme_style: str | None = None) -> None:
        self._is_dark = is_dark
        theme = get_anime_theme(theme_style)
        if hasattr(self, "skeleton"):
            self.skeleton.set_theme_style(theme["id"])
        accent = theme["accent_light"] if is_dark else theme["accent"]

        if is_dark:
            bg = "rgba(20, 25, 36, 244)"
            border = "rgba(148, 163, 184, 55)"
            inp_bg = "rgba(15, 19, 28, 230)"
            txt = "#F8FAFC"
            muted = "#94A3B8"
            chip_bg = "rgba(35, 43, 61, 220)"
            chip_fg = "#CBD5E1"
            badge_bg = theme["accent_bg_dark"]
        else:
            bg = "rgba(255, 255, 255, 248)"
            border = "rgba(203, 213, 225, 220)"
            inp_bg = "#F8FAFC"
            txt = "#0F172A"
            muted = "#64748B"
            chip_bg = "#F1F5F9"
            chip_fg = "#334155"
            badge_bg = theme["accent_bg_light"]

        self.surface.setStyleSheet(
            f"""
            QFrame#qcSurface {{
                background-color: {bg};
                border: 1.2px solid {border};
                border-radius: 14px;
            }}
            QLineEdit#qcUrlEdit {{
                background-color: {inp_bg};
                color: {txt};
                border: 1.4px solid {accent};
                border-radius: 10px;
                padding: 6px 14px;
                font-size: 14px;
                font-family: "Segoe UI", "Microsoft YaHei UI";
            }}
            QPushButton#qcPrimaryBtn {{
                background-color: {accent};
                color: #FFFFFF;
                border: none;
                border-radius: 10px;
                padding: 0 20px;
                font-size: 14px;
                font-weight: 700;
            }}
            QPushButton#qcPrimaryBtn:hover {{
                background-color: {theme['gradient'][0]};
            }}
            QLabel#qcTitle {{
                color: {txt};
                font-size: 15px;
                font-weight: 700;
            }}
            QLabel#qcMutedText {{
                color: {muted};
                font-size: 12px;
            }}
            QLabel#qcFooterText {{
                color: {muted};
                font-size: 11px;
            }}
            QLabel#qcThumb {{
                background-color: #0F172A;
                color: #94A3B8;
                border-radius: 10px;
                font-size: 12px;
                font-weight: 600;
            }}
            QLabel#qcPlatformBadge {{
                background-color: {badge_bg};
                color: {accent};
                border-radius: 6px;
                padding: 2px 8px;
                font-size: 11px;
                font-weight: 700;
            }}
            QLabel#qcCacheBadge {{
                background-color: rgba(16, 185, 129, 32);
                color: #059669;
                border-radius: 6px;
                padding: 2px 8px;
                font-size: 11px;
                font-weight: 700;
            }}
            QLabel#qcErrorText {{
                color: #EF4444;
                font-size: 13px;
                font-weight: 600;
            }}
            QPushButton#qcChip {{
                background-color: {chip_bg};
                color: {chip_fg};
                border: 1px solid {border};
                border-radius: 8px;
                padding: 5px 12px;
                font-size: 12px;
                font-weight: 600;
            }}
            QPushButton#qcChip:checked {{
                background-color: #0284C7;
                color: #FFFFFF;
                border-color: #0284C7;
            }}
            """
        )

    def _select_chip(self, idx: int) -> None:
        self._selected_chip_idx = idx % len(self._chip_buttons)
        for i, btn in enumerate(self._chip_buttons):
            btn.setChecked(i == self._selected_chip_idx)

    def _toggle_subtitle_chip(self) -> None:
        self._subtitle_enabled = self.btn_sub_chip.isChecked()
        self.btn_sub_chip.setText("字幕: 开" if self._subtitle_enabled else "字幕: 关")

    # -------------------- Open & State Transitions --------------------

    def open_panel(self, initial_url: str = "") -> None:
        """Open Quick Capture centered at 30% height of the screen under the mouse cursor."""
        cfg = self.db.get_settings()
        is_dark = cfg.get("theme_mode") == "dark"
        self.apply_theme(is_dark=is_dark)
        self._subtitle_enabled = bool(cfg.get("subtitle", True))
        self.btn_sub_chip.setChecked(self._subtitle_enabled)
        self.btn_sub_chip.setText("字幕: 开" if self._subtitle_enabled else "字幕: 关")

        # Determine initial URL: explicit -> sentinel's last candidate -> current clipboard
        candidate_raw = (initial_url or "").strip()
        if not candidate_raw:
            clip = (QApplication.clipboard().text() or "").strip()
            cleaned = clean_share_input(clip, resolve_short_links=False)
            if cleaned and cleaned[0].platform_id != "generic":
                candidate_raw = cleaned[0].normalized_url
            elif self.sentinel.last_candidate_url:
                candidate_raw = self.sentinel.last_candidate_url

        self.url_edit.setText(candidate_raw)
        if candidate_raw:
            self._load_or_probe_url(candidate_raw)
        else:
            self._set_state_empty()

        self.adjustSize()
        self._position_on_active_screen()

        if (QGuiApplication.platformName() or "").lower() != "offscreen":
            _try_enable_win11_mica(int(self.winId()), dark=is_dark)

        self.show()
        self.raise_()
        self.activateWindow()
        self.url_edit.setFocus()
        self.url_edit.selectAll()

    def _position_on_active_screen(self) -> None:
        screen = QGuiApplication.screenAt(QCursor.pos()) or QGuiApplication.primaryScreen()
        avail: QRect = screen.availableGeometry() if screen else QRect(0, 0, 1920, 1080)
        x = avail.x() + (avail.width() - self.width()) // 2
        y = avail.y() + int(avail.height() * 0.26)
        self.move(max(avail.x() + 12, x), max(avail.y() + 12, y))

    def _load_or_probe_url(self, raw_text: str) -> None:
        cleaned = clean_share_input(raw_text, resolve_short_links=False)
        if not cleaned:
            self._set_state_empty()
            return
        norm_url = cleaned[0].normalized_url
        self._current_norm_url = norm_url

        # Zero-wait path: check 2-tier SpeculativePrefetcher cache first!
        cached = self.prefetcher.get_cached(norm_url)
        if cached is not None:
            self._apply_entry(cached, is_cached_hit=True)
            return

        self._set_state_probing()
        self.prefetcher.trigger_prefetch(norm_url)

    def _on_debounced_url_edit(self) -> None:
        txt = self.url_edit.text().strip()
        if not txt:
            self._set_state_empty()
            return
        self._load_or_probe_url(txt)

    def _set_state_empty(self) -> None:
        self.state = "empty"
        self._current_norm_url = ""
        self._current_entry = None
        self.skeleton.stop()
        self.stack.setCurrentIndex(0)
        self.adjustSize()

    def _set_state_probing(self) -> None:
        self.state = "probing"
        self._current_entry = None
        self.stack.setCurrentIndex(1)
        self.skeleton.start()
        self.adjustSize()

    def _on_probe_started(self, norm_url: str) -> None:
        if norm_url == self._current_norm_url and self.state not in ("ready", "cached"):
            self._set_state_probing()

    def _on_probe_finished(self, norm_url: str, entry_obj: object) -> None:
        if norm_url != self._current_norm_url or not isinstance(entry_obj, PrefetchEntry):
            return
        self._apply_entry(entry_obj, is_cached_hit=entry_obj.from_cache)

    def _apply_entry(self, entry: PrefetchEntry, is_cached_hit: bool) -> None:
        self.skeleton.stop()
        self._current_entry = entry

        if entry.ok and entry.media:
            self.state = "cached" if is_cached_hit else "ready"
            media = entry.media
            self.lbl_platform_badge.setText(entry.platform_name or media.get("platform_name") or "视频")
            self.lbl_cache_badge.setVisible(is_cached_hit)

            title = str(media.get("title") or "未命名视频")
            self.lbl_title.setText(title[:86] + ("..." if len(title) > 86 else ""))

            parts = []
            if media.get("uploader"):
                parts.append(str(media["uploader"]))
            if media.get("duration"):
                parts.append(f"时长 {format_mmss(float(media['duration']))}")
            if media.get("engine_name"):
                parts.append(f"引擎 {media['engine_name']} ({media.get('elapsed_ms', 0)}ms)")
            self.lbl_submeta.setText("  ·  ".join(parts) or entry.url[:68])

            pix = QPixmap()
            if entry.thumbnail_bytes:
                pix.loadFromData(entry.thumbnail_bytes)
            if not pix.isNull():
                scaled = pix.scaled(
                    160, 90, Qt.AspectRatioMode.KeepAspectRatioByExpanding, Qt.TransformationMode.SmoothTransformation
                )
                cropped = scaled.copy(
                    max(0, (scaled.width() - 160) // 2),
                    max(0, (scaled.height() - 90) // 2),
                    min(160, scaled.width()),
                    min(90, scaled.height()),
                )
                rounded = QPixmap(160, 90)
                rounded.fill(Qt.GlobalColor.transparent)
                p = QPainter(rounded)
                p.setRenderHint(QPainter.RenderHint.Antialiasing, True)
                clip_path = QPainterPath()
                clip_path.addRoundedRect(0.0, 0.0, 160.0, 90.0, 10.0, 10.0)
                p.setClipPath(clip_path)
                p.drawPixmap(0, 0, cropped)
                p.end()
                self.thumb_lbl.setPixmap(rounded)
            else:
                self.thumb_lbl.setPixmap(QPixmap())
                self.thumb_lbl.setText(entry.platform_name or "MEDIA")

            self.stack.setCurrentIndex(2)
        else:
            self.state = "failed"
            code_tag = f"[{entry.error_code}] " if entry.error_code else ""
            self.lbl_fail_reason.setText(f"{code_tag}{entry.error_hint or '解析预览失败，仍可直接投递下载任务'}")
            self.btn_fail_cookie.setVisible(entry.error_code in ("auth_required", "risk_control"))
            self.stack.setCurrentIndex(3)

        self.adjustSize()

    # -------------------- Actions & Keyboard Flow --------------------

    def commit_download(self, start_paused: bool = False) -> bool:
        raw = self.url_edit.text().strip()
        if not raw:
            return False

        _label, q_key, only_aud, aud_fmt = QUICK_QUALITY_CHIPS[self._selected_chip_idx]
        overrides = {
            "quality": q_key,
            "only_audio": only_aud,
            "audio_format": aud_fmt,
            "subtitle": self._subtitle_enabled,
        }
        media = (self._current_entry.media or {}) if (self._current_entry and self._current_entry.ok) else {}
        trace = self._current_entry.trace if self._current_entry else None

        try:
            tasks = self.queue_mgr.submit_tasks(
                raw_text=raw,
                overrides=overrides,
                prefetched_title=str(media.get("title") or ""),
                prefetched_thumbnail=str(media.get("thumbnail") or ""),
                prefetched_duration=float(media.get("duration") or 0.0),
                prefetched_uploader=str(media.get("uploader") or ""),
                prefetched_trace=trace,
                start_paused=start_paused,
            )
        except Exception as exc:
            self.state = "failed"
            self.lbl_fail_reason.setText(str(exc))
            self.stack.setCurrentIndex(3)
            self.adjustSize()
            return False

        if self._current_norm_url:
            self.sentinel.suppress_url_24h(self._current_norm_url)
        self.skeleton.stop()
        self.hide()

        if tasks:
            mode_txt = "已加入暂停队列" if start_paused else "已开始极速下载"
            self._on_notify(mode_txt, tasks[0].get("title") or raw[:48])
        return True

    def dismiss_and_suppress(self) -> None:
        if self._current_norm_url:
            self.sentinel.suppress_url_24h(self._current_norm_url)
        else:
            self.sentinel.snooze(10.0)
        self.skeleton.stop()
        self.hide()

    def _handle_open_cookie(self) -> None:
        self.skeleton.stop()
        self.hide()
        self._on_open_cookie_settings()

    def _handle_ignore_domain(self) -> None:
        target = self._current_norm_url or self.url_edit.text().strip()
        host = self.sentinel.suppress_domain_forever(target)
        self.skeleton.stop()
        self.hide()
        if host:
            self._on_notify("已忽略域名", f"剪贴板哨兵将不再提示来自 {host} 的链接")

    def keyPressEvent(self, event: QKeyEvent) -> None:  # noqa: N802
        key = event.key()
        mods = event.modifiers()

        if key == Qt.Key.Key_Escape:
            self.dismiss_and_suppress()
            event.accept()
            return

        if key in (Qt.Key.Key_Return, Qt.Key.Key_Enter):
            paused = bool(mods & Qt.KeyboardModifier.ControlModifier)
            self.commit_download(start_paused=paused)
            event.accept()
            return

        if key == Qt.Key.Key_Tab or (
            key in (Qt.Key.Key_Right, Qt.Key.Key_Left) and not self.url_edit.hasSelectedText() and not self.url_edit.hasFocus()
        ):
            step = -1 if (key == Qt.Key.Key_Left or (mods & Qt.KeyboardModifier.ShiftModifier)) else 1
            self._select_chip(self._selected_chip_idx + step)
            event.accept()
            return

        super().keyPressEvent(event)

    def focusNextPrevChild(self, _next: bool) -> bool:  # noqa: N802
        # Let Tab cycle quality chips inside Quick Capture rather than jumping focus off the URL bar
        self._select_chip(self._selected_chip_idx + (1 if _next else -1))
        return True

    def event(self, ev: QEvent) -> bool:
        if ev.type() == QEvent.Type.WindowDeactivate and self.isVisible():
            if (QGuiApplication.platformName() or "").lower() != "offscreen":
                self.skeleton.stop()
                self.hide()
        return super().event(ev)
