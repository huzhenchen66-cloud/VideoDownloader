"""Speculative Pre-Probe Engine (`core/prefetch.py`):
Silently probes clipboard video URLs + cover thumbnail bytes in the background with:
- Automatic cancellation of stale in-flight probes when a new URL is copied
- Two-tier cache: L1 in-memory `OrderedDict` LRU(20) + L2 SQLite `prefetch_cache` (7-day TTL, 10-min negative TTL)
Enables 0ms instant rendering when the user presses `Ctrl+Shift+D`.
"""

from __future__ import annotations

from collections import OrderedDict
from dataclasses import dataclass
import io
import threading
import time
from typing import Any

from PIL import Image
from PyQt6.QtCore import QObject, pyqtSignal
import requests

from ..engines.base import ErrorCode, ResolutionError, classify_exception
from .db import Database
from .logging_setup import get_logger
from .os_utils import resolve_proxy_for_platform
from .task_queue import TaskQueueManager
from .url_cleaner import clean_share_input

logger = get_logger("prefetch")

MEMORY_LRU_LIMIT = 20
MAX_THUMBNAIL_BYTES = 180 * 1024


@dataclass
class PrefetchEntry:
    url: str
    ok: bool
    from_cache: bool = False
    platform_id: str = "generic"
    platform_name: str = "通用视频"
    media: dict[str, Any] | None = None
    thumbnail_bytes: bytes = b""
    error_code: str = ""
    error_hint: str = ""
    trace: dict[str, Any] | None = None
    created_at: float = 0.0


def _fetch_and_compress_thumbnail(
    thumb_url: str,
    platform_id: str,
    proxy_mode: str,
    proxy_url: str,
    cancel_ev: threading.Event,
) -> bytes:
    """Download cover image and resize to a crisp `320x180` JPEG (`<= 180KB`) for instant 0ms display."""
    if not thumb_url or not thumb_url.startswith(("http://", "https://")) or cancel_ev.is_set():
        return b""

    resolved_px = resolve_proxy_for_platform(platform_id, proxy_mode, proxy_url)
    proxies = (
        {"http": None, "https": None}
        if resolved_px == ""
        else ({"http": resolved_px, "https": resolved_px} if resolved_px else None)
    )
    headers = {
        "User-Agent": "Mozilla/5.0 (Windows NT 10.0; Win64; x64) AppleWebKit/537.36 Chrome/131.0.0.0 Safari/537.36",
    }
    if platform_id == "bilibili" or "hdslb.com" in thumb_url:
        headers["Referer"] = "https://www.bilibili.com/"

    try:
        resp = requests.get(thumb_url, headers=headers, proxies=proxies, timeout=4.5)
        if not resp.ok or cancel_ev.is_set():
            return b""
        raw = resp.content
        if not raw:
            return b""
        with Image.open(io.BytesIO(raw)) as im:
            rgb = im.convert("RGB")
            rgb.thumbnail((360, 202), Image.Resampling.LANCZOS)
            buf = io.BytesIO()
            rgb.save(buf, format="JPEG", quality=84, optimize=True)
            out = buf.getvalue()
            return out if len(out) <= MAX_THUMBNAIL_BYTES else b""
    except Exception:
        return b""


class SpeculativePrefetcher(QObject):
    """Background metadata + thumbnail pre-warmer with L1 Memory LRU + L2 SQLite persistence."""

    probe_started = pyqtSignal(str)
    probe_finished = pyqtSignal(str, object)  # (normalized_url, PrefetchEntry)

    def __init__(self, db: Database, queue_mgr: TaskQueueManager, parent: QObject | None = None) -> None:
        super().__init__(parent)
        self.db = db
        self.queue_mgr = queue_mgr
        self._mem_lock = threading.RLock()
        self._mem_lru: OrderedDict[str, PrefetchEntry] = OrderedDict()

        self._job_lock = threading.Lock()
        self._active_url: str = ""
        self._active_cancel: threading.Event | None = None

    def get_cached(self, raw_or_norm_url: str) -> PrefetchEntry | None:
        """Instant synchronous lookup across L1 memory LRU and L2 SQLite cache (`< 1ms`)."""
        cleaned = clean_share_input(raw_or_norm_url, resolve_short_links=False)
        norm_url = cleaned[0].normalized_url if cleaned else (raw_or_norm_url or "").strip()
        if not norm_url:
            return None

        with self._mem_lock:
            hit = self._mem_lru.get(norm_url)
            if hit is not None:
                self._mem_lru.move_to_end(norm_url)
                cached_copy = PrefetchEntry(
                    url=hit.url,
                    ok=hit.ok,
                    from_cache=True,
                    platform_id=hit.platform_id,
                    platform_name=hit.platform_name,
                    media=hit.media,
                    thumbnail_bytes=hit.thumbnail_bytes,
                    error_code=hit.error_code,
                    error_hint=hit.error_hint,
                    trace=hit.trace,
                    created_at=hit.created_at,
                )
                return cached_copy

        db_hit = self.db.get_prefetch_cache(norm_url)
        if not db_hit:
            return None

        payload = db_hit["payload"]
        is_neg = db_hit["is_negative"]
        entry = PrefetchEntry(
            url=norm_url,
            ok=(not is_neg),
            from_cache=True,
            platform_id=str(payload.get("platform_id") or "generic"),
            platform_name=str(payload.get("platform_name") or "通用视频"),
            media=payload.get("media"),
            thumbnail_bytes=db_hit["thumbnail_bytes"],
            error_code=str(payload.get("error_code") or ""),
            error_hint=str(payload.get("error_hint") or ""),
            trace=payload.get("trace"),
            created_at=float(payload.get("created_at") or time.time()),
        )
        self._put_mem(norm_url, entry)
        return entry

    def _put_mem(self, norm_url: str, entry: PrefetchEntry) -> None:
        with self._mem_lock:
            self._mem_lru[norm_url] = entry
            self._mem_lru.move_to_end(norm_url)
            while len(self._mem_lru) > MEMORY_LRU_LIMIT:
                self._mem_lru.popitem(last=False)

    def cancel_active(self) -> None:
        with self._job_lock:
            if self._active_cancel is not None:
                self._active_cancel.set()
            self._active_url = ""
            self._active_cancel = None

    def trigger_prefetch(self, raw_text: str, force_refresh: bool = False) -> str:
        """Start background speculative probe for `raw_text`. Returns normalized URL (or `''` if invalid)."""
        cleaned = clean_share_input(raw_text, resolve_short_links=False)
        if not cleaned:
            return ""
        item = cleaned[0]
        norm_url = item.normalized_url

        if not force_refresh:
            cached = self.get_cached(norm_url)
            if cached is not None:
                self.probe_finished.emit(norm_url, cached)
                return norm_url

        with self._job_lock:
            if self._active_url == norm_url and self._active_cancel and not self._active_cancel.is_set():
                return norm_url
            if self._active_cancel is not None:
                self._active_cancel.set()
            cancel_ev = threading.Event()
            self._active_url = norm_url
            self._active_cancel = cancel_ev

        self.probe_started.emit(norm_url)
        t = threading.Thread(
            target=self._probe_worker,
            args=(norm_url, item.platform_id, item.platform_name, cancel_ev),
            daemon=True,
            name="vd-prefetch",
        )
        t.start()
        return norm_url

    def _probe_worker(
        self,
        norm_url: str,
        initial_pid: str,
        initial_pname: str,
        cancel_ev: threading.Event,
    ) -> None:
        if cancel_ev.is_set():
            return

        # Resolve short link (e.g. b23.tv / v.douyin.com) inside background thread
        resolved_list = clean_share_input(norm_url, resolve_short_links=True)
        target_url = resolved_list[0].normalized_url if resolved_list else norm_url
        pid = resolved_list[0].platform_id if resolved_list else initial_pid
        pname = resolved_list[0].platform_name if resolved_list else initial_pname

        if cancel_ev.is_set():
            return

        opts = self.queue_mgr.build_download_options()
        now = time.time()

        try:
            media = self.queue_mgr.engine_chain.parse(target_url, pid, opts)
            if cancel_ev.is_set():
                return

            media_dict = media.to_dict()
            thumb_bytes = _fetch_and_compress_thumbnail(
                media.thumbnail,
                media.platform_id or pid,
                opts.proxy_mode,
                opts.proxy_url,
                cancel_ev,
            )
            if cancel_ev.is_set():
                return

            entry = PrefetchEntry(
                url=norm_url,
                ok=True,
                from_cache=False,
                platform_id=media.platform_id or pid,
                platform_name=media.platform_name or pname,
                media=media_dict,
                thumbnail_bytes=thumb_bytes,
                trace=media.trace,
                created_at=now,
            )
            payload = {
                "platform_id": entry.platform_id,
                "platform_name": entry.platform_name,
                "media": media_dict,
                "trace": media.trace,
                "created_at": now,
            }
            self._put_mem(norm_url, entry)
            self.db.set_prefetch_cache(norm_url, payload, thumbnail_bytes=thumb_bytes, is_negative=False)
            if target_url != norm_url:
                self._put_mem(target_url, entry)
                self.db.set_prefetch_cache(target_url, payload, thumbnail_bytes=thumb_bytes, is_negative=False)

            logger.info("Speculative prefetch ready | platform=%s | title=%s", entry.platform_id, media.title[:40])
            self.probe_finished.emit(norm_url, entry)

        except Exception as exc:
            if cancel_ev.is_set():
                return
            if isinstance(exc, ResolutionError):
                code_str = str(exc.primary_code)
                hint = exc.hint
                trace_dict = exc.trace.to_dict()
            else:
                c_enum, _, hint = classify_exception(exc, pid)
                code_str = str(c_enum)
                trace_dict = None

            entry = PrefetchEntry(
                url=norm_url,
                ok=False,
                from_cache=False,
                platform_id=pid,
                platform_name=pname,
                error_code=code_str,
                error_hint=hint,
                trace=trace_dict,
                created_at=now,
            )
            # Only write negative cache for deterministic errors (don't block transient network timeouts for 10m!)
            if code_str not in (str(ErrorCode.NETWORK_TIMEOUT),):
                neg_payload = {
                    "platform_id": pid,
                    "platform_name": pname,
                    "error_code": code_str,
                    "error_hint": hint,
                    "trace": trace_dict,
                    "created_at": now,
                }
                self._put_mem(norm_url, entry)
                self.db.set_prefetch_cache(norm_url, neg_payload, is_negative=True, ttl_seconds=600.0)

            logger.info("Speculative prefetch failed [%s] for %s: %s", code_str, norm_url, hint[:100])
            self.probe_finished.emit(norm_url, entry)
