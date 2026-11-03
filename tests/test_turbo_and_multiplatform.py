"""Comprehensive tests for Turbo Chunk Download Acceleration, Bilibili SPI Bootstrap & Multi-platform Extractors."""

from __future__ import annotations

import http.server
import json
import os
from pathlib import Path
import socketserver
import tempfile
import threading
import time
from typing import Any

import pytest

from videodownloader.core.bilibili_helper import ensure_bilibili_bootstrap_cookie
from videodownloader.core.fast_downloader import (
    MIN_CHUNK_ACCELERATION_BYTES,
    SpeedSmoother,
    download_stream_multi_chunk,
    download_stream_sequential,
    probe_range_support,
)
from videodownloader.engines.base import DownloadOptions, ParsedMedia
from videodownloader.engines.cn_short_engine import NativeCNAndSnifferEngine
from videodownloader.engines.ytdlp_engine import YtdlpEngine


class MockRangeHTTPHandler(http.server.BaseHTTPRequestHandler):
    """Local HTTP test server supporting byte ranges (206 Partial Content)."""

    data = b"0123456789ABCDEFGHIJKLMNOPQRSTUVWXYZ" * 150000  # ~5.4 MB test payload

    def do_HEAD(self) -> None:
        self.send_response(200)
        self.send_header("Content-Type", "video/mp4")
        self.send_header("Content-Length", str(len(self.data)))
        self.send_header("Accept-Ranges", "bytes")
        self.end_headers()

    def do_GET(self) -> None:
        range_header = self.headers.get("Range")
        total = len(self.data)
        if not range_header:
            self.send_response(200)
            self.send_header("Content-Type", "video/mp4")
            self.send_header("Content-Length", str(total))
            self.send_header("Accept-Ranges", "bytes")
            self.end_headers()
            self.wfile.write(self.data)
            return

        # Parse `bytes=start-end`
        range_val = range_header.replace("bytes=", "").strip()
        parts = range_val.split("-")
        start = int(parts[0]) if parts[0] else 0
        end = int(parts[1]) if len(parts) > 1 and parts[1] else total - 1
        end = min(end, total - 1)
        chunk = self.data[start : end + 1]

        self.send_response(206)
        self.send_header("Content-Type", "video/mp4")
        self.send_header("Content-Range", f"bytes {start}-{end}/{total}")
        self.send_header("Content-Length", str(len(chunk)))
        self.end_headers()
        self.wfile.write(chunk)

    def log_message(self, format: str, *args: Any) -> None:
        pass


@pytest.fixture(scope="module")
def local_range_server():
    server = socketserver.TCPServer(("127.0.0.1", 0), MockRangeHTTPHandler)
    port = server.server_address[1]
    thread = threading.Thread(target=server.serve_forever, daemon=True)
    thread.start()
    yield f"http://127.0.0.1:{port}/sample.mp4"
    server.shutdown()
    server.server_close()


def test_speed_smoother_ema():
    smoother = SpeedSmoother(alpha=0.5)
    smoother.last_time = time.perf_counter() - 0.5
    s1 = smoother.update(1024 * 1024)
    assert s1 > 0

    smoother.last_time = time.perf_counter() - 0.5
    s2 = smoother.update(3 * 1024 * 1024)
    assert s2 > s1


def test_bilibili_spi_bootstrap_cookie():
    with tempfile.TemporaryDirectory() as tmp_dir:
        tmp_path = Path(tmp_dir)

        def mock_fetch():
            return {
                "b_3": "TEST_BUVID3_VALUE_12345",
                "b_4": "TEST_BUVID4_VALUE_67890",
            }

        cookie_file = ensure_bilibili_bootstrap_cookie(target_dir=tmp_path, fetch_fn=mock_fetch)
        assert cookie_file is not None
        assert os.path.exists(cookie_file)
        content = Path(cookie_file).read_text(encoding="utf-8")
        assert "buvid3" in content
        assert "TEST_BUVID3_VALUE_12345" in content
        assert "buvid4" in content

        # Second call within TTL should return cached path without re-fetching
        call_count = [0]

        def fail_fetch():
            call_count[0] += 1
            raise RuntimeError("Should not be called")

        cached_file = ensure_bilibili_bootstrap_cookie(target_dir=tmp_path, fetch_fn=fail_fetch)
        assert cached_file == cookie_file
        assert call_count[0] == 0


def test_fast_downloader_range_probe_and_multi_chunk(local_range_server):
    url = local_range_server
    supports_range, total_bytes, final_url = probe_range_support(url, headers={})
    assert supports_range is True
    assert total_bytes == len(MockRangeHTTPHandler.data)
    assert total_bytes >= MIN_CHUNK_ACCELERATION_BYTES

    with tempfile.TemporaryDirectory() as tmp_dir:
        out_file = str(Path(tmp_dir) / "downloaded.mp4")
        events: list[str] = []

        def on_prog(e):
            events.append(e.speed)

        download_stream_multi_chunk(
            url=url,
            target_path=out_file,
            headers={},
            total_bytes=total_bytes,
            num_workers=4,
            on_progress=on_prog,
            title="Turbo Test",
        )

        assert os.path.exists(out_file)
        assert os.path.getsize(out_file) == total_bytes
        assert Path(out_file).read_bytes() == MockRangeHTTPHandler.data
        assert any("⚡" in s for s in events if s)


def test_fast_downloader_sequential_fallback(local_range_server):
    url = local_range_server
    with tempfile.TemporaryDirectory() as tmp_dir:
        out_file = str(Path(tmp_dir) / "sequential.mp4")
        download_stream_sequential(
            url=url,
            target_path=out_file,
            headers={},
            total_bytes=len(MockRangeHTTPHandler.data),
            title="Sequential Test",
        )
        assert os.path.exists(out_file)
        assert os.path.getsize(out_file) == len(MockRangeHTTPHandler.data)


def test_ytdlp_engine_turbo_opts_and_bilibili_injection():
    engine = YtdlpEngine()
    opts_yt = engine._base_ydl_opts(
        url="https://www.youtube.com/watch?v=dQw4w9WgXcQ",
        platform_id="youtube",
        options=DownloadOptions(save_dir="."),
    )
    assert opts_yt.get("http_chunk_size") == 10485760
    assert opts_yt.get("buffersize") == 1048576
    assert opts_yt.get("concurrent_fragment_downloads") >= 2
    assert "youtube" in opts_yt.get("extractor_args", {})

    # Bilibili should auto-inject bootstrap cookies
    opts_bili = engine._base_ydl_opts(
        url="https://www.bilibili.com/video/BV1xx411c7mD",
        platform_id="bilibili",
        options=DownloadOptions(save_dir="."),
    )
    assert "cookiefile" in opts_bili or "cookiesfrombrowser" in opts_bili


def test_cn_short_engine_can_handle_expanded_platforms():
    engine = NativeCNAndSnifferEngine()
    assert engine.can_handle("https://v.douyin.com/abc/", "douyin")
    assert engine.can_handle("https://xhslink.com/abc", "xiaohongshu")
    assert engine.can_handle("https://v.kuaishou.com/xyz", "kuaishou")
    assert engine.can_handle("https://weibo.com/123/456", "weibo")
    assert engine.can_handle("https://www.zhihu.com/zvideo/789", "zhihu")
    assert engine.can_handle("https://example.com/test.mp4", "direct")
    assert not engine.can_handle("https://www.youtube.com/watch?v=123", "youtube")
