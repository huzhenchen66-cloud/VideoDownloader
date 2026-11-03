"""Thread-safe SQLite persistence (Schema v3: PRAGMA user_version migration, ResolutionTrace, Prefetch Cache & Clipboard Suppression)."""

from __future__ import annotations

import hashlib
import json
import os
from pathlib import Path
import sqlite3
import threading
import time
from typing import Any

from .logging_setup import get_logger
from .os_utils import resolve_task_media_path, validate_media_file
from .paths import get_db_path, get_downloads_dir

logger = get_logger("db")

SCHEMA_VERSION = 4

DEFAULT_HOTKEYS: dict[str, str] = {
    "capture": "Ctrl+Shift+D",
    "paste_capture": "Ctrl+Shift+V",
    "show": "Ctrl+Shift+S",
    "pause": "Ctrl+Shift+P",
}

DEFAULT_SETTINGS: dict[str, Any] = {
    "save_path": "",  # populated dynamically from get_downloads_dir() when empty
    "quality": "best_1080p",
    "only_audio": False,
    "audio_format": "mp3",
    "subtitle": True,
    "fragment_threads": 8,
    "concurrent_tasks": 3,
    # Network Proxy settings (auto = overseas via system/custom proxy, domestic CN direct!)
    "proxy_mode": "auto",  # auto | system | custom | direct
    "proxy_url": "",
    # Cookie authentication
    "browser_cookie": "none",  # none | edge | chrome | firefox | brave | safari
    "cookie_file": "",
    # Desktop App Essentials (v3.0)
    "hotkey_enabled": True,
    "hotkeys": dict(DEFAULT_HOTKEYS),
    "clipboard_watch": True,
    "clipboard_prefetch": True,
    "clipboard_toast": True,
    "close_to_tray": True,
    "close_to_tray_notified": False,
    "auto_check_update": True,
    "update_channel": "stable",
    "github_repo": "",
    "theme_mode": "light",  # light | dark
    "theme_style": "miku",  # miku | sakura | violet | amber | azure
    "sound_enabled": True,
    "sound_volume": 70,
    "custom_wallpaper_path": "",
    "onboarding_done": False,
    # Optional Web / iPhone LAN Server (Default OFF so Desktop UI opens cleanly without browser jump!)
    "web_server_auto_start": False,
    "web_server_port": 8765,
    # AI Workspace settings (OpenAI-compatible: DashScope / DeepSeek / Qwen / SiliconFlow / Ollama / OpenAI)
    "llm_provider": "dashscope",
    "llm_base_url": "https://dashscope.aliyuncs.com/compatible-mode/v1",
    "llm_api_key": "",
    "llm_model": "deepseek-v4.1-flash-1017",
    # Optional Cloud ASR (SiliconFlow SenseVoice / Groq Whisper / OpenAI Whisper)
    "asr_base_url": "https://api.siliconflow.cn/v1",
    "asr_api_key": "",
    "asr_model": "FunAudioLLM/SenseVoiceSmall",
}


def hash_url_key(url: str) -> str:
    return hashlib.sha256((url or "").strip().encode("utf-8")).hexdigest()[:32]


class Database:
    """Lightweight SQLite store with WAL mode and `PRAGMA user_version` schema migrations."""

    def __init__(self, db_path: Path | None = None) -> None:
        self.db_path = db_path or get_db_path()
        self.db_path.parent.mkdir(parents=True, exist_ok=True)
        self._lock = threading.RLock()
        self.interrupted_on_boot_ids: list[str] = []
        self._init_schema()
        self.reconcile_completed_tasks()

    def _connect(self) -> sqlite3.Connection:
        conn = sqlite3.connect(str(self.db_path), timeout=30.0, check_same_thread=False)
        conn.row_factory = sqlite3.Row
        conn.execute("PRAGMA journal_mode=WAL;")
        conn.execute("PRAGMA synchronous=NORMAL;")
        return conn

    def get_schema_version(self) -> int:
        with self._lock, self._connect() as conn:
            row = conn.execute("PRAGMA user_version;").fetchone()
            return int(row[0]) if row else 0

    def _init_schema(self) -> None:
        with self._lock, self._connect() as conn:
            conn.executescript(
                """
                CREATE TABLE IF NOT EXISTS settings (
                    key TEXT PRIMARY KEY,
                    value TEXT NOT NULL
                );

                CREATE TABLE IF NOT EXISTS tasks (
                    id TEXT PRIMARY KEY,
                    raw_input TEXT NOT NULL,
                    url TEXT NOT NULL,
                    platform_id TEXT NOT NULL DEFAULT 'generic',
                    platform_name TEXT NOT NULL DEFAULT '通用视频',
                    engine_used TEXT NOT NULL DEFAULT '',
                    title TEXT NOT NULL DEFAULT '等待解析...',
                    description TEXT NOT NULL DEFAULT '',
                    uploader TEXT NOT NULL DEFAULT '',
                    thumbnail TEXT NOT NULL DEFAULT '',
                    duration REAL NOT NULL DEFAULT 0,
                    quality TEXT NOT NULL DEFAULT 'best_1080p',
                    only_audio INTEGER NOT NULL DEFAULT 0,
                    audio_format TEXT NOT NULL DEFAULT 'mp3',
                    subtitle INTEGER NOT NULL DEFAULT 1,
                    status TEXT NOT NULL DEFAULT 'queued',
                    progress REAL NOT NULL DEFAULT 0,
                    speed TEXT NOT NULL DEFAULT '',
                    eta TEXT NOT NULL DEFAULT '',
                    file_path TEXT NOT NULL DEFAULT '',
                    file_size INTEGER NOT NULL DEFAULT 0,
                    subtitle_path TEXT NOT NULL DEFAULT '',
                    transcript_json TEXT NOT NULL DEFAULT '',
                    ai_summary_json TEXT NOT NULL DEFAULT '',
                    ai_storyboard_json TEXT NOT NULL DEFAULT '',
                    ai_clean_script_json TEXT NOT NULL DEFAULT '',
                    custom_script TEXT NOT NULL DEFAULT '',
                    retry_count INTEGER NOT NULL DEFAULT 0,
                    error TEXT NOT NULL DEFAULT '',
                    error_code TEXT NOT NULL DEFAULT '',
                    trace_json TEXT NOT NULL DEFAULT '',
                    created_at REAL NOT NULL,
                    updated_at REAL NOT NULL
                );

                CREATE INDEX IF NOT EXISTS idx_tasks_created_at ON tasks(created_at DESC);
                CREATE INDEX IF NOT EXISTS idx_tasks_status ON tasks(status);

                CREATE TABLE IF NOT EXISTS prefetch_cache (
                    url_hash TEXT PRIMARY KEY,
                    url TEXT NOT NULL,
                    payload_json TEXT NOT NULL,
                    thumbnail_blob BLOB,
                    is_negative INTEGER NOT NULL DEFAULT 0,
                    created_at REAL NOT NULL,
                    expires_at REAL NOT NULL
                );
                CREATE INDEX IF NOT EXISTS idx_prefetch_expires ON prefetch_cache(expires_at);

                CREATE TABLE IF NOT EXISTS clipboard_suppressed (
                    kind TEXT NOT NULL,
                    value TEXT NOT NULL,
                    expires_at REAL NOT NULL,
                    PRIMARY KEY (kind, value)
                );
                """
            )

            # Incremental schema migration for databases created by v1/v2/v3 (`user_version < 4`)
            cur_ver = int(conn.execute("PRAGMA user_version;").fetchone()[0])
            if cur_ver < SCHEMA_VERSION:
                existing_cols = {
                    r["name"] for r in conn.execute("PRAGMA table_info(tasks);").fetchall()
                }
                if "error_code" not in existing_cols:
                    conn.execute("ALTER TABLE tasks ADD COLUMN error_code TEXT NOT NULL DEFAULT '';")
                if "trace_json" not in existing_cols:
                    conn.execute("ALTER TABLE tasks ADD COLUMN trace_json TEXT NOT NULL DEFAULT '';")
                if "description" not in existing_cols:
                    conn.execute("ALTER TABLE tasks ADD COLUMN description TEXT NOT NULL DEFAULT '';")
                if "ai_storyboard_json" not in existing_cols:
                    conn.execute("ALTER TABLE tasks ADD COLUMN ai_storyboard_json TEXT NOT NULL DEFAULT '';")
                if "ai_clean_script_json" not in existing_cols:
                    conn.execute("ALTER TABLE tasks ADD COLUMN ai_clean_script_json TEXT NOT NULL DEFAULT '';")
                if "custom_script" not in existing_cols:
                    conn.execute("ALTER TABLE tasks ADD COLUMN custom_script TEXT NOT NULL DEFAULT '';")
                conn.execute(f"PRAGMA user_version = {SCHEMA_VERSION};")
                logger.info("Migrated SQLite schema from v%d -> v%d", cur_ver, SCHEMA_VERSION)

            # Prune expired prefetch & clipboard suppression entries on startup
            now = time.time()
            conn.execute("DELETE FROM prefetch_cache WHERE expires_at > 0 AND expires_at < ?", (now,))
            conn.execute("DELETE FROM clipboard_suppressed WHERE expires_at > 0 AND expires_at < ?", (now,))

            # Detect active tasks interrupted by an abrupt process exit -> mark `'interrupted'`
            crashed_rows = conn.execute(
                "SELECT id FROM tasks WHERE status IN ('queued', 'parsing', 'downloading', 'merging', 'retrying')"
            ).fetchall()
            if crashed_rows:
                self.interrupted_on_boot_ids = [r["id"] for r in crashed_rows]
                conn.execute(
                    """
                    UPDATE tasks
                    SET status = 'interrupted', speed = '', eta = '', updated_at = ?
                    WHERE status IN ('queued', 'parsing', 'downloading', 'merging', 'retrying')
                    """,
                    (now,),
                )
                logger.info(
                    "Marked %d unfinished tasks as 'interrupted' for recovery prompt",
                    len(self.interrupted_on_boot_ids),
                )
            conn.commit()

    def reconcile_completed_tasks(self) -> None:
        """Auto-heal moved file paths across restarts and flag any corrupt HTML-as-MP4 artifacts."""
        save_dir = self.get_settings().get("save_path", "") or str(get_downloads_dir())
        with self._lock, self._connect() as conn:
            rows = conn.execute(
                "SELECT id, file_path, file_size FROM tasks WHERE status = 'completed'"
            ).fetchall()
            now = time.time()
            for r in rows:
                tid = r["id"]
                stored = r["file_path"] or ""
                resolved = resolve_task_media_path(stored, save_dir)
                if not resolved:
                    continue
                ok, reason = validate_media_file(resolved, min_bytes=2048)
                if not ok:
                    try:
                        os.remove(resolved)
                    except OSError:
                        pass
                    conn.execute(
                        "UPDATE tasks SET status = 'failed', progress = 0, file_size = 0, "
                        "error = ?, error_code = 'format_invalid', updated_at = ? WHERE id = ?",
                        (f"{reason}，请点击重试重新下载", now, tid),
                    )
                elif resolved != stored or not r["file_size"]:
                    try:
                        sz = os.path.getsize(resolved)
                    except OSError:
                        sz = r["file_size"] or 0
                    conn.execute(
                        "UPDATE tasks SET file_path = ?, file_size = ?, updated_at = ? WHERE id = ?",
                        (resolved, sz, now, tid),
                    )
            conn.commit()

    # -------------------- Settings --------------------

    def get_settings(self) -> dict[str, Any]:
        result = dict(DEFAULT_SETTINGS)
        result["hotkeys"] = dict(DEFAULT_HOTKEYS)
        if not result["save_path"]:
            result["save_path"] = str(get_downloads_dir())

        with self._lock, self._connect() as conn:
            rows = conn.execute("SELECT key, value FROM settings").fetchall()
        for row in rows:
            try:
                val = json.loads(row["value"])
            except Exception:
                val = row["value"]
            if row["key"] == "hotkeys" and isinstance(val, dict):
                merged_hk = dict(DEFAULT_HOTKEYS)
                merged_hk.update({str(k): str(v) for k, v in val.items() if v is not None})
                result["hotkeys"] = merged_hk
            else:
                result[row["key"]] = val

        if not result.get("save_path"):
            result["save_path"] = str(get_downloads_dir())
        return result

    def update_settings(self, patch: dict[str, Any]) -> dict[str, Any]:
        current = self.get_settings()
        for k, v in patch.items():
            if v is None:
                continue
            if k == "hotkeys" and isinstance(v, dict):
                merged = dict(current.get("hotkeys") or DEFAULT_HOTKEYS)
                merged.update({str(hk): str(hv) for hk, hv in v.items() if hv is not None})
                current["hotkeys"] = merged
            else:
                current[k] = v

        with self._lock, self._connect() as conn:
            for k, v in current.items():
                conn.execute(
                    "INSERT INTO settings(key, value) VALUES(?, ?) "
                    "ON CONFLICT(key) DO UPDATE SET value=excluded.value",
                    (k, json.dumps(v, ensure_ascii=False)),
                )
            conn.commit()
        return current

    # -------------------- Tasks --------------------

    def _row_to_task(self, row: sqlite3.Row, save_dir: str = "") -> dict[str, Any]:
        d = dict(row)
        d["only_audio"] = bool(d.get("only_audio"))
        d["subtitle"] = bool(d.get("subtitle"))
        d.setdefault("error_code", "")
        fp = d.get("file_path") or ""
        if fp:
            resolved = resolve_task_media_path(fp, save_dir)
            if resolved:
                d["file_path"] = resolved
                d["file_exists"] = True
            else:
                d["file_exists"] = False
        else:
            d["file_exists"] = False

        for json_col, out_key in (
            ("transcript_json", "transcript"),
            ("ai_summary_json", "ai_summary"),
            ("trace_json", "trace"),
            ("ai_storyboard_json", "ai_storyboard"),
            ("ai_clean_script_json", "ai_clean_script"),
        ):
            raw = d.pop(json_col, "") or ""
            if raw:
                try:
                    d[out_key] = json.loads(raw)
                except Exception:
                    d[out_key] = None
            else:
                d[out_key] = None
        d.setdefault("description", "")
        d.setdefault("custom_script", "")
        return d

    def create_task(self, task: dict[str, Any]) -> dict[str, Any]:
        now = time.time()
        trace_val = task.get("trace")
        record = {
            "id": task["id"],
            "raw_input": task.get("raw_input", task["url"]),
            "url": task["url"],
            "platform_id": task.get("platform_id", "generic"),
            "platform_name": task.get("platform_name", "通用视频"),
            "engine_used": task.get("engine_used", ""),
            "title": task.get("title") or "等待解析...",
            "description": task.get("description") or "",
            "uploader": task.get("uploader") or "",
            "thumbnail": task.get("thumbnail") or "",
            "duration": float(task.get("duration") or 0),
            "quality": task.get("quality") or "best_1080p",
            "only_audio": 1 if task.get("only_audio") else 0,
            "audio_format": task.get("audio_format") or "mp3",
            "subtitle": 1 if task.get("subtitle", True) else 0,
            "status": task.get("status") or "queued",
            "progress": float(task.get("progress") or 0),
            "speed": task.get("speed") or "",
            "eta": task.get("eta") or "",
            "file_path": task.get("file_path") or "",
            "file_size": int(task.get("file_size") or 0),
            "subtitle_path": task.get("subtitle_path") or "",
            "transcript_json": json.dumps(task.get("transcript"), ensure_ascii=False) if task.get("transcript") else "",
            "ai_summary_json": json.dumps(task.get("ai_summary"), ensure_ascii=False) if task.get("ai_summary") else "",
            "ai_storyboard_json": json.dumps(task.get("ai_storyboard"), ensure_ascii=False) if task.get("ai_storyboard") else "",
            "ai_clean_script_json": json.dumps(task.get("ai_clean_script"), ensure_ascii=False) if task.get("ai_clean_script") else "",
            "custom_script": task.get("custom_script") or "",
            "retry_count": int(task.get("retry_count") or 0),
            "error": task.get("error") or "",
            "error_code": task.get("error_code") or "",
            "trace_json": json.dumps(trace_val, ensure_ascii=False) if trace_val else "",
            "created_at": task.get("created_at", now),
            "updated_at": now,
        }
        cols = ", ".join(record.keys())
        placeholders = ", ".join("?" for _ in record)
        save_dir = self.get_settings().get("save_path", "")
        with self._lock, self._connect() as conn:
            conn.execute(f"INSERT INTO tasks ({cols}) VALUES ({placeholders})", tuple(record.values()))
            conn.commit()
            row = conn.execute("SELECT * FROM tasks WHERE id = ?", (record["id"],)).fetchone()
        return self._row_to_task(row, save_dir)

    def update_task(self, task_id: str, **fields: Any) -> dict[str, Any] | None:
        if not fields:
            return self.get_task(task_id)

        mapped: dict[str, Any] = {}
        for k, v in fields.items():
            if k == "only_audio":
                mapped["only_audio"] = 1 if v else 0
            elif k == "subtitle":
                mapped["subtitle"] = 1 if v else 0
            elif k == "transcript":
                mapped["transcript_json"] = json.dumps(v, ensure_ascii=False) if v is not None else ""
            elif k == "ai_summary":
                mapped["ai_summary_json"] = json.dumps(v, ensure_ascii=False) if v is not None else ""
            elif k == "trace":
                mapped["trace_json"] = json.dumps(v, ensure_ascii=False) if v is not None else ""
            elif k == "ai_storyboard":
                mapped["ai_storyboard_json"] = json.dumps(v, ensure_ascii=False) if v is not None else ""
            elif k == "ai_clean_script":
                mapped["ai_clean_script_json"] = json.dumps(v, ensure_ascii=False) if v is not None else ""
            else:
                mapped[k] = v

        with self._lock, self._connect() as conn:
            valid_cols = {r["name"] for r in conn.execute("PRAGMA table_info(tasks);").fetchall()}

        filtered = {k: v for k, v in mapped.items() if k in valid_cols}
        if not filtered:
            return self.get_task(task_id)

        filtered["updated_at"] = time.time()
        assignments = ", ".join(f"{col} = ?" for col in filtered.keys())
        params = list(filtered.values()) + [task_id]
        save_dir = self.get_settings().get("save_path", "")

        with self._lock, self._connect() as conn:
            conn.execute(f"UPDATE tasks SET {assignments} WHERE id = ?", params)
            conn.commit()
            row = conn.execute("SELECT * FROM tasks WHERE id = ?", (task_id,)).fetchone()
        return self._row_to_task(row, save_dir) if row else None

    def get_task(self, task_id: str) -> dict[str, Any] | None:
        save_dir = self.get_settings().get("save_path", "")
        with self._lock, self._connect() as conn:
            row = conn.execute("SELECT * FROM tasks WHERE id = ?", (task_id,)).fetchone()
        return self._row_to_task(row, save_dir) if row else None

    def list_tasks(self, limit: int = 300) -> list[dict[str, Any]]:
        save_dir = self.get_settings().get("save_path", "")
        with self._lock, self._connect() as conn:
            rows = conn.execute(
                "SELECT * FROM tasks ORDER BY created_at DESC LIMIT ?",
                (limit,),
            ).fetchall()
        return [self._row_to_task(r, save_dir) for r in rows]

    def list_interrupted_tasks(self) -> list[dict[str, Any]]:
        save_dir = self.get_settings().get("save_path", "")
        with self._lock, self._connect() as conn:
            rows = conn.execute(
                "SELECT * FROM tasks WHERE status = 'interrupted' ORDER BY updated_at DESC"
            ).fetchall()
        return [self._row_to_task(r, save_dir) for r in rows]

    def delete_task(self, task_id: str) -> bool:
        with self._lock, self._connect() as conn:
            cur = conn.execute("DELETE FROM tasks WHERE id = ?", (task_id,))
            conn.commit()
            return cur.rowcount > 0

    def clear_all_history(self, include_active: bool = False) -> int:
        """Delete task history records (preserving downloaded files on disk) and compact SQLite via `VACUUM`."""
        with self._lock, self._connect() as conn:
            if include_active:
                cur = conn.execute("DELETE FROM tasks;")
            else:
                cur = conn.execute(
                    "DELETE FROM tasks WHERE status IN ('completed', 'failed', 'canceled', 'interrupted', 'paused');"
                )
            deleted = cur.rowcount
            conn.execute("DELETE FROM prefetch_cache;")
            conn.commit()

        # Run VACUUM outside an open transaction to reclaim disk space cleanly
        with self._lock:
            vconn = sqlite3.connect(str(self.db_path), timeout=30.0, isolation_level=None)
            try:
                vconn.execute("VACUUM;")
            finally:
                vconn.close()
        logger.info("Cleared %d task history records and executed VACUUM", deleted)
        return deleted

    # -------------------- Speculative Prefetch Cache (7d TTL / 10m Negative TTL) --------------------

    def get_prefetch_cache(self, url: str) -> dict[str, Any] | None:
        key = hash_url_key(url)
        now = time.time()
        with self._lock, self._connect() as conn:
            row = conn.execute(
                "SELECT payload_json, thumbnail_blob, is_negative, expires_at FROM prefetch_cache WHERE url_hash = ?",
                (key,),
            ).fetchone()
            if not row:
                return None
            if row["expires_at"] and float(row["expires_at"]) < now:
                conn.execute("DELETE FROM prefetch_cache WHERE url_hash = ?", (key,))
                conn.commit()
                return None
            try:
                payload = json.loads(row["payload_json"])
            except Exception:
                return None
            return {
                "payload": payload,
                "thumbnail_bytes": bytes(row["thumbnail_blob"]) if row["thumbnail_blob"] else b"",
                "is_negative": bool(row["is_negative"]),
            }

    def set_prefetch_cache(
        self,
        url: str,
        payload: dict[str, Any],
        thumbnail_bytes: bytes = b"",
        is_negative: bool = False,
        ttl_seconds: float | None = None,
    ) -> None:
        key = hash_url_key(url)
        now = time.time()
        ttl = ttl_seconds if ttl_seconds is not None else (600.0 if is_negative else 7 * 86400.0)
        blob = thumbnail_bytes if (thumbnail_bytes and len(thumbnail_bytes) <= 256 * 1024) else None
        with self._lock, self._connect() as conn:
            conn.execute(
                """
                INSERT INTO prefetch_cache(url_hash, url, payload_json, thumbnail_blob, is_negative, created_at, expires_at)
                VALUES (?, ?, ?, ?, ?, ?, ?)
                ON CONFLICT(url_hash) DO UPDATE SET
                    payload_json=excluded.payload_json,
                    thumbnail_blob=excluded.thumbnail_blob,
                    is_negative=excluded.is_negative,
                    created_at=excluded.created_at,
                    expires_at=excluded.expires_at
                """,
                (
                    key,
                    url,
                    json.dumps(payload, ensure_ascii=False),
                    blob,
                    1 if is_negative else 0,
                    now,
                    now + ttl,
                ),
            )
            conn.commit()

    # -------------------- Clipboard Suppression (24h URL Dedup & Domain Blacklist) --------------------

    def add_clipboard_suppression(self, kind: str, value: str, ttl_seconds: float = 86400.0) -> None:
        """`kind`: `'url'` (default 24h TTL) or `'domain'` (`ttl_seconds=0` means permanent blacklist)."""
        norm_val = (value or "").strip().lower()
        if not norm_val:
            return
        expires = (time.time() + ttl_seconds) if ttl_seconds > 0 else 0.0
        with self._lock, self._connect() as conn:
            conn.execute(
                """
                INSERT INTO clipboard_suppressed(kind, value, expires_at)
                VALUES (?, ?, ?)
                ON CONFLICT(kind, value) DO UPDATE SET expires_at=excluded.expires_at
                """,
                (kind, norm_val, expires),
            )
            conn.commit()

    def is_clipboard_suppressed(self, kind: str, value: str) -> bool:
        norm_val = (value or "").strip().lower()
        if not norm_val:
            return False
        now = time.time()
        with self._lock, self._connect() as conn:
            row = conn.execute(
                "SELECT expires_at FROM clipboard_suppressed WHERE kind = ? AND value = ?",
                (kind, norm_val),
            ).fetchone()
            if not row:
                return False
            exp = float(row["expires_at"] or 0.0)
            if exp > 0 and exp < now:
                conn.execute("DELETE FROM clipboard_suppressed WHERE kind = ? AND value = ?", (kind, norm_val))
                conn.commit()
                return False
            return True

    def list_suppressed_domains(self) -> list[str]:
        with self._lock, self._connect() as conn:
            rows = conn.execute(
                "SELECT value FROM clipboard_suppressed WHERE kind = 'domain' ORDER BY value ASC"
            ).fetchall()
        return [str(r["value"]) for r in rows]

    def remove_clipboard_suppression(self, kind: str, value: str) -> None:
        norm_val = (value or "").strip().lower()
        with self._lock, self._connect() as conn:
            conn.execute("DELETE FROM clipboard_suppressed WHERE kind = ? AND value = ?", (kind, norm_val))
            conn.commit()
