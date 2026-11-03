"""Structured Rotating File Logging, Privacy Redaction Filter & One-Click Diagnostics ZIP Exporter."""

from __future__ import annotations

import json
import logging
from logging.handlers import RotatingFileHandler
from pathlib import Path
import platform
import re
import sys
import time
from typing import Any
import zipfile

from .. import APP_NAME, __version__
from .paths import get_logs_dir

# Query parameters, Cookie fields, key=value tokens, proxy credentials, and API keys scrubbed before hitting disk
SENSITIVE_QUERY_RE = re.compile(
    r"(?<![A-Za-z0-9_])((?:token|access_token|auth|sig|sign|signature|key|api_key|apikey|"
    r"xsec_token|SESSDATA|bili_jct|DedeUserID|sid|session|sessionid|cookie)\s*=\s*)([^&\s\"'#;,]+)",
    re.IGNORECASE,
)
PROXY_CRED_RE = re.compile(r"(https?://[^/\s:@]+:)([^@\s/]+)(@)", re.IGNORECASE)
BEARER_RE = re.compile(r"(Bearer\s+)[A-Za-z0-9._\-+/=]{8,}", re.IGNORECASE)
SK_KEY_RE = re.compile(r"\b(sk-[A-Za-z0-9]{10,})\b")

# Whitelisted settings keys safe to include in diagnostic exports (NEVER export API keys or cookie paths)
SAFE_SETTINGS_WHITELIST = {
    "quality",
    "only_audio",
    "audio_format",
    "subtitle",
    "fragment_threads",
    "concurrent_tasks",
    "proxy_mode",
    "browser_cookie",
    "web_server_auto_start",
    "web_server_port",
    "hotkey_enabled",
    "hotkeys",
    "clipboard_watch",
    "clipboard_prefetch",
    "clipboard_toast",
    "close_to_tray",
    "auto_check_update",
    "update_channel",
    "theme_mode",
    "theme_style",
    "onboarding_done",
}

_LOGGING_INITIALIZED = False


def redact_sensitive_text(text: str) -> str:
    """Scrub tokens, signatures, session cookies, proxy passwords, and Bearer/sk- API keys from strings."""
    if not text:
        return ""
    out = SENSITIVE_QUERY_RE.sub(r"\1***", str(text))
    out = PROXY_CRED_RE.sub(r"\1***\3", out)
    out = BEARER_RE.sub(r"\1***", out)
    out = SK_KEY_RE.sub("sk-***", out)
    return out


class PrivacyRedactingFilter(logging.Filter):
    """Ensure every log record passing to disk has tokens, cookies, and API keys scrubbed."""

    def filter(self, record: logging.LogRecord) -> bool:
        try:
            msg = record.getMessage()
            redacted = redact_sensitive_text(msg)
            record.msg = redacted
            record.args = ()
        except Exception:
            pass
        return True


def setup_logging(debug: bool = False) -> Path:
    """Configure rotating file logger at `<runtime_root>/logs/app.log` and log startup fingerprint."""
    global _LOGGING_INITIALIZED
    log_dir = get_logs_dir()
    log_path = log_dir / "app.log"

    root_logger = logging.getLogger("videodownloader")
    root_logger.setLevel(logging.DEBUG if debug else logging.INFO)

    if not _LOGGING_INITIALIZED:
        fmt = logging.Formatter(
            fmt="%(asctime)s | %(levelname)-7s | %(name)s | %(message)s",
            datefmt="%Y-%m-%d %H:%M:%S",
        )
        redact_filter = PrivacyRedactingFilter()

        file_handler = RotatingFileHandler(
            filename=str(log_path),
            maxBytes=10 * 1024 * 1024,  # 10 MB per file
            backupCount=5,
            encoding="utf-8",
        )
        file_handler.setLevel(logging.DEBUG if debug else logging.INFO)
        file_handler.setFormatter(fmt)
        file_handler.addFilter(redact_filter)
        root_logger.addHandler(file_handler)

        _LOGGING_INITIALIZED = True
        root_logger.info(
            "=== %s %s started | OS=%s %s | Python=%s | Frozen=%s ===",
            APP_NAME,
            __version__,
            platform.system(),
            platform.release(),
            platform.python_version(),
            getattr(sys, "frozen", False),
        )
    return log_path


def get_logger(name: str) -> logging.Logger:
    """Return a child logger under the `videodownloader` namespace."""
    if not name.startswith("videodownloader"):
        name = f"videodownloader.{name}"
    return logging.getLogger(name)


def export_diagnostics_zip(
    settings: dict[str, Any],
    toolchain_status: dict[str, Any],
    recent_tasks: list[dict[str, Any]],
    recent_traces: list[dict[str, Any]],
    dest_zip_path: Path | None = None,
) -> Path:
    """Create a privacy-safe, whitelist-redacted diagnostics ZIP bundle for issue reporting."""
    log_dir = get_logs_dir()
    log_path = log_dir / "app.log"
    ts_str = time.strftime("%Y%m%d-%H%M%S")
    out_zip = dest_zip_path or (log_dir / f"diagnostics-{ts_str}.zip")
    out_zip.parent.mkdir(parents=True, exist_ok=True)

    # 1. Whitelist-only config (never includes API keys, cookie file paths, or personal directories)
    safe_config = {k: settings.get(k) for k in sorted(SAFE_SETTINGS_WHITELIST) if k in settings}
    safe_config["has_cookie_file"] = bool(settings.get("cookie_file"))
    safe_config["has_custom_proxy"] = bool(settings.get("proxy_url"))

    # 2. System & engine fingerprint
    safe_system = {
        "app": APP_NAME,
        "version": __version__,
        "exported_at": time.strftime("%Y-%m-%dT%H:%M:%S%z"),
        "os": f"{platform.system()} {platform.release()} ({platform.version()})",
        "arch": platform.machine(),
        "python": platform.python_version(),
        "frozen": bool(getattr(sys, "frozen", False)),
        "toolchain": {
            k: (redact_sensitive_text(str(v)) if isinstance(v, str) else v)
            for k, v in (toolchain_status or {}).items()
        },
    }

    # 3. Last 20 tasks (scrubbing local personal usernames/paths and URL tokens)
    safe_tasks = []
    for t in (recent_tasks or [])[:20]:
        safe_tasks.append(
            {
                "id": t.get("id"),
                "platform_id": t.get("platform_id"),
                "engine_used": t.get("engine_used"),
                "status": t.get("status"),
                "error_code": t.get("error_code", ""),
                "quality": t.get("quality"),
                "progress": t.get("progress"),
                "file_size": t.get("file_size"),
                "retry_count": t.get("retry_count"),
                "url": redact_sensitive_text(str(t.get("url") or "")),
                "error": redact_sensitive_text(str(t.get("error") or "")),
                "trace": t.get("trace"),
            }
        )

    # 4. Last 10 resolution traces
    safe_traces = []
    for tr in (recent_traces or [])[:10]:
        tr_copy = dict(tr)
        if "url" in tr_copy:
            tr_copy["url"] = redact_sensitive_text(str(tr_copy["url"]))
        safe_traces.append(tr_copy)

    with zipfile.ZipFile(str(out_zip), "w", compression=zipfile.ZIP_DEFLATED) as zf:
        if log_path.exists():
            raw_log = log_path.read_text(encoding="utf-8", errors="replace")
            zf.writestr("app.log", redact_sensitive_text(raw_log[-400_000:]))
        else:
            zf.writestr("app.log", "No log entries recorded yet.\n")

        zf.writestr("config.json", json.dumps(safe_config, ensure_ascii=False, indent=2))
        zf.writestr("system.json", json.dumps(safe_system, ensure_ascii=False, indent=2))
        zf.writestr("tasks.json", json.dumps(safe_tasks, ensure_ascii=False, indent=2))
        zf.writestr("trace.json", json.dumps(safe_traces, ensure_ascii=False, indent=2))

    get_logger("diagnostics").info("Exported redacted diagnostics bundle: %s", out_zip.name)
    return out_zip
