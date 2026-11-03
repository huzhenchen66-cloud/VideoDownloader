"""Offline unit tests for Structured `ErrorCode`, `ResolutionTrace` 4-Stage Pipeline & Retry Policy."""

from __future__ import annotations

import json
from pathlib import Path
import sqlite3
import threading
import time

import pytest

from videodownloader.core.db import Database
from videodownloader.core.task_queue import TaskQueueManager
from videodownloader.engines.base import (
    BaseVideoEngine,
    DownloadCancelledError,
    DownloadOptions,
    DownloadResult,
    ErrorCode,
    FormatChoice,
    MultiEngineChain,
    ParsedMedia,
    ResolutionError,
    classify_exception,
)
from videodownloader.engines.ytdlp_engine import YtdlpEngine

FIXTURES_DIR = Path(__file__).resolve().parent / "fixtures"


@pytest.mark.parametrize(
    ("raw_msg", "platform_id", "expected_code", "expected_retryable"),
    [
        ("Sign in to confirm your age. Cookies are needed", "youtube", ErrorCode.AUTH_REQUIRED, False),
        ("该视频需要大会员登录后观看", "bilibili", ErrorCode.AUTH_REQUIRED, False),
        ("HTTP Error 429: Too Many Requests", "youtube", ErrorCode.RISK_CONTROL, True),
        ("Connection timed out (errno 10060)", "youtube", ErrorCode.NETWORK_TIMEOUT, True),
        ("This video is not available in your country", "youtube", ErrorCode.GEO_BLOCKED, False),
        ("This video is DRM Widevine protected", "generic", ErrorCode.UNSUPPORTED, False),
        ("Requested format is not available", "bilibili", ErrorCode.FORMAT_INVALID, False),
        ("No space left on device (ENOSPC)", "bilibili", ErrorCode.DISK_FULL, False),
    ],
)
def test_classify_exception_taxonomy(raw_msg: str, platform_id: str, expected_code: ErrorCode, expected_retryable: bool):
    code, retryable, hint = classify_exception(raw_msg, platform_id)
    assert code == expected_code
    assert retryable is expected_retryable
    assert len(hint) >= 6


def test_classify_cancelled_exception():
    code, retryable, _ = classify_exception(DownloadCancelledError("pause"), "youtube")
    assert code == ErrorCode.CANCELLED
    assert retryable is False


def test_ytdlp_fixture_info_to_parsed_media():
    info = json.loads((FIXTURES_DIR / "ytdlp_info_sample.json").read_text(encoding="utf-8"))
    media = YtdlpEngine._info_to_parsed_media(info, info["webpage_url"], "youtube")
    assert media.title == "4K HDR Nature Landscape Sample"
    assert media.platform_id == "youtube"
    assert media.has_subtitles is True
    assert len(media.formats) == 3
    assert media.formats[0].label.startswith("2160p")
    assert "H.264" in media.formats[1].label


class _MockSkippedEngine(BaseVideoEngine):
    name = "cn-native-sniffer"

    def can_handle(self, url: str, platform_id: str, prefer_fallback: bool = False) -> bool:
        return False

    def skip_reason(self, url: str, platform_id: str, prefer_fallback: bool = False) -> str:
        return "host 不在移动端直连注册表"

    def parse(self, url: str, platform_id: str, options: DownloadOptions) -> ParsedMedia:
        raise AssertionError("Should be skipped")

    def download(self, url, platform_id, options, stop_event, stop_reason_getter, on_progress=None) -> DownloadResult:
        raise AssertionError("Should be skipped")


class _MockRiskFailedEngine(BaseVideoEngine):
    name = "browser-cdp-sniffer"

    def can_handle(self, url: str, platform_id: str, prefer_fallback: bool = False) -> bool:
        return True

    def parse(self, url: str, platform_id: str, options: DownloadOptions) -> ParsedMedia:
        raise RuntimeError("HTTP 403 Forbidden: 触发风控校验")

    def download(self, url, platform_id, options, stop_event, stop_reason_getter, on_progress=None) -> DownloadResult:
        raise RuntimeError("HTTP 403 Forbidden: 触发风控校验")


class _MockSuccessYtdlpEngine(BaseVideoEngine):
    name = "yt-dlp"

    def can_handle(self, url: str, platform_id: str, prefer_fallback: bool = False) -> bool:
        return True

    def parse(self, url: str, platform_id: str, options: DownloadOptions) -> ParsedMedia:
        return ParsedMedia(
            url=url,
            title="Explainable Multi-Engine Video",
            platform_id=platform_id,
            formats=[FormatChoice(format_id="137", label="1080p (MP4 · H.264)")],
        )

    def download(self, url, platform_id, options, stop_event, stop_reason_getter, on_progress=None) -> DownloadResult:
        out = Path(options.save_dir) / "ok.mp4"
        out.write_bytes(b"\x00\x00\x00\x18ftypmp42" + b"X" * 4096)
        return DownloadResult(file_path=str(out), title="Explainable Video", engine_used=self.name)


class _MockNotRunFallbackEngine(BaseVideoEngine):
    name = "web-media-sniffer"

    def can_handle(self, url: str, platform_id: str, prefer_fallback: bool = False) -> bool:
        return prefer_fallback

    def parse(self, url: str, platform_id: str, options: DownloadOptions) -> ParsedMedia:
        raise RuntimeError("Unsupported URL")

    def download(self, url, platform_id, options, stop_event, stop_reason_getter, on_progress=None) -> DownloadResult:
        raise RuntimeError("Unsupported URL")


def test_four_stage_resolution_trace_tree_and_not_run_stages(tmp_path: Path):
    chain = MultiEngineChain(
        [
            _MockSkippedEngine(),
            _MockRiskFailedEngine(),
            _MockSuccessYtdlpEngine(),
            _MockNotRunFallbackEngine(),
        ]
    )
    opts = DownloadOptions(save_dir=str(tmp_path))
    media = chain.parse("https://www.youtube.com/watch?v=demo123", "youtube", opts)

    assert media.trace is not None
    stages = media.trace["stages"]
    assert [s["status"] for s in stages] == ["skipped", "failed", "success", "not_run"]
    assert stages[1]["error_code"] == str(ErrorCode.RISK_CONTROL)
    assert media.trace["final_engine"] == "yt-dlp"

    recent = chain.get_recent_traces(limit=5)
    assert len(recent) == 1


def test_resolution_error_prioritizes_auth_required_over_generic_fallback(tmp_path: Path):
    class _AuthFailEngine(BaseVideoEngine):
        name = "yt-dlp"

        def can_handle(self, url: str, platform_id: str, prefer_fallback: bool = False) -> bool:
            return True

        def parse(self, url: str, platform_id: str, options: DownloadOptions) -> ParsedMedia:
            raise RuntimeError("ERROR: Private video. Sign in to confirm you have access")

        def download(self, url, platform_id, options, stop_event, stop_reason_getter, on_progress=None):
            raise RuntimeError("ERROR: Private video. Sign in to confirm you have access")

    chain = MultiEngineChain([_MockSkippedEngine(), _AuthFailEngine(), _MockNotRunFallbackEngine()])
    opts = DownloadOptions(save_dir=str(tmp_path))

    with pytest.raises(ResolutionError) as exc_info:
        chain.parse("https://example.com/private/1", "generic", opts)

    err = exc_info.value
    assert err.primary_code == ErrorCode.AUTH_REQUIRED
    assert err.retryable is False
    tree_str = err.trace.format_tree()
    assert "Stage 1" in tree_str and "跳过" in tree_str
    assert "Stage 2" in tree_str and "auth_required" in tree_str


def test_disk_full_aborts_chain_immediately_without_running_fallbacks(tmp_path: Path):
    fallback_called = False

    class _DiskFullEngine(BaseVideoEngine):
        name = "yt-dlp"

        def can_handle(self, url, platform_id, prefer_fallback=False) -> bool:
            return True

        def parse(self, url, platform_id, options) -> ParsedMedia:
            raise OSError(28, "No space left on device")

        def download(self, url, platform_id, options, stop_event, stop_reason_getter, on_progress=None):
            raise OSError(28, "No space left on device")

    class _SpyFallback(BaseVideoEngine):
        name = "spy-fallback"

        def can_handle(self, url, platform_id, prefer_fallback=False) -> bool:
            return True

        def parse(self, url, platform_id, options) -> ParsedMedia:
            nonlocal fallback_called
            fallback_called = True
            return ParsedMedia(url=url, title="Should never run")

        def download(self, url, platform_id, options, stop_event, stop_reason_getter, on_progress=None):
            raise AssertionError("Should never run")

    chain = MultiEngineChain([_DiskFullEngine(), _SpyFallback()])
    with pytest.raises(ResolutionError) as exc_info:
        chain.parse("https://example.com/v", "youtube", DownloadOptions(save_dir=str(tmp_path)))

    assert exc_info.value.primary_code == ErrorCode.DISK_FULL
    assert fallback_called is False


def test_task_queue_fast_fails_non_retryable_auth_error(tmp_path: Path, monkeypatch):
    monkeypatch.setenv("VIDEODOWNLOADER_HOME", str(tmp_path))
    db = Database(db_path=tmp_path / "fastfail.db")
    mgr = TaskQueueManager(db)

    attempts = 0

    class _AlwaysAuthRequiredEngine(BaseVideoEngine):
        name = "yt-dlp"

        def can_handle(self, url, platform_id, prefer_fallback=False) -> bool:
            return True

        def parse(self, url, platform_id, options) -> ParsedMedia:
            raise RuntimeError("Login required")

        def download(self, url, platform_id, options, stop_event, stop_reason_getter, on_progress=None):
            nonlocal attempts
            attempts += 1
            raise RuntimeError("Login required: cookies are needed")

    mgr.engine_chain = MultiEngineChain([_AlwaysAuthRequiredEngine()])
    created = mgr.submit_tasks("https://www.youtube.com/watch?v=dQw4w9WgXcQ")
    tid = created[0]["id"]

    deadline = time.time() + 4.0
    while time.time() < deadline:
        t = db.get_task(tid)
        if t and t["status"] == "failed":
            break
        time.sleep(0.05)

    mgr.stop()
    final_task = db.get_task(tid)
    assert final_task is not None
    assert final_task["status"] == "failed"
    assert final_task["error_code"] == "auth_required"
    assert final_task["retry_count"] == 0
    assert attempts == 1  # Zero wasted retries on AUTH_REQUIRED!
    assert final_task["trace"] is not None
