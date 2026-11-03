"""Bilibili anti-risk bootstrap helper: automatically fetches official buvid3/buvid4 SPI cookies.

Bilibili limits unauthenticated guest downloads to throttled speeds or throws HTTP 412.
By generating fresh `buvid3` / `buvid4` device fingerprint cookies, downloads bypass 412
risk-control and unlock full CDN bandwidth without requiring user account credentials.
"""

from __future__ import annotations

import os
from pathlib import Path
import threading
import time
from typing import Callable

import requests

from .logging_setup import get_logger
from .paths import get_data_dir

logger = get_logger("bilibili_helper")

SPI_URL = "https://api.bilibili.com/x/frontend/finger/spi"
CACHE_TTL_SECONDS = 6 * 3600  # 6 hours
USER_AGENT = (
    "Mozilla/5.0 (Windows NT 10.0; Win64; x64) "
    "AppleWebKit/537.36 (KHTML, like Gecko) Chrome/131.0.0.0 Safari/537.36"
)

_LOCK = threading.Lock()


def _fetch_spi() -> dict:
    resp = requests.get(
        SPI_URL,
        headers={"User-Agent": USER_AGENT, "Referer": "https://www.bilibili.com/"},
        timeout=8,
    )
    resp.raise_for_status()
    data = resp.json() or {}
    return data.get("data") or {}


def ensure_bilibili_bootstrap_cookie(
    target_dir: Path | None = None,
    fetch_fn: Callable[[], dict] = _fetch_spi,
    max_age_seconds: int = CACHE_TTL_SECONDS,
) -> str | None:
    """Ensure a fresh Bilibili bootstrap cookie file exists; return its absolute path or None."""
    data_dir = target_dir or get_data_dir()
    data_dir.mkdir(parents=True, exist_ok=True)
    cookie_path = data_dir / "bilibili_spi_cookies.txt"

    with _LOCK:
        if cookie_path.exists():
            try:
                age = time.time() - cookie_path.stat().st_mtime
                if age < max_age_seconds and cookie_path.stat().st_size > 50:
                    return str(cookie_path)
            except OSError:
                pass

        try:
            spi_data = fetch_fn()
            b_3 = spi_data.get("b_3")
            b_4 = spi_data.get("b_4")
            if not b_3 and not b_4:
                return str(cookie_path) if cookie_path.exists() else None

            lines = [
                "# Netscape HTTP Cookie File",
                "# Generated automatically by VideoDownloader Bilibili SPI Bootstrap",
            ]
            expire = int(time.time()) + 365 * 86400
            if b_3:
                lines.append(f".bilibili.com\tTRUE\t/\tFALSE\t{expire}\tbuvid3\t{b_3}")
            if b_4:
                lines.append(f".bilibili.com\tTRUE\t/\tFALSE\t{expire}\tbuvid4\t{b_4}")

            temp_path = cookie_path.with_suffix(".tmp")
            temp_path.write_text("\n".join(lines) + "\n", encoding="utf-8")
            temp_path.replace(cookie_path)
            logger.info("Generated fresh Bilibili buvid3/buvid4 SPI bootstrap cookies")
            return str(cookie_path)
        except Exception as exc:
            logger.debug("Bilibili SPI bootstrap failed (%s); fallback to existing cache or guest", exc)
            return str(cookie_path) if cookie_path.exists() else None
