"""Command Palette Modal (`ui/command_palette.py` — invoked via `Ctrl+K`).

Raycast / VSCode style fuzzy command launcher for power users:
- Instant theme switching (Miku / Sakura / Violet / Amber / Azure)
- Dark / Light mode toggle
- Download Stats modal launcher
- Pause / Resume all downloads
- Open downloads folder
- Export redacted diagnostics ZIP
- Toggle LAN Web Server
- Navigate to Settings
"""

from __future__ import annotations

from typing import Any, Callable

from PyQt6.QtCore import QEvent, QRect, Qt
from PyQt6.QtGui import QColor, QFont, QKeyEvent, QPainter
from PyQt6.QtWidgets import (
    QDialog,
    QFrame,
    QGraphicsDropShadowEffect,
    QHBoxLayout,
    QLabel,
    QLineEdit,
    QListWidget,
    QListWidgetItem,
    QVBoxLayout,
    QWidget,
)

from .tokens import ANIME_THEMES, get_anime_theme, get_palette


class CommandPaletteDialog(QDialog):
    """Frameless 580px wide Command Palette dialog with keyboard-first fuzzy search."""

    def __init__(
        self,
        on_switch_theme: Callable[[str], None] | None = None,
        on_toggle_dark: Callable[[], None] | None = None,
        on_show_stats: Callable[[], None] | None = None,
        on_pause_all: Callable[[], None] | None = None,
        on_resume_all: Callable[[], None] | None = None,
        on_open_downloads: Callable[[], None] | None = None,
        on_export_diagnostics: Callable[[], None] | None = None,
        on_toggle_web: Callable[[], None] | None = None,
        on_open_settings: Callable[[], None] | None = None,
        on_quick_capture: Callable[[], None] | None = None,
        parent: QWidget | None = None,
        **kwargs: Any,
    ) -> None:
        super().__init__(
            parent,
            Qt.WindowType.FramelessWindowHint | Qt.WindowType.WindowStaysOnTopHint,
        )
        self.setAttribute(Qt.WidgetAttribute.WA_TranslucentBackground, True)
        self.setFixedWidth(600)
        self.setFixedHeight(390)

        self._on_switch_theme = on_switch_theme or (lambda _: None)
        self._on_toggle_dark = on_toggle_dark or (lambda: None)
        self._on_show_stats = on_show_stats or kwargs.get("on_open_stats") or (lambda: None)
        self._on_pause_all = on_pause_all or (lambda: None)
        self._on_resume_all = on_resume_all or (lambda: None)
        self._on_open_downloads = on_open_downloads or (lambda: None)
        self._on_export_diagnostics = on_export_diagnostics or (lambda: None)
        self._on_toggle_web = on_toggle_web or (lambda: None)
        self._on_open_settings = on_open_settings or (lambda: None)
        self._on_quick_capture = on_quick_capture or (lambda: None)

        self._is_dark: bool = False
        self._theme_style: str = "miku"

        self._all_commands: list[tuple[str, str, str, Callable[[], None]]] = [
            ("捕获", "唤出快速捕获浮层 (Quick Capture)", "quick capture download pop", self._on_quick_capture),
            ("主题", "切换二次元主题: 初音未来 (极光青 Miku)", "miku teal", lambda: self._on_switch_theme("miku")),
            ("主题", "切换二次元主题: 樱花粉 (少女心 Sakura)", "sakura pink", lambda: self._on_switch_theme("sakura")),
            ("主题", "切换二次元主题: 星空幻境 (霓虹紫 Violet)", "violet neon purple", lambda: self._on_switch_theme("violet")),
            ("主题", "切换二次元主题: 元气暖阳 (暖金 Amber)", "amber gold sunshine", lambda: self._on_switch_theme("amber")),
            ("主题", "切换二次元主题: 琉璃晴空 (冰蓝 Azure)", "azure sky blue", lambda: self._on_switch_theme("azure")),
            ("外观", "切换深色 / 浅色模式", "dark light theme mode", self._on_toggle_dark),
            ("战报", "查看下载成就与数据统计战报 (Stats)", "stats dashboard report", self._on_show_stats),
            ("控制", "暂停全部下载任务", "pause all stop", self._on_pause_all),
            ("控制", "恢复全部下载任务", "resume start continue all", self._on_resume_all),
            ("文件", "打开视频保存目录", "open folder downloads explore", self._on_open_downloads),
            ("维护", "一键导出脱敏系统诊断包 (.zip)", "export diagnostics bug log", self._on_export_diagnostics),
            ("服务", "启动 / 停止局域网手机互联服务", "web server iphone lan pwa", self._on_toggle_web),
            ("页面", "跳转到详细设置页面", "settings preferences config", self._on_open_settings),
        ]

        self._build_ui()
        self.apply_theme(False, "miku")

    def _build_ui(self) -> None:
        root = QVBoxLayout(self)
        root.setContentsMargins(12, 12, 12, 12)

        self.surface = QFrame(self)
        self.surface.setObjectName("cpSurface")

        shadow = QGraphicsDropShadowEffect(self)
        shadow.setBlurRadius(28)
        shadow.setOffset(0, 8)
        shadow.setColor(QColor(0, 0, 0, 85))
        self.surface.setGraphicsEffect(shadow)

        s_lay = QVBoxLayout(self.surface)
        s_lay.setContentsMargins(16, 16, 16, 14)
        s_lay.setSpacing(10)

        # Search Bar
        search_row = QHBoxLayout()
        search_row.setSpacing(8)

        self.input_edit = QLineEdit(self.surface)
        self.input_edit.setObjectName("cpInput")
        self.input_edit.setPlaceholderText("输入命令名称或关键字... (Esc 退出)")
        self.input_edit.textChanged.connect(self._filter_commands)
        self.search_input = self.input_edit
        search_row.addWidget(self.input_edit, 1)
        s_lay.addLayout(search_row)

        # Results List
        self.cmd_list = QListWidget(self.surface)
        self.cmd_list.setObjectName("cpList")
        self.cmd_list.setFocusPolicy(Qt.FocusPolicy.NoFocus)
        self.cmd_list.itemDoubleClicked.connect(self._execute_current)
        s_lay.addWidget(self.cmd_list, 1)

        # Footer Hint
        hint = QLabel("↑ / ↓ 切换选择   ·   Enter 执行   ·   Esc 关闭", self.surface)
        hint.setObjectName("cpHint")
        s_lay.addWidget(hint)

        root.addWidget(self.surface)

    def apply_theme(self, is_dark: bool, theme_style: str = "miku") -> None:
        self._is_dark = is_dark
        self._theme_style = theme_style
        theme = get_anime_theme(theme_style)
        pal = get_palette(is_dark, theme_style)

        bg = "rgba(20, 25, 36, 248)" if is_dark else "rgba(255, 255, 255, 250)"
        border = "rgba(148, 163, 184, 55)" if is_dark else "rgba(203, 213, 225, 220)"
        inp_bg = "rgba(15, 19, 28, 230)" if is_dark else "#F8FAFC"
        txt = pal["text_primary"]
        muted = pal["text_muted"]
        accent = pal["accent"]

        self.surface.setStyleSheet(
            f"""
            QFrame#cpSurface {{
                background-color: {bg};
                border: 1.2px solid {border};
                border-radius: 14px;
            }}
            QLineEdit#cpInput {{
                background-color: {inp_bg};
                color: {txt};
                border: 1.4px solid {accent};
                border-radius: 10px;
                padding: 8px 14px;
                font-size: 14px;
                font-family: "Segoe UI", "Microsoft YaHei UI";
            }}
            QListWidget#cpList {{
                background: transparent;
                border: none;
                outline: none;
            }}
            QListWidget#cpList::item {{
                height: 38px;
                border-radius: 8px;
                padding: 4px 10px;
                color: {txt};
                font-size: 13px;
                font-family: "Segoe UI", "Microsoft YaHei UI";
            }}
            QListWidget#cpList::item:selected {{
                background-color: {theme['accent_bg_dark'] if is_dark else theme['accent_bg_light']};
                color: {accent};
                font-weight: 600;
            }}
            QLabel#cpHint {{
                color: {muted};
                font-size: 11px;
            }}
            """
        )

    def open_palette(self) -> None:
        """Show palette centered relative to parent window."""
        self.input_edit.clear()
        self._filter_commands("")
        if self.parent():
            parent_rect = self.parent().geometry()
            x = parent_rect.x() + (parent_rect.width() - self.width()) // 2
            y = parent_rect.y() + (parent_rect.height() - self.height()) // 2
            self.move(max(20, x), max(20, y))
        self.show()
        self.input_edit.setFocus()

    def _filter_commands(self, text: str) -> None:
        self.cmd_list.clear()
        q = text.lower().strip()
        matched = []
        for cat, title, kw, action in self._all_commands:
            if not q or q in title.lower() or q in kw.lower() or q in cat.lower():
                matched.append((cat, title, action))

        for cat, title, action in matched:
            item = QListWidgetItem(f"[{cat}]  {title}")
            item.setData(Qt.ItemDataRole.UserRole, action)
            self.cmd_list.addItem(item)

        if self.cmd_list.count() > 0:
            self.cmd_list.setCurrentRow(0)

    def _execute_current(self) -> None:
        item = self.cmd_list.currentItem()
        if not item:
            return
        action = item.data(Qt.ItemDataRole.UserRole)
        self.hide()
        if callable(action):
            action()

    def keyPressEvent(self, event: QKeyEvent) -> None:  # noqa: N802
        key = event.key()
        if key == Qt.Key.Key_Escape:
            self.hide()
            event.accept()
            return
        if key in (Qt.Key.Key_Return, Qt.Key.Key_Enter):
            self._execute_current()
            event.accept()
            return
        if key == Qt.Key.Key_Down:
            curr = self.cmd_list.currentRow()
            if curr < self.cmd_list.count() - 1:
                self.cmd_list.setCurrentRow(curr + 1)
            event.accept()
            return
        if key == Qt.Key.Key_Up:
            curr = self.cmd_list.currentRow()
            if curr > 0:
                self.cmd_list.setCurrentRow(curr - 1)
            event.accept()
            return
        super().keyPressEvent(event)
