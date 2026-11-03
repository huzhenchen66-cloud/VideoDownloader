"""Manna-inspired Rounded Media Task Card (`ui/task_card.py`):
- Airy card layout with rounded 16:9 cover thumbnail (or aurora vector media badge), title, status pill,
  slim Fluent `ProgressBar`, circular `FluentIcon` action buttons, and 1-click Cookie settings jump on `auth_required`.
- Expandable 4-Stage Engine `ResolutionTrace` tree (`Stage 1..4 跳过/失败/成功 + 耗时 + 原因`).
"""

from __future__ import annotations

import contextlib
import io
from typing import Any, Callable

with contextlib.redirect_stdout(io.StringIO()):
    from qfluentwidgets import CardWidget, FluentIcon as FIF, ProgressBar, PushButton, ToolButton

from PyQt6.QtCore import QPointF, QRectF, Qt
from PyQt6.QtGui import QColor, QFont, QLinearGradient, QPainter, QPainterPath, QPixmap, QPolygonF
from PyQt6.QtWidgets import QFrame, QHBoxLayout, QLabel, QVBoxLayout, QWidget

from ..engines.base import ResolutionTrace, StageRecord
from .desktop_theme import format_mmss, format_size
from .tokens import MONO_STACK, STATUS_TEXT, get_anime_theme, get_palette, status_badge_colors


def format_trace_dict_as_tree(trace_dict: dict[str, Any] | None) -> str:
    """Format a persisted `trace` dict into the human-readable 4-stage resolution tree."""
    if not trace_dict or not isinstance(trace_dict, dict):
        return "暂无解析轨迹记录"
    raw_stages = trace_dict.get("stages") or []
    stages = [
        StageRecord(
            stage=int(s.get("stage") or (i + 1)),
            engine=str(s.get("engine") or ""),
            status=str(s.get("status") or ""),
            elapsed_ms=int(s.get("elapsed_ms") or 0),
            reason=str(s.get("reason") or ""),
            error_code=str(s.get("error_code") or ""),
        )
        for i, s in enumerate(raw_stages)
    ]
    rt = ResolutionTrace(
        url=str(trace_dict.get("url") or ""),
        platform_id=str(trace_dict.get("platform_id") or "generic"),
        operation=str(trace_dict.get("operation") or "download"),
        stages=stages,
        final_engine=str(trace_dict.get("final_engine") or ""),
        total_ms=int(trace_dict.get("total_ms") or 0),
        succeeded=bool(trace_dict.get("succeeded")),
    )
    header = (
        f"总耗时 {rt.total_ms}ms · 最终命中: {rt.final_engine or '无'}\n"
        if rt.stages
        else ""
    )
    return header + rt.format_tree()


PLATFORM_COVER_GRADIENTS: dict[str, tuple[str, str]] = {
    "bilibili": ("#FB7299", "#FF9EBA"),
    "youtube": ("#CC0000", "#FF4D4D"),
    "douyin": ("#121824", "#00F2FE"),
    "xiaohongshu": ("#E02438", "#FF6B81"),
    "kuaishou": ("#E64A19", "#FFA040"),
    "weibo": ("#C62828", "#FFA726"),
    "twitter": ("#101726", "#1D9BF0"),
    "tiktok": ("#010101", "#25F4EE"),
    "instagram": ("#833AB4", "#FD1D1D"),
    "zhihu": ("#0066FF", "#40A9FF"),
}


def render_placeholder_cover(platform_name: str, only_audio: bool = False, theme_style: str = "miku") -> QPixmap:
    """Draw an aesthetic anime-inspired or platform-tailored gradient media thumbnail placeholder with glowing accent."""
    w, h = 120, 68
    pix = QPixmap(w, h)
    pix.fill(Qt.GlobalColor.transparent)

    p = QPainter(pix)
    p.setRenderHint(QPainter.RenderHint.Antialiasing, True)

    path = QPainterPath()
    path.addRoundedRect(0.0, 0.0, float(w), float(h), 8.0, 8.0)

    grad = QLinearGradient(0, 0, w, h)
    if only_audio:
        grad.setColorAt(0.0, QColor("#06B6D4"))
        grad.setColorAt(1.0, QColor("#6366F1"))
    else:
        plat_lower = (platform_name or "").lower()
        matched_plat_gradient: tuple[str, str] | None = None
        for pid, (c_start, c_end) in PLATFORM_COVER_GRADIENTS.items():
            if pid in plat_lower or (pid == "bilibili" and "哔哩" in plat_lower) or (pid == "douyin" and "抖音" in plat_lower) or (pid == "xiaohongshu" and "红书" in plat_lower) or (pid == "kuaishou" and "快手" in plat_lower) or (pid == "weibo" and "微博" in plat_lower) or (pid == "zhihu" and "知乎" in plat_lower):
                matched_plat_gradient = (c_start, c_end)
                break

        if matched_plat_gradient:
            grad.setColorAt(0.0, QColor(matched_plat_gradient[0]))
            grad.setColorAt(1.0, QColor(matched_plat_gradient[1]))
        else:
            theme = get_anime_theme(theme_style)
            c1, c2 = theme["gradient"]
            grad.setColorAt(0.0, QColor(c1))
            grad.setColorAt(0.5, QColor(theme.get("accent_light", c1)))
            grad.setColorAt(1.0, QColor(c2))
    p.fillPath(path, grad)

    # Translucent center play circle
    p.setPen(Qt.PenStyle.NoPen)
    p.setBrush(QColor(255, 255, 255, 60))
    p.drawEllipse(QRectF(w / 2 - 14, h / 2 - 17, 28, 28))

    tri = QPolygonF(
        [
            QPointF(w / 2 - 4, h / 2 - 10),
            QPointF(w / 2 + 7, h / 2 - 3),
            QPointF(w / 2 - 4, h / 2 + 4),
        ]
    )
    p.setBrush(QColor(255, 255, 255, 240))
    p.drawPolygon(tri)

    font = QFont("Microsoft YaHei UI", 8, QFont.Weight.Bold)
    p.setFont(font)
    p.setPen(QColor(255, 255, 255, 230))
    tag = (platform_name or "MEDIA").split("(")[0].strip()[:10]
    p.drawText(QRectF(4, h - 19, w - 8, 16), Qt.AlignmentFlag.AlignCenter, tag)
    p.end()
    return pix


class MediaTaskCard(CardWidget):
    """Single task card displaying live progress, media actions & expandable 4-Stage `ResolutionTrace`."""

    def __init__(
        self,
        task: dict[str, Any],
        is_dark: bool,
        thumbnail_bytes: bytes,
        on_pause: Callable[[str], None],
        on_resume: Callable[[str], None],
        on_cancel: Callable[[str], None],
        on_delete: Callable[[str], None],
        on_play: Callable[[str], None],
        on_reveal_folder: Callable[[str], None],
        on_open_ai: Callable[[str], None],
        on_open_cookie_settings: Callable[[], None],
        expanded_initial: bool = False,
        on_toggle_trace: Callable[[str, bool], None] | None = None,
        theme_style: str = "miku",
        parent: QWidget | None = None,
    ) -> None:
        super().__init__(parent)
        self.task_id: str = str(task["id"])
        self._is_dark = is_dark
        self._theme_style = theme_style
        self._trace_expanded = expanded_initial
        self._on_toggle_trace = on_toggle_trace

        self._on_pause = on_pause
        self._on_resume = on_resume
        self._on_cancel = on_cancel
        self._on_delete = on_delete
        self._on_play = on_play
        self._on_reveal_folder = on_reveal_folder
        self._on_open_ai = on_open_ai
        self._on_open_cookie_settings = on_open_cookie_settings

        self._build_layout()
        self.update_from_task(task, is_dark=is_dark, thumbnail_bytes=thumbnail_bytes)

    def _build_layout(self) -> None:
        outer = QVBoxLayout(self)
        outer.setContentsMargins(16, 14, 16, 14)
        outer.setSpacing(10)

        top_row = QHBoxLayout()
        top_row.setSpacing(16)

        # 1. Cover Thumbnail (120x68 rounded)
        self.cover_lbl = QLabel()
        self.cover_lbl.setFixedSize(120, 68)
        self.cover_lbl.setAlignment(Qt.AlignmentFlag.AlignCenter)
        top_row.addWidget(self.cover_lbl)

        # 2. Title + Metadata + Slim Progress Bar
        mid_col = QVBoxLayout()
        mid_col.setSpacing(5)

        title_row = QHBoxLayout()
        title_row.setSpacing(8)
        self.title_lbl = QLabel()
        self.title_lbl.setWordWrap(False)
        title_row.addWidget(self.title_lbl, 1)

        self.status_badge = QLabel()
        title_row.addWidget(self.status_badge)
        mid_col.addLayout(title_row)

        self.meta_lbl = QLabel()
        self.meta_lbl.setWordWrap(True)
        mid_col.addWidget(self.meta_lbl)

        self.progress_bar = ProgressBar()
        self.progress_bar.setFixedHeight(4)
        self.progress_bar.setRange(0, 1000)
        mid_col.addWidget(self.progress_bar)
        mid_col.addStretch(1)
        top_row.addLayout(mid_col, 1)

        # 3. Right Vector Action Bar
        self.actions_wrap = QWidget()
        self.actions_lay = QHBoxLayout(self.actions_wrap)
        self.actions_lay.setContentsMargins(0, 0, 0, 0)
        self.actions_lay.setSpacing(6)
        top_row.addWidget(self.actions_wrap)

        outer.addLayout(top_row)

        # 4. Collapsible 4-Stage ResolutionTrace Panel
        self.trace_frame = QFrame()
        self.trace_frame.setVisible(self._trace_expanded)
        tf_lay = QVBoxLayout(self.trace_frame)
        tf_lay.setContentsMargins(12, 10, 12, 10)
        tf_lay.setSpacing(4)

        self.trace_header_lbl = QLabel("四级多引擎解析轨迹 (ResolutionTrace)")
        tf_lay.addWidget(self.trace_header_lbl)

        self.trace_body_lbl = QLabel()
        self.trace_body_lbl.setTextInteractionFlags(Qt.TextInteractionFlag.TextSelectableByMouse)
        tf_lay.addWidget(self.trace_body_lbl)
        outer.addWidget(self.trace_frame)

    def _set_cover(self, thumbnail_bytes: bytes, platform_name: str, only_audio: bool) -> None:
        if thumbnail_bytes:
            src = QPixmap()
            if src.loadFromData(thumbnail_bytes) and not src.isNull():
                scaled = src.scaled(
                    120, 68, Qt.AspectRatioMode.KeepAspectRatioByExpanding, Qt.TransformationMode.SmoothTransformation
                )
                cropped = scaled.copy(
                    max(0, (scaled.width() - 120) // 2),
                    max(0, (scaled.height() - 68) // 2),
                    min(120, scaled.width()),
                    min(68, scaled.height()),
                )
                rounded = QPixmap(120, 68)
                rounded.fill(Qt.GlobalColor.transparent)
                p = QPainter(rounded)
                p.setRenderHint(QPainter.RenderHint.Antialiasing, True)
                path = QPainterPath()
                path.addRoundedRect(0.0, 0.0, 120.0, 68.0, 8.0, 8.0)
                p.setClipPath(path)
                p.drawPixmap(0, 0, cropped)
                p.end()
                self.cover_lbl.setPixmap(rounded)
                return
        self.cover_lbl.setPixmap(
            render_placeholder_cover(platform_name, only_audio=only_audio, theme_style=self._theme_style)
        )

    def _toggle_trace_clicked(self) -> None:
        self._trace_expanded = not self._trace_expanded
        self.trace_frame.setVisible(self._trace_expanded)
        if self._on_toggle_trace:
            self._on_toggle_trace(self.task_id, self._trace_expanded)

    def update_from_task(
        self,
        task: dict[str, Any],
        is_dark: bool,
        thumbnail_bytes: bytes = b"",
        theme_style: str | None = None,
    ) -> None:
        self._is_dark = is_dark
        if theme_style:
            self._theme_style = theme_style
        pal = get_palette(is_dark, self._theme_style)
        st = str(task.get("status") or "queued")
        pct = float(task.get("progress") or 0.0)
        err = str(task.get("error") or "")
        err_code = str(task.get("error_code") or "")
        trace = task.get("trace")

        self._set_cover(
            thumbnail_bytes=thumbnail_bytes,
            platform_name=str(task.get("platform_name") or "Video"),
            only_audio=bool(task.get("only_audio")),
        )

        raw_title = str(task.get("title") or "等待解析...")
        display_title = raw_title[:68] + ("..." if len(raw_title) > 68 else "")
        self.title_lbl.setText(display_title)
        self.title_lbl.setToolTip(task.get("file_path") or task.get("url") or raw_title)
        self.title_lbl.setStyleSheet(f"color: {pal['text_primary']}; font-size: 14px; font-weight: 600;")

        fg, bg = status_badge_colors(st, is_dark, self._theme_style)
        st_label = STATUS_TEXT.get(st, st)
        if st == "downloading":
            spd = str(task.get("speed") or "").strip()
            if spd:
                st_label = f"下载中 {pct:.0f}% · {spd}"
            elif pct > 0:
                st_label = f"下载中 {pct:.0f}%"
        self.status_badge.setText(st_label)
        self.status_badge.setStyleSheet(
            f"background-color: {bg}; color: {fg}; border-radius: 6px; padding: 2px 8px; font-size: 11px; font-weight: 700;"
        )

        # Metadata / error line
        if st in ("failed", "retrying") and err:
            code_prefix = f"[{err_code}] " if err_code else ""
            self.meta_lbl.setText(f"{code_prefix}{err[:140]}")
            self.meta_lbl.setStyleSheet(f"color: {pal['danger']}; font-size: 12px;")
        else:
            meta_parts: list[str] = []
            if task.get("platform_name"):
                meta_parts.append(str(task["platform_name"]))
            if task.get("engine_used"):
                meta_parts.append(f"引擎: {task['engine_used']}")
            q_map = {
                "best_1080p": "1080P H.264",
                "best_4k": "4K 原画",
                "fast_720p": "720P",
                "save_480p": "480P",
                "best_mkv": "MKV 母带",
            }
            if task.get("only_audio"):
                meta_parts.append(f"音频 {str(task.get('audio_format') or 'mp3').upper()}")
            else:
                meta_parts.append(q_map.get(str(task.get("quality") or ""), "高清 MP4"))
            if task.get("duration"):
                meta_parts.append(format_mmss(float(task["duration"])))
            if task.get("file_size"):
                meta_parts.append(format_size(task["file_size"]))
            if st in ("downloading", "merging") and task.get("speed"):
                spd_txt = str(task["speed"])
                if task.get("eta"):
                    spd_txt += f" · 剩余 {task['eta']}"
                meta_parts.append(spd_txt)
            self.meta_lbl.setText("  ·  ".join(meta_parts))
            self.meta_lbl.setStyleSheet(f"color: {pal['text_secondary']}; font-size: 12px;")

        # Progress bar visibility (hide once completed for airy Manna look)
        if st == "completed":
            self.progress_bar.setVisible(False)
        else:
            self.progress_bar.setVisible(True)
            self.progress_bar.setValue(int(max(0.0, min(100.0, pct)) * 10))

        # Rebuild action buttons cleanly
        while self.actions_lay.count():
            item = self.actions_lay.takeAt(0)
            w = item.widget()
            if w is not None:
                w.deleteLater()

        if st in ("queued", "parsing", "downloading", "merging", "retrying"):
            btn_pause = ToolButton(FIF.PAUSE, self.actions_wrap)
            btn_pause.setToolTip("暂停任务")
            btn_pause.clicked.connect(lambda: self._on_pause(self.task_id))
            self.actions_lay.addWidget(btn_pause)

            btn_cancel = ToolButton(FIF.CLOSE, self.actions_wrap)
            btn_cancel.setToolTip("取消任务")
            btn_cancel.clicked.connect(lambda: self._on_cancel(self.task_id))
            self.actions_lay.addWidget(btn_cancel)

        elif st in ("paused", "interrupted", "failed", "canceled"):
            if err_code in ("auth_required", "risk_control"):
                btn_cookie = PushButton(FIF.CERTIFICATE, "设置 Cookie", self.actions_wrap)
                btn_cookie.setToolTip("该站点需要登录鉴权，点击直达 Cookie 设置")
                btn_cookie.clicked.connect(self._on_open_cookie_settings)
                self.actions_lay.addWidget(btn_cookie)

            btn_retry = ToolButton(FIF.SYNC if st == "failed" else FIF.PLAY, self.actions_wrap)
            btn_retry.setToolTip("重新尝试下载" if st == "failed" else "继续下载")
            btn_retry.clicked.connect(lambda: self._on_resume(self.task_id))
            self.actions_lay.addWidget(btn_retry)

            btn_del = ToolButton(FIF.DELETE, self.actions_wrap)
            btn_del.setToolTip("移除记录")
            btn_del.clicked.connect(lambda: self._on_delete(self.task_id))
            self.actions_lay.addWidget(btn_del)

        elif st == "completed":
            btn_play = ToolButton(FIF.PLAY, self.actions_wrap)
            btn_play.setToolTip("在内置播放器中播放")
            btn_play.clicked.connect(lambda: self._on_play(self.task_id))
            self.actions_lay.addWidget(btn_play)

            btn_folder = ToolButton(FIF.FOLDER, self.actions_wrap)
            btn_folder.setToolTip("在资源管理器中定位文件")
            btn_folder.clicked.connect(lambda: self._on_reveal_folder(self.task_id))
            self.actions_lay.addWidget(btn_folder)

            btn_ai = ToolButton(FIF.CHAT, self.actions_wrap)
            btn_ai.setToolTip("送入 AI 视频精读")
            btn_ai.clicked.connect(lambda: self._on_open_ai(self.task_id))
            self.actions_lay.addWidget(btn_ai)

            btn_del = ToolButton(FIF.DELETE, self.actions_wrap)
            btn_del.setToolTip("移除记录")
            btn_del.clicked.connect(lambda: self._on_delete(self.task_id))
            self.actions_lay.addWidget(btn_del)

        if trace:
            btn_trace = ToolButton(FIF.CODE, self.actions_wrap)
            btn_trace.setToolTip("查看四级引擎解析轨迹 (ResolutionTrace)")
            btn_trace.clicked.connect(self._toggle_trace_clicked)
            self.actions_lay.addWidget(btn_trace)

        # Trace body styling
        self.trace_frame.setStyleSheet(
            f"background-color: {pal['bg_trace']}; border: 1px solid {pal['border']}; border-radius: 8px;"
        )
        self.trace_header_lbl.setStyleSheet(
            f"color: {pal['accent']}; font-size: 11px; font-weight: 700; border: none;"
        )
        self.trace_body_lbl.setText(format_trace_dict_as_tree(trace))
        self.trace_body_lbl.setStyleSheet(
            f"color: {pal['text_secondary']}; font-family: {MONO_STACK}; font-size: 12px; border: none;"
        )
        self.trace_frame.setVisible(bool(trace) and self._trace_expanded)
