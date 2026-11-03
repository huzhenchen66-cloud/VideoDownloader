"""High-speed multi-threaded segmented chunk downloader for direct HTTP/HTTPS media streams.

Accelerates direct MP4/M4A downloads from domestic and international CDNs
(Douyin, Xiaohongshu, Kuaishou, Weibo, Zhihu, direct URLs) by partitioning
media into parallel byte-range streams (e.g. 6-8 threads), achieving up to 5x-10x
speed improvements over default sequential single-stream downloads.
"""

from __future__ import annotations

from concurrent.futures import ThreadPoolExecutor, as_completed
import os
from pathlib import Path
import threading
import time
from typing import Any, Callable

import requests

from ..engines.base import DownloadCancelledError, ProgressCallback, ProgressEvent
from .logging_setup import get_logger

logger = get_logger("fast_downloader")

DEFAULT_WORKERS = 8
MIN_CHUNK_ACCELERATION_BYTES = 3 * 1024 * 1024  # 3 MiB
READ_BUFFER_SIZE = 512 * 1024  # 512 KiB read buffer


def _format_speed(bps: float) -> str:
    if bps <= 0:
        return ""
    if bps >= 1024 * 1024:
        return f"{bps / (1024 * 1024):.1f} MB/s"
    if bps >= 1024:
        return f"{bps / 1024:.0f} KB/s"
    return f"{int(bps)} B/s"


def _format_eta(seconds: float | None) -> str:
    if seconds is None or seconds < 0 or seconds > 86400:
        return ""
    sec = int(seconds)
    if sec >= 3600:
        return f"{sec // 3600}:{(sec % 3600) // 60:02d}:{sec % 60:02d}"
    return f"{sec // 60:02d}:{sec % 60:02d}"


class SpeedSmoother:
    """Exponential moving average speed smoother for stable, flicker-free UI speed display."""

    def __init__(self, alpha: float = 0.35) -> None:
        self.alpha = alpha
        self.ema_speed: float = 0.0
        self.last_time: float = time.perf_counter()
        self.last_bytes: int = 0

    def update(self, current_bytes: int) -> float:
        now = time.perf_counter()
        dt = now - self.last_time
        if dt < 0.25:
            return self.ema_speed

        delta_b = current_bytes - self.last_bytes
        instant_speed = delta_b / dt if dt > 0 else 0.0
        if self.ema_speed <= 0.0:
            self.ema_speed = instant_speed
        else:
            self.ema_speed = self.alpha * instant_speed + (1.0 - self.alpha) * self.ema_speed

        self.last_time = now
        self.last_bytes = current_bytes
        return self.ema_speed


def probe_range_support(
    url: str,
    headers: dict[str, str],
    proxies: dict[str, str | None] | None = None,
    timeout: float = 10.0,
) -> tuple[bool, int, str]:
    """Probe whether the URL supports HTTP Range requests and return (supports_range, total_bytes, final_url)."""
    probe_headers = dict(headers)
    probe_headers["Range"] = "bytes=0-0"
    try:
        with requests.get(
            url,
            headers=probe_headers,
            proxies=proxies,
            stream=True,
            allow_redirects=True,
            timeout=timeout,
        ) as resp:
            final_url = resp.url
            if resp.status_code == 206:
                cr = resp.headers.get("Content-Range", "")
                total_len = 0
                if "/" in cr:
                    total_str = cr.split("/")[-1].strip()
                    if total_str.isdigit():
                        total_len = int(total_str)
                if total_len <= 0:
                    cl = resp.headers.get("Content-Length")
                    if cl and cl.isdigit():
                        total_len = int(cl)
                return True, total_len, final_url

            # Server returned 200 (does not support partial ranges)
            cl = resp.headers.get("Content-Length")
            total_len = int(cl) if (cl and cl.isdigit()) else 0
            accept_ranges = resp.headers.get("Accept-Ranges", "").lower()
            return (accept_ranges == "bytes"), total_len, final_url
    except Exception as exc:
        logger.debug("Range probe failed (%s), fallback to sequential streaming", exc)
        return False, 0, url


def download_stream_multi_chunk(
    url: str,
    target_path: str,
    headers: dict[str, str],
    total_bytes: int,
    num_workers: int = DEFAULT_WORKERS,
    proxies: dict[str, str | None] | None = None,
    stop_event: threading.Event | None = None,
    stop_reason_getter: Callable[[], str] | None = None,
    on_progress: ProgressCallback | None = None,
    title: str = "",
) -> None:
    """Download direct media using parallel HTTP Range workers."""
    num_workers = max(2, min(16, num_workers))
    part_target = target_path + ".part"
    os.makedirs(os.path.dirname(target_path) or ".", exist_ok=True)

    # Pre-allocate file size
    with open(part_target, "wb") as f:
        if total_bytes > 0:
            f.seek(total_bytes - 1)
            f.write(b"\0")

    chunk_size = total_bytes // num_workers
    ranges: list[tuple[int, int]] = []
    for i in range(num_workers):
        start = i * chunk_size
        end = (start + chunk_size - 1) if (i < num_workers - 1) else (total_bytes - 1)
        ranges.append((start, end))

    file_lock = threading.Lock()
    downloaded_bytes = 0
    downloaded_lock = threading.Lock()
    smoother = SpeedSmoother()

    def _worker(worker_id: int, start: int, end: int) -> None:
        nonlocal downloaded_bytes
        cur_pos = start
        retries = 3

        while cur_pos <= end and retries > 0:
            if stop_event and stop_event.is_set():
                reason = stop_reason_getter() if stop_reason_getter else "cancel"
                raise DownloadCancelledError(reason or "cancel")

            req_headers = dict(headers)
            req_headers["Range"] = f"bytes={cur_pos}-{end}"
            try:
                with requests.get(
                    url,
                    headers=req_headers,
                    proxies=proxies,
                    stream=True,
                    timeout=20.0,
                ) as resp:
                    if resp.status_code not in (200, 206):
                        raise RuntimeError(f"HTTP {resp.status_code}")

                    for chunk in resp.iter_content(chunk_size=READ_BUFFER_SIZE):
                        if stop_event and stop_event.is_set():
                            reason = stop_reason_getter() if stop_reason_getter else "cancel"
                            raise DownloadCancelledError(reason or "cancel")
                        if not chunk:
                            continue

                        chunk_len = len(chunk)
                        with file_lock:
                            with open(part_target, "r+b") as fp:
                                fp.seek(cur_pos)
                                fp.write(chunk)

                        cur_pos += chunk_len
                        with downloaded_lock:
                            downloaded_bytes += chunk_len

                        if on_progress:
                            cur_speed = smoother.update(downloaded_bytes)
                            pct = (downloaded_bytes * 100.0 / total_bytes) if total_bytes > 0 else 0.0
                            eta_sec = (total_bytes - downloaded_bytes) / cur_speed if cur_speed > 0 else None
                            on_progress(
                                ProgressEvent(
                                    status="downloading",
                                    progress=round(min(99.0, max(0.0, pct)), 1),
                                    speed=f"⚡ {_format_speed(cur_speed)} ({num_workers}线程)",
                                    eta=_format_eta(eta_sec),
                                    title=title,
                                )
                            )

            except DownloadCancelledError:
                raise
            except Exception as exc:
                retries -= 1
                logger.debug("Worker %d chunk retry (%d left): %s", worker_id, retries, exc)
                time.sleep(0.5)

        if cur_pos <= end:
            raise RuntimeError(f"Worker {worker_id} 未能完成区间 bytes={cur_pos}-{end} 下载")

    with ThreadPoolExecutor(max_workers=num_workers) as executor:
        futures = [executor.submit(_worker, i, r[0], r[1]) for i, r in enumerate(ranges)]
        for f in as_completed(futures):
            f.result()

    if os.path.exists(target_path):
        try:
            os.remove(target_path)
        except OSError:
            pass
    os.replace(part_target, target_path)


def download_stream_sequential(
    url: str,
    target_path: str,
    headers: dict[str, str],
    total_bytes: int = 0,
    proxies: dict[str, str | None] | None = None,
    stop_event: threading.Event | None = None,
    stop_reason_getter: Callable[[], str] | None = None,
    on_progress: ProgressCallback | None = None,
    title: str = "",
) -> None:
    """Download direct media using optimized single connection with 1MB stream buffers and speed smoothing."""
    part_target = target_path + ".part"
    os.makedirs(os.path.dirname(target_path) or ".", exist_ok=True)
    existing_bytes = os.path.getsize(part_target) if os.path.exists(part_target) else 0

    req_headers = dict(headers)
    if existing_bytes > 0:
        req_headers["Range"] = f"bytes={existing_bytes}-"

    smoother = SpeedSmoother()

    with requests.get(
        url,
        headers=req_headers,
        proxies=proxies,
        stream=True,
        allow_redirects=True,
        timeout=25.0,
    ) as resp:
        if resp.status_code == 416 and existing_bytes > 0:
            os.replace(part_target, target_path)
            return

        resp.raise_for_status()
        ctype = (resp.headers.get("Content-Type") or "").lower()
        if "text/html" in ctype:
            raise RuntimeError("目标地址返回了 HTML 网页而非有效音视频流")

        is_partial = resp.status_code == 206
        mode = "ab" if (is_partial and existing_bytes > 0) else "wb"
        downloaded = existing_bytes if (is_partial and existing_bytes > 0) else 0
        cl = resp.headers.get("Content-Length")
        content_len = int(cl) if (cl and cl.isdigit()) else 0
        real_total = (downloaded + content_len) if content_len > 0 else total_bytes

        with open(part_target, mode) as fp:
            for chunk in resp.iter_content(chunk_size=1024 * 1024):  # 1 MiB chunk
                if stop_event and stop_event.is_set():
                    reason = stop_reason_getter() if stop_reason_getter else "cancel"
                    raise DownloadCancelledError(reason or "cancel")
                if not chunk:
                    continue

                fp.write(chunk)
                downloaded += len(chunk)

                if on_progress:
                    cur_speed = smoother.update(downloaded)
                    pct = (downloaded * 100.0 / real_total) if real_total > 0 else 0.0
                    eta_sec = (real_total - downloaded) / cur_speed if (real_total > 0 and cur_speed > 0) else None
                    on_progress(
                        ProgressEvent(
                            status="downloading",
                            progress=round(min(99.0, max(0.0, pct)), 1),
                            speed=_format_speed(cur_speed),
                            eta=_format_eta(eta_sec),
                            title=title,
                        )
                    )

    if os.path.exists(target_path):
        try:
            os.remove(target_path)
        except OSError:
            pass
    os.replace(part_target, target_path)
