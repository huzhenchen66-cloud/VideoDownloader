"""Concurrent download task scheduler with structured ErrorCode retry policy, ResolutionTrace persistence & pub/sub."""

from __future__ import annotations

import asyncio
import os
from pathlib import Path
import queue
import threading
import time
from typing import Any, Callable
import uuid

from ..engines.base import (
    DownloadCancelledError,
    DownloadOptions,
    ErrorCode,
    MultiEngineChain,
    ParsedMedia,
    ProgressEvent,
    ResolutionError,
    classify_exception,
)
from ..engines.cdp_sniffer_engine import BrowserCDPSnifferEngine
from ..engines.cn_short_engine import NO_GENERIC_OG_PLATFORMS, NativeCNAndSnifferEngine
from ..engines.ytdlp_engine import MEDIA_EXTENSIONS, SUBTITLE_EXTENSIONS, YtdlpEngine
from .db import Database
from .logging_setup import get_logger
from .os_utils import resolve_task_media_path, validate_media_file
from .url_cleaner import clean_share_input

logger = get_logger("queue")

MAX_WORKER_THREADS = 6
MAX_AUTO_RETRIES = 2

SyncSubscriber = Callable[[str, Any], None]


class FallbackWebSnifferEngine(NativeCNAndSnifferEngine):
    """Stage-4 fallback sniffer (optional `videofetch` + generic HTML5 `<video src>` / OpenGraph sniffer)."""

    name = "web-media-sniffer"

    def can_handle(self, url: str, platform_id: str, prefer_fallback: bool = False) -> bool:
        if platform_id in ("douyin", "xiaohongshu", "direct") or platform_id in NO_GENERIC_OG_PLATFORMS:
            return False
        return prefer_fallback

    def skip_reason(self, url: str, platform_id: str, prefer_fallback: bool = False) -> str:
        if self.can_handle(url, platform_id, prefer_fallback=prefer_fallback):
            return ""
        if platform_id in NO_GENERIC_OG_PLATFORMS or platform_id in ("douyin", "xiaohongshu"):
            return f"平台 {platform_id} 禁止通用 HTML/OG 兜底嗅探（防止误抓播放器框架）"
        return "仅在前序引擎未命中时作为第 4 级兜底运行"


class _TaskRuntimeControl:
    def __init__(self) -> None:
        self.stop_event = threading.Event()
        self.stop_reason: str = ""  # "pause" | "cancel"


class TaskQueueManager:
    """Thread-safe task queue coordinating multi-engine downloads, SQLite state, Desktop GUI, Tray & WebSockets."""

    def __init__(self, db: Database) -> None:
        self.db = db
        self.engine_chain = MultiEngineChain(
            [
                NativeCNAndSnifferEngine(),
                BrowserCDPSnifferEngine(),
                YtdlpEngine(),
                FallbackWebSnifferEngine(),
            ]
        )
        self._queue: queue.Queue[str] = queue.Queue()
        self._controls: dict[str, _TaskRuntimeControl] = {}
        self._controls_lock = threading.RLock()
        self._shutdown = threading.Event()

        # Asyncio WebSocket event listeners: list of (loop, asyncio.Queue)
        self._subscribers: list[tuple[asyncio.AbstractEventLoop, asyncio.Queue[dict[str, Any]]]] = []
        # In-process Desktop UI & System Tray listeners (zero HTTP overhead!)
        self._sync_subscribers: list[SyncSubscriber] = []
        self._sub_lock = threading.Lock()
        self._workers_started = False

    def start(self) -> None:
        if self._workers_started:
            return
        self._shutdown.clear()
        self._workers_started = True
        for idx in range(MAX_WORKER_THREADS):
            t = threading.Thread(target=self._worker_loop, args=(idx,), daemon=True, name=f"vd-worker-{idx}")
            t.start()

    def stop(self) -> None:
        self._shutdown.set()
        with self._controls_lock:
            for ctrl in self._controls.values():
                ctrl.stop_reason = "pause"
                ctrl.stop_event.set()
        self._workers_started = False

    def has_active_downloads(self) -> bool:
        for t in self.db.list_tasks(limit=200):
            if t.get("status") in ("queued", "parsing", "downloading", "merging", "retrying"):
                return True
        return False

    def get_aggregate_progress(self) -> dict[str, Any]:
        """Compute aggregate active task count & overall progress `[0..100]` for the System Tray ring."""
        tasks = self.db.list_tasks(limit=200)
        active = [
            t for t in tasks if t.get("status") in ("queued", "parsing", "downloading", "merging", "retrying")
        ]
        failed_recent = any(t.get("status") == "failed" for t in tasks[:5])
        if not active:
            return {"active_count": 0, "progress": 0.0, "has_error": failed_recent}
        avg = sum(float(t.get("progress") or 0.0) for t in active) / len(active)
        return {"active_count": len(active), "progress": round(avg, 1), "has_error": False}

    # -------------------- Pub/Sub for Desktop GUI, System Tray & WebSocket --------------------

    def subscribe_sync(self, callback: SyncSubscriber) -> None:
        with self._sub_lock:
            if callback not in self._sync_subscribers:
                self._sync_subscribers.append(callback)

    def unsubscribe_sync(self, callback: SyncSubscriber) -> None:
        with self._sub_lock:
            self._sync_subscribers = [cb for cb in self._sync_subscribers if cb is not callback]

    def subscribe(self, loop: asyncio.AbstractEventLoop) -> asyncio.Queue[dict[str, Any]]:
        q: asyncio.Queue[dict[str, Any]] = asyncio.Queue(maxsize=200)
        with self._sub_lock:
            self._subscribers.append((loop, q))
        return q

    def unsubscribe(self, q: asyncio.Queue[dict[str, Any]]) -> None:
        with self._sub_lock:
            self._subscribers = [(lp, sq) for (lp, sq) in self._subscribers if sq is not q]

    def broadcast(self, event_type: str, payload: Any) -> None:
        message = {"type": event_type, "data": payload, "ts": time.time()}
        with self._sub_lock:
            subs = list(self._subscribers)
            sync_subs = list(self._sync_subscribers)

        for cb in sync_subs:
            try:
                cb(event_type, payload)
            except Exception:
                pass

        for loop, q in subs:
            try:
                loop.call_soon_threadsafe(self._push_to_async_queue, q, message)
            except Exception:
                pass

    @staticmethod
    def _push_to_async_queue(q: asyncio.Queue[dict[str, Any]], msg: dict[str, Any]) -> None:
        if q.full():
            try:
                q.get_nowait()
            except Exception:
                pass
        try:
            q.put_nowait(msg)
        except Exception:
            pass

    # -------------------- Options & Parsing --------------------

    def build_download_options(self, overrides: dict[str, Any] | None = None) -> DownloadOptions:
        cfg = self.db.get_settings()
        if overrides:
            cfg.update({k: v for k, v in overrides.items() if v is not None})
        return DownloadOptions(
            save_dir=cfg["save_path"],
            quality=cfg.get("quality", "best_1080p"),
            only_audio=bool(cfg.get("only_audio", False)),
            audio_format=cfg.get("audio_format", "mp3"),
            subtitle=bool(cfg.get("subtitle", True)),
            fragment_threads=int(cfg.get("fragment_threads", 8)),
            proxy_mode=cfg.get("proxy_mode", "auto"),
            proxy_url=cfg.get("proxy_url", ""),
            browser_cookie=cfg.get("browser_cookie", "none"),
            cookie_file=cfg.get("cookie_file", ""),
        )

    def parse_input(self, raw_text: str) -> dict[str, Any]:
        """Clean dirty clipboard text and probe metadata + 4-stage `ResolutionTrace`."""
        cleaned_list = clean_share_input(raw_text, resolve_short_links=True)
        if not cleaned_list:
            raise ValueError("未在输入内容中识别到有效的 http/https 视频链接")

        first = cleaned_list[0]
        opts = self.build_download_options()
        media: ParsedMedia = self.engine_chain.parse(first.normalized_url, first.platform_id, opts)
        return {
            "cleaned": {
                "extracted_url": first.extracted_url,
                "normalized_url": first.normalized_url,
                "platform_id": media.platform_id or first.platform_id,
                "platform_name": media.platform_name or first.platform_name,
                "batch_count": len(cleaned_list),
                "all_urls": [c.normalized_url for c in cleaned_list],
            },
            "media": media.to_dict(),
        }

    # -------------------- Task Lifecycle --------------------

    def submit_tasks(
        self,
        raw_text: str,
        overrides: dict[str, Any] | None = None,
        prefetched_title: str = "",
        prefetched_thumbnail: str = "",
        prefetched_duration: float = 0.0,
        prefetched_uploader: str = "",
        prefetched_description: str = "",
        prefetched_trace: dict[str, Any] | None = None,
        start_paused: bool = False,
    ) -> list[dict[str, Any]]:
        """Clean 1..N URLs from input text and enqueue download tasks (or queue paused when `start_paused=True`)."""
        self.start()
        cleaned_list = clean_share_input(raw_text, resolve_short_links=False)
        if not cleaned_list:
            raise ValueError("未识别到有效链接，请粘贴包含网址的分享口令或 URL")

        opts = self.build_download_options(overrides)
        created_tasks: list[dict[str, Any]] = []
        initial_status = "paused" if start_paused else "queued"

        for idx, item in enumerate(cleaned_list):
            task_id = uuid.uuid4().hex[:12]
            use_prefetch = idx == 0 and len(cleaned_list) == 1
            record = self.db.create_task(
                {
                    "id": task_id,
                    "raw_input": raw_text.strip(),
                    "url": item.normalized_url,
                    "platform_id": item.platform_id,
                    "platform_name": item.platform_name,
                    "title": prefetched_title
                    if (use_prefetch and prefetched_title)
                    else ("已入队 (暂停)" if start_paused else "队列等待解析..."),
                    "uploader": prefetched_uploader if use_prefetch else "",
                    "description": prefetched_description if use_prefetch else "",
                    "thumbnail": prefetched_thumbnail if use_prefetch else "",
                    "duration": prefetched_duration if use_prefetch else 0.0,
                    "quality": opts.quality,
                    "only_audio": opts.only_audio,
                    "audio_format": opts.audio_format,
                    "subtitle": opts.subtitle,
                    "status": initial_status,
                    "trace": prefetched_trace if use_prefetch else None,
                }
            )
            if not start_paused:
                with self._controls_lock:
                    self._controls[task_id] = _TaskRuntimeControl()
                self._queue.put(task_id)
            self.broadcast("task_created", record)
            created_tasks.append(record)
            logger.info("Task created | id=%s | status=%s | platform=%s | url=%s", task_id, initial_status, item.platform_id, item.normalized_url)

        return created_tasks

    def pause_task(self, task_id: str) -> dict[str, Any] | None:
        task = self.db.get_task(task_id)
        if not task:
            return None
        if task["status"] in ("completed", "failed", "canceled", "paused"):
            return task

        with self._controls_lock:
            ctrl = self._controls.get(task_id)
            if ctrl:
                ctrl.stop_reason = "pause"
                ctrl.stop_event.set()

        updated = self.db.update_task(task_id, status="paused", speed="", eta="")
        if updated:
            self.broadcast("task_updated", updated)
        return updated

    def resume_task(self, task_id: str) -> dict[str, Any] | None:
        self.start()
        task = self.db.get_task(task_id)
        if not task:
            return None
        if task["status"] not in ("paused", "interrupted", "failed", "canceled"):
            return task

        with self._controls_lock:
            self._controls[task_id] = _TaskRuntimeControl()

        updated = self.db.update_task(
            task_id,
            status="queued",
            error="",
            error_code="",
            speed="",
            eta="",
            retry_count=0,
        )
        self._queue.put(task_id)
        if updated:
            self.broadcast("task_updated", updated)
        return updated

    def pause_all(self) -> int:
        count = 0
        for t in self.db.list_tasks(limit=200):
            if t.get("status") in ("queued", "parsing", "downloading", "merging", "retrying"):
                self.pause_task(t["id"])
                count += 1
        return count

    def resume_all(self) -> int:
        count = 0
        for t in reversed(self.db.list_tasks(limit=200)):
            if t.get("status") in ("paused", "interrupted"):
                self.resume_task(t["id"])
                count += 1
        return count

    def resume_interrupted_tasks(self) -> int:
        count = 0
        for t in reversed(self.db.list_interrupted_tasks()):
            self.resume_task(t["id"])
            count += 1
        return count

    def toggle_pause_resume_all(self) -> str:
        """Toggle pause/resume across active/paused tasks (for `Ctrl+Shift+P` global hotkey)."""
        if self.has_active_downloads():
            n = self.pause_all()
            return f"已暂停 {n} 个活动任务"
        n = self.resume_all()
        return f"已恢复 {n} 个暂停任务" if n else "当前没有活动或待恢复的任务"

    def cancel_task(self, task_id: str) -> dict[str, Any] | None:
        task = self.db.get_task(task_id)
        if not task:
            return None
        with self._controls_lock:
            ctrl = self._controls.get(task_id)
            if ctrl:
                ctrl.stop_reason = "cancel"
                ctrl.stop_event.set()

        updated = self.db.update_task(task_id, status="canceled", speed="", eta="")
        if updated:
            self.broadcast("task_updated", updated)
        return updated

    def delete_task(self, task_id: str, delete_file: bool = False) -> bool:
        task = self.db.get_task(task_id)
        if task and task["status"] not in ("completed", "failed", "canceled", "interrupted"):
            self.cancel_task(task_id)
        if task and delete_file:
            save_dir = self.db.get_settings().get("save_path", "")
            for key in ("file_path", "subtitle_path"):
                fp = task.get(key) or ""
                resolved = resolve_task_media_path(fp, save_dir) if key == "file_path" else fp
                target = resolved or fp
                if target and os.path.exists(target):
                    try:
                        os.remove(target)
                    except Exception:
                        pass
        ok = self.db.delete_task(task_id)
        if ok:
            self.broadcast("task_deleted", {"id": task_id})
        return ok

    def clean_invalid_records(self) -> int:
        """Remove completed records whose underlying media file was moved/deleted externally."""
        save_dir = self.db.get_settings().get("save_path", "")
        removed = 0
        for t in self.db.list_tasks(limit=1000):
            if t.get("status") != "completed":
                continue
            resolved = resolve_task_media_path(t.get("file_path") or "", save_dir)
            if not resolved:
                if self.db.delete_task(t["id"]):
                    self.broadcast("task_deleted", {"id": t["id"]})
                    removed += 1
        return removed

    def scan_local_folder(self) -> dict[str, Any]:
        """Import any valid media files from `save_path` into the Media Library."""
        self.db.reconcile_completed_tasks()
        save_dir = self.db.get_settings()["save_path"]
        if not os.path.isdir(save_dir):
            return {"added": 0, "save_dir": save_dir}

        existing_paths = {
            os.path.abspath(os.path.normpath(t["file_path"])).lower()
            for t in self.db.list_tasks(limit=1000)
            if t.get("file_path")
        }

        added = 0
        for entry in os.scandir(save_dir):
            if not entry.is_file():
                continue
            ext = Path(entry.name).suffix.lower()
            if ext not in MEDIA_EXTENSIONS:
                continue
            norm = os.path.abspath(os.path.normpath(entry.path))
            if norm.lower() in existing_paths:
                continue

            ok, _ = validate_media_file(norm, min_bytes=2048)
            if not ok:
                continue

            stem = Path(norm).stem
            sub_found = ""
            for sub_ext in SUBTITLE_EXTENSIONS:
                cand = str(Path(norm).with_suffix(sub_ext))
                if os.path.exists(cand):
                    sub_found = cand
                    break

            st = entry.stat()
            rec = self.db.create_task(
                {
                    "id": uuid.uuid4().hex[:12],
                    "raw_input": norm,
                    "url": f"file://{norm}",
                    "platform_id": "local",
                    "platform_name": "本地媒体",
                    "engine_used": "local-scan",
                    "title": stem,
                    "only_audio": ext in (".mp3", ".m4a", ".aac", ".wav", ".flac", ".ogg"),
                    "audio_format": ext.lstrip("."),
                    "status": "completed",
                    "progress": 100.0,
                    "file_path": norm,
                    "file_size": st.st_size,
                    "subtitle_path": sub_found,
                    "created_at": st.st_mtime,
                }
            )
            self.broadcast("task_created", rec)
            added += 1

        return {"added": added, "save_dir": save_dir}

    # -------------------- Worker Loop --------------------

    def _worker_loop(self, worker_index: int) -> None:
        while not self._shutdown.is_set():
            try:
                limit = max(1, min(MAX_WORKER_THREADS, int(self.db.get_settings().get("concurrent_tasks", 3))))
            except Exception:
                limit = 3

            if worker_index >= limit:
                time.sleep(0.35)
                continue

            try:
                task_id = self._queue.get(timeout=0.35)
            except queue.Empty:
                continue

            try:
                self._execute_task(task_id)
            finally:
                self._queue.task_done()

    def _execute_task(self, task_id: str) -> None:
        task = self.db.get_task(task_id)
        if not task or task["status"] in ("paused", "interrupted", "canceled", "completed"):
            return

        with self._controls_lock:
            ctrl = self._controls.get(task_id)
            if not ctrl:
                ctrl = _TaskRuntimeControl()
                self._controls[task_id] = ctrl

        if ctrl.stop_event.is_set():
            return

        opts = self.build_download_options(
            {
                "quality": task["quality"],
                "only_audio": task["only_audio"],
                "audio_format": task["audio_format"],
                "subtitle": task["subtitle"],
            }
        )

        cleaned = clean_share_input(task["url"], resolve_short_links=True)
        target_url = cleaned[0].normalized_url if cleaned else task["url"]
        platform_id = cleaned[0].platform_id if cleaned else task["platform_id"]
        platform_name = cleaned[0].platform_name if cleaned else task["platform_name"]

        updated = self.db.update_task(
            task_id,
            url=target_url,
            platform_id=platform_id,
            platform_name=platform_name,
            status="downloading",
            error="",
            error_code="",
        )
        if updated:
            self.broadcast("task_updated", updated)

        last_emit = [0.0]

        def _on_progress(ev: ProgressEvent) -> None:
            now = time.perf_counter()
            if ev.status == "downloading" and (now - last_emit[0]) < 0.25:
                return
            last_emit[0] = now
            patch: dict[str, Any] = {
                "status": ev.status,
                "progress": ev.progress,
                "speed": ev.speed,
                "eta": ev.eta,
            }
            if ev.title and task.get("title") in ("", "等待解析...", "队列等待解析...", "已入队 (暂停)"):
                patch["title"] = ev.title
            snap = self.db.update_task(task_id, **patch)
            if snap:
                self.broadcast("task_updated", snap)

        try:
            res = self.engine_chain.download(
                url=target_url,
                platform_id=platform_id,
                options=opts,
                stop_event=ctrl.stop_event,
                stop_reason_getter=lambda: ctrl.stop_reason,
                on_progress=_on_progress,
            )
            abs_file = os.path.abspath(res.file_path)
            fsize = os.path.getsize(abs_file) if os.path.exists(abs_file) else 0
            done_patch: dict[str, Any] = {
                "status": "completed",
                "progress": 100.0,
                "speed": "",
                "eta": "",
                "file_path": abs_file,
                "file_size": fsize,
                "subtitle_path": res.subtitle_path,
                "engine_used": res.engine_used,
                "title": res.title or task["title"],
                "error": "",
                "error_code": "",
            }
            if res.trace:
                done_patch["trace"] = res.trace
            if res.thumbnail:
                done_patch["thumbnail"] = res.thumbnail
            if res.duration:
                done_patch["duration"] = res.duration
            if res.uploader:
                done_patch["uploader"] = res.uploader
            if getattr(res, "description", ""):
                done_patch["description"] = res.description

            final_rec = self.db.update_task(task_id, **done_patch)
            if final_rec:
                self.broadcast("task_updated", final_rec)
                self.broadcast("task_completed", final_rec)

        except DownloadCancelledError as cancel_exc:
            new_status = "paused" if cancel_exc.reason == "pause" else "canceled"
            rec = self.db.update_task(task_id, status=new_status, speed="", eta="")
            if rec:
                self.broadcast("task_updated", rec)

        except Exception as exc:
            current = self.db.get_task(task_id)
            if not current or current["status"] in ("paused", "canceled"):
                return

            if isinstance(exc, ResolutionError):
                err_code = exc.primary_code
                is_retryable = exc.retryable
                err_msg = exc.hint
                trace_dict = exc.trace.to_dict()
            else:
                err_code, is_retryable, err_msg = classify_exception(exc, platform_id)
                trace_dict = current.get("trace")

            retries = int(current.get("retry_count") or 0)
            if is_retryable and retries < MAX_AUTO_RETRIES and not ctrl.stop_event.is_set():
                next_retry = retries + 1
                backoff = 3.5 if err_code == ErrorCode.RISK_CONTROL else 1.5
                rec = self.db.update_task(
                    task_id,
                    status="retrying",
                    retry_count=next_retry,
                    speed="",
                    eta="",
                    error_code=str(err_code),
                    trace=trace_dict,
                    error=f"第 {next_retry}/{MAX_AUTO_RETRIES} 次自动重试中 ({err_msg})",
                )
                if rec:
                    self.broadcast("task_updated", rec)
                time.sleep(backoff)
                if not ctrl.stop_event.is_set():
                    self._queue.put(task_id)
                return

            rec = self.db.update_task(
                task_id,
                status="failed",
                speed="",
                eta="",
                error_code=str(err_code),
                trace=trace_dict,
                error=err_msg[:500],
            )
            if rec:
                self.broadcast("task_updated", rec)
            logger.error(
                "Task failed | id=%s | platform=%s | code=%s | retryable=%s | error=%s",
                task_id,
                platform_id,
                err_code,
                is_retryable,
                err_msg[:200],
            )
