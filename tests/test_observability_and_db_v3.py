"""Offline unit tests for Logging Redaction, Whitelisted Diagnostics ZIP & SQLite Schema v3 Migrations."""

from __future__ import annotations

import json
from pathlib import Path
import sqlite3
import zipfile

from videodownloader.core.db import SCHEMA_VERSION, Database
from videodownloader.core.logging_setup import export_diagnostics_zip, redact_sensitive_text, setup_logging


def test_redact_sensitive_tokens_cookies_and_api_keys():
    dirty = (
        "GET https://example.com/video.mp4?xsec_token=SECRET123&sig=ABCDEF999&id=42 "
        "Cookie: SESSDATA=my_bili_sess_9988; bili_jct=csrf_7766; theme=dark "
        "Authorization: Bearer eyJhbGciOiJIUzI1NiIsInR5cCI6IkpXVCJ9 "
        "LLM key sk-proj1234567890abcdef"
    )
    clean = redact_sensitive_text(dirty)
    assert "SECRET123" not in clean
    assert "ABCDEF999" not in clean
    assert "my_bili_sess_9988" not in clean
    assert "csrf_7766" not in clean
    assert "eyJhbGciOiJIUzI1NiIsInR5cCI6IkpXVCJ9" not in clean
    assert "sk-proj1234567890abcdef" not in clean
    assert "id=42" in clean
    assert "xsec_token=***" in clean
    assert "sk-***" in clean


def test_export_diagnostics_zip_never_leaks_secrets(tmp_path: Path, monkeypatch):
    monkeypatch.setenv("VIDEODOWNLOADER_HOME", str(tmp_path))
    log_path = setup_logging()
    log_path.write_text(
        "2026-11-01 | INFO | url=https://a.com/v?access_token=TOP_SECRET_TOKEN&v=1 key=sk-abcdefghijkl12345\n",
        encoding="utf-8",
    )

    sensitive_settings = {
        "quality": "best_1080p",
        "proxy_mode": "auto",
        "proxy_url": "http://user:SUPER_SECRET_PASS@127.0.0.1:7890",
        "cookie_file": r"C:\Users\SecretPerson\cookies.txt",
        "llm_api_key": "sk-liveSecretKey9999999999",
        "asr_api_key": "sk-asrSecretKey8888888888",
        "save_path": r"C:\Users\SecretPerson\PrivateFolder",
        "theme_mode": "dark",
    }
    tasks = [
        {
            "id": "t1",
            "platform_id": "xiaohongshu",
            "status": "failed",
            "error_code": "auth_required",
            "url": "https://www.xiaohongshu.com/explore/123?xsec_token=XHS_PRIV_TOKEN",
            "file_path": r"C:\Users\SecretPerson\PrivateFolder\secret.mp4",
            "error": "Failed with token=ERR_SECRET_111",
        }
    ]
    zip_out = export_diagnostics_zip(
        settings=sensitive_settings,
        toolchain_status={"ytdlp_active_version": "2026.10.26"},
        recent_tasks=tasks,
        recent_traces=[{"url": "https://v.com/?token=TRACE_SECRET_222", "stages": []}],
        dest_zip_path=tmp_path / "diag_test.zip",
    )
    assert zip_out.is_file()

    with zipfile.ZipFile(zip_out, "r") as zf:
        names = set(zf.namelist())
        assert {"app.log", "config.json", "system.json", "tasks.json", "trace.json"} == names
        combined = "\n".join(zf.read(n).decode("utf-8") for n in names)

    for forbidden in (
        "TOP_SECRET_TOKEN",
        "SUPER_SECRET_PASS",
        "SecretPerson",
        "sk-liveSecretKey9999999999",
        "sk-asrSecretKey8888888888",
        "XHS_PRIV_TOKEN",
        "ERR_SECRET_111",
        "TRACE_SECRET_222",
    ):
        assert forbidden not in combined

    with zipfile.ZipFile(zip_out, "r") as zf:
        cfg_exported = json.loads(zf.read("config.json").decode("utf-8"))
    assert cfg_exported["has_cookie_file"] is True
    assert cfg_exported["has_custom_proxy"] is True
    assert "llm_api_key" not in cfg_exported


def test_sqlite_v2_to_v3_migration_and_interrupted_recovery(tmp_path: Path, monkeypatch):
    monkeypatch.setenv("VIDEODOWNLOADER_HOME", str(tmp_path))
    legacy_db_path = tmp_path / "legacy_v2.db"

    # Create a legacy v2 schema WITHOUT `error_code`, `trace_json`, or `user_version`
    conn = sqlite3.connect(str(legacy_db_path))
    conn.executescript(
        """
        CREATE TABLE settings (key TEXT PRIMARY KEY, value TEXT NOT NULL);
        CREATE TABLE tasks (
            id TEXT PRIMARY KEY,
            raw_input TEXT NOT NULL,
            url TEXT NOT NULL,
            platform_id TEXT NOT NULL DEFAULT 'youtube',
            platform_name TEXT NOT NULL DEFAULT 'YouTube',
            engine_used TEXT NOT NULL DEFAULT '',
            title TEXT NOT NULL DEFAULT 'Crashed Mid Download',
            uploader TEXT NOT NULL DEFAULT '',
            thumbnail TEXT NOT NULL DEFAULT '',
            duration REAL NOT NULL DEFAULT 0,
            quality TEXT NOT NULL DEFAULT 'best_1080p',
            only_audio INTEGER NOT NULL DEFAULT 0,
            audio_format TEXT NOT NULL DEFAULT 'mp3',
            subtitle INTEGER NOT NULL DEFAULT 1,
            status TEXT NOT NULL DEFAULT 'downloading',
            progress REAL NOT NULL DEFAULT 42.5,
            speed TEXT NOT NULL DEFAULT '3.2 MB/s',
            eta TEXT NOT NULL DEFAULT '00:15',
            file_path TEXT NOT NULL DEFAULT '',
            file_size INTEGER NOT NULL DEFAULT 0,
            subtitle_path TEXT NOT NULL DEFAULT '',
            transcript_json TEXT NOT NULL DEFAULT '',
            ai_summary_json TEXT NOT NULL DEFAULT '',
            retry_count INTEGER NOT NULL DEFAULT 0,
            error TEXT NOT NULL DEFAULT '',
            created_at REAL NOT NULL DEFAULT 1000.0,
            updated_at REAL NOT NULL DEFAULT 1000.0
        );
        INSERT INTO tasks (id, raw_input, url) VALUES ('crashed_01', 'https://youtu.be/abc', 'https://youtu.be/abc');
        """
    )
    conn.commit()
    conn.close()

    # Boot v3 Database over the v2 file
    db = Database(db_path=legacy_db_path)
    assert db.get_schema_version() == SCHEMA_VERSION
    assert db.interrupted_on_boot_ids == ["crashed_01"]

    recovered = db.get_task("crashed_01")
    assert recovered is not None
    assert recovered["status"] == "interrupted"
    assert recovered["error_code"] == ""
    assert recovered["trace"] is None
    assert len(db.list_interrupted_tasks()) == 1


def test_clear_all_history_preserves_downloaded_files_and_vacuums(tmp_path: Path, monkeypatch):
    monkeypatch.setenv("VIDEODOWNLOADER_HOME", str(tmp_path))
    db = Database(db_path=tmp_path / "vacuum_test.db")
    media_file = tmp_path / "keep_me.mp4"
    media_file.write_bytes(b"\x00\x00\x00\x18ftypmp42" + b"M" * 4096)

    db.create_task(
        {
            "id": "done_1",
            "url": "https://youtu.be/1",
            "status": "completed",
            "file_path": str(media_file),
        }
    )
    db.set_prefetch_cache("https://youtu.be/1", {"title": "Cached"}, b"thumb")

    deleted = db.clear_all_history(include_active=False)
    assert deleted == 1
    assert db.list_tasks() == []
    assert db.get_prefetch_cache("https://youtu.be/1") is None
    assert media_file.is_file()  # User's downloaded video MUST remain untouched!


def test_prefetch_cache_ttl_and_clipboard_suppression(tmp_path: Path, monkeypatch):
    monkeypatch.setenv("VIDEODOWNLOADER_HOME", str(tmp_path))
    db = Database(db_path=tmp_path / "cache_test.db")

    db.set_prefetch_cache("https://youtu.be/live", {"media": {"title": "Fast"}}, b"123", ttl_seconds=60.0)
    hit = db.get_prefetch_cache("https://youtu.be/live")
    assert hit is not None
    assert hit["payload"]["media"]["title"] == "Fast"
    assert hit["thumbnail_bytes"] == b"123"

    # Expired TTL entry is auto-evicted on read
    db.set_prefetch_cache("https://youtu.be/old", {"media": {"title": "Old"}}, ttl_seconds=-5.0)
    assert db.get_prefetch_cache("https://youtu.be/old") is None

    # URL 24h suppression & permanent domain blacklist
    db.add_clipboard_suppression("url", "https://youtu.be/dup", ttl_seconds=3600.0)
    assert db.is_clipboard_suppressed("url", "https://youtu.be/dup") is True

    db.add_clipboard_suppression("domain", "example.org", ttl_seconds=0.0)
    assert "example.org" in db.list_suppressed_domains()
    db.remove_clipboard_suppression("domain", "example.org")
    assert "example.org" not in db.list_suppressed_domains()
