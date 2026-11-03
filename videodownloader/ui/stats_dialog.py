"""Download Achievements & Statistics Card (`ui/stats_dialog.py`).

Viral Social Sharing Generator:
- Aggregates SQLite task database (total completed videos, total gigabytes, bandwidth saved, platform breakdown).
- Renders an aesthetic anime-styled achievement card with gradients, badge awards, and platform bars.
- 1-click 'Copy Card to Clipboard' (for pasting straight into WeChat/QQ/Twitter/Bilibili) + 'Save Image'.
"""

from __future__ import annotations

from collections import Counter
from datetime import datetime
from pathlib import Path
import time
from typing import Any

from PyQt6.QtCore import QPointF, QRectF, Qt
from PyQt6.QtGui import (
    QColor,
    QFont,
    QGuiApplication,
    QImage,
    QLinearGradient,
    QPainter,
    QPainterPath,
    QPen,
    QPixmap,
)
from PyQt6.QtWidgets import (
    QApplication,
    QDialog,
    QFileDialog,
    QFrame,
    QGraphicsDropShadowEffect,
    QHBoxLayout,
    QLabel,
    QPushButton,
    QVBoxLayout,
    QWidget,
)

from ..core.db import Database
from .desktop_theme import format_size
from .tokens import get_anime_theme, get_palette


def compute_download_stats(db: Database) -> dict[str, Any]:
    """Aggregate statistics from SQLite database for social viral achievement cards."""
    tasks = db.list_tasks(limit=500)
    completed = [t for t in tasks if t.get("status") == "completed"]
    total_count = len(completed)
    total_bytes = sum(int(t.get("file_size") or 0) for t in completed)
    total_duration = sum(float(t.get("duration") or 0.0) for t in completed)

    platforms: list[str] = []
    for t in completed:
        pname = str(t.get("platform_name") or "")
        if not pname or pname == "通用视频":
            url = str(t.get("url") or "")
            if "bilibili.com" in url or "b23.tv" in url:
                pname = "bilibili"
            elif "youtube.com" in url or "youtu.be" in url:
                pname = "youtube"
            elif "douyin.com" in url:
                pname = "douyin"
            else:
                pname = "通用视频"
        platforms.append(pname.split("(")[0].strip().lower())

    counter = Counter(platforms)
    top_platform = counter.most_common(1)[0][0] if counter else "无"
    saved_bytes = int(total_bytes * 1.35)

    if total_count >= 50 or total_bytes >= 20 * 1024 * 1024 * 1024:
        honor_title = "🏆 4K 终极多媒体藏镜人"
    elif total_count >= 15:
        honor_title = "⭐ 高清番剧追光学者"
    elif total_count >= 1:
        honor_title = "✨ 极光漫游探索家"
    else:
        honor_title = "🌱 初入漫境新学员"

    return {
        "total_tasks": len(tasks),
        "total_completed": total_count,
        "total_bytes": total_bytes,
        "total_duration": total_duration,
        "saved_bytes": saved_bytes,
        "honor_title": honor_title,
        "top_platform": top_platform,
        "platform_counts": dict(counter),
        "top_platforms": counter.most_common(4),
    }


class StatsDialog(QDialog):
    """Viral Achievement & Download Statistics Modal."""

    def __init__(self, db: Database, is_dark: bool = False, theme_style: str = "miku", parent: QWidget | None = None) -> None:
        super().__init__(parent, Qt.WindowType.Window | Qt.WindowType.WindowCloseButtonHint)
        self.setWindowTitle("下载成就与数据战报")
        self.setFixedSize(540, 680)
        self.db = db
        self._is_dark = is_dark
        self._theme_style = theme_style
        self._rendered_pixmap: QPixmap | None = None
        self._cached_card_pixmap: QPixmap | None = None

        self._build_ui()
        self._compute_and_render()

    def _compute_and_render(self) -> None:
        self._render_report_card()
        self._cached_card_pixmap = self._rendered_pixmap

    def _build_ui(self) -> None:
        root = QVBoxLayout(self)
        root.setContentsMargins(18, 18, 18, 18)
        root.setSpacing(14)

        # Preview Container
        self.card_lbl = QLabel(self)
        self.card_lbl.setAlignment(Qt.AlignmentFlag.AlignCenter)
        root.addWidget(self.card_lbl, 1)

        # Action Buttons
        btn_row = QHBoxLayout()
        btn_row.setSpacing(12)

        self.btn_copy = QPushButton("📋 复制战报图片到剪贴板", self)
        self.btn_copy.setFixedHeight(38)
        self.btn_copy.clicked.connect(self._copy_image_to_clipboard)
        btn_row.addWidget(self.btn_copy, 1)

        self.btn_save = QPushButton("💾 保存高清图片", self)
        self.btn_save.setFixedHeight(38)
        self.btn_save.clicked.connect(self._save_image_file)
        btn_row.addWidget(self.btn_save, 1)

        root.addLayout(btn_row)

    def _render_report_card(self) -> None:
        stats = compute_download_stats(self.db)
        total_count = stats["total_completed"]
        total_bytes = stats["total_bytes"]
        total_duration = stats["total_duration"]
        top_platforms = stats["top_platforms"]
        saved_bytes = stats["saved_bytes"]
        honor_title = stats["honor_title"]

        w, h = 504, 600
        pix = QPixmap(w, h)
        pix.fill(Qt.GlobalColor.transparent)

        p = QPainter(pix)
        p.setRenderHint(QPainter.RenderHint.Antialiasing, True)
        p.setRenderHint(QPainter.RenderHint.TextAntialiasing, True)

        theme = get_anime_theme(self._theme_style)
        c_pri, c_sec = theme["gradient"]

        # Outer rounded squircle background
        path = QPainterPath()
        path.addRoundedRect(0.0, 0.0, float(w), float(h), 18.0, 18.0)

        bg_col = QColor("#131722" if self._is_dark else "#FFFFFF")
        p.fillPath(path, bg_col)

        # Border
        p.setPen(QPen(QColor(theme["accent_light"] if self._is_dark else theme["accent"]), 1.5))
        p.drawPath(path)

        # Header Ribbon
        header_grad = QLinearGradient(0, 0, w, 110)
        header_grad.setColorAt(0.0, QColor(c_pri))
        header_grad.setColorAt(1.0, QColor(c_sec))

        h_path = QPainterPath()
        h_path.addRoundedRect(0.0, 0.0, float(w), 110.0, 18.0, 18.0)
        # Flatten bottom corners of header
        flat_rect = QRectF(0.0, 30.0, float(w), 80.0)
        h_path.addRect(flat_rect)
        p.fillPath(h_path, header_grad)

        # Header Titles
        p.setPen(QColor("#FFFFFF"))
        font_h = QFont("Microsoft YaHei UI", 16, QFont.Weight.Bold)
        p.setFont(font_h)
        p.drawText(QRectF(20, 20, w - 40, 32), Qt.AlignmentFlag.AlignLeft, "VideoDownloader · 下载成就战报")

        font_sub = QFont("Microsoft YaHei UI", 10, QFont.Weight.Normal)
        p.setFont(font_sub)
        p.drawText(
            QRectF(20, 56, w - 40, 24),
            Qt.AlignmentFlag.AlignLeft,
            f"生成时间: {datetime.now().strftime('%Y-%m-%d %H:%M')}  ·  {theme['name']}",
        )

        p.drawText(QRectF(20, 80, w - 40, 24), Qt.AlignmentFlag.AlignLeft, honor_title)

        # Stat Big Numbers
        txt_main = QColor("#F1F5F9" if self._is_dark else "#0F172A")
        txt_sub = QColor("#94A3B8" if self._is_dark else "#64748B")

        stats_box_y = 126
        # 4 Stat Pillars
        pillars = [
            (f"{total_count}", "累计下载视频", 20, stats_box_y),
            (format_size(total_bytes), "总下载数据量", w // 2 + 10, stats_box_y),
            (format_size(saved_bytes), "预估节省流量", 20, stats_box_y + 80),
            (f"{total_duration / 60:.0f} 分钟" if total_duration > 0 else "0", "已收录视频总时长", w // 2 + 10, stats_box_y + 80),
        ]

        for val, lbl, px, py in pillars:
            # Subtle card backdrop
            b_path = QPainterPath()
            b_path.addRoundedRect(QRectF(px, py, w // 2 - 30, 68), 10.0, 10.0)
            box_bg = QColor(theme["accent_bg_dark"] if self._is_dark else theme["accent_bg_light"])
            p.fillPath(b_path, box_bg)

            p.setPen(QColor(theme["accent_light"] if self._is_dark else theme["accent"]))
            p.setFont(QFont("Segoe UI", 17, QFont.Weight.Bold))
            p.drawText(QRectF(px + 12, py + 10, w // 2 - 54, 26), Qt.AlignmentFlag.AlignLeft, val)

            p.setPen(txt_sub)
            p.setFont(QFont("Microsoft YaHei UI", 9))
            p.drawText(QRectF(px + 12, py + 38, w // 2 - 54, 20), Qt.AlignmentFlag.AlignLeft, lbl)

        # Platform Distribution Bars
        chart_y = 295
        p.setPen(txt_main)
        p.setFont(QFont("Microsoft YaHei UI", 11, QFont.Weight.Bold))
        p.drawText(QRectF(20, chart_y, w - 40, 24), Qt.AlignmentFlag.AlignLeft, "最常访问的媒体站点分布")

        bar_y = chart_y + 32
        if top_platforms and total_count > 0:
            for pname, pcount in top_platforms:
                ratio = pcount / float(total_count)
                p.setPen(txt_sub)
                p.setFont(QFont("Microsoft YaHei UI", 9))
                p.drawText(QRectF(20, bar_y, 110, 20), Qt.AlignmentFlag.AlignLeft, pname[:8])

                # Bar background
                bar_w_max = w - 210
                p.setPen(Qt.PenStyle.NoPen)
                p.setBrush(QColor(148, 163, 184, 40))
                p.drawRoundedRect(QRectF(135, bar_y + 4, bar_w_max, 10), 5.0, 5.0)

                # Active Bar
                active_w = max(10, int(bar_w_max * ratio))
                p.setBrush(QColor(theme["accent"]))
                p.drawRoundedRect(QRectF(135, bar_y + 4, active_w, 10), 5.0, 5.0)

                # Count text
                p.setPen(txt_sub)
                p.drawText(QRectF(w - 65, bar_y, 45, 20), Qt.AlignmentFlag.AlignRight, f"{pcount}部")
                bar_y += 28
        else:
            p.setPen(txt_sub)
            p.setFont(QFont("Microsoft YaHei UI", 10))
            p.drawText(QRectF(20, bar_y + 20, w - 40, 30), Qt.AlignmentFlag.AlignCenter, "暂无已完成的下载任务数据")

        # Footer Slogan
        p.setPen(QColor(148, 163, 184, 180))
        p.setFont(QFont("Segoe UI", 9))
        p.drawText(
            QRectF(20, h - 35, w - 40, 20),
            Qt.AlignmentFlag.AlignCenter,
            "Powered by VideoDownloader · 四级多引擎媒体解析框架",
        )

        p.end()
        self._rendered_pixmap = pix
        self.card_lbl.setPixmap(pix)

    def _copy_image_to_clipboard(self) -> None:
        if hasattr(self, "_rendered_pixmap"):
            QApplication.clipboard().setPixmap(self._rendered_pixmap)
            self.btn_copy.setText("✅ 已复制到剪贴板！可以直接粘贴")

    def _save_image_file(self) -> None:
        if hasattr(self, "_rendered_pixmap"):
            default_name = f"VideoDownloader_战报_{int(time.time())}.png"
            path, _ = QFileDialog.getSaveFileName(self, "保存战报图片", default_name, "PNG Image (*.png)")
            if path:
                self._rendered_pixmap.save(path, "PNG")
                self.btn_save.setText("✅ 已保存到本地")
