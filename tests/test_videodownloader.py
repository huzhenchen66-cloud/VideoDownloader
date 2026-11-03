"""End-to-end automated test suite for VideoDownloader v2.0 (Desktop UI + Multi-Engine + Smart Proxy + AI + MCP)."""

from __future__ import annotations

import os
import threading
from pathlib import Path
from typing import Any

import pytest
from fastapi.testclient import TestClient

from videodownloader.ai.llm_service import ask_video_question, summarize_video_transcript
from videodownloader.ai.mcp_server import VideoDownloaderMCPServer
from videodownloader.ai.transcript import parse_subtitle_text
from videodownloader.core.db import Database
from videodownloader.core.os_utils import (
    resolve_proxy_for_platform,
    resolve_task_media_path,
    validate_media_file,
)
from videodownloader.core.url_cleaner import clean_share_input
from videodownloader.engines.base import (
    BaseVideoEngine,
    DownloadOptions,
    DownloadResult,
    MultiEngineChain,
    ParsedMedia,
)
from videodownloader.core.task_queue import FallbackWebSnifferEngine
from videodownloader.engines.ytdlp_engine import build_ytdlp_format_spec
from videodownloader.main import create_app


# -------------------- 1. Dirty Mobile Clipboard URL Cleaner --------------------


def test_clean_dirty_mobile_share_texts():
    douyin_paste = "7.56 复制打开抖音，看看【测试作者的作品】https://v.douyin.com/iY8abcde/ 快来围观！"
    res = clean_share_input(douyin_paste, resolve_short_links=False)
    assert len(res) == 1
    assert res[0].extracted_url == "https://v.douyin.com/iY8abcde/"
    assert res[0].platform_id == "douyin"

    bili_paste = "【超详细大模型Agent教程】 https://www.bilibili.com/video/BV1xx411c7mD?p=2&spm_id_from=333.999.0.0&vd_source=abc"
    res_bili = clean_share_input(bili_paste, resolve_short_links=False)
    assert len(res_bili) == 1
    assert res_bili[0].platform_id == "bilibili"
    # Must preserve episode `?p=2` while stripping tracking params!
    assert res_bili[0].normalized_url == "https://www.bilibili.com/video/BV1xx411c7mD/?p=2"

    batch_paste = """
    第一条：https://youtu.be/dQw4w9WgXcQ
    第二条小红书：http://xhslink.com/a/123456，超好看！
    第三条小红书新版短链：https://xhslink.cn/m/abc123
    第四条快手：https://v.kuaishou.com/abcXYZ
    """
    res_batch = clean_share_input(batch_paste, resolve_short_links=False)
    assert len(res_batch) == 4
    assert res_batch[0].platform_id == "youtube"
    assert res_batch[1].platform_id == "xiaohongshu"
    assert res_batch[2].platform_id == "xiaohongshu"
    assert res_batch[3].platform_id == "kuaishou"


# -------------------- 2. Smart Split Proxy & Anti-Fake-HTML Media Validation (v2.0) --------------------


def test_smart_split_proxy_routing():
    # In `auto` mode, domestic CN platforms MUST return `""` (force direct connection bypassing VPN!)
    assert resolve_proxy_for_platform("bilibili", "auto", "http://127.0.0.1:15715") == ""
    assert resolve_proxy_for_platform("douyin", "auto", "http://127.0.0.1:15715") == ""
    assert resolve_proxy_for_platform("xiaohongshu", "auto", "http://127.0.0.1:15715") == ""
    assert resolve_proxy_for_platform("kuaishou", "auto", "http://127.0.0.1:15715") == ""

    # Overseas platforms MUST route through configured/system proxy in `auto` mode
    assert resolve_proxy_for_platform("youtube", "auto", "127.0.0.1:15715") == "http://127.0.0.1:15715"
    assert resolve_proxy_for_platform("tiktok", "auto", "http://127.0.0.1:7890") == "http://127.0.0.1:7890"
    assert resolve_proxy_for_platform("twitter", "auto", "socks5://127.0.0.1:1080") == "socks5://127.0.0.1:1080"

    # Explicit `direct` mode bypasses proxy for all sites
    assert resolve_proxy_for_platform("youtube", "direct", "http://127.0.0.1:15715") == ""


def test_validate_media_file_and_db_startup_reconciliation(tmp_path: Path, monkeypatch):
    monkeypatch.setenv("VIDEODOWNLOADER_HOME", str(tmp_path))

    # 1. Fake HTML webpage disguised as `.mp4` (exact bug from old og:video fallback)
    fake_yt_mp4 = tmp_path / "fake_youtube_embed.mp4"
    fake_yt_mp4.write_bytes(b"<!DOCTYPE html><html><head><title>YouTube</title></head>" + b"A" * 10000)
    ok_fake, reason_fake = validate_media_file(str(fake_yt_mp4))
    assert not ok_fake
    assert "网页" in reason_fake or "HTML" in reason_fake or "无效" in reason_fake

    # 2. Valid binary MP4 in new moved directory
    new_save_dir = tmp_path / "moved_downloads"
    new_save_dir.mkdir(parents=True, exist_ok=True)
    real_mp4 = new_save_dir / "Chico 说 [BV1Js411v7Y3].mp4"
    real_mp4.write_bytes(b"\x00\x00\x00\x18ftypmp42" + b"\x11\x22\x33\x44" * 2000)
    ok_real, _ = validate_media_file(str(real_mp4))
    assert ok_real

    # Path healer resolves moved file by basename
    old_stale_path = str(tmp_path / "old_folder" / "Chico 说 [BV1Js411v7Y3].mp4")
    healed = resolve_task_media_path(old_stale_path, str(new_save_dir))
    assert Path(healed) == real_mp4

    # Database reconciliation across restart: heals moved valid file & cleans corrupt HTML-as-MP4 record
    db_file = tmp_path / "reconcile_test.db"
    db1 = Database(db_path=db_file)
    db1.update_settings({"save_path": str(new_save_dir)})
    db1.create_task(
        {
            "id": "task_corrupt_html",
            "url": "https://www.youtube.com/shorts/23WzlMahCC8",
            "platform_id": "youtube",
            "title": "Fake HTML MP4",
            "status": "completed",
            "progress": 100.0,
            "file_path": str(fake_yt_mp4),
        }
    )
    db1.create_task(
        {
            "id": "task_moved_valid",
            "url": "https://www.bilibili.com/video/BV1Js411v7Y3/",
            "platform_id": "bilibili",
            "title": "Chico 说",
            "status": "completed",
            "progress": 100.0,
            "file_path": old_stale_path,
        }
    )

    # Simulate next app launch
    db2 = Database(db_path=db_file)
    corrupt_task = db2.get_task("task_corrupt_html")
    assert corrupt_task is not None
    assert corrupt_task["status"] == "failed"
    assert not fake_yt_mp4.exists()

    valid_task = db2.get_task("task_moved_valid")
    assert valid_task is not None
    assert valid_task["status"] == "completed"
    assert valid_task["file_exists"] is True
    assert Path(valid_task["file_path"]) == real_mp4


def test_ytdlp_h264_format_sorting_and_fallback_guard():
    # Standard 1080p MP4 preset MUST prioritize `vcodec:h264` for universal playback compatibility
    fmt_1080, ext_1080, sort_1080, _ = build_ytdlp_format_spec("best_1080p", False, "mp3", has_ffmpeg=True)
    assert ext_1080 == "mp4"
    assert any("vcodec:h264" in s for s in sort_1080)
    assert "1080" in fmt_1080

    # 4K preset unlocks full resolution sorting
    _, ext_4k, sort_4k, _ = build_ytdlp_format_spec("best_4k", False, "mp3", has_ffmpeg=True)
    assert ext_4k == "mp4"
    assert "res" in sort_4k

    # Generic fallback HTML sniffer MUST refuse YouTube/Bilibili/TikTok so it never scrapes og:video embed pages
    fb = FallbackWebSnifferEngine()
    assert not fb.can_handle("https://www.youtube.com/watch?v=g3K6EL8O8HE", "youtube", prefer_fallback=True)
    assert not fb.can_handle("https://www.bilibili.com/video/BV1Js411v7Y3/", "bilibili", prefer_fallback=True)
    assert not fb.can_handle("https://www.tiktok.com/@user/video/123", "tiktok", prefer_fallback=True)


# -------------------- 3. Multi-Engine Chain of Responsibility --------------------


class _FailingPrimaryEngine(BaseVideoEngine):
    name = "mock-primary"

    def can_handle(self, url: str, platform_id: str, prefer_fallback: bool = False) -> bool:
        return True

    def parse(self, url: str, platform_id: str, options: DownloadOptions) -> ParsedMedia:
        raise RuntimeError("Simulated primary anti-bot block")

    def download(self, url, platform_id, options, stop_event, stop_reason_getter, on_progress=None) -> DownloadResult:
        raise RuntimeError("Simulated download failure")


class _WorkingFallbackEngine(BaseVideoEngine):
    name = "mock-fallback"

    def can_handle(self, url: str, platform_id: str, prefer_fallback: bool = False) -> bool:
        return prefer_fallback

    def parse(self, url: str, platform_id: str, options: DownloadOptions) -> ParsedMedia:
        return ParsedMedia(url=url, title="Fallback Parsed Title", platform_id=platform_id)

    def download(self, url, platform_id, options, stop_event, stop_reason_getter, on_progress=None) -> DownloadResult:
        out = Path(options.save_dir) / "demo.mp4"
        out.write_bytes(b"0123456789" * 500)
        return DownloadResult(file_path=str(out), title="Fallback Video", engine_used=self.name)


def test_multi_engine_fallback_and_telemetry(tmp_path: Path):
    chain = MultiEngineChain([_FailingPrimaryEngine(), _WorkingFallbackEngine()])
    opts = DownloadOptions(save_dir=str(tmp_path))

    media = chain.parse("https://example.com/v/1", "douyin", opts)
    assert media.title == "Fallback Parsed Title"
    assert media.engine_name == "mock-fallback"
    assert chain.telemetry["mock-primary"]["failures"] == 1
    assert chain.telemetry["mock-fallback"]["successes"] == 1

    dl = chain.download(
        "https://example.com/v/1",
        "douyin",
        opts,
        stop_event=threading.Event(),
        stop_reason_getter=lambda: "",
    )
    assert dl.engine_used == "mock-fallback"
    assert Path(dl.file_path).exists()


# -------------------- 4. Subtitle Parser & AI Offline Timeline Copilot --------------------


def test_subtitle_parsing_and_offline_ai_copilot():
    sample_srt = """1
00:00:05,000 --> 00:00:09,200
大家好，今天我们讲解 AI Agent 与 MCP 协议的核心架构设计。

2
00:01:24,500 --> 00:01:30,000
第一部分是多引擎责任链降级机制，保证高可用解析。

3
00:03:10,000 --> 00:03:18,000
第二部分是通过 FastAPI 实现支持 iOS Safari 的 HTTP Range 流式传输。
"""
    segs = parse_subtitle_text(sample_srt)
    assert len(segs) == 3
    assert segs[1]["time_str"] == "01:24"
    assert segs[1]["start"] == 84.5

    summary = summarize_video_transcript("AI Agent 实战课", segs, settings={"llm_api_key": ""})
    assert summary["mode"] == "extractive_offline"
    assert len(summary["chapters"]) >= 1
    assert "overview" in summary

    qa = ask_video_question("AI Agent 实战课", segs, "iOS Safari 流式传输在第几分钟？", settings={"llm_api_key": ""})
    assert qa["mode"] == "extractive_offline"
    assert "03:10" in qa["answer"]


# -------------------- 5. MCP JSON-RPC 2.0 Server --------------------


def test_mcp_server_protocol(tmp_path: Path):
    db = Database(db_path=tmp_path / "mcp_test.db")
    mcp = VideoDownloaderMCPServer(db=db)

    init_resp = mcp.handle_request({"jsonrpc": "2.0", "id": 1, "method": "initialize", "params": {}})
    assert init_resp is not None
    assert init_resp["result"]["serverInfo"]["name"] == "videodownloader-mcp"

    tools_resp = mcp.handle_request({"jsonrpc": "2.0", "id": 2, "method": "tools/list"})
    assert tools_resp is not None
    tool_names = {t["name"] for t in tools_resp["result"]["tools"]}
    assert {"parse_video_url", "download_video", "get_video_transcript", "summarize_video"}.issubset(tool_names)


# -------------------- 6. FastAPI + iOS Safari HTTP 206 Range Streaming + AI Studio API --------------------


def test_fastapi_endpoints_and_ios_range_streaming(tmp_path: Path, monkeypatch):
    monkeypatch.setenv("VIDEODOWNLOADER_HOME", str(tmp_path))
    app = create_app(db_path=tmp_path / "api_test.db", server_port=8765)

    sample_mp4 = tmp_path / "sample_video.mp4"
    sample_bytes = b"ABCDEFGHIJKLMNOPQRSTUVWXYZ0123456789" * 200  # 7200 bytes (> 4KB validation threshold)
    sample_mp4.write_bytes(sample_bytes)

    sample_srt = tmp_path / "sample_video.srt"
    sample_srt.write_text(
        "1\n00:00:12,000 --> 00:00:16,000\n这里是测试字幕时间轴跳转到十二秒。\n",
        encoding="utf-8",
    )

    with TestClient(app) as client:
        r_index = client.get("/")
        assert r_index.status_code == 200
        assert "VideoDownloader" in r_index.text

        r_manifest = client.get("/manifest.json")
        assert r_manifest.status_code == 200

        r_sys = client.get("/api/system/info")
        assert r_sys.status_code == 200
        sys_data: dict[str, Any] = r_sys.json()
        assert sys_data["lan"]["qr_data_uri"].startswith("data:image/png;base64,")

        db: Database = app.state.db
        db.create_task(
            {
                "id": "task_ios_01",
                "url": "https://www.bilibili.com/video/BV1test/",
                "platform_id": "bilibili",
                "platform_name": "哔哩哔哩",
                "title": "测试 iOS Range 流媒体视频",
                "status": "completed",
                "progress": 100.0,
                "file_path": str(sample_mp4),
                "file_size": len(sample_bytes),
                "subtitle_path": str(sample_srt),
            }
        )

        r_probe = client.get("/api/media/task_ios_01/stream", headers={"Range": "bytes=0-1"})
        assert r_probe.status_code == 206
        assert r_probe.headers["Content-Range"] == f"bytes 0-1/{len(sample_bytes)}"
        assert r_probe.content == b"AB"

        r_seek = client.get("/api/media/task_ios_01/stream", headers={"Range": "bytes=10-19"})
        assert r_seek.status_code == 206
        assert r_seek.content == sample_bytes[10:20]

        r_dl = client.get("/api/media/task_ios_01/download")
        assert r_dl.status_code == 200
        assert "attachment;" in r_dl.headers["Content-Disposition"]

        r_sum = client.post("/api/tasks/task_ios_01/ai/summarize")
        assert r_sum.status_code == 200
        assert r_sum.json()["summary"]["chapters"][0]["time_str"] == "00:12"

        r_ask = client.post("/api/tasks/task_ios_01/ai/ask", json={"question": "测试字幕讲了什么？"})
        assert r_ask.status_code == 200
        assert "00:12" in r_ask.json()["answer"]


# -------------------- 7. Native PyQt6 Desktop Window Smoke Test (Offscreen) --------------------


def test_native_pyqt6_desktop_window_offscreen(tmp_path: Path, monkeypatch):
    monkeypatch.setenv("VIDEODOWNLOADER_HOME", str(tmp_path))
    os.environ.setdefault("QT_QPA_PLATFORM", "offscreen")

    QtWidgets = pytest.importorskip("PyQt6.QtWidgets", exc_type=ImportError)
    from videodownloader.desktop_app import MainWindow

    app = QtWidgets.QApplication.instance() or QtWidgets.QApplication([])
    win = MainWindow()
    try:
        # Web server MUST be OFF by default (strictly optional, never forced!)
        assert win.web_mgr.is_running is False
        # Pure brand title without version suffix; version sits subtly in bottom-right badge
        assert win.windowTitle() == "VideoDownloader"
        assert win.version_badge_lbl.text() == "v3.0.0"
    finally:
        win._force_quit = True
        win.close()
        app.processEvents()
