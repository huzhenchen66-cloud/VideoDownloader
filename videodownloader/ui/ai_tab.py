"""Page 3: AI Video Copilot (Storyboard Generation, Script Extraction, LLM Timeline Summary & Interactive Video Q&A)."""

from __future__ import annotations

import contextlib
import io
import os
from pathlib import Path
from typing import Any, Callable

with contextlib.redirect_stdout(io.StringIO()):
    from qfluentwidgets import (
        CardWidget,
        ComboBox,
        EditableComboBox,
        FluentIcon as FIF,
        InfoBar,
        InfoBarPosition,
        LineEdit,
        PrimaryPushButton,
        PushButton,
    )

from PyQt6.QtCore import Qt
from PyQt6.QtWidgets import (
    QApplication,
    QDialog,
    QDialogButtonBox,
    QFileDialog,
    QHBoxLayout,
    QLabel,
    QLineEdit,
    QListWidget,
    QListWidgetItem,
    QPlainTextEdit,
    QSplitter,
    QTextBrowser,
    QVBoxLayout,
    QWidget,
)

from ..ai.llm_service import (
    LLM_PROVIDER_PRESETS,
    ask_video_question,
    extract_clean_script,
    generate_storyboard_script,
    summarize_video_transcript,
    test_llm_connection,
)
from ..ai.transcript import extract_transcript_for_task, parse_raw_text_to_segments
from ..core.db import Database
from .desktop_theme import run_in_thread
from .tokens import get_palette


class AICopilotTab(QWidget):
    """Native Desktop AI Video Knowledge & Storyboard Copilot."""

    def __init__(self, db: Database, on_seek_video: Callable[[str, float], None], parent: QWidget | None = None) -> None:
        super().__init__(parent)
        self.setObjectName("aiCopilotPage")
        self.db = db
        self.on_seek_video = on_seek_video
        self._bridges: list[Any] = []
        self._chat_history: dict[str, list[dict[str, str]]] = {}
        self._task_ids: list[str] = []
        self._current_markdown: str = ""
        self._is_dark: bool = self.db.get_settings().get("theme_mode") == "dark"

        self._build_ui()
        self.load_llm_settings()
        self.apply_theme(self._is_dark)

    def _build_ui(self) -> None:
        layout = QVBoxLayout(self)
        layout.setContentsMargins(24, 18, 24, 16)
        layout.setSpacing(12)

        # -------------------- Top Action Toolbar --------------------
        self.top_card = CardWidget(self)
        top_row = QHBoxLayout(self.top_card)
        top_row.setContentsMargins(16, 12, 16, 12)
        top_row.setSpacing(10)

        self.lbl_select = QLabel("选择视频:")
        top_row.addWidget(self.lbl_select)

        self.video_combo = ComboBox(self.top_card)
        self.video_combo.setMinimumWidth(260)
        self.video_combo.currentIndexChanged.connect(self._on_video_selected)
        top_row.addWidget(self.video_combo, 1)

        self.btn_storyboard = PrimaryPushButton(FIF.VIDEO, "拆解分镜脚本", self.top_card)
        self.btn_storyboard.setToolTip("调用 AI 大模型拆解影视级分镜脚本（含景别/运镜/构图/口播/BGM）")
        self.btn_storyboard.clicked.connect(self._run_storyboard)
        top_row.addWidget(self.btn_storyboard)

        self.btn_clean_script = PushButton(FIF.FONT_SIZE, "提取文案与语种", self.top_card)
        self.btn_clean_script.setToolTip("识别视频语言，剔除口癖语气词并提炼爆款文案金句")
        self.btn_clean_script.clicked.connect(self._run_clean_script)
        top_row.addWidget(self.btn_clean_script)

        self.btn_summarize = PushButton(FIF.CHAT, "章节速读与要点", self.top_card)
        self.btn_summarize.setToolTip("生成时间轴章节导读与核心知识点")
        self.btn_summarize.clicked.connect(self._run_summary)
        top_row.addWidget(self.btn_summarize)

        self.btn_custom_script = PushButton(FIF.EDIT, "粘贴/编辑台词", self.top_card)
        self.btn_custom_script.setToolTip("为无字幕视频手动补充口播台词或脚本")
        self.btn_custom_script.clicked.connect(self._open_custom_script_dialog)
        top_row.addWidget(self.btn_custom_script)

        layout.addWidget(self.top_card)

        # -------------------- Inline AI Service Strip --------------------
        self.cfg_card = CardWidget(self)
        cfg_row = QHBoxLayout(self.cfg_card)
        cfg_row.setContentsMargins(16, 10, 16, 10)
        cfg_row.setSpacing(10)

        self.lbl_provider = QLabel("AI 服务:")
        cfg_row.addWidget(self.lbl_provider)

        self._provider_keys: list[str] = list(LLM_PROVIDER_PRESETS.keys())
        self.provider_combo = ComboBox(self.cfg_card)
        for key in self._provider_keys:
            self.provider_combo.addItem(LLM_PROVIDER_PRESETS[key]["name"])
        self.provider_combo.currentIndexChanged.connect(self._on_provider_changed)
        cfg_row.addWidget(self.provider_combo)

        self.base_url_edit = LineEdit(self.cfg_card)
        self.base_url_edit.setPlaceholderText("API Base URL")
        cfg_row.addWidget(self.base_url_edit, 1)

        self.model_combo = EditableComboBox(self.cfg_card)
        self.model_combo.setMinimumWidth(210)
        self.model_combo.setPlaceholderText("选择或输入模型...")
        self.model_edit = self.model_combo  # Backward-compatibility alias
        cfg_row.addWidget(self.model_combo)

        self.api_key_edit = LineEdit(self.cfg_card)
        self.api_key_edit.setEchoMode(QLineEdit.EchoMode.Password)
        self.api_key_edit.setPlaceholderText("API Key (留空启用离线规则)")
        cfg_row.addWidget(self.api_key_edit, 1)

        self.btn_test_conn = PushButton(FIF.SPEED_HIGH, "测试连接", self.cfg_card)
        self.btn_test_conn.clicked.connect(self._test_connection)
        cfg_row.addWidget(self.btn_test_conn)

        btn_save_cfg = PushButton(FIF.SAVE, "保存配置", self.cfg_card)
        btn_save_cfg.clicked.connect(self.save_llm_settings)
        cfg_row.addWidget(btn_save_cfg)
        layout.addWidget(self.cfg_card)

        # -------------------- Main Splitter --------------------
        splitter = QSplitter(Qt.Orientation.Horizontal)

        # Left Card: Timestamped Transcript & Shot Lines
        self.left_box = CardWidget(self)
        left_lay = QVBoxLayout(self.left_box)
        left_lay.setContentsMargins(16, 14, 16, 14)
        left_lay.setSpacing(8)

        left_hdr = QHBoxLayout()
        self.lbl_timeline = QLabel("台词与时间轴 (双击跳转原片)")
        left_hdr.addWidget(self.lbl_timeline)
        left_hdr.addStretch(1)

        self.lbl_source_badge = QLabel("")
        self.lbl_source_badge.setStyleSheet("font-size: 11px; opacity: 0.8;")
        left_hdr.addWidget(self.lbl_source_badge)
        left_lay.addLayout(left_hdr)

        self.transcript_list = QListWidget()
        self.transcript_list.itemDoubleClicked.connect(self._on_transcript_item_activated)
        left_lay.addWidget(self.transcript_list, 1)
        splitter.addWidget(self.left_box)

        # Right Card: Storyboard, Script & Q&A
        self.right_box = CardWidget(self)
        right_lay = QVBoxLayout(self.right_box)
        right_lay.setContentsMargins(16, 14, 16, 14)
        right_lay.setSpacing(8)

        right_hdr = QHBoxLayout()
        self.lbl_notes = QLabel("影视分镜与精读工作台")
        right_hdr.addWidget(self.lbl_notes)
        right_hdr.addStretch(1)

        self.btn_export_md = PushButton(FIF.DOWNLOAD, "导出分镜脚本 (.md)", self.right_box)
        self.btn_export_md.clicked.connect(self._export_markdown)
        right_hdr.addWidget(self.btn_export_md)

        self.btn_copy_text = PushButton(FIF.COPY, "复制文本", self.right_box)
        self.btn_copy_text.clicked.connect(self._copy_text)
        right_hdr.addWidget(self.btn_copy_text)
        right_lay.addLayout(right_hdr)

        self.notes_browser = QTextBrowser()
        self.notes_browser.setOpenExternalLinks(True)
        right_lay.addWidget(self.notes_browser, 1)

        # Interactive Q&A row
        qa_row = QHBoxLayout()
        qa_row.setSpacing(8)
        self.qa_input = LineEdit(self.right_box)
        self.qa_input.setPlaceholderText("就视频内容或分镜向 AI 提问或要求修改（按 Enter 发送）...")
        self.qa_input.returnPressed.connect(self._ask_question)
        qa_row.addWidget(self.qa_input, 1)

        self.btn_ask = PrimaryPushButton(FIF.SEND, "发送", self.right_box)
        self.btn_ask.clicked.connect(self._ask_question)
        qa_row.addWidget(self.btn_ask)
        right_lay.addLayout(qa_row)

        splitter.addWidget(self.right_box)
        splitter.setStretchFactor(0, 2)
        splitter.setStretchFactor(1, 4)
        layout.addWidget(splitter, 1)

    def apply_theme(self, is_dark: bool) -> None:
        self._is_dark = is_dark
        pal = get_palette(is_dark)
        for lbl in (self.lbl_select, self.lbl_provider, self.lbl_timeline, self.lbl_notes):
            lbl.setStyleSheet(f"color: {pal['text_primary']}; font-weight: 600; font-size: 13px;")

        pane_qss = (
            f"background-color: {pal['bg_trace']}; color: {pal['text_primary']}; "
            f"border: 1px solid {pal['border']}; border-radius: 8px; padding: 8px; line-height: 1.5;"
        )
        self.transcript_list.setStyleSheet(pane_qss)
        self.notes_browser.setStyleSheet(pane_qss)
        self._on_video_selected()

    def _populate_model_combo(self, provider_key: str, selected_model: str = "") -> None:
        self.model_combo.blockSignals(True)
        self.model_combo.clear()
        preset = LLM_PROVIDER_PRESETS.get(provider_key, {})
        models = preset.get("models") or [preset.get("default_model", "deepseek-v4.1-flash-1017")]
        for m in models:
            self.model_combo.addItem(m)
        target = selected_model or preset.get("default_model", "")
        if target:
            if target not in models:
                self.model_combo.insertItem(0, target)
            self.model_combo.setCurrentText(target)
        self.model_combo.blockSignals(False)

    def load_llm_settings(self) -> None:
        cfg = self.db.get_settings()
        prov = cfg.get("llm_provider", "dashscope")
        if prov in self._provider_keys:
            self.provider_combo.blockSignals(True)
            self.provider_combo.setCurrentIndex(self._provider_keys.index(prov))
            self.provider_combo.blockSignals(False)
        preset = LLM_PROVIDER_PRESETS.get(prov, LLM_PROVIDER_PRESETS.get("dashscope", {}))
        self.base_url_edit.setText(cfg.get("llm_base_url") or preset.get("base_url", ""))
        self._populate_model_combo(prov, cfg.get("llm_model") or preset.get("default_model", ""))
        self.api_key_edit.setText(cfg.get("llm_api_key", ""))

    def _current_provider_key(self) -> str:
        idx = self.provider_combo.currentIndex()
        if 0 <= idx < len(self._provider_keys):
            return self._provider_keys[idx]
        return "dashscope"

    def _on_provider_changed(self) -> None:
        key = self._current_provider_key()
        preset = LLM_PROVIDER_PRESETS.get(key)
        if preset:
            self.base_url_edit.setText(preset["base_url"])
            self._populate_model_combo(key, preset.get("default_model", ""))

    def save_llm_settings(self) -> None:
        self.db.update_settings(
            {
                "llm_provider": self._current_provider_key(),
                "llm_base_url": self.base_url_edit.text().strip(),
                "llm_model": self.model_combo.currentText().strip(),
                "llm_api_key": self.api_key_edit.text().strip(),
            }
        )
        InfoBar.success(
            title="AI 配置已保存",
            content="大模型服务参数已同步更新。",
            position=InfoBarPosition.TOP,
            duration=2400,
            parent=self,
        )

    def _test_connection(self) -> None:
        self.btn_test_conn.setEnabled(False)
        self.btn_test_conn.setText("测试中...")
        cfg = {
            "llm_provider": self._current_provider_key(),
            "llm_base_url": self.base_url_edit.text().strip(),
            "llm_model": self.model_combo.currentText().strip(),
            "llm_api_key": self.api_key_edit.text().strip(),
        }

        def _work() -> dict[str, Any]:
            return test_llm_connection(cfg)

        def _done(res: dict[str, Any]) -> None:
            self.btn_test_conn.setEnabled(True)
            self.btn_test_conn.setText("测试连接")
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
                    content=res.get("message", "请检查网络、Base URL 或 API Key"),
                    position=InfoBarPosition.TOP,
                    duration=4500,
                    parent=self,
                )

        self._bridges.append(run_in_thread(_work, _done, lambda e: self.btn_test_conn.setEnabled(True), self))

    def refresh_completed_tasks(self, completed_tasks: list[dict[str, Any]], select_task_id: str = "") -> None:
        cur_id = select_task_id or self._current_task_id()
        self.video_combo.blockSignals(True)
        self.video_combo.clear()
        self._task_ids = []
        for t in completed_tasks:
            self._task_ids.append(t["id"])
            plat = f"[{t.get('platform_name', '')}] " if t.get("platform_name") else ""
            self.video_combo.addItem(f"{plat}{t.get('title', '视频')[:52]}")
        if cur_id and cur_id in self._task_ids:
            self.video_combo.setCurrentIndex(self._task_ids.index(cur_id))
        self.video_combo.blockSignals(False)
        self._on_video_selected()

    def _current_task_id(self) -> str:
        idx = self.video_combo.currentIndex()
        if 0 <= idx < len(self._task_ids):
            return self._task_ids[idx]
        return ""

    def _current_task(self) -> dict[str, Any] | None:
        tid = self._current_task_id()
        return self.db.get_task(tid) if tid else None

    def _on_video_selected(self) -> None:
        pal = get_palette(self._is_dark)
        task = self._current_task()
        self.transcript_list.clear()
        self.lbl_source_badge.setText("")
        if not task:
            self.notes_browser.setHtml(f"<p style='color:{pal['text_muted']};'>请先下载视频或从上方下拉框选择媒体。</p>")
            self._current_markdown = ""
            return

        tr = task.get("transcript")
        if tr and tr.get("segments"):
            self._populate_transcript(tr)
        else:
            # Auto-extract transcript or fallback segments
            cfg = self.db.get_settings()
            try:
                tr = extract_transcript_for_task(task, cfg)
                self.db.update_task(task["id"], transcript=tr)
                self._populate_transcript(tr)
            except Exception:
                pass

        # Load persisted storyboard, clean script, or summary
        if task.get("ai_storyboard"):
            sb = task["ai_storyboard"]
            self._current_markdown = sb.get("markdown", "")
            self.notes_browser.setMarkdown(self._current_markdown)
        elif task.get("ai_clean_script"):
            cs = task["ai_clean_script"]
            self._current_markdown = cs.get("markdown", "")
            self.notes_browser.setMarkdown(self._current_markdown)
        elif task.get("ai_summary"):
            self._render_summary_and_chat(task["id"], task["ai_summary"])
        else:
            self.notes_browser.setHtml(
                f"<div style='color:{pal['text_secondary']};line-height:1.6;padding:12px;'>"
                f"<h3 style='color:{pal['text_primary']};margin-top:0;'>✨ 欢迎使用 AI 视频分镜与精读工作台</h3>"
                f"<p>当前选中：《<b>{task.get('title', '视频')}</b>》</p>"
                f"<ul>"
                f"<li>点击 <b>🎬 拆解分镜脚本</b>：AI 自动生成专业影视级分镜表格（镜号/景别/运镜/画面构图/台词/BGM）</li>"
                f"<li>点击 <b>📝 提取文案与语种</b>：精准识别语种，去口癖并整理完整文案逐字稿与金句</li>"
                f"<li>点击 <b>📑 章节速读与要点</b>：生成时间轴结构化概览</li>"
                f"<li>若该视频无字幕，可点击 <b>✍️ 粘贴/编辑台词</b> 手动补充文案，系统将自动拆解分镜</li>"
                f"</ul>"
                f"</div>"
            )

    def _populate_transcript(self, transcript: dict[str, Any]) -> None:
        self.transcript_list.clear()
        src = transcript.get("source", "")
        note = transcript.get("note", "")
        src_map = {
            "local_subtitle": "本地外挂字幕",
            "companion_subtitle": "伴随字幕文件",
            "remote_subtitle": "官方/自动字幕",
            "cloud_asr": "AI 语音转写",
            "user_custom": "用户自定义文案",
            "metadata_description": "视频文案自动提取",
            "metadata_title": "视频主题推演",
        }
        badge_text = src_map.get(src, "字幕已载入")
        segs = transcript.get("segments") or []
        self.lbl_source_badge.setText(f"来源: {badge_text} ({len(segs)} 段)")

        for seg in segs:
            item = QListWidgetItem(f"[{seg.get('time_str', '00:00')}]  {seg.get('text', '')}")
            item.setData(Qt.ItemDataRole.UserRole, float(seg.get("start") or 0.0))
            self.transcript_list.addItem(item)

    def _on_transcript_item_activated(self, item: QListWidgetItem) -> None:
        task = self._current_task()
        if not task:
            return
        sec = float(item.data(Qt.ItemDataRole.UserRole) or 0.0)
        self.on_seek_video(task["id"], sec)

    def _run_storyboard(self) -> None:
        task = self._current_task()
        if not task:
            return
        self.save_llm_settings()
        cfg = self.db.get_settings()
        self.btn_storyboard.setEnabled(False)
        self.btn_storyboard.setText("分镜生成中...")

        def _work() -> dict[str, Any]:
            tr = task.get("transcript")
            if not tr or not tr.get("segments"):
                tr = extract_transcript_for_task(task, cfg)
                self.db.update_task(task["id"], transcript=tr)
            sb = generate_storyboard_script(task.get("title", "短视频"), tr.get("segments", []), cfg)
            self.db.update_task(task["id"], ai_storyboard=sb)
            return sb

        def _done(sb: dict[str, Any]) -> None:
            self.btn_storyboard.setEnabled(True)
            self.btn_storyboard.setText("拆解分镜脚本")
            self._current_markdown = sb.get("markdown", "")
            self.notes_browser.setMarkdown(self._current_markdown)
            InfoBar.success(
                title="分镜脚本拆解完成",
                content=f"已成功生成 {sb.get('shots_count', 0)} 镜专业分镜报告（由 {sb.get('model', '')} 驱动）",
                position=InfoBarPosition.TOP,
                duration=3500,
                parent=self,
            )

        def _err(msg: str) -> None:
            self.btn_storyboard.setEnabled(True)
            self.btn_storyboard.setText("拆解分镜脚本")
            InfoBar.error(title="分镜生成失败", content=msg, position=InfoBarPosition.TOP, duration=4500, parent=self)

        self._bridges.append(run_in_thread(_work, _done, _err, self))

    def _run_clean_script(self) -> None:
        task = self._current_task()
        if not task:
            return
        self.save_llm_settings()
        cfg = self.db.get_settings()
        self.btn_clean_script.setEnabled(False)
        self.btn_clean_script.setText("提取中...")

        def _work() -> dict[str, Any]:
            tr = task.get("transcript")
            if not tr or not tr.get("segments"):
                tr = extract_transcript_for_task(task, cfg)
                self.db.update_task(task["id"], transcript=tr)
            cs = extract_clean_script(task.get("title", "短视频"), tr.get("segments", []), cfg)
            self.db.update_task(task["id"], ai_clean_script=cs)
            return cs

        def _done(cs: dict[str, Any]) -> None:
            self.btn_clean_script.setEnabled(True)
            self.btn_clean_script.setText("提取文案与语种")
            self._current_markdown = cs.get("markdown", "")
            self.notes_browser.setMarkdown(self._current_markdown)
            InfoBar.success(
                title="纯净文案提炼完成",
                content="已成功识别语种并整理逐字稿与金句。",
                position=InfoBarPosition.TOP,
                duration=3000,
                parent=self,
            )

        def _err(msg: str) -> None:
            self.btn_clean_script.setEnabled(True)
            self.btn_clean_script.setText("提取文案与语种")
            InfoBar.error(title="文案提取失败", content=msg, position=InfoBarPosition.TOP, duration=4000, parent=self)

        self._bridges.append(run_in_thread(_work, _done, _err, self))

    def _run_summary(self) -> None:
        task = self._current_task()
        if not task:
            return
        self.save_llm_settings()
        cfg = self.db.get_settings()
        self.btn_summarize.setEnabled(False)
        self.btn_summarize.setText("分析中...")

        def _work() -> tuple[dict[str, Any], dict[str, Any]]:
            tr = task.get("transcript")
            if not tr or not tr.get("segments"):
                tr = extract_transcript_for_task(task, cfg)
            summary = summarize_video_transcript(task.get("title", "视频"), tr["segments"], cfg)
            self.db.update_task(task["id"], transcript=tr, ai_summary=summary)
            return tr, summary

        def _done(pair: tuple[dict[str, Any], dict[str, Any]]) -> None:
            self.btn_summarize.setEnabled(True)
            self.btn_summarize.setText("章节速读与要点")
            tr, summary = pair
            self._populate_transcript(tr)
            self._render_summary_and_chat(task["id"], summary)

        def _err(msg: str) -> None:
            self.btn_summarize.setEnabled(True)
            self.btn_summarize.setText("章节速读与要点")
            InfoBar.error(title="摘要生成失败", content=msg, position=InfoBarPosition.TOP, duration=4000, parent=self)

        self._bridges.append(run_in_thread(_work, _done, _err, self))

    def _render_summary_and_chat(self, task_id: str, summary: dict[str, Any] | None) -> None:
        pal = get_palette(self._is_dark)
        parts: list[str] = []
        if summary:
            tags = " ".join(f"<code>#{t}</code>" for t in (summary.get("tags") or []))
            parts.append(
                f"<p style='color:{pal['text_muted']};font-size:12px;'>驱动引擎: <b>{summary.get('model', '')}</b> {tags}</p>"
            )
            parts.append(
                f"<div style='background:{pal['accent_bg']};color:{pal['text_primary']};padding:10px;border-radius:8px;line-height:1.6;'>"
                f"<b>核心摘要：</b>{summary.get('overview', '')}</div>"
            )
            chapters = summary.get("chapters") or []
            if chapters:
                parts.append(f"<h4 style='color:{pal['text_primary']};'>时间轴章节导读</h4><ul>")
                for ch in chapters:
                    parts.append(
                        f"<li><b>[{ch.get('time_str', '00:00')}] {ch.get('title', '')}</b> — {ch.get('summary', '')}</li>"
                    )
                parts.append("</ul>")
            pts = summary.get("key_points") or []
            if pts:
                parts.append(f"<h4 style='color:{pal['text_primary']};'>关键知识要点</h4><ul>")
                for p in pts:
                    parts.append(f"<li>{p}</li>")
                parts.append("</ul>")

        hist = self._chat_history.get(task_id) or []
        if hist:
            parts.append(f"<hr/><h4 style='color:{pal['text_primary']};'>视频与分镜问答记录</h4>")
            for m in hist:
                role_label = "提问" if m["role"] == "user" else "解答"
                parts.append(f"<p><b>[{role_label}]</b> {m['content']}</p>")

        self.notes_browser.setHtml("".join(parts) or "<p>暂无笔记</p>")

    def _open_custom_script_dialog(self) -> None:
        task = self._current_task()
        if not task:
            InfoBar.warning(title="未选择视频", content="请先选择需要补充台词的视频任务。", parent=self)
            return

        dialog = QDialog(self)
        dialog.setWindowTitle(f"补充/编辑视频台词文案 - 《{task.get('title', '')[:20]}》")
        dialog.resize(560, 420)
        d_lay = QVBoxLayout(dialog)

        tip = QLabel("可在此粘贴视频的旁白口播、台词文本或简介，系统将自动划分为时间轴，并以此拆解分镜：")
        tip.setWordWrap(True)
        d_lay.addWidget(tip)

        text_edit = QPlainTextEdit(dialog)
        # Prefill with current custom script or description
        current_script = task.get("custom_script") or ""
        if not current_script and task.get("transcript"):
            current_script = "\n".join(s.get("text", "") for s in task["transcript"].get("segments", []))
        if not current_script:
            current_script = task.get("description") or ""
        text_edit.setPlainText(current_script)
        d_lay.addWidget(text_edit, 1)

        btns = QDialogButtonBox(QDialogButtonBox.StandardButton.Ok | QDialogButtonBox.StandardButton.Cancel, dialog)
        btns.accepted.connect(dialog.accept)
        btns.rejected.connect(dialog.reject)
        d_lay.addWidget(btns)

        if dialog.exec() == QDialog.DialogCode.Accepted:
            raw_text = text_edit.toPlainText().strip()
            if raw_text:
                segs = parse_raw_text_to_segments(raw_text, float(task.get("duration") or 0.0))
                tr = {"source": "user_custom", "segments": segs, "note": "用户手动补充台词"}
                self.db.update_task(task["id"], custom_script=raw_text, transcript=tr)
                self._populate_transcript(tr)
                InfoBar.success(
                    title="台词已更新",
                    content=f"已成功解析 {len(segs)} 条时间轴台词！可点击【拆解分镜脚本】生成全新分镜。",
                    position=InfoBarPosition.TOP,
                    duration=3500,
                    parent=self,
                )

    def _export_markdown(self) -> None:
        task = self._current_task()
        text = self._current_markdown or self.notes_browser.toPlainText()
        if not text:
            InfoBar.warning(title="无内容可导出", content="请先生成分镜脚本或文案再进行导出。", parent=self)
            return
        default_name = f"{task.get('title', '视频分镜')[:30]}_分镜脚本.md" if task else "分镜脚本.md"
        fp, _ = QFileDialog.getSaveFileName(self, "导出分镜脚本为 Markdown", default_name, "Markdown (*.md);;Text (*.txt)")
        if fp:
            try:
                with open(fp, "w", encoding="utf-8") as f:
                    f.write(text)
                InfoBar.success(
                    title="导出成功",
                    content=f"分镜脚本已保存至 {Path(fp).name}",
                    position=InfoBarPosition.TOP,
                    duration=3000,
                    parent=self,
                )
            except Exception as exc:
                InfoBar.error(title="保存失败", content=str(exc), parent=self)

    def _copy_text(self) -> None:
        text = self._current_markdown or self.notes_browser.toPlainText()
        if text:
            clipboard = QApplication.clipboard()
            if clipboard:
                clipboard.setText(text)
            InfoBar.success(title="已复制", content="文本已复制到系统剪贴板。", position=InfoBarPosition.TOP, duration=2000, parent=self)

    def _ask_question(self) -> None:
        task = self._current_task()
        q = self.qa_input.text().strip()
        if not task or not q:
            return
        self.qa_input.clear()
        tid = task["id"]
        hist = self._chat_history.setdefault(tid, [])
        hist.append({"role": "user", "content": q})
        self._render_summary_and_chat(tid, task.get("ai_summary"))
        self.btn_ask.setEnabled(False)
        cfg = self.db.get_settings()

        def _work() -> str:
            tr = task.get("transcript")
            if not tr or not tr.get("segments"):
                tr = extract_transcript_for_task(task, cfg)
                self.db.update_task(tid, transcript=tr)
            res = ask_video_question(task.get("title", "视频"), tr["segments"], q, cfg, hist[:-1])
            return str(res.get("answer") or "")

        def _done(ans: str) -> None:
            self.btn_ask.setEnabled(True)
            hist.append({"role": "assistant", "content": ans})
            latest = self.db.get_task(tid) or task
            self._render_summary_and_chat(tid, latest.get("ai_summary"))

        def _err(msg: str) -> None:
            self.btn_ask.setEnabled(True)
            hist.append({"role": "assistant", "content": f"[错误] {msg}"})
            self._render_summary_and_chat(tid, task.get("ai_summary"))

        self._bridges.append(run_in_thread(_work, _done, _err, self))
