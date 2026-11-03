"""Tests for AI Storyboard Script Generation, Metadata Transcript Fallback & Refactored Settings UI."""

from __future__ import annotations

import os
from pathlib import Path
import pytest

if os.path.isdir(r"C:\Windows\Fonts"):
    os.environ.setdefault("QT_QPA_FONTDIR", r"C:\Windows\Fonts")
os.environ.setdefault("QT_QPA_PLATFORM", "offscreen")

from PyQt6.QtWidgets import QApplication

from videodownloader.ai.llm_service import (
    LLM_PROVIDER_PRESETS,
    extract_clean_script,
    generate_storyboard_script,
    test_llm_connection as ping_llm_connection,
)
from videodownloader.ai.transcript import extract_transcript_for_task, parse_raw_text_to_segments
from videodownloader.core.db import Database
from videodownloader.core.task_queue import TaskQueueManager
from videodownloader.desktop_app import MainWindow
from videodownloader.ui.ai_tab import AICopilotTab
from videodownloader.ui.settings_page import SettingsAndDiagnosticsPage


def _get_qapp() -> QApplication:
    return QApplication.instance() or QApplication([])


def test_ping_llm_connection_handles_missing_key():
    res = ping_llm_connection({"llm_api_key": "", "llm_provider": "dashscope"})
    assert res["ok"] is False
    assert "连接失败" in res["message"]


def test_parse_raw_text_to_segments():
    # 1. Plain sentences
    raw = "第一句话。第二句话！第三句话？"
    segs = parse_raw_text_to_segments(raw, total_duration=30.0)
    assert len(segs) == 3
    assert segs[0]["text"] == "第一句话"
    assert segs[0]["start"] == 0.0
    assert segs[2]["end"] == 30.0

    # 2. Bracketed timestamps
    timed = "[00:00] 开头介绍\n[00:05] 中间操作步骤\n[00:18] 结尾互动"
    segs_t = parse_raw_text_to_segments(timed)
    assert len(segs_t) == 3
    assert segs_t[0]["text"] == "开头介绍"
    assert segs_t[1]["start"] == 5.0
    assert segs_t[2]["time_str"] == "00:18"


def test_extract_transcript_metadata_fallback_no_crash():
    # Video task with no subtitle file and no ASR key
    task_desc = {
        "id": "t1",
        "title": "超好喝生椰拿铁做法",
        "description": "今天教大家在家做生椰拿铁。准备一杯冰块倒入厚椰乳。淋上浓缩咖啡即可！",
        "duration": 25.0,
        "platform_name": "小红书",
    }
    res = extract_transcript_for_task(task_desc, settings={})
    assert res["is_fallback"] is True
    assert res["source"] == "metadata_description"
    assert len(res["segments"]) >= 3

    # Video task with empty description
    task_empty = {
        "id": "t2",
        "title": "风景延时摄影",
        "description": "",
        "duration": 15.0,
    }
    res_empty = extract_transcript_for_task(task_empty, settings={})
    assert res_empty["is_fallback"] is True
    assert res_empty["source"] == "metadata_title"
    assert len(res_empty["segments"]) == 1


def test_storyboard_script_offline_fallback():
    segs = [
        {"start": 0.0, "end": 5.0, "time_str": "00:00", "text": "第一步萃取咖啡"},
        {"start": 5.0, "end": 12.0, "time_str": "00:05", "text": "第二步加入椰乳"},
    ]
    sb = generate_storyboard_script("咖啡教程", segs, settings={"llm_api_key": ""})
    assert sb["mode"] == "extractive_offline"
    assert sb["shots_count"] >= 1
    assert "分镜脚本" in sb["markdown"]
    assert "景别" in sb["markdown"]


def test_extract_clean_script_offline_fallback():
    segs = [
        {"start": 0.0, "end": 4.0, "time_str": "00:00", "text": "那个嗯今天分享这个教程"},
    ]
    cs = extract_clean_script("测试视频", segs, settings={"llm_api_key": ""})
    assert cs["mode"] == "extractive_offline"
    assert "纯净文案" in cs["markdown"]


def test_ai_tab_and_settings_page_instantiation(tmp_path: Path):
    _get_qapp()
    db = Database(tmp_path / "test.db")
    qm = TaskQueueManager(db=db)

    ai_tab = AICopilotTab(db=db, on_seek_video=lambda tid, s: None)
    assert ai_tab.video_combo is not None
    assert "dashscope" in ai_tab._provider_keys

    settings_page = SettingsAndDiagnosticsPage(
        db=db,
        queue_mgr=qm,
        on_theme_changed=lambda is_d, st=None: None,
        on_hotkeys_changed=lambda: None,
        on_settings_saved=lambda: None,
        on_history_cleared=lambda: None,
    )
    assert settings_page.theme_combo is not None
    assert settings_page.ai_provider_combo is not None
    assert hasattr(settings_page, "export_sanitized_diagnostics")
    assert hasattr(settings_page, "check_updates_manually")


def test_claude_and_custom_provider_presets():
    assert "claude" in LLM_PROVIDER_PRESETS
    assert "custom" in LLM_PROVIDER_PRESETS
    dashscope_models = LLM_PROVIDER_PRESETS["dashscope"]["models"]
    assert "deepseek-v4.1-flash-1017" in dashscope_models
    assert "deepseek-v4-pro-0813" in dashscope_models
    assert "deepseek-v4-pro" in dashscope_models
    assert "qwen-plus" in dashscope_models

    claude_models = LLM_PROVIDER_PRESETS["claude"]["models"]
    assert any("claude-3-7-sonnet" in m for m in claude_models)
    assert any("claude-3-5-sonnet" in m for m in claude_models)


def test_db_schema_v4_and_safe_update_task(tmp_path: Path):
    db = Database(tmp_path / "test_v4.db")
    assert db.get_schema_version() >= 4

    # Create task with description and custom_script
    t = db.create_task(
        {
            "id": "task_v4_test",
            "url": "https://www.bilibili.com/video/BV1nYx86pEeA",
            "title": "部落冲突测试",
            "description": "夯木节升级规划与19本前瞻",
            "custom_script": "[00:00] 开场台词",
        }
    )
    assert t["description"] == "夯木节升级规划与19本前瞻"
    assert t["custom_script"] == "[00:00] 开场台词"

    # Test update_task with ai_storyboard and arbitrary unknown column to ensure no crash
    updated = db.update_task(
        "task_v4_test",
        ai_storyboard={"markdown": "### 分镜", "shots_count": 5},
        ai_clean_script={"markdown": "### 纯净文案"},
        random_non_existent_column="test_safe_filter",
    )
    assert updated is not None
    assert updated["ai_storyboard"]["shots_count"] == 5
    assert updated["ai_clean_script"]["markdown"] == "### 纯净文案"


def test_bilibili_ai_subtitle_extraction_live():
    from videodownloader.ai.transcript import fetch_bilibili_subtitles
    segs, desc, title = fetch_bilibili_subtitles("https://www.bilibili.com/video/BV1nYx86pEeA")
    assert len(segs) >= 50, f"Expected >= 50 subtitle lines, got {len(segs)}"
    assert "00:00" in segs[0]["time_str"]
    assert segs[0]["text"] != ""
    assert len(desc) > 0


def test_editable_model_combos_in_ui(tmp_path: Path):
    from qfluentwidgets import EditableComboBox
    _get_qapp()
    db = Database(tmp_path / "test_ui.db")
    qm = TaskQueueManager(db=db)

    ai_tab = AICopilotTab(db=db, on_seek_video=lambda tid, s: None)
    # Check that model_combo is an EditableComboBox and has options
    assert isinstance(ai_tab.model_combo, EditableComboBox)
    assert ai_tab.model_combo.count() >= 3

    # Switch to Claude and verify Claude models appear
    claude_idx = ai_tab._provider_keys.index("claude")
    ai_tab.provider_combo.setCurrentIndex(claude_idx)
    assert "claude" in ai_tab.model_combo.currentText()

    # Enter custom model and save
    ai_tab.model_combo.setText("my-fine-tuned-claude")
    ai_tab.save_llm_settings()
    saved = db.get_settings()
    assert saved["llm_provider"] == "claude"
    assert saved["llm_model"] == "my-fine-tuned-claude"
