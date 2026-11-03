"""Page 4: Optional Web & iPhone LAN Interconnection (`ui/web_tools_tab.py` — Fluent UI v3.0, zero emoji)."""

from __future__ import annotations

import contextlib
import io
from typing import Any, Callable
import webbrowser

with contextlib.redirect_stdout(io.StringIO()):
    from qfluentwidgets import (
        CardWidget,
        CheckBox,
        FluentIcon as FIF,
        InfoBar,
        InfoBarPosition,
        PrimaryPushButton,
        PushButton,
    )

from PyQt6.QtCore import Qt
from PyQt6.QtWidgets import QApplication, QHBoxLayout, QLabel, QVBoxLayout, QWidget

from ..core.db import Database
from ..core.web_server_manager import EmbeddedWebServerManager
from .desktop_theme import data_uri_to_pixmap
from .tokens import get_palette


class WebAndToolsTab(QWidget):
    """Controls the optional Web/iPhone LAN Server (never forced on startup)."""

    def __init__(
        self,
        db: Database,
        web_mgr: EmbeddedWebServerManager,
        on_status_changed: Callable[[], None],
        on_internal_copy: Callable[[str], None] | None = None,
        parent: QWidget | None = None,
    ) -> None:
        super().__init__(parent)
        self.setObjectName("lanConnectPage")
        self.db = db
        self.web_mgr = web_mgr
        self.on_status_changed = on_status_changed
        self._on_internal_copy = on_internal_copy
        self._is_dark: bool = self.db.get_settings().get("theme_mode") == "dark"

        self._build_ui()
        self.apply_theme(self._is_dark)
        self.refresh_web_state()

    def _build_ui(self) -> None:
        layout = QVBoxLayout(self)
        layout.setContentsMargins(24, 20, 24, 16)
        layout.setSpacing(16)

        self.web_card = CardWidget(self)
        web_lay = QHBoxLayout(self.web_card)
        web_lay.setContentsMargins(24, 22, 24, 22)
        web_lay.setSpacing(28)

        left_col = QVBoxLayout()
        left_col.setSpacing(12)

        self.title_lbl = QLabel("网页端与 iPhone 局域网互联 (可选服务)")
        left_col.addWidget(self.title_lbl)

        self.desc_lbl = QLabel(
            "桌面客户端已原生内置完整的视频下载、播放器与目录定位能力，启动时绝不强制跳转浏览器。\n"
            "当您需要使用「网页多画面对比播放墙」或「手机扫码投递链接 / 在 iPhone Safari 直接点播电脑已下载视频」时，可按需开启此服务："
        )
        self.desc_lbl.setWordWrap(True)
        left_col.addWidget(self.desc_lbl)

        btn_row = QHBoxLayout()
        btn_row.setSpacing(10)
        self.btn_toggle_web = PrimaryPushButton(FIF.WIFI, "开启多端互联服务", self.web_card)
        self.btn_toggle_web.clicked.connect(self.toggle_web_server)
        btn_row.addWidget(self.btn_toggle_web)

        self.btn_open_browser = PushButton(FIF.GLOBE, "在浏览器中打开", self.web_card)
        self.btn_open_browser.clicked.connect(self.open_web_in_browser)
        btn_row.addWidget(self.btn_open_browser)

        self.btn_copy_lan = PushButton(FIF.LINK, "复制手机局域网地址", self.web_card)
        self.btn_copy_lan.clicked.connect(self._copy_lan_url)
        btn_row.addWidget(self.btn_copy_lan)
        btn_row.addStretch(1)
        left_col.addLayout(btn_row)

        self.chk_auto_start = CheckBox("启动软件时在后台静默开启局域网服务（不自动弹出浏览器）", self.web_card)
        self.chk_auto_start.setChecked(bool(self.db.get_settings().get("web_server_auto_start", False)))
        self.chk_auto_start.toggled.connect(self._on_auto_start_toggled)
        left_col.addWidget(self.chk_auto_start)

        self.web_url_label = QLabel("当前状态：未开启")
        self.web_url_label.setTextInteractionFlags(Qt.TextInteractionFlag.TextSelectableByMouse)
        left_col.addWidget(self.web_url_label)
        left_col.addStretch(1)
        web_lay.addLayout(left_col, 1)

        # QR Code display card
        qr_col = QVBoxLayout()
        self.qr_label = QLabel("开启服务后\n生成扫码二维码")
        self.qr_label.setAlignment(Qt.AlignmentFlag.AlignCenter)
        self.qr_label.setFixedSize(176, 176)
        qr_col.addWidget(self.qr_label, alignment=Qt.AlignmentFlag.AlignCenter)
        web_lay.addLayout(qr_col)

        layout.addWidget(self.web_card)

        # MCP Agent Server info card
        self.mcp_card = CardWidget(self)
        mcp_lay = QVBoxLayout(self.mcp_card)
        mcp_lay.setContentsMargins(24, 18, 24, 18)
        mcp_lay.setSpacing(8)
        self.mcp_title = QLabel("AI Agent 标准协议接入 (Model Context Protocol)")
        mcp_lay.addWidget(self.mcp_title)
        self.mcp_desc = QLabel(
            "本程序同时内置标准 MCP stdio 服务端。在 Claude Desktop、Cursor 或 Windsurf 中配置启动参数 "
            "`VideoDownloader.exe --mcp`，即可赋予 AI 助手视频元数据探测、下载投递与字幕时间轴提取能力。"
        )
        self.mcp_desc.setWordWrap(True)
        mcp_lay.addWidget(self.mcp_desc)
        layout.addWidget(self.mcp_card)

        layout.addStretch(1)

    def apply_theme(self, is_dark: bool) -> None:
        self._is_dark = is_dark
        pal = get_palette(is_dark)
        self.title_lbl.setStyleSheet(f"font-size: 16px; font-weight: 700; color: {pal['text_primary']};")
        self.mcp_title.setStyleSheet(f"font-size: 15px; font-weight: 700; color: {pal['text_primary']};")
        self.desc_lbl.setStyleSheet(f"color: {pal['text_secondary']}; font-size: 13px;")
        self.mcp_desc.setStyleSheet(f"color: {pal['text_secondary']}; font-size: 13px;")
        self.web_url_label.setStyleSheet(f"color: {pal['accent']}; font-weight: 600; font-size: 13px;")
        self.qr_label.setStyleSheet(
            f"background: {pal['bg_trace']}; border: 1px dashed {pal['border_strong']}; "
            f"border-radius: 12px; color: {pal['text_muted']}; font-size: 12px;"
        )

    def _on_auto_start_toggled(self, checked: bool) -> None:
        self.db.update_settings({"web_server_auto_start": bool(checked)})

    def toggle_web_server(self) -> None:
        if self.web_mgr.is_running:
            self.web_mgr.stop()
        else:
            self.web_mgr.start()
        self.refresh_web_state()
        self.on_status_changed()

    def open_web_in_browser(self) -> None:
        if not self.web_mgr.is_running:
            self.web_mgr.start()
            self.refresh_web_state()
            self.on_status_changed()
        webbrowser.open(self.web_mgr.local_url)

    def _copy_lan_url(self) -> None:
        if not self.web_mgr.is_running:
            InfoBar.info(
                title="服务未开启",
                content="请先点击「开启多端互联服务」。",
                position=InfoBarPosition.TOP,
                duration=2500,
                parent=self,
            )
            return
        lan = self.web_mgr.get_lan_info()
        url = lan.get("primary_url") or self.web_mgr.local_url
        if self._on_internal_copy:
            self._on_internal_copy(url)
        QApplication.clipboard().setText(url)
        InfoBar.success(
            title="已复制局域网地址",
            content=f"{url}（手机连接同一 Wi-Fi 即可扫码或粘贴访问）",
            position=InfoBarPosition.TOP,
            duration=3200,
            parent=self,
        )

    def refresh_web_state(self) -> None:
        if self.web_mgr.is_running:
            lan: dict[str, Any] = self.web_mgr.get_lan_info()
            primary_lan = lan.get("primary_url") or self.web_mgr.local_url
            self.btn_toggle_web.setIcon(FIF.CLOSE)
            self.btn_toggle_web.setText("停止互联服务")
            self.btn_copy_lan.setEnabled(True)
            self.web_url_label.setText(f"服务运行中  |  本机: {self.web_mgr.local_url}   |   手机局域网: {primary_lan}")
            eps = lan.get("endpoints") or []
            if eps and eps[0].get("qr_data_uri"):
                pix = data_uri_to_pixmap(eps[0]["qr_data_uri"])
                if not pix.isNull():
                    self.qr_label.setPixmap(
                        pix.scaled(160, 160, Qt.AspectRatioMode.KeepAspectRatio, Qt.TransformationMode.SmoothTransformation)
                    )
        else:
            self.btn_toggle_web.setIcon(FIF.WIFI)
            self.btn_toggle_web.setText("开启多端互联服务")
            self.btn_copy_lan.setEnabled(False)
            self.web_url_label.setText("当前状态：未开启（桌面客户端独立运行中，按需启用）")
            self.qr_label.setPixmap(data_uri_to_pixmap(""))
            self.qr_label.setText("开启服务后\n生成扫码二维码")
