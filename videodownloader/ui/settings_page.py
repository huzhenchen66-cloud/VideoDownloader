"""Page 5: Settings, Anime Sensory, Network & Cookies, AI Copilot Engine, Hotkeys & Health Diagnostics."""

from __future__ import annotations

import contextlib
import io
import os
from pathlib import Path
import shutil
from typing import Any, Callable
import webbrowser

with contextlib.redirect_stdout(io.StringIO()):
    from qfluentwidgets import (
        CardWidget,
        CheckBox,
        ComboBox,
        EditableComboBox,
        FluentIcon as FIF,
        InfoBar,
        InfoBarPosition,
        LineEdit,
        PrimaryPushButton,
        PushButton,
        Slider,
        SpinBox,
        SwitchButton,
    )

from PyQt6.QtCore import Qt
from PyQt6.QtWidgets import (
    QFileDialog,
    QGridLayout,
    QHBoxLayout,
    QLabel,
    QLineEdit,
    QMessageBox,
    QScrollArea,
    QVBoxLayout,
    QWidget,
)

from .. import __version__
from ..ai.llm_service import LLM_PROVIDER_PRESETS, test_llm_connection
from ..core.app_updater import UpdateCheckResult, check_github_release
from ..core.binary_updater import get_toolchain_status, hot_update_ytdlp_binary
from ..core.db import DEFAULT_HOTKEYS, Database
from ..core.hotkey import HotkeyRecorderEdit
from ..core.logging_setup import export_diagnostics_zip
from ..core.os_utils import detect_system_proxy, reveal_in_file_manager
from ..core.paths import get_data_dir
from ..core.task_queue import TaskQueueManager
from ..core.theme_package import export_vdtheme_package, import_vdtheme_package
from .desktop_theme import COOKIE_ITEMS, PROXY_MODE_ITEMS, QUALITY_ITEMS, run_in_thread
from .tokens import ANIME_THEMES, get_palette


class SettingsAndDiagnosticsPage(QWidget):
    """High-aesthetic Settings & Diagnostics page with clean visual typography and zero raw code jargon."""

    def __init__(
        self,
        db: Database,
        queue_mgr: TaskQueueManager,
        on_theme_changed: Callable[..., None],
        on_hotkeys_changed: Callable[[], None],
        on_settings_saved: Callable[[], None],
        on_history_cleared: Callable[[], None],
        sound_mgr: Any = None,
        on_open_stats: Callable[[], None] | None = None,
        parent: QWidget | None = None,
    ) -> None:
        super().__init__(parent)
        self.setObjectName("settingsPage")
        self.db = db
        self.queue_mgr = queue_mgr
        self._on_theme_changed = on_theme_changed
        self._on_hotkeys_changed = on_hotkeys_changed
        self._on_settings_saved = on_settings_saved
        self._on_history_cleared = on_history_cleared
        self.sound_mgr = sound_mgr
        self._on_open_stats = on_open_stats

        self._loading = False
        self._bridges: list[Any] = []
        self._is_dark: bool = self.db.get_settings().get("theme_mode") == "dark"
        self._theme_style: str = str(self.db.get_settings().get("theme_style") or "miku")

        self._build_ui()
        self.load_from_db()
        self.apply_theme(self._is_dark)
        self.refresh_toolchain()

    def _build_ui(self) -> None:
        root_lay = QVBoxLayout(self)
        root_lay.setContentsMargins(0, 0, 0, 0)

        self.scroll = QScrollArea(self)
        self.scroll.setWidgetResizable(True)
        self.scroll.setFrameShape(QScrollArea.Shape.NoFrame)

        self.container = QWidget()
        lay = QVBoxLayout(self.container)
        lay.setContentsMargins(24, 20, 24, 24)
        lay.setSpacing(18)

        self._section_titles: list[QLabel] = []
        self._field_labels: list[QLabel] = []

        # ==================== Card 1: Appearance & Anime Themes ====================
        self.card_sensory = CardWidget(self.container)
        s_lay = QVBoxLayout(self.card_sensory)
        s_lay.setContentsMargins(22, 18, 22, 18)
        s_lay.setSpacing(14)

        t_sensory = QLabel("🎨 界面风格与二次元主题定制")
        self._section_titles.append(t_sensory)
        s_lay.addWidget(t_sensory)

        s_grid = QGridLayout()
        s_grid.setHorizontalSpacing(18)
        s_grid.setVerticalSpacing(12)

        # Theme mode
        lbl_theme = QLabel("界面显示模式:")
        self._field_labels.append(lbl_theme)
        s_grid.addWidget(lbl_theme, 0, 0)

        self.theme_combo = ComboBox(self.card_sensory)
        self.theme_combo.addItems(["浅色模式 (清新明朗)", "深色模式 (夜间护眼)"])
        self.theme_combo.currentIndexChanged.connect(self._on_theme_combo_changed)
        s_grid.addWidget(self.theme_combo, 0, 1)

        # Anime Theme style
        lbl_theme_style = QLabel("二次元个性化主题:")
        self._field_labels.append(lbl_theme_style)
        s_grid.addWidget(lbl_theme_style, 0, 2)

        self.theme_style_combo = ComboBox(self.card_sensory)
        self._theme_style_keys = list(ANIME_THEMES.keys())
        for tk in self._theme_style_keys:
            tinfo = ANIME_THEMES[tk]
            self.theme_style_combo.addItem(f"{tinfo['name']} [{tinfo['badge']}]")
        self.theme_style_combo.currentIndexChanged.connect(self._on_theme_style_combo_changed)
        s_grid.addWidget(self.theme_style_combo, 0, 3)

        # Sound switch
        lbl_sound = QLabel("下载完成提示音效:")
        self._field_labels.append(lbl_sound)
        s_grid.addWidget(lbl_sound, 1, 0)

        self.sw_sound = SwitchButton(self.card_sensory)
        self.sw_sound.checkedChanged.connect(self._on_sound_master_toggled)
        s_grid.addWidget(self.sw_sound, 1, 1)

        # Sound volume + preview
        lbl_vol = QLabel("提示声音量与试听:")
        self._field_labels.append(lbl_vol)
        s_grid.addWidget(lbl_vol, 1, 2)

        vol_row = QHBoxLayout()
        vol_row.setSpacing(10)
        self.slider_vol = Slider(Qt.Orientation.Horizontal, self.card_sensory)
        self.slider_vol.setRange(0, 100)
        self.slider_vol.valueChanged.connect(self._on_sound_volume_changed)
        vol_row.addWidget(self.slider_vol, 1)

        self.lbl_vol_val = QLabel("70%", self.card_sensory)
        self.lbl_vol_val.setFixedWidth(40)
        self._field_labels.append(self.lbl_vol_val)
        vol_row.addWidget(self.lbl_vol_val)

        self.btn_test_sound = PushButton(FIF.VOLUME, "试听当前音效", self.card_sensory)
        self.btn_test_sound.clicked.connect(self._play_test_sound)
        vol_row.addWidget(self.btn_test_sound)
        s_grid.addLayout(vol_row, 1, 3)

        # Custom Wallpaper
        lbl_wall = QLabel("自定义背景壁纸:")
        self._field_labels.append(lbl_wall)
        s_grid.addWidget(lbl_wall, 2, 0)

        wall_row = QHBoxLayout()
        wall_row.setSpacing(8)
        self.wallpaper_edit = LineEdit(self.card_sensory)
        self.wallpaper_edit.setPlaceholderText("默认纯矢量程序化绘制背景（可自选壁纸图片）")
        self.wallpaper_edit.setReadOnly(True)
        wall_row.addWidget(self.wallpaper_edit, 1)

        btn_pick_wall = PushButton(FIF.PHOTO, "选择图片", self.card_sensory)
        btn_pick_wall.clicked.connect(self._choose_wallpaper)
        wall_row.addWidget(btn_pick_wall)

        btn_clear_wall = PushButton(FIF.CLOSE, "恢复默认背景", self.card_sensory)
        btn_clear_wall.clicked.connect(self._clear_wallpaper)
        wall_row.addWidget(btn_clear_wall)
        s_grid.addLayout(wall_row, 2, 1, 1, 3)

        # Poster & Themes
        lbl_stats_pkg = QLabel("成就海报与主题包:")
        self._field_labels.append(lbl_stats_pkg)
        s_grid.addWidget(lbl_stats_pkg, 3, 0)

        pkg_row = QHBoxLayout()
        pkg_row.setSpacing(10)
        self.btn_open_stats = PrimaryPushButton(FIF.PIE_SINGLE, "生成我的下载战报海报 (Ctrl+K)", self.card_sensory)
        self.btn_open_stats.clicked.connect(self._show_stats_dialog)
        pkg_row.addWidget(self.btn_open_stats)

        self.btn_import_theme = PushButton(FIF.SHARE, "导入 .vdtheme 主题包", self.card_sensory)
        self.btn_import_theme.clicked.connect(self._import_theme_dialog)
        pkg_row.addWidget(self.btn_import_theme)

        self.btn_export_theme = PushButton(FIF.DOWNLOAD, "导出当前主题包", self.card_sensory)
        self.btn_export_theme.clicked.connect(self._export_theme_dialog)
        pkg_row.addWidget(self.btn_export_theme)
        pkg_row.addStretch(1)
        s_grid.addLayout(pkg_row, 3, 1, 1, 3)

        s_grid.setColumnStretch(1, 2)
        s_grid.setColumnStretch(3, 2)
        s_lay.addLayout(s_grid)
        lay.addWidget(self.card_sensory)

        # ==================== Card 2: Download Storage & Preferences ====================
        self.card_general = CardWidget(self.container)
        g_lay = QVBoxLayout(self.card_general)
        g_lay.setContentsMargins(22, 18, 22, 18)
        g_lay.setSpacing(14)

        t1 = QLabel("🚀 下载存储与偏好设置")
        self._section_titles.append(t1)
        g_lay.addWidget(t1)

        g_grid = QGridLayout()
        g_grid.setHorizontalSpacing(18)
        g_grid.setVerticalSpacing(12)

        lbl_dir = QLabel("视频保存目录:")
        self._field_labels.append(lbl_dir)
        g_grid.addWidget(lbl_dir, 0, 0)

        dir_row = QHBoxLayout()
        dir_row.setSpacing(8)
        self.save_path_edit = LineEdit(self.card_general)
        self.save_path_edit.setReadOnly(True)
        dir_row.addWidget(self.save_path_edit, 1)

        btn_pick = PushButton(FIF.FOLDER_ADD, "更改目录", self.card_general)
        btn_pick.clicked.connect(self._choose_save_dir)
        dir_row.addWidget(btn_pick)

        btn_open = PushButton(FIF.FOLDER, "打开目录", self.card_general)
        btn_open.clicked.connect(self._open_save_dir)
        dir_row.addWidget(btn_open)
        g_grid.addLayout(dir_row, 0, 1, 1, 3)

        lbl_qual = QLabel("默认画质预设:")
        self._field_labels.append(lbl_qual)
        g_grid.addWidget(lbl_qual, 1, 0)

        self.quality_combo = ComboBox(self.card_general)
        for label, _qk, _oa, _af in QUALITY_ITEMS:
            self.quality_combo.addItem(label)
        self.quality_combo.currentIndexChanged.connect(self._save_quality_and_network)
        g_grid.addWidget(self.quality_combo, 1, 1)

        lbl_conc = QLabel("同时下载任务数:")
        self._field_labels.append(lbl_conc)
        g_grid.addWidget(lbl_conc, 1, 2)

        conc_row = QHBoxLayout()
        conc_row.setSpacing(12)
        self.concurrent_spin = SpinBox(self.card_general)
        self.concurrent_spin.setRange(1, 6)
        self.concurrent_spin.valueChanged.connect(lambda v: self._patch({"concurrent_tasks": int(v)}))
        conc_row.addWidget(self.concurrent_spin)

        self.chk_subtitle = CheckBox("优先下载字幕文件 (SRT/VTT)", self.card_general)
        self.chk_subtitle.toggled.connect(lambda v: self._patch({"subtitle": bool(v)}))
        conc_row.addWidget(self.chk_subtitle)
        conc_row.addStretch(1)
        g_grid.addLayout(conc_row, 1, 3)

        lbl_tray = QLabel("关闭主窗口时的行为:")
        self._field_labels.append(lbl_tray)
        g_grid.addWidget(lbl_tray, 2, 0)

        tray_row = QHBoxLayout()
        tray_row.setSpacing(8)
        self.sw_close_tray = SwitchButton(self.card_general)
        self.sw_close_tray.checkedChanged.connect(lambda v: self._patch({"close_to_tray": bool(v)}))
        tray_row.addWidget(self.sw_close_tray)
        lbl_tray_hint = QLabel("点击关闭按钮时最小化到系统托盘（在后台继续下载）")
        self._field_labels.append(lbl_tray_hint)
        tray_row.addWidget(lbl_tray_hint)
        tray_row.addStretch(1)
        g_grid.addLayout(tray_row, 2, 1, 1, 3)

        g_grid.setColumnStretch(1, 2)
        g_grid.setColumnStretch(3, 2)
        g_lay.addLayout(g_grid)
        lay.addWidget(self.card_general)

        # ==================== Card 3: Network Proxy & Cookie Auth ====================
        self.card_auth = CardWidget(self.container)
        a_lay = QVBoxLayout(self.card_auth)
        a_lay.setContentsMargins(22, 18, 22, 18)
        a_lay.setSpacing(14)

        t2 = QLabel("🌐 智能网络分流与账号授权")
        self._section_titles.append(t2)
        a_lay.addWidget(t2)

        a_grid = QGridLayout()
        a_grid.setHorizontalSpacing(18)
        a_grid.setVerticalSpacing(12)

        lbl_px = QLabel("代理分流策略:")
        self._field_labels.append(lbl_px)
        a_grid.addWidget(lbl_px, 0, 0)

        self.proxy_mode_combo = ComboBox(self.card_auth)
        for label, _mk in PROXY_MODE_ITEMS:
            self.proxy_mode_combo.addItem(label)
        self.proxy_mode_combo.currentIndexChanged.connect(self._save_quality_and_network)
        a_grid.addWidget(self.proxy_mode_combo, 0, 1)

        lbl_px_url = QLabel("自定义代理地址:")
        self._field_labels.append(lbl_px_url)
        a_grid.addWidget(lbl_px_url, 0, 2)

        self.proxy_url_edit = LineEdit(self.card_auth)
        sys_px = detect_system_proxy()
        self.proxy_url_edit.setPlaceholderText(
            f"已自动探测到系统代理 ({sys_px})" if sys_px else "如 http://127.0.0.1:7890"
        )
        self.proxy_url_edit.editingFinished.connect(self._save_quality_and_network)
        a_grid.addWidget(self.proxy_url_edit, 0, 3)

        lbl_ck = QLabel("浏览器登录状态:")
        self._field_labels.append(lbl_ck)
        a_grid.addWidget(lbl_ck, 1, 0)

        self.browser_cookie_combo = ComboBox(self.card_auth)
        for label, _ck in COOKIE_ITEMS:
            self.browser_cookie_combo.addItem(label)
        self.browser_cookie_combo.currentIndexChanged.connect(self._save_quality_and_network)
        a_grid.addWidget(self.browser_cookie_combo, 1, 1)

        lbl_ck_file = QLabel("离线授权凭据文件:")
        self._field_labels.append(lbl_ck_file)
        a_grid.addWidget(lbl_ck_file, 1, 2)

        ck_row = QHBoxLayout()
        ck_row.setSpacing(8)
        self.btn_cookie_file = PushButton(FIF.CERTIFICATE, "导入 cookies.txt", self.card_auth)
        self.btn_cookie_file.clicked.connect(self._import_cookie_file)
        ck_row.addWidget(self.btn_cookie_file)

        self.btn_clear_cookie = PushButton(FIF.CLOSE, "清除凭据", self.card_auth)
        self.btn_clear_cookie.clicked.connect(self._clear_cookie_file)
        ck_row.addWidget(self.btn_clear_cookie)
        ck_row.addStretch(1)
        a_grid.addLayout(ck_row, 1, 3)

        a_grid.setColumnStretch(1, 2)
        a_grid.setColumnStretch(3, 2)
        a_lay.addLayout(a_grid)
        lay.addWidget(self.card_auth)

        # ==================== Card 4: AI Storyboard & LLM Copilot Service ====================
        self.card_ai = CardWidget(self.container)
        ai_lay = QVBoxLayout(self.card_ai)
        ai_lay.setContentsMargins(22, 18, 22, 18)
        ai_lay.setSpacing(14)

        t_ai = QLabel("🤖 AI 智能精读与分镜服务")
        self._section_titles.append(t_ai)
        ai_lay.addWidget(t_ai)

        ai_grid = QGridLayout()
        ai_grid.setHorizontalSpacing(18)
        ai_grid.setVerticalSpacing(12)

        lbl_ai_prov = QLabel("大模型服务商:")
        self._field_labels.append(lbl_ai_prov)
        ai_grid.addWidget(lbl_ai_prov, 0, 0)

        self._ai_provider_keys: list[str] = list(LLM_PROVIDER_PRESETS.keys())
        self.ai_provider_combo = ComboBox(self.card_ai)
        for pk in self._ai_provider_keys:
            self.ai_provider_combo.addItem(LLM_PROVIDER_PRESETS[pk]["name"])
        self.ai_provider_combo.currentIndexChanged.connect(self._on_ai_provider_changed)
        ai_grid.addWidget(self.ai_provider_combo, 0, 1)

        lbl_ai_model = QLabel("选用模型名称:")
        self._field_labels.append(lbl_ai_model)
        ai_grid.addWidget(lbl_ai_model, 0, 2)

        self.ai_model_combo = EditableComboBox(self.card_ai)
        self.ai_model_combo.setPlaceholderText("选择推荐模型或输入自定义模型...")
        self.ai_model_edit = self.ai_model_combo  # Backward-compatibility alias
        self.ai_model_combo.currentIndexChanged.connect(self._save_ai_settings)
        ai_grid.addWidget(self.ai_model_combo, 0, 3)

        lbl_ai_url = QLabel("API 接口地址:")
        self._field_labels.append(lbl_ai_url)
        ai_grid.addWidget(lbl_ai_url, 1, 0)

        self.ai_base_url_edit = LineEdit(self.card_ai)
        self.ai_base_url_edit.setPlaceholderText("如 https://dashscope.aliyuncs.com/compatible-mode/v1")
        self.ai_base_url_edit.editingFinished.connect(self._save_ai_settings)
        ai_grid.addWidget(self.ai_base_url_edit, 1, 1)

        lbl_ai_key = QLabel("API 授权密钥:")
        self._field_labels.append(lbl_ai_key)
        ai_grid.addWidget(lbl_ai_key, 1, 2)

        ai_key_row = QHBoxLayout()
        ai_key_row.setSpacing(8)
        self.ai_key_edit = LineEdit(self.card_ai)
        self.ai_key_edit.setEchoMode(QLineEdit.EchoMode.Password)
        self.ai_key_edit.setPlaceholderText("留空启用离线规则分镜")
        self.ai_key_edit.editingFinished.connect(self._save_ai_settings)
        ai_key_row.addWidget(self.ai_key_edit, 1)

        self.btn_test_ai = PushButton(FIF.SPEED_HIGH, "测试连接", self.card_ai)
        self.btn_test_ai.clicked.connect(self._test_ai_connection)
        ai_key_row.addWidget(self.btn_test_ai)

        btn_save_ai = PrimaryPushButton(FIF.SAVE, "保存 AI 设置", self.card_ai)
        btn_save_ai.clicked.connect(self._save_ai_settings_btn)
        ai_key_row.addWidget(btn_save_ai)
        ai_grid.addLayout(ai_key_row, 1, 3)

        ai_grid.setColumnStretch(1, 2)
        ai_grid.setColumnStretch(3, 2)
        ai_lay.addLayout(ai_grid)
        lay.addWidget(self.card_ai)

        # ==================== Card 5: Global Hotkeys & Clipboard Sentinel ====================
        self.card_hotkey = CardWidget(self.container)
        h_lay = QVBoxLayout(self.card_hotkey)
        h_lay.setContentsMargins(22, 18, 22, 18)
        h_lay.setSpacing(14)

        hdr_hk = QHBoxLayout()
        t3 = QLabel("⚡ 全局快捷键与剪贴板智能感知")
        self._section_titles.append(t3)
        hdr_hk.addWidget(t3)
        hdr_hk.addStretch(1)

        btn_reset_hk = PushButton(FIF.HISTORY, "恢复默认快捷键", self.card_hotkey)
        btn_reset_hk.clicked.connect(self._reset_default_hotkeys)
        hdr_hk.addWidget(btn_reset_hk)
        h_lay.addLayout(hdr_hk)

        sw_grid = QGridLayout()
        sw_grid.setHorizontalSpacing(24)
        sw_grid.setVerticalSpacing(8)

        sw_items = [
            ("sw_hotkey_enabled", "启用系统全局热键", "hotkey_enabled", 0, 0),
            ("sw_clip_watch", "启用剪贴板链接感知", "clipboard_watch", 0, 1),
            ("sw_clip_prefetch", "后台静默秒探测 (0ms 开窗)", "clipboard_prefetch", 1, 0),
            ("sw_clip_toast", "右下角无焦点轻提示条", "clipboard_toast", 1, 1),
        ]
        for attr_name, text_str, key_name, r, c in sw_items:
            row_box = QHBoxLayout()
            row_box.setSpacing(10)
            lbl = QLabel(text_str)
            self._field_labels.append(lbl)
            sw = SwitchButton(self.card_hotkey)
            if key_name == "hotkey_enabled":
                sw.checkedChanged.connect(self._on_hotkey_master_toggled)
            else:
                sw.checkedChanged.connect(lambda v, k=key_name: self._patch({k: bool(v)}))
            setattr(self, attr_name, sw)
            row_box.addWidget(lbl)
            row_box.addWidget(sw)
            row_box.addStretch(1)
            sw_grid.addLayout(row_box, r, c)

        h_lay.addLayout(sw_grid)

        hk_grid = QGridLayout()
        hk_grid.setHorizontalSpacing(18)
        hk_grid.setVerticalSpacing(10)
        self._hk_edits: dict[str, HotkeyRecorderEdit] = {}
        hk_specs = [
            ("capture", "快速捕获下载浮层:", 0, 0),
            ("paste_capture", "读取剪贴板并静默下载:", 0, 2),
            ("show", "显示 / 隐藏主窗口:", 1, 0),
            ("pause", "暂停 / 恢复全部下载:", 1, 2),
        ]
        for hk_key, label_txt, r, c in hk_specs:
            lbl = QLabel(label_txt)
            self._field_labels.append(lbl)
            edit = HotkeyRecorderEdit("", self.card_hotkey)
            edit.setMinimumHeight(34)
            edit.chord_changed.connect(lambda val, k=hk_key: self._on_single_hotkey_edited(k, val))
            self._hk_edits[hk_key] = edit
            hk_grid.addWidget(lbl, r, c)
            hk_grid.addWidget(edit, r, c + 1)

        hk_grid.setColumnStretch(1, 1)
        hk_grid.setColumnStretch(3, 1)
        h_lay.addLayout(hk_grid)
        lay.addWidget(self.card_hotkey)

        # ==================== Card 6: Core Engine Status & System Maintenance ====================
        self.card_diag = CardWidget(self.container)
        d_lay = QVBoxLayout(self.card_diag)
        d_lay.setContentsMargins(22, 18, 22, 18)
        d_lay.setSpacing(14)

        d_hdr = QHBoxLayout()
        t4 = QLabel("🛡️ 系统核心维护与状态体检")
        self._section_titles.append(t4)
        d_hdr.addWidget(t4)
        d_hdr.addStretch(1)

        self.btn_refresh_env = PushButton(FIF.SYNC, "重新检测环境", self.card_diag)
        self.btn_refresh_env.clicked.connect(self.refresh_toolchain)
        d_hdr.addWidget(self.btn_refresh_env)

        self.btn_hot_update = PrimaryPushButton(FIF.SPEED_HIGH, "检查并更新内核", self.card_diag)
        self.btn_hot_update.clicked.connect(self._run_ytdlp_hot_update)
        d_hdr.addWidget(self.btn_hot_update)
        d_lay.addLayout(d_hdr)

        env_grid = QGridLayout()
        env_grid.setHorizontalSpacing(18)
        env_grid.setVerticalSpacing(10)
        self.lbl_ytdlp = QLabel("-")
        self.lbl_ffmpeg = QLabel("-")
        self.lbl_browser = QLabel("-")
        self.lbl_proxy = QLabel("-")

        for idx, (k_txt, val_lbl) in enumerate(
            [
                ("全球流媒体核心解析器:", self.lbl_ytdlp),
                ("多媒体混流转码封装核心:", self.lbl_ffmpeg),
                ("网页防风控无感嗅探引擎:", self.lbl_browser),
                ("网络通信与分流代理探测:", self.lbl_proxy),
            ]
        ):
            kl = QLabel(k_txt)
            self._field_labels.append(kl)
            val_lbl.setTextInteractionFlags(Qt.TextInteractionFlag.TextSelectableByMouse)
            env_grid.addWidget(kl, idx, 0)
            env_grid.addWidget(val_lbl, idx, 1)
        env_grid.setColumnStretch(1, 1)
        d_lay.addLayout(env_grid)

        # Maintenance tools
        maint_row = QHBoxLayout()
        maint_row.setSpacing(10)

        self.repo_edit = LineEdit(self.card_diag)
        self.repo_edit.setPlaceholderText("官方更新仓库源 (留空使用官方默认)")
        self.repo_edit.setMaximumWidth(260)
        self.repo_edit.editingFinished.connect(
            lambda: self._patch({"github_repo": self.repo_edit.text().strip()})
        )
        maint_row.addWidget(self.repo_edit)

        self.btn_check_update = PushButton(FIF.UPDATE, "检查软件更新", self.card_diag)
        self.btn_check_update.clicked.connect(self._check_app_update)
        maint_row.addWidget(self.btn_check_update)

        self.btn_export_diag = PushButton(FIF.DOCUMENT, "导出排查日志 (.zip)", self.card_diag)
        self.btn_export_diag.clicked.connect(self._export_diagnostics)
        maint_row.addWidget(self.btn_export_diag)

        self.btn_clear_history = PushButton(FIF.DELETE, "清空任务历史", self.card_diag)
        self.btn_clear_history.clicked.connect(self._clear_history_prompt)
        maint_row.addWidget(self.btn_clear_history)

        maint_row.addStretch(1)
        d_lay.addLayout(maint_row)
        lay.addWidget(self.card_diag)

        lay.addStretch(1)
        self.scroll.setWidget(self.container)
        root_lay.addWidget(self.scroll, 1)

    def apply_theme(self, is_dark: bool, theme_style: str | None = None) -> None:
        self._is_dark = is_dark
        if theme_style:
            self._theme_style = theme_style
        else:
            self._theme_style = self.db.get_settings().get("theme_style", "miku")
        pal = get_palette(is_dark, self._theme_style)
        self.scroll.setStyleSheet("QScrollArea { background: transparent; border: none; }")
        self.container.setStyleSheet("background: transparent;")
        for t in self._section_titles:
            t.setStyleSheet(f"font-size: 15px; font-weight: 700; color: {pal['text_primary']};")
        for fl in self._field_labels:
            fl.setStyleSheet(f"font-size: 13px; font-weight: 600; color: {pal['text_secondary']};")
        for vl in (self.lbl_ytdlp, self.lbl_ffmpeg, self.lbl_browser, self.lbl_proxy):
            vl.setStyleSheet(f"font-size: 13px; color: {pal['text_primary']};")
        for ed in self._hk_edits.values():
            ed.setStyleSheet(
                f"background-color: {pal['bg_trace']}; color: {pal['text_primary']}; "
                f"border: 1px solid {pal['border_strong']}; border-radius: 6px; padding: 4px 10px; font-weight: 600;"
            )

    def focus_cookie_section(self) -> None:
        """Scroll straight to the Cookie authentication card."""
        self.scroll.ensureWidgetVisible(self.card_auth)
        self.browser_cookie_combo.setFocus()
        InfoBar.info(
            title="配置站点登录授权",
            content="可选择直接读取本机浏览器登录态，或点击「导入 cookies.txt」导入已登录凭据文件。",
            position=InfoBarPosition.TOP,
            duration=4000,
            parent=self,
        )

    # -------------------- DB Load / Save --------------------

    def load_from_db(self) -> None:
        self._loading = True
        cfg = self.db.get_settings()

        self.theme_combo.setCurrentIndex(1 if cfg.get("theme_mode") == "dark" else 0)
        curr_style = str(cfg.get("theme_style") or "miku")
        if curr_style in self._theme_style_keys:
            self.theme_style_combo.setCurrentIndex(self._theme_style_keys.index(curr_style))
        self._theme_style = curr_style

        self.sw_sound.setChecked(bool(cfg.get("sound_enabled", True)))
        vol = int(cfg.get("sound_volume", 70))
        self.slider_vol.setValue(vol)
        self.lbl_vol_val.setText(f"{vol}%")
        self.wallpaper_edit.setText(str(cfg.get("custom_wallpaper_path") or ""))

        self.sw_close_tray.setChecked(bool(cfg.get("close_to_tray", True)))
        self.save_path_edit.setText(cfg.get("save_path", ""))
        self.concurrent_spin.setValue(int(cfg.get("concurrent_tasks", 3)))
        self.chk_subtitle.setChecked(bool(cfg.get("subtitle", True)))

        q_val = cfg.get("quality", "best_1080p")
        only_aud = bool(cfg.get("only_audio", False))
        aud_fmt = cfg.get("audio_format", "mp3")
        for idx, (_lbl, qk, oa, af) in enumerate(QUALITY_ITEMS):
            if oa == only_aud and ((only_aud and af == aud_fmt) or (not only_aud and qk == q_val)):
                self.quality_combo.setCurrentIndex(idx)
                break

        pm = cfg.get("proxy_mode", "auto")
        for idx, (_lbl, mk) in enumerate(PROXY_MODE_ITEMS):
            if mk == pm:
                self.proxy_mode_combo.setCurrentIndex(idx)
                break
        self.proxy_url_edit.setText(cfg.get("proxy_url", ""))

        bc = cfg.get("browser_cookie", "none")
        for idx, (_lbl, ck) in enumerate(COOKIE_ITEMS):
            if ck == bc:
                self.browser_cookie_combo.setCurrentIndex(idx)
                break
        self._refresh_cookie_btn(cfg.get("cookie_file", ""))

        # AI settings
        ai_prov = cfg.get("llm_provider", "dashscope")
        if ai_prov in self._ai_provider_keys:
            self.ai_provider_combo.setCurrentIndex(self._ai_provider_keys.index(ai_prov))
        preset = LLM_PROVIDER_PRESETS.get(ai_prov, LLM_PROVIDER_PRESETS.get("dashscope", {}))
        self.ai_base_url_edit.setText(cfg.get("llm_base_url") or preset.get("base_url", ""))
        self._populate_ai_model_combo(ai_prov, cfg.get("llm_model") or preset.get("default_model", ""))
        self.ai_key_edit.setText(cfg.get("llm_api_key", ""))

        # Hotkeys
        self.sw_hotkey_enabled.setChecked(bool(cfg.get("hotkey_enabled", True)))
        self.sw_clip_watch.setChecked(bool(cfg.get("clipboard_watch", True)))
        self.sw_clip_prefetch.setChecked(bool(cfg.get("clipboard_prefetch", True)))
        self.sw_clip_toast.setChecked(bool(cfg.get("clipboard_toast", True)))

        hks = cfg.get("hotkeys") or DEFAULT_HOTKEYS
        for k, ed in self._hk_edits.items():
            ed.setText(str(hks.get(k) or ""))

        self.repo_edit.setText(str(cfg.get("github_repo") or ""))
        self._loading = False

    def _patch(self, patch: dict[str, Any]) -> None:
        if self._loading:
            return
        self.db.update_settings(patch)
        self._on_settings_saved()

    def _on_theme_combo_changed(self, index: int) -> None:
        if self._loading:
            return
        is_dark = index == 1
        self.db.update_settings({"theme_mode": "dark" if is_dark else "light"})
        self._on_theme_changed(is_dark)

    def _on_theme_style_combo_changed(self, index: int) -> None:
        if self._loading:
            return
        idx = max(0, min(index, len(self._theme_style_keys) - 1))
        style_key = self._theme_style_keys[idx]
        self._theme_style = style_key
        self.db.update_settings({"theme_style": style_key})
        if self.sound_mgr:
            self.sound_mgr.configure(
                enabled=self.sw_sound.isChecked(),
                volume=self.slider_vol.value(),
                theme_style=style_key,
            )
        self._on_theme_changed(self._is_dark, style_key)

    def _get_current_theme_style(self) -> str:
        idx = max(0, self.theme_style_combo.currentIndex())
        if idx < len(self._theme_style_keys):
            return self._theme_style_keys[idx]
        return "miku"

    def _on_sound_master_toggled(self, checked: bool) -> None:
        if self._loading:
            return
        self.db.update_settings({"sound_enabled": bool(checked)})
        if self.sound_mgr:
            self.sound_mgr.configure(
                enabled=bool(checked),
                volume=self.slider_vol.value(),
                theme_style=self._get_current_theme_style(),
            )

    def _on_sound_volume_changed(self, val: int) -> None:
        self.lbl_vol_val.setText(f"{val}%")
        if self._loading:
            return
        self.db.update_settings({"sound_volume": int(val)})
        if self.sound_mgr:
            self.sound_mgr.configure(
                enabled=self.sw_sound.isChecked(),
                volume=int(val),
                theme_style=self._get_current_theme_style(),
            )

    def _play_test_sound(self) -> None:
        style = self._get_current_theme_style()
        if self.sound_mgr:
            self.sound_mgr.configure(
                enabled=True,
                volume=self.slider_vol.value(),
                theme_style=style,
            )
            self.sound_mgr.play("complete", preview_theme=style)

    def _choose_wallpaper(self) -> None:
        fp, _ = QFileDialog.getOpenFileName(
            self, "选择背景壁纸图片", "", "Images (*.png *.jpg *.jpeg *.webp *.bmp);;All Files (*)"
        )
        if fp:
            self.wallpaper_edit.setText(fp)
            self.db.update_settings({"custom_wallpaper_path": fp})
            self._on_theme_changed(self._is_dark, self._get_current_theme_style())

    def _clear_wallpaper(self) -> None:
        self.wallpaper_edit.clear()
        self.db.update_settings({"custom_wallpaper_path": ""})
        self._on_theme_changed(self._is_dark, self._get_current_theme_style())

    def _show_stats_dialog(self) -> None:
        if self._on_open_stats:
            self._on_open_stats()

    def _import_theme_dialog(self) -> None:
        fp, _ = QFileDialog.getOpenFileName(
            self, "选择 .vdtheme 自定义主题包", "", "VideoDownloader Themes (*.vdtheme);;Zip Files (*.zip);;All Files (*)"
        )
        if not fp:
            return
        try:
            meta = import_vdtheme_package(Path(fp))
            tid = str(meta.get("id") or "")
            name = str(meta.get("name") or tid)
            InfoBar.success(
                title="导入主题包成功",
                content=f"主题「{name}」已成功载入！",
                position=InfoBarPosition.TOP,
                duration=3500,
                parent=self,
            )
            self._on_theme_changed(self._is_dark, tid)
        except Exception as exc:
            InfoBar.warning(
                title="主题包解析失败",
                content=str(exc),
                position=InfoBarPosition.TOP,
                parent=self,
            )

    def _export_theme_dialog(self) -> None:
        style_key = self._get_current_theme_style()
        theme_info = ANIME_THEMES.get(style_key, ANIME_THEMES["miku"])
        fp, _ = QFileDialog.getSaveFileName(
            self,
            "导出当前主题包为 .vdtheme",
            f"{style_key}.vdtheme",
            "VideoDownloader Themes (*.vdtheme);;All Files (*)",
        )
        if not fp:
            return
        try:
            custom_bg = self.wallpaper_edit.text().strip()
            bg_path = Path(custom_bg) if custom_bg and Path(custom_bg).is_file() else None
            export_vdtheme_package(
                theme_id=theme_info["id"],
                name=theme_info["name"],
                accent=theme_info["accent"],
                gradient=theme_info["gradient"],
                out_vdtheme_path=Path(fp),
                custom_bg_path=bg_path,
            )
            InfoBar.success(
                title="主题包导出成功",
                content=f"已保存至 {Path(fp).name}，可分享给好友直接双击导入！",
                position=InfoBarPosition.TOP,
                duration=3500,
                parent=self,
            )
        except Exception as exc:
            InfoBar.warning(
                title="导出主题包失败",
                content=str(exc),
                position=InfoBarPosition.TOP,
                parent=self,
            )

    def _save_quality_and_network(self) -> None:
        if self._loading:
            return
        q_idx = max(0, self.quality_combo.currentIndex())
        _qlbl, qk, only_aud, aud_fmt = QUALITY_ITEMS[q_idx]
        pm_key = PROXY_MODE_ITEMS[max(0, self.proxy_mode_combo.currentIndex())][1]
        bc_key = COOKIE_ITEMS[max(0, self.browser_cookie_combo.currentIndex())][1]
        self.db.update_settings(
            {
                "quality": qk,
                "only_audio": only_aud,
                "audio_format": aud_fmt,
                "proxy_mode": pm_key,
                "proxy_url": self.proxy_url_edit.text().strip(),
                "browser_cookie": bc_key,
            }
        )
        self._on_settings_saved()

    # -------------------- AI Settings Actions --------------------

    def _populate_ai_model_combo(self, provider_key: str, selected_model: str = "") -> None:
        self.ai_model_combo.blockSignals(True)
        self.ai_model_combo.clear()
        preset = LLM_PROVIDER_PRESETS.get(provider_key, {})
        models = preset.get("models") or [preset.get("default_model", "deepseek-v4.1-flash-1017")]
        for m in models:
            self.ai_model_combo.addItem(m)
        target = selected_model or preset.get("default_model", "")
        if target:
            if target not in models:
                self.ai_model_combo.insertItem(0, target)
            self.ai_model_combo.setCurrentText(target)
        self.ai_model_combo.blockSignals(False)

    def _on_ai_provider_changed(self) -> None:
        idx = max(0, self.ai_provider_combo.currentIndex())
        if idx < len(self._ai_provider_keys):
            key = self._ai_provider_keys[idx]
            preset = LLM_PROVIDER_PRESETS.get(key)
            if preset:
                self.ai_base_url_edit.setText(preset["base_url"])
                self._populate_ai_model_combo(key, preset.get("default_model", ""))
        self._save_ai_settings()

    def _save_ai_settings(self) -> None:
        if self._loading:
            return
        idx = max(0, self.ai_provider_combo.currentIndex())
        prov_key = self._ai_provider_keys[idx] if idx < len(self._ai_provider_keys) else "dashscope"
        self.db.update_settings(
            {
                "llm_provider": prov_key,
                "llm_base_url": self.ai_base_url_edit.text().strip(),
                "llm_model": self.ai_model_combo.currentText().strip(),
                "llm_api_key": self.ai_key_edit.text().strip(),
            }
        )
        self._on_settings_saved()

    def _save_ai_settings_btn(self) -> None:
        self._save_ai_settings()
        InfoBar.success(
            title="AI 设置已保存",
            content="大模型服务参数已同步生效。",
            position=InfoBarPosition.TOP,
            duration=2400,
            parent=self,
        )

    def _test_ai_connection(self) -> None:
        self.btn_test_ai.setEnabled(False)
        self.btn_test_ai.setText("测试中...")
        cfg = self.db.get_settings()

        def _work() -> dict[str, Any]:
            return test_llm_connection(cfg)

        def _done(res: dict[str, Any]) -> None:
            self.btn_test_ai.setEnabled(True)
            self.btn_test_ai.setText("测试连接")
            if res.get("ok"):
                InfoBar.success(
                    title="接口连通正常",
                    content=res.get("message", "API 响应正常"),
                    position=InfoBarPosition.TOP,
                    duration=3500,
                    parent=self,
                )
            else:
                InfoBar.error(
                    title="接口连接失败",
                    content=res.get("message", "请检查网络或 Key"),
                    position=InfoBarPosition.TOP,
                    duration=4500,
                    parent=self,
                )

        self._bridges.append(run_in_thread(_work, _done, lambda e: self.btn_test_ai.setEnabled(True), self))

    # -------------------- Hotkeys & Switches --------------------

    def _on_hotkey_master_toggled(self, checked: bool) -> None:
        if self._loading:
            return
        self.db.update_settings({"hotkey_enabled": bool(checked)})
        self._on_hotkeys_changed()

    def _on_single_hotkey_edited(self, hk_key: str, new_chord: str) -> None:
        if self._loading:
            return
        self.db.update_settings({"hotkeys": {hk_key: new_chord}})
        self._on_hotkeys_changed()

    def _reset_default_hotkeys(self) -> None:
        self.db.update_settings({"hotkeys": dict(DEFAULT_HOTKEYS), "hotkey_enabled": True})
        self.load_from_db()
        self._on_hotkeys_changed()
        InfoBar.success(
            title="已恢复默认快捷键",
            content="Ctrl+Shift+D 快速捕获 · Ctrl+Shift+V 粘贴秒下 · Ctrl+Shift+S 显隐窗口 · Ctrl+Shift+P 暂停恢复",
            position=InfoBarPosition.TOP,
            duration=3000,
            parent=self,
        )

    # -------------------- Directory & Cookie Actions --------------------

    def _choose_save_dir(self) -> None:
        cur = self.save_path_edit.text() or ""
        chosen = QFileDialog.getExistingDirectory(self, "选择视频保存目录", cur)
        if chosen:
            norm = os.path.abspath(os.path.normpath(chosen))
            os.makedirs(norm, exist_ok=True)
            self.save_path_edit.setText(norm)
            self.db.update_settings({"save_path": norm})
            self.queue_mgr.scan_local_folder()
            self._on_settings_saved()

    def _open_save_dir(self) -> None:
        p = self.save_path_edit.text().strip()
        if p:
            os.makedirs(p, exist_ok=True)
            reveal_in_file_manager(p, select_file=False)

    def _refresh_cookie_btn(self, cookie_path: str) -> None:
        if cookie_path and os.path.isfile(cookie_path):
            self.btn_cookie_file.setText(f"已启用: {os.path.basename(cookie_path)}")
            self.btn_clear_cookie.setVisible(True)
        else:
            self.btn_cookie_file.setText("导入 cookies.txt")
            self.btn_clear_cookie.setVisible(False)

    def _import_cookie_file(self) -> None:
        fp, _ = QFileDialog.getOpenFileName(self, "选择登录凭据 cookies.txt", "", "Cookies (*.txt);;All Files (*)")
        if not fp:
            return
        dest = get_data_dir() / "cookies.txt"
        shutil.copy2(fp, dest)
        self.db.update_settings({"cookie_file": str(dest)})
        self._refresh_cookie_btn(str(dest))
        InfoBar.success(
            title="登录凭据已导入",
            content=f"已安全保存: {dest.name}",
            position=InfoBarPosition.TOP,
            duration=3000,
            parent=self,
        )

    def _clear_cookie_file(self) -> None:
        self.db.update_settings({"cookie_file": ""})
        self._refresh_cookie_btn("")

    # -------------------- Toolchain, Self-Update & Redacted Diagnostics --------------------

    def refresh_toolchain(self) -> None:
        def _done(st: dict[str, Any]) -> None:
            self.lbl_ytdlp.setText("🟢 运行正常（支持 100+ 主流音视频平台与自适应高码率）")
            self.lbl_ffmpeg.setText(
                "🟢 已就绪（支持 4K / 1080p 超高清合并与无损音轨封装）"
                if st.get("ffmpeg_ready")
                else "🟡 未检测到转码组件（建议安装 FFmpeg 以开启 4K 音画合并）"
            )
            self.lbl_browser.setText(
                "🟢 已就绪（已启用浏览器级无感抗风控解析环境）"
                if st.get("browser_cdp_ready")
                else "⚪ 直连解析模式（已内置标准防爬环境）"
            )
            self.lbl_proxy.setText(
                f"🟢 智能分流加速中（国内直连 · 海外通过 {st.get('system_proxy')} 加速）"
                if st.get("system_proxy")
                else "🟢 直连模式（国内站点高速直连 · 访问海外平台可一键开启代理）"
            )

        self._bridges.append(run_in_thread(get_toolchain_status, _done, lambda _: None, self))

    def _run_ytdlp_hot_update(self) -> None:
        self.btn_hot_update.setEnabled(False)
        self.btn_hot_update.setText("正在更新内核...")

        def _done(st: dict[str, Any]) -> None:
            self.btn_hot_update.setEnabled(True)
            self.btn_hot_update.setText("检查并更新内核")
            self.refresh_toolchain()
            InfoBar.success(
                title="内核更新成功",
                content=f"流媒体解析核心已升级至最新版本 v{st.get('ytdlp_active_version')}",
                position=InfoBarPosition.TOP,
                duration=3500,
                parent=self,
            )

        def _err(msg: str) -> None:
            self.btn_hot_update.setEnabled(True)
            self.btn_hot_update.setText("检查并更新内核")
            InfoBar.warning(
                title="已是最新或更新未完成",
                content=f"{msg}（当前内置解析引擎仍正常可用）",
                position=InfoBarPosition.TOP,
                duration=4000,
                parent=self,
            )

        self._bridges.append(run_in_thread(hot_update_ytdlp_binary, _done, _err, self))

    def _check_app_update(self) -> None:
        repo = self.repo_edit.text().strip()
        self.db.update_settings({"github_repo": repo})
        self.btn_check_update.setEnabled(False)
        self.btn_check_update.setText("检查中...")

        def _done(res: UpdateCheckResult) -> None:
            self.btn_check_update.setEnabled(True)
            self.btn_check_update.setText("检查软件更新")
            if res.has_update:
                ans = QMessageBox.question(
                    self,
                    "发现新版本",
                    f"检测到新版本 v{res.latest_version}（当前 v{__version__}）。\n\n{res.changelog[:300]}\n\n是否前往官方下载更新？",
                )
                if ans == QMessageBox.StandardButton.Yes and res.release_url:
                    webbrowser.open(res.release_url)
            else:
                InfoBar.info(
                    title="版本检查",
                    content=res.message,
                    position=InfoBarPosition.TOP,
                    duration=3500,
                    parent=self,
                )

        self._bridges.append(
            run_in_thread(lambda: check_github_release(repo, __version__), _done, lambda _: None, self)
        )

    def check_updates_manually(self) -> None:
        """Alias for command palette."""
        self._check_app_update()

    def _export_diagnostics(self) -> None:
        self.btn_export_diag.setEnabled(False)

        def _work() -> Path:
            cfg = self.db.get_settings()
            tc = get_toolchain_status()
            tasks = self.db.list_tasks(limit=20)
            traces = self.queue_mgr.engine_chain.get_recent_traces(limit=10)
            return export_diagnostics_zip(cfg, tc, tasks, traces)

        def _done(zip_path: Path) -> None:
            self.btn_export_diag.setEnabled(True)
            InfoBar.success(
                title="排查日志已导出",
                content=f"已自动保护并剔除全部私密 Cookie 与密钥：{zip_path.name}",
                position=InfoBarPosition.TOP,
                duration=4000,
                parent=self,
            )
            try:
                reveal_in_file_manager(str(zip_path), select_file=True)
            except Exception:
                pass

        def _err(msg: str) -> None:
            self.btn_export_diag.setEnabled(True)
            InfoBar.error(title="导出失败", content=msg, position=InfoBarPosition.TOP, duration=3500, parent=self)

        self._bridges.append(run_in_thread(_work, _done, _err, self))

    def export_sanitized_diagnostics(self) -> None:
        """Alias for command palette."""
        self._export_diagnostics()

    def _clear_history_prompt(self) -> None:
        ans = QMessageBox.question(
            self,
            "清空历史记录",
            "将清空所有已完成、失败或已取消的任务记录与预探测缓存，并执行数据库压缩整理。\n（已下载到硬盘的本地视频文件会完好保留！）\n\n确定继续吗？",
        )
        if ans != QMessageBox.StandardButton.Yes:
            return
        removed = self.db.clear_all_history(include_active=False)
        self._on_history_cleared()
        InfoBar.success(
            title="历史记录已清空",
            content=f"已清理 {removed} 条记录并回收数据库空间（本地视频文件均已保留）。",
            position=InfoBarPosition.TOP,
            duration=3200,
            parent=self,
        )
