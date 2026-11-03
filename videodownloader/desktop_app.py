"""Native Fluent Desktop Application (`videodownloader/desktop_app.py` — v3.0):
- Pure brand naming (`VideoDownloader` window title & header; subtle dynamic `v{__version__}` badge in bottom-right corner)
- Win11 Fluent left navigation (`MSFluentWindow` + `FluentIcon` pure vector icons, zero emoji icons)
- Press `Enter` in URL input to download immediately, or press `Ctrl+Shift+D` anywhere for 0ms Quick Capture
- Manna-style rounded `MediaTaskCard` list with expandable 4-Stage `ResolutionTrace` tree
- System Tray dynamic progress ring, Single-Instance wakeup, and Non-Intrusive Clipboard Sentinel.
"""

from __future__ import annotations

import contextlib
import io
import os
from pathlib import Path
import sys
from typing import Any

with contextlib.redirect_stdout(io.StringIO()):
    from qfluentwidgets import (
        CardWidget,
        ComboBox,
        FluentIcon as FIF,
        InfoBar,
        InfoBarPosition,
        LineEdit,
        MSFluentWindow,
        NavigationItemPosition,
        PillPushButton,
        PrimaryPushButton,
        PushButton,
        Theme,
        ToolButton,
        setTheme,
        setThemeColor,
    )

from PyQt6.QtCore import Qt, QTimer, pyqtSignal
from PyQt6.QtGui import QColor, QGuiApplication, QIcon, QKeySequence, QShortcut
from PyQt6.QtWidgets import (
    QApplication,
    QFileDialog,
    QFrame,
    QHBoxLayout,
    QLabel,
    QMessageBox,
    QScrollArea,
    QVBoxLayout,
    QWidget,
)

from . import APP_NAME, __version__
from .core.app_updater import UpdateCheckResult, check_github_release
from .core.clipboard_sentinel import ClipboardSentinel
from .core.db import DEFAULT_HOTKEYS, Database
from .core.hotkey import GlobalHotkeyManager
from .core.logging_setup import get_logger, setup_logging
from .core.os_utils import reveal_in_file_manager
from .core.paths import get_assets_dir
from .core.prefetch import PrefetchEntry, SpeculativePrefetcher
from .core.single_instance import SingleInstanceGuard
from .core.sound_manager import SoundEffectManager
from .core.task_queue import TaskQueueManager
from .core.web_server_manager import EmbeddedWebServerManager
from .ui.ai_tab import AICopilotTab
from .ui.backdrop import ThemeEmptyStateWidget
from .ui.command_palette import CommandPaletteDialog
from .ui.desktop_theme import QUALITY_ITEMS, format_mmss, run_in_thread
from .ui.library_player_tab import LibraryPlayerTab
from .ui.quick_capture import QuickCaptureDialog
from .ui.settings_page import SettingsAndDiagnosticsPage
from .ui.stats_dialog import StatsDialog
from .ui.task_card import MediaTaskCard, format_trace_dict_as_tree
from .ui.tokens import BRAND_ACCENT, get_anime_theme, get_palette
from .ui.tray import SystemTrayController
from .ui.web_tools_tab import WebAndToolsTab

logger = get_logger("ui")


class MainWindow(MSFluentWindow):
    """Primary Fluent Desktop Window for `VideoDownloader`."""

    queue_event_signal = pyqtSignal(str, object)

    def __init__(self, db: Database | None = None, start_minimized: bool = False) -> None:
        super().__init__()
        self.db = db or Database()
        cfg = self.db.get_settings()
        self._is_dark: bool = cfg.get("theme_mode") == "dark"
        self._theme_style: str = str(cfg.get("theme_style") or "miku")

        init_pal = get_palette(self._is_dark, self._theme_style)
        setThemeColor(QColor(init_pal["accent"]))
        setTheme(Theme.DARK if self._is_dark else Theme.LIGHT)

        # Pure brand title (NO version number or suffix in window title!)
        self.setWindowTitle(APP_NAME)
        self.resize(1160, 780)
        self.setMinimumSize(940, 640)

        icon_ico = get_assets_dir() / "icon.ico"
        icon_png = get_assets_dir() / "icon.png"
        if icon_ico.exists():
            self.setWindowIcon(QIcon(str(icon_ico)))
        elif icon_png.exists():
            self.setWindowIcon(QIcon(str(icon_png)))

        self.queue_mgr = TaskQueueManager(self.db)
        self.queue_mgr.start()
        self.web_mgr = EmbeddedWebServerManager(self.db, self.queue_mgr)

        self.sound_mgr = SoundEffectManager(self)
        self.sound_mgr.configure(
            enabled=bool(cfg.get("sound_enabled", True)),
            volume=int(cfg.get("sound_volume", 70)),
            theme_style=self._theme_style,
        )

        self.prefetcher = SpeculativePrefetcher(self.db, self.queue_mgr, self)
        self.sentinel = ClipboardSentinel(
            db=self.db,
            prefetcher=self.prefetcher,
            is_app_foreground=self._is_any_app_window_active,
            parent=self,
        )
        self.quick_capture = QuickCaptureDialog(
            db=self.db,
            queue_mgr=self.queue_mgr,
            prefetcher=self.prefetcher,
            sentinel=self.sentinel,
            on_open_cookie_settings=self.jump_to_cookie_settings,
            on_notify=self._notify_user,
            parent=self,
        )
        self.sentinel.open_capture_requested.connect(self.open_quick_capture)

        self.command_palette = CommandPaletteDialog(
            parent=self,
            on_switch_theme=lambda style: self._switch_theme_from_palette(style),
            on_toggle_dark=lambda dark: self._toggle_dark_from_palette(dark),
            on_pause_all=self.queue_mgr.pause_all,
            on_resume_all=self.queue_mgr.resume_all,
            on_open_downloads=self._open_save_dir,
            on_open_stats=self._show_stats_dialog,
            on_export_diagnostics=lambda: self.settings_page.export_sanitized_diagnostics(),
            on_quick_capture=lambda: self.open_quick_capture(""),
            on_check_updates=lambda: self.settings_page.check_updates_manually(),
            on_clear_completed=self._clear_failed_canceled,
        )
        self._shortcut_cmd_palette = QShortcut(QKeySequence("Ctrl+K"), self)
        self._shortcut_cmd_palette.activated.connect(self.command_palette.open_palette)

        self._force_quit: bool = False
        self._start_minimized: bool = start_minimized
        self._prefetched_media: dict[str, Any] | None = None
        self._bridges: list[Any] = []
        self._active_filter: str = "all"  # all | active | completed | failed
        self._expanded_traces: set[str] = set()
        self._card_widgets: dict[str, MediaTaskCard] = {}
        self._rendered_order: list[str] = []

        self._build_pages()

        # System Tray & Global Hotkeys
        self.tray_ctrl = SystemTrayController(
            parent_window=self,
            queue_mgr=self.queue_mgr,
            on_show_main=self.bring_to_front,
            on_quick_capture=lambda: self.open_quick_capture(""),
            on_paste_download=self._hotkey_paste_and_download,
            on_toggle_pause_all=self._hotkey_toggle_pause_all,
            on_open_downloads=self._open_save_dir,
            on_quit_app=self.quit_application,
        )
        self.hotkey_mgr = GlobalHotkeyManager(self, self)
        self.hotkey_mgr.activated.connect(self._on_global_hotkey)
        self.reload_hotkeys(show_conflict_toast=not start_minimized)

        # Subscribe to task queue updates
        self.queue_event_signal.connect(self._on_queue_event)
        self.queue_mgr.subscribe_sync(lambda ev, data: self.queue_event_signal.emit(ev, data))
        self.prefetcher.probe_finished.connect(self._on_prefetch_ready_for_cards)

        # Sync existing media folder into persistent library
        self.queue_mgr.scan_local_folder()
        self.apply_theme(self._is_dark)
        self.refresh_all_task_views()

        # Optional background LAN server (never auto-pops browser!)
        if cfg.get("web_server_auto_start", False):
            self.web_mgr.start()
            self.web_tab.refresh_web_state()

        if not start_minimized:
            QTimer.singleShot(250, self._show_startup_banners)

        if cfg.get("auto_check_update", True) and cfg.get("github_repo"):
            QTimer.singleShot(30_000, self._silent_check_update)

    # -------------------- Navigation & Pages Construction --------------------

    def _build_pages(self) -> None:
        self.download_page = self._build_download_page()
        self.library_tab = LibraryPlayerTab(
            db=self.db,
            queue_mgr=self.queue_mgr,
            on_open_ai=self._jump_to_ai_for_task,
            on_library_changed=self.refresh_all_task_views,
            parent=self,
        )
        self.ai_tab = AICopilotTab(
            db=self.db,
            on_seek_video=self._seek_and_play_in_library,
            parent=self,
        )
        self.web_tab = WebAndToolsTab(
            db=self.db,
            web_mgr=self.web_mgr,
            on_status_changed=lambda: None,
            on_internal_copy=self.sentinel.mark_internal_copy,
            parent=self,
        )
        self.settings_page = SettingsAndDiagnosticsPage(
            db=self.db,
            queue_mgr=self.queue_mgr,
            on_theme_changed=self.apply_theme,
            on_hotkeys_changed=lambda: self.reload_hotkeys(show_conflict_toast=True),
            on_settings_saved=self._on_settings_updated,
            on_history_cleared=self.refresh_all_task_views,
            sound_mgr=self.sound_mgr,
            on_open_stats=self._show_stats_dialog,
            parent=self,
        )

        self.addSubInterface(self.download_page, FIF.DOWNLOAD, "下载")
        self.library_tab_sub = self.addSubInterface(self.library_tab, FIF.VIDEO, "媒体库")
        self.ai_tab_sub = self.addSubInterface(self.ai_tab, FIF.CHAT, "AI 分镜精读")
        self.web_tab_sub = self.addSubInterface(self.web_tab, FIF.WIFI, "多端互联")

        # Bottom navigation items: Quick Theme Switch + Settings
        self.navigationInterface.addItem(
            routeKey="themeSwitch",
            icon=FIF.CONSTRACT,
            text="主题",
            onClick=self._toggle_theme_quick,
            selectable=False,
            position=NavigationItemPosition.BOTTOM,
        )
        self.addSubInterface(
            self.settings_page,
            FIF.SETTING,
            "设置",
            position=NavigationItemPosition.BOTTOM,
        )

    def _build_download_page(self) -> QWidget:
        page = QWidget(self)
        page.setObjectName("downloadPage")
        lay = QVBoxLayout(page)
        lay.setContentsMargins(24, 18, 24, 8)
        lay.setSpacing(14)

        # 1. Airy Brand Header (Pure `VideoDownloader`, zero version numbers!)
        hdr_row = QHBoxLayout()
        hdr_col = QVBoxLayout()
        hdr_col.setSpacing(2)

        self.brand_title_lbl = QLabel(APP_NAME)
        hdr_col.addWidget(self.brand_title_lbl)

        self.brand_sub_lbl = QLabel("粘贴视频链接按 Enter 立即下载  ·  复制链接后台自动预热，随时按 Ctrl+Shift+D 全局秒捕")
        hdr_col.addWidget(self.brand_sub_lbl)
        hdr_row.addLayout(hdr_col, 1)

        self.btn_quick_cap_top = PushButton(FIF.SPEED_HIGH, "快速捕获 (Ctrl+Shift+D)", page)
        self.btn_quick_cap_top.clicked.connect(lambda: self.open_quick_capture(""))
        hdr_row.addWidget(self.btn_quick_cap_top, alignment=Qt.AlignmentFlag.AlignVCenter)
        lay.addLayout(hdr_row)

        # 2. Hero URL Input Card (Pressing Enter downloads immediately!)
        self.hero_card = CardWidget(page)
        hero_lay = QVBoxLayout(self.hero_card)
        hero_lay.setContentsMargins(18, 16, 18, 16)
        hero_lay.setSpacing(10)

        input_row = QHBoxLayout()
        input_row.setSpacing(10)

        self.url_input = LineEdit(self.hero_card)
        self.url_input.setPlaceholderText(
            "粘贴 YouTube / B站 / 抖音 / 快手 / 小红书 / TikTok / X 等任意链接或分享口令，直接按 Enter 开始下载..."
        )
        self.url_input.setMinimumHeight(40)
        self.url_input.setClearButtonEnabled(True)
        self.url_input.returnPressed.connect(self._submit_download)
        input_row.addWidget(self.url_input, 1)

        self.quick_quality_combo = ComboBox(self.hero_card)
        self.quick_quality_combo.setMinimumHeight(40)
        for label, _qk, _oa, _af in QUALITY_ITEMS:
            self.quick_quality_combo.addItem(label.split("(")[0].strip())
        self.quick_quality_combo.currentIndexChanged.connect(self._on_quick_quality_changed)
        input_row.addWidget(self.quick_quality_combo)

        self.btn_start_dl = PrimaryPushButton(FIF.DOWNLOAD, "立即下载", self.hero_card)
        self.btn_start_dl.setMinimumHeight(40)
        self.btn_start_dl.clicked.connect(self._submit_download)
        input_row.addWidget(self.btn_start_dl)

        btn_paste_go = PushButton(FIF.PASTE, "粘贴秒下", self.hero_card)
        btn_paste_go.setMinimumHeight(40)
        btn_paste_go.setToolTip("读取系统剪贴板并立即开始下载")
        btn_paste_go.clicked.connect(self._paste_and_download)
        input_row.addWidget(btn_paste_go)

        self.btn_parse = ToolButton(FIF.SEARCH, self.hero_card)
        self.btn_parse.setFixedSize(40, 40)
        self.btn_parse.setToolTip("解析预览画质与四级引擎轨迹 (不立即下载)")
        self.btn_parse.clicked.connect(self._probe_url)
        input_row.addWidget(self.btn_parse)

        btn_batch_txt = ToolButton(FIF.FOLDER_ADD, self.hero_card)
        btn_batch_txt.setFixedSize(40, 40)
        btn_batch_txt.setToolTip("从 TXT 文件批量导入链接")
        btn_batch_txt.clicked.connect(self._import_batch_txt)
        input_row.addWidget(btn_batch_txt)

        hero_lay.addLayout(input_row)

        # Collapsible inline parse preview card (with 4-stage trace!)
        self.preview_frame = QFrame(self.hero_card)
        self.preview_frame.setVisible(False)
        pv_lay = QVBoxLayout(self.preview_frame)
        pv_lay.setContentsMargins(12, 10, 12, 10)
        pv_lay.setSpacing(4)
        self.preview_title_lbl = QLabel()
        self.preview_title_lbl.setWordWrap(True)
        pv_lay.addWidget(self.preview_title_lbl)
        self.preview_trace_lbl = QLabel()
        self.preview_trace_lbl.setTextInteractionFlags(Qt.TextInteractionFlag.TextSelectableByMouse)
        pv_lay.addWidget(self.preview_trace_lbl)
        hero_lay.addWidget(self.preview_frame)

        lay.addWidget(self.hero_card)

        # 3. Filter Pills & Toolbar Row
        filter_row = QHBoxLayout()
        filter_row.setSpacing(8)
        self._filter_pills: dict[str, PillPushButton] = {}
        for f_key, f_label in (
            ("all", "全部任务"),
            ("active", "进行中"),
            ("completed", "已完成"),
            ("failed", "异常 / 未完成"),
        ):
            pill = PillPushButton(f_label, page)
            pill.setChecked(f_key == "all")
            pill.clicked.connect(lambda _, k=f_key: self._set_filter(k))
            filter_row.addWidget(pill)
            self._filter_pills[f_key] = pill

        filter_row.addStretch(1)

        btn_open_dir = PushButton(FIF.FOLDER, "下载目录", page)
        btn_open_dir.clicked.connect(self._open_save_dir)
        filter_row.addWidget(btn_open_dir)

        btn_stats = PushButton(FIF.PIE_SINGLE, "战报", page)
        btn_stats.setToolTip("查看我的下载战报与成就海报 (Ctrl+K)")
        btn_stats.clicked.connect(self._show_stats_dialog)
        filter_row.addWidget(btn_stats)

        btn_clear_err = PushButton(FIF.BROOM, "清理异常项", page)
        btn_clear_err.clicked.connect(self._clear_failed_canceled)
        filter_row.addWidget(btn_clear_err)
        lay.addLayout(filter_row)

        # 4. Scrollable Manna-Style Task Cards Area
        self.cards_scroll = QScrollArea(page)
        self.cards_scroll.setWidgetResizable(True)
        self.cards_scroll.setFrameShape(QScrollArea.Shape.NoFrame)

        self.cards_host = QWidget()
        self.cards_lay = QVBoxLayout(self.cards_host)
        self.cards_lay.setContentsMargins(0, 2, 4, 8)
        self.cards_lay.setSpacing(10)

        cfg = self.db.get_settings()
        self.empty_card = ThemeEmptyStateWidget(
            is_dark=self._is_dark,
            theme_style=self._theme_style,
            custom_wallpaper_path=str(cfg.get("custom_wallpaper_path") or ""),
            on_quick_capture=lambda: self.open_quick_capture(""),
            on_open_palette=self.command_palette.open_palette,
            parent=self.cards_host,
        )
        self.cards_lay.addWidget(self.empty_card)
        self.cards_lay.addStretch(1)

        self.cards_scroll.setWidget(self.cards_host)
        lay.addWidget(self.cards_scroll, 1)

        # 5. Subtle Bottom-Right Footer Micro-Badge (ONLY place showing `v{__version__}`!)
        footer_row = QHBoxLayout()
        footer_row.setContentsMargins(2, 0, 2, 2)
        self.footer_hint_lbl = QLabel("")
        footer_row.addWidget(self.footer_hint_lbl)
        footer_row.addStretch(1)

        self.version_badge_lbl = QLabel(f"v{__version__}")
        self.version_badge_lbl.setObjectName("subtleVersionBadge")
        footer_row.addWidget(self.version_badge_lbl)
        lay.addLayout(footer_row)

        self._sync_quick_quality_from_db()
        return page

    # -------------------- Theme & Styling --------------------

    def _toggle_theme_quick(self) -> None:
        new_dark = not self._is_dark
        self.db.update_settings({"theme_mode": "dark" if new_dark else "light"})
        self.settings_page.load_from_db()
        self.apply_theme(new_dark)

    def apply_theme(self, is_dark: bool, theme_style: str | None = None) -> None:
        self._is_dark = is_dark
        if theme_style:
            self._theme_style = theme_style
        else:
            self._theme_style = str(self.db.get_settings().get("theme_style") or "miku")

        pal = get_palette(is_dark, self._theme_style)
        setThemeColor(QColor(pal["accent"]))
        setTheme(Theme.DARK if is_dark else Theme.LIGHT)

        self.download_page.setStyleSheet(f"QWidget#downloadPage {{ background-color: {pal['bg_window']}; }}")
        self.cards_scroll.setStyleSheet("QScrollArea { background: transparent; border: none; }")
        self.cards_host.setStyleSheet("background: transparent;")

        self.brand_title_lbl.setStyleSheet(
            f"font-size: 22px; font-weight: 700; color: {pal['text_primary']}; letter-spacing: 0.2px;"
        )
        self.brand_sub_lbl.setStyleSheet(f"font-size: 12px; color: {pal['text_secondary']};")

        self.preview_frame.setStyleSheet(
            f"background-color: {pal['bg_trace']}; border: 1px solid {pal['border']}; border-radius: 8px;"
        )
        self.preview_title_lbl.setStyleSheet(
            f"color: {pal['text_primary']}; font-size: 13px; font-weight: 600; border: none;"
        )
        self.preview_trace_lbl.setStyleSheet(
            f"color: {pal['text_secondary']}; font-family: Consolas; font-size: 12px; border: none;"
        )

        cfg = self.db.get_settings()
        custom_wall = str(cfg.get("custom_wallpaper_path") or "")

        if hasattr(self, "sound_mgr"):
            self.sound_mgr.configure(
                enabled=bool(cfg.get("sound_enabled", True)),
                volume=int(cfg.get("sound_volume", 70)),
                theme_style=self._theme_style,
            )

        if hasattr(self, "empty_card"):
            self.empty_card.apply_theme(is_dark, self._theme_style, custom_wallpaper_path=custom_wall)

        self.footer_hint_lbl.setStyleSheet(f"font-size: 11px; color: {pal['text_muted']};")
        self.version_badge_lbl.setStyleSheet(
            f"font-size: 11px; color: {pal['text_muted']}; padding: 1px 4px;"
        )

        self.library_tab.apply_theme(is_dark)
        self.ai_tab.apply_theme(is_dark)
        self.web_tab.apply_theme(is_dark)
        self.settings_page.apply_theme(is_dark, self._theme_style)
        self.quick_capture.apply_theme(is_dark, self._theme_style)
        self._rebuild_cards_list(force_recreate=True)

    def _switch_theme_from_palette(self, theme_style: str) -> None:
        self.db.update_settings({"theme_style": theme_style})
        self.settings_page.load_from_db()
        self.apply_theme(self._is_dark, theme_style)

    def _toggle_dark_from_palette(self, is_dark: bool) -> None:
        self.db.update_settings({"theme_mode": "dark" if is_dark else "light"})
        self.settings_page.load_from_db()
        self.apply_theme(is_dark, self._theme_style)

    def _show_stats_dialog(self) -> None:
        dlg = StatsDialog(
            db=self.db,
            is_dark=self._is_dark,
            theme_style=self._theme_style,
            parent=self,
        )
        dlg.exec()

    # -------------------- Hotkeys, Quick Capture & Startup Banners --------------------

    def _is_any_app_window_active(self) -> bool:
        active = QApplication.activeWindow()
        return active is not None and active in (self, self.quick_capture)

    def reload_hotkeys(self, show_conflict_toast: bool = False) -> None:
        cfg = self.db.get_settings()
        enabled = bool(cfg.get("hotkey_enabled", True))
        bindings = cfg.get("hotkeys") or DEFAULT_HOTKEYS
        conflicts = self.hotkey_mgr.register_all(bindings, enabled=enabled)
        cap_hk = bindings.get("capture") or "Ctrl+Shift+D"
        self.btn_quick_cap_top.setText(f"快速捕获 ({cap_hk})" if enabled and cap_hk else "快速捕获")

        if conflicts and show_conflict_toast:
            msg = "；".join(conflicts.values())
            InfoBar.warning(
                title="部分全局快捷键被其他软件占用",
                content=f"{msg}。可前往「设置 -> 全局快捷键」录制新组合键。",
                position=InfoBarPosition.TOP,
                duration=5500,
                parent=self,
            )

    def _on_global_hotkey(self, action: str) -> None:
        if action == "capture":
            if self.quick_capture.isVisible():
                self.quick_capture.hide()
            else:
                self.open_quick_capture("")
        elif action == "paste_capture":
            self._hotkey_paste_and_download()
        elif action == "show":
            if self.isVisible() and not self.isMinimized() and self.isActiveWindow():
                self.hide()
            else:
                self.bring_to_front()
        elif action == "pause":
            self._hotkey_toggle_pause_all()

    def open_quick_capture(self, url: str = "") -> None:
        self.sentinel.toast.hide()
        self.quick_capture.open_panel(initial_url=url)

    def _hotkey_paste_and_download(self) -> None:
        clip = (QApplication.clipboard().text() or "").strip()
        if not clip:
            self._notify_user("剪贴板为空", "未检测到可下载的视频链接")
            return
        cached = self.prefetcher.get_cached(clip)
        media = (cached.media or {}) if (cached and cached.ok) else {}
        try:
            tasks = self.queue_mgr.submit_tasks(
                raw_text=clip,
                prefetched_title=str(media.get("title") or ""),
                prefetched_thumbnail=str(media.get("thumbnail") or ""),
                prefetched_duration=float(media.get("duration") or 0.0),
                prefetched_uploader=str(media.get("uploader") or ""),
                prefetched_trace=cached.trace if cached else None,
            )
            if tasks:
                self.sentinel.suppress_url_24h(tasks[0]["url"])
                self._notify_user("已开始后台下载", tasks[0].get("title") or tasks[0]["url"][:48])
        except Exception as exc:
            self.open_quick_capture(clip)
            logger.debug("Paste-download opened Quick Capture due to input error: %s", exc)

    def _hotkey_toggle_pause_all(self) -> None:
        summary = self.queue_mgr.toggle_pause_resume_all()
        self._notify_user(APP_NAME, summary)
        self.refresh_all_task_views()

    def _notify_user(self, title: str, message: str) -> None:
        if self.isVisible() and not self.isMinimized():
            InfoBar.success(
                title=title,
                content=message,
                position=InfoBarPosition.TOP_RIGHT,
                duration=2800,
                parent=self,
            )
        else:
            self.tray_ctrl.notify(title, message, ms=2800)

    def handle_single_instance_message(self, cmd: str) -> None:
        upper = (cmd or "SHOW").upper()
        if upper == "CAPTURE":
            self.open_quick_capture("")
        else:
            self.bring_to_front()

    def bring_to_front(self) -> None:
        if self.isMinimized():
            self.showNormal()
        else:
            self.show()
        self.raise_()
        self.activateWindow()

    def jump_to_cookie_settings(self) -> None:
        self.bring_to_front()
        self.switchTo(self.settings_page)
        self.settings_page.focus_cookie_section()

    def _show_startup_banners(self) -> None:
        if (QGuiApplication.platformName() or "").lower() == "offscreen":
            return
        cfg = self.db.get_settings()

        # 1. Interrupted tasks recovery prompt
        interrupted = self.db.list_interrupted_tasks()
        if interrupted:
            bar = InfoBar.warning(
                title=f"检测到上次退出时有 {len(interrupted)} 个下载任务未完成",
                content="点击右侧按钮即可从断点处一键恢复全部任务。",
                position=InfoBarPosition.TOP,
                duration=12000,
                parent=self.download_page,
            )
            btn_recover = PushButton(FIF.PLAY, "一键恢复", bar)
            btn_recover.clicked.connect(lambda: (self.queue_mgr.resume_interrupted_tasks(), bar.close()))
            bar.addWidget(btn_recover)

        # 2. First-run lightweight welcome InfoBar (non-blocking!)
        if not cfg.get("onboarding_done", False):
            self.db.update_settings({"onboarding_done": True})
            hk = (cfg.get("hotkeys") or {}).get("capture", "Ctrl+Shift+D")
            save_dir = cfg.get("save_path", "")
            InfoBar.info(
                title=f"欢迎使用 {APP_NAME}",
                content=f"默认保存至 {save_dir} · 随时按 {hk} 呼出全局快速捕获浮层。",
                position=InfoBarPosition.TOP,
                duration=7500,
                parent=self.download_page,
            )

    def _silent_check_update(self) -> None:
        repo = str(self.db.get_settings().get("github_repo") or "").strip()
        if not repo:
            return

        def _done(res: UpdateCheckResult) -> None:
            if res.has_update:
                InfoBar.info(
                    title=f"发现新版本 v{res.latest_version}",
                    content="可前往「设置 -> 检查软件更新」查看更新日志并升级。",
                    position=InfoBarPosition.TOP_RIGHT,
                    duration=8000,
                    parent=self,
                )

        self._bridges.append(
            run_in_thread(lambda: check_github_release(repo, __version__), _done, lambda _: None, self)
        )

    # -------------------- Download Page Actions --------------------

    def _sync_quick_quality_from_db(self) -> None:
        cfg = self.db.get_settings()
        q_val = cfg.get("quality", "best_1080p")
        only_aud = bool(cfg.get("only_audio", False))
        aud_fmt = cfg.get("audio_format", "mp3")
        self.quick_quality_combo.blockSignals(True)
        for idx, (_lbl, qk, oa, af) in enumerate(QUALITY_ITEMS):
            if oa == only_aud and ((only_aud and af == aud_fmt) or (not only_aud and qk == q_val)):
                self.quick_quality_combo.setCurrentIndex(idx)
                break
        self.quick_quality_combo.blockSignals(False)

    def _on_quick_quality_changed(self, index: int) -> None:
        if 0 <= index < len(QUALITY_ITEMS):
            _lbl, qk, only_aud, aud_fmt = QUALITY_ITEMS[index]
            self.db.update_settings(
                {
                    "quality": qk,
                    "only_audio": only_aud,
                    "audio_format": aud_fmt,
                }
            )
            self.settings_page.load_from_db()

    def _on_settings_updated(self) -> None:
        self._sync_quick_quality_from_db()
        self.ai_tab.load_llm_settings()
        self.refresh_all_task_views()

    def _paste_and_download(self) -> None:
        clip = (QApplication.clipboard().text() or "").strip()
        if not clip:
            InfoBar.warning(
                title="剪贴板为空",
                content="未在系统剪贴板中检测到链接或分享口令。",
                position=InfoBarPosition.TOP,
                duration=2500,
                parent=self.download_page,
            )
            return
        self.url_input.setText(clip)
        self._submit_download()

    def _probe_url(self) -> None:
        raw = self.url_input.text().strip()
        if not raw:
            InfoBar.info(
                title="请输入链接",
                content="请先在输入框中粘贴视频链接或分享口令。",
                position=InfoBarPosition.TOP,
                duration=2500,
                parent=self.download_page,
            )
            return
        self.btn_parse.setEnabled(False)

        def _done(res: dict[str, Any]) -> None:
            self.btn_parse.setEnabled(True)
            media = res["media"]
            cleaned = res["cleaned"]
            self._prefetched_media = media
            fmts = " / ".join(f["label"] for f in (media.get("formats") or [])[:5]) or "最佳无水印直链"
            dur = format_mmss(media.get("duration") or 0)
            self.preview_title_lbl.setText(
                f"{media.get('title')}   ({cleaned.get('platform_name')} · {dur} · 可选: {fmts})"
            )
            self.preview_trace_lbl.setText(format_trace_dict_as_tree(media.get("trace")))
            self.preview_frame.setVisible(True)

        def _err(msg: str) -> None:
            self.btn_parse.setEnabled(True)
            InfoBar.error(
                title="解析预览失败",
                content=msg[:200],
                position=InfoBarPosition.TOP,
                duration=4500,
                parent=self.download_page,
            )

        self._bridges.append(run_in_thread(lambda: self.queue_mgr.parse_input(raw), _done, _err, self))

    def _submit_download(self) -> None:
        raw = self.url_input.text().strip()
        if not raw:
            InfoBar.info(
                title="请输入链接",
                content="粘贴视频链接后直接按 Enter 即可开始下载。",
                position=InfoBarPosition.TOP,
                duration=2400,
                parent=self.download_page,
            )
            return

        pre = self._prefetched_media or {}
        if not pre:
            cached = self.prefetcher.get_cached(raw)
            if cached and cached.ok and cached.media:
                pre = dict(cached.media)
                if cached.trace and not pre.get("trace"):
                    pre["trace"] = cached.trace

        try:
            created = self.queue_mgr.submit_tasks(
                raw_text=raw,
                prefetched_title=str(pre.get("title") or ""),
                prefetched_thumbnail=str(pre.get("thumbnail") or ""),
                prefetched_duration=float(pre.get("duration") or 0.0),
                prefetched_uploader=str(pre.get("uploader") or ""),
                prefetched_trace=pre.get("trace"),
            )
        except Exception as exc:
            InfoBar.warning(
                title="链接识别提示",
                content=str(exc),
                position=InfoBarPosition.TOP,
                duration=3500,
                parent=self.download_page,
            )
            return

        if created:
            self.sentinel.suppress_url_24h(created[0]["url"])
        self.url_input.clear()
        self._prefetched_media = None
        self.preview_frame.setVisible(False)
        self.refresh_all_task_views()

        if len(created) > 1:
            InfoBar.success(
                title="批量任务已入队",
                content=f"已识别并投递 {len(created)} 条下载任务。",
                position=InfoBarPosition.TOP,
                duration=3000,
                parent=self.download_page,
            )

    def _import_batch_txt(self) -> None:
        fp, _ = QFileDialog.getOpenFileName(
            self, "选择包含视频链接的 TXT 文件 (每行一个链接)", "", "Text Files (*.txt);;All Files (*)"
        )
        if not fp:
            return
        content = ""
        for enc in ("utf-8-sig", "utf-8", "gbk"):
            try:
                content = Path(fp).read_text(encoding=enc)
                break
            except Exception:
                continue
        if not content.strip():
            return
        try:
            created = self.queue_mgr.submit_tasks(raw_text=content.strip())
            self.refresh_all_task_views()
            InfoBar.success(
                title="批量导入成功",
                content=f"已从 {Path(fp).name} 投递 {len(created)} 条下载任务。",
                position=InfoBarPosition.TOP,
                duration=3200,
                parent=self.download_page,
            )
        except Exception as exc:
            InfoBar.warning(title="批量导入失败", content=str(exc), position=InfoBarPosition.TOP, parent=self)

    def _open_save_dir(self) -> None:
        p = self.db.get_settings().get("save_path", "")
        if p:
            os.makedirs(p, exist_ok=True)
            reveal_in_file_manager(p, select_file=False)

    def _clear_failed_canceled(self) -> None:
        for t in self.db.list_tasks(limit=300):
            if t.get("status") in ("failed", "canceled", "interrupted"):
                self.queue_mgr.delete_task(t["id"], delete_file=False)
        self.refresh_all_task_views()

    def _set_filter(self, key: str) -> None:
        self._active_filter = key
        for k, pill in self._filter_pills.items():
            pill.setChecked(k == key)
        self._rebuild_cards_list(force_recreate=False)

    # -------------------- Task Cards Live Rendering --------------------

    def _on_queue_event(self, event_type: str, _payload: object) -> None:
        self.refresh_all_task_views()
        if hasattr(self, "sound_mgr"):
            if event_type == "task_completed":
                active_cnt = sum(
                    1
                    for t in self.db.list_tasks(limit=100)
                    if t.get("status") in ("queued", "parsing", "downloading", "merging", "retrying")
                )
                self.sound_mgr.notify_task_finished(status="completed", pending_count=active_cnt)
            elif event_type == "task_failed":
                self.sound_mgr.notify_task_finished(status="failed", pending_count=0)

    def _on_prefetch_ready_for_cards(self, _norm_url: str, _entry: object) -> None:
        # Refresh card thumbnails if a background prefetch just cached cover bytes
        self._rebuild_cards_list(force_recreate=False)

    def _filter_tasks(self, all_tasks: list[dict[str, Any]]) -> list[dict[str, Any]]:
        if self._active_filter == "active":
            return [
                t
                for t in all_tasks
                if t.get("status") in ("queued", "parsing", "downloading", "merging", "retrying", "paused")
            ]
        if self._active_filter == "completed":
            return [t for t in all_tasks if t.get("status") == "completed"]
        if self._active_filter == "failed":
            return [t for t in all_tasks if t.get("status") in ("failed", "interrupted", "canceled")]
        return all_tasks

    def _lookup_thumb_bytes(self, task: dict[str, Any]) -> bytes:
        url = str(task.get("url") or "")
        if not url or url.startswith("file://"):
            return b""
        cached: PrefetchEntry | None = self.prefetcher.get_cached(url)
        return cached.thumbnail_bytes if cached else b""

    def refresh_all_task_views(self) -> None:
        all_tasks = self.db.list_tasks(limit=200)
        completed = [t for t in all_tasks if t.get("status") == "completed"]
        active_cnt = sum(
            1 for t in all_tasks if t.get("status") in ("queued", "parsing", "downloading", "merging", "retrying")
        )
        failed_cnt = sum(1 for t in all_tasks if t.get("status") in ("failed", "interrupted"))

        self._filter_pills["all"].setText(f"全部 ({len(all_tasks)})" if all_tasks else "全部任务")
        self._filter_pills["active"].setText(f"进行中 ({active_cnt})" if active_cnt else "进行中")
        self._filter_pills["completed"].setText(f"已完成 ({len(completed)})" if completed else "已完成")
        self._filter_pills["failed"].setText(f"异常 ({failed_cnt})" if failed_cnt else "异常 / 未完成")

        self.footer_hint_lbl.setText(
            f"进行中 {active_cnt}  ·  媒体库 {len(completed)}" if (active_cnt or completed) else ""
        )

        self._rebuild_cards_list(force_recreate=False, preloaded_tasks=all_tasks)
        self.library_tab.update_tasks(completed)
        self.ai_tab.refresh_completed_tasks(completed)

    def _on_card_trace_toggled(self, task_id: str, expanded: bool) -> None:
        if expanded:
            self._expanded_traces.add(task_id)
        else:
            self._expanded_traces.discard(task_id)

    def _rebuild_cards_list(
        self,
        force_recreate: bool = False,
        preloaded_tasks: list[dict[str, Any]] | None = None,
    ) -> None:
        all_tasks = preloaded_tasks if preloaded_tasks is not None else self.db.list_tasks(limit=200)
        visible_tasks = self._filter_tasks(all_tasks)[:80]
        new_ids = [str(t["id"]) for t in visible_tasks]

        self.empty_card.setVisible(len(visible_tasks) == 0)

        # Fast-path: update existing card widgets in-place without layout thrashing
        if not force_recreate and new_ids == self._rendered_order:
            for t in visible_tasks:
                card = self._card_widgets.get(str(t["id"]))
                if card is not None:
                    card.update_from_task(
                        t,
                        is_dark=self._is_dark,
                        thumbnail_bytes=self._lookup_thumb_bytes(t),
                    )
            return

        # Full order rebuild
        for old_card in self._card_widgets.values():
            self.cards_lay.removeWidget(old_card)
            old_card.deleteLater()
        self._card_widgets.clear()
        self._rendered_order = new_ids

        # Insert before trailing stretch (which sits right after `empty_card`)
        insert_pos = 1
        for t in visible_tasks:
            tid = str(t["id"])
            card = MediaTaskCard(
                task=t,
                is_dark=self._is_dark,
                thumbnail_bytes=self._lookup_thumb_bytes(t),
                on_pause=lambda x: self.queue_mgr.pause_task(x),
                on_resume=lambda x: self.queue_mgr.resume_task(x),
                on_cancel=lambda x: self.queue_mgr.cancel_task(x),
                on_delete=lambda x: self.queue_mgr.delete_task(x, delete_file=False),
                on_play=self._play_from_queue,
                on_reveal_folder=self.library_tab.reveal_task_in_folder,
                on_open_ai=self._jump_to_ai_for_task,
                on_open_cookie_settings=self.jump_to_cookie_settings,
                expanded_initial=(tid in self._expanded_traces),
                on_toggle_trace=self._on_card_trace_toggled,
                theme_style=self._theme_style,
                parent=self.cards_host,
            )
            self.cards_lay.insertWidget(insert_pos, card)
            insert_pos += 1
            self._card_widgets[tid] = card

    # -------------------- Cross-Page Navigation & Lifecycle --------------------

    def _play_from_queue(self, task_id: str) -> None:
        self.switchTo(self.library_tab)
        self.library_tab.play_task(task_id)

    def _seek_and_play_in_library(self, task_id: str, seconds: float) -> None:
        self.switchTo(self.library_tab)
        self.library_tab.play_task(task_id, seek_seconds=seconds)

    def _jump_to_ai_for_task(self, task_id: str) -> None:
        completed = [t for t in self.db.list_tasks(limit=200) if t.get("status") == "completed"]
        self.ai_tab.refresh_completed_tasks(completed, select_task_id=task_id)
        self.switchTo(self.ai_tab)

    def quit_application(self) -> None:
        """Explicit full exit from System Tray menu."""
        if self.queue_mgr.has_active_downloads():
            ans = QMessageBox.question(
                self,
                "确认退出",
                f"当前仍有视频正在下载，退出 {APP_NAME} 将暂停任务并在下次启动时提醒恢复。\n确定现在彻底退出吗？",
            )
            if ans != QMessageBox.StandardButton.Yes:
                return
        self._force_quit = True
        self.close()
        app = QApplication.instance()
        if app is not None:
            app.quit()

    def closeEvent(self, event) -> None:  # noqa: N802
        platform_name = (QGuiApplication.platformName() or "").lower()
        cfg = self.db.get_settings()

        # Close-to-tray behavior when clicking window 'X' in normal desktop sessions
        if (
            not self._force_quit
            and platform_name != "offscreen"
            and cfg.get("close_to_tray", True)
            and self.tray_ctrl.tray.isVisible()
        ):
            event.ignore()
            self.hide()
            if not cfg.get("close_to_tray_notified", False):
                self.db.update_settings({"close_to_tray_notified": True})
                hk = (cfg.get("hotkeys") or {}).get("capture", "Ctrl+Shift+D")
                self.tray_ctrl.notify(
                    f"{APP_NAME} 已驻留系统托盘",
                    f"后台剪贴板预热与 {hk} 全局秒捕保持就绪；右键托盘图标可彻底退出。",
                )
            return

        # Full shutdown cleanup
        self.hotkey_mgr.shutdown()
        self.sentinel.shutdown()
        self.prefetcher.cancel_active()
        self.quick_capture.hide()
        self.tray_ctrl.shutdown()
        self.library_tab.stop_playback(release_file=True)
        self.web_mgr.stop()
        self.queue_mgr.stop()
        event.accept()


def run_desktop_gui(start_minimized: bool = False) -> None:
    setup_logging()
    app = QApplication.instance() or QApplication(sys.argv)
    app.setApplicationName(APP_NAME)
    app.setApplicationVersion(__version__)
    app.setQuitOnLastWindowClosed(False)

    guard = SingleInstanceGuard()
    if not guard.try_acquire_or_notify("SHOW"):
        sys.exit(0)

    win = MainWindow(start_minimized=start_minimized)
    guard.message_received.connect(win.handle_single_instance_message)

    if not start_minimized:
        win.show()

    exit_code = app.exec()
    guard.close()
    sys.exit(exit_code)
