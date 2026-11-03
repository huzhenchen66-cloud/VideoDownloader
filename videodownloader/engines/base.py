"""Base interfaces, structured ErrorCode taxonomy, 4-Stage ResolutionTrace, and MultiEngineChain orchestrator."""

from __future__ import annotations

from abc import ABC, abstractmethod
from collections import deque
from dataclasses import asdict, dataclass, field
from enum import StrEnum
import threading
import time
from typing import Any, Callable

from ..core.logging_setup import get_logger, redact_sensitive_text

logger = get_logger("engines")

# Resource-level concurrency semaphores (prevents spawning multiple headless Chrome/FFmpeg instances simultaneously)
BROWSER_SEM = threading.Semaphore(1)
YTDLP_SEM = threading.Semaphore(2)
FFMPEG_SEM = threading.Semaphore(1)


class ErrorCode(StrEnum):
    """Structured error taxonomy across all 4 extraction engines (documented in `docs/errors.md`)."""

    NETWORK_TIMEOUT = "network_timeout"
    RISK_CONTROL = "risk_control"
    AUTH_REQUIRED = "auth_required"
    GEO_BLOCKED = "geo_blocked"
    UNSUPPORTED = "unsupported"
    FORMAT_INVALID = "format_invalid"
    DISK_FULL = "disk_full"
    CANCELLED = "cancelled"
    UNKNOWN = "unknown"


ERROR_RETRYABLE: dict[ErrorCode, bool] = {
    ErrorCode.NETWORK_TIMEOUT: True,
    ErrorCode.RISK_CONTROL: True,
    ErrorCode.AUTH_REQUIRED: False,
    ErrorCode.GEO_BLOCKED: False,
    ErrorCode.UNSUPPORTED: False,
    ErrorCode.FORMAT_INVALID: False,
    ErrorCode.DISK_FULL: False,
    ErrorCode.CANCELLED: False,
    ErrorCode.UNKNOWN: True,
}


class DownloadCancelledError(Exception):
    """Raised cooperatively when a task is paused or cancelled mid-download."""

    def __init__(self, reason: str = "cancel") -> None:
        super().__init__(reason)
        self.reason = reason


@dataclass
class FormatChoice:
    format_id: str
    label: str
    resolution: str = ""
    ext: str = "mp4"
    filesize: int = 0
    has_audio: bool = True
    has_video: bool = True


@dataclass
class StageRecord:
    """Single stage execution record in the 4-stage resolution pipeline."""

    stage: int
    engine: str
    status: str  # "skipped" | "failed" | "success" | "not_run"
    elapsed_ms: int = 0
    reason: str = ""
    error_code: str = ""

    def to_dict(self) -> dict[str, Any]:
        return asdict(self)


@dataclass
class ResolutionTrace:
    """Explainable execution trace across all 4 engines (`cn_short -> cdp_sniffer -> ytdlp -> web_sniffer`)."""

    url: str
    platform_id: str
    operation: str  # "parse" | "download"
    stages: list[StageRecord] = field(default_factory=list)
    final_engine: str = ""
    total_ms: int = 0
    succeeded: bool = False

    def to_dict(self) -> dict[str, Any]:
        return {
            "url": redact_sensitive_text(self.url),
            "platform_id": self.platform_id,
            "operation": self.operation,
            "final_engine": self.final_engine,
            "total_ms": self.total_ms,
            "succeeded": self.succeeded,
            "stages": [s.to_dict() for s in self.stages],
        }

    def format_tree(self) -> str:
        """Render human-readable tree for UI expandable trace & `videodownloader --explain <url>`."""
        status_cn = {
            "skipped": "跳过",
            "failed": "失败",
            "success": "成功",
            "not_run": "未执行",
        }
        lines: list[str] = []
        total = len(self.stages)
        for idx, st in enumerate(self.stages):
            branch = "└─" if idx == total - 1 else "├─"
            st_label = status_cn.get(st.status, st.status)
            timing = f"{st.elapsed_ms / 1000:.2f}s" if st.elapsed_ms >= 100 else (
                f"{st.elapsed_ms}ms" if st.elapsed_ms > 0 else "-"
            )
            code_badge = f" [{st.error_code}]" if st.error_code else ""
            reason_part = f"  {st.reason}" if st.reason else ""
            if st.status in ("skipped", "not_run"):
                lines.append(f"{branch} Stage {st.stage}  {st.engine:<20} {st_label:<4}{reason_part}")
            else:
                lines.append(
                    f"{branch} Stage {st.stage}  {st.engine:<20} {st_label:<4}  {timing:<6}{code_badge}{reason_part}"
                )
        return "\n".join(lines)


@dataclass
class EngineFailure:
    """Structured failure entry captured from an individual engine attempt."""

    stage: int
    engine: str
    code: ErrorCode
    retryable: bool
    elapsed_ms: int
    raw_message: str
    hint: str

    def to_dict(self) -> dict[str, Any]:
        return {
            "stage": self.stage,
            "engine": self.engine,
            "code": str(self.code),
            "retryable": self.retryable,
            "elapsed_ms": self.elapsed_ms,
            "raw_message": redact_sensitive_text(self.raw_message),
            "hint": self.hint,
        }


class ResolutionError(RuntimeError):
    """Structured exception raised when all applicable engines fail to resolve or download `url`."""

    def __init__(
        self,
        failures: list[EngineFailure],
        trace: ResolutionTrace,
        platform_id: str = "generic",
    ) -> None:
        self.failures = failures
        self.trace = trace
        self.platform_id = platform_id

        primary = self._select_primary_failure(failures)
        self.primary_code: ErrorCode = primary.code if primary else ErrorCode.UNSUPPORTED
        self.retryable: bool = primary.retryable if primary else False
        self.hint: str = (
            primary.hint
            if primary
            else "当前链接不在任何可用解析引擎的支持范围内，请检查链接是否完整。"
        )
        super().__init__(self.hint)

    @staticmethod
    def _select_primary_failure(failures: list[EngineFailure]) -> EngineFailure | None:
        if not failures:
            return None
        # Prefer specific, actionable diagnoses over generic fallback sniffer failures
        priority = {
            ErrorCode.DISK_FULL: 0,
            ErrorCode.AUTH_REQUIRED: 1,
            ErrorCode.GEO_BLOCKED: 2,
            ErrorCode.NETWORK_TIMEOUT: 3,
            ErrorCode.RISK_CONTROL: 4,
            ErrorCode.UNSUPPORTED: 5,
            ErrorCode.FORMAT_INVALID: 6,
            ErrorCode.UNKNOWN: 7,
        }
        return min(failures, key=lambda f: (priority.get(f.code, 9), -f.stage))

    def to_dict(self) -> dict[str, Any]:
        return {
            "primary_code": str(self.primary_code),
            "retryable": self.retryable,
            "hint": self.hint,
            "failures": [f.to_dict() for f in self.failures],
            "trace": self.trace.to_dict(),
        }


@dataclass
class ParsedMedia:
    url: str
    title: str
    platform_id: str = "generic"
    platform_name: str = "通用视频"
    uploader: str = ""
    thumbnail: str = ""
    duration: float = 0.0
    description: str = ""
    engine_name: str = ""
    elapsed_ms: int = 0
    direct_video_url: str = ""
    direct_audio_url: str = ""
    direct_headers: dict[str, str] = field(default_factory=dict)
    has_subtitles: bool = False
    formats: list[FormatChoice] = field(default_factory=list)
    trace: dict[str, Any] | None = None

    def to_dict(self) -> dict[str, Any]:
        return asdict(self)


@dataclass
class DownloadOptions:
    save_dir: str
    quality: str = "best_1080p"  # best_4k | best_1080p | fast_720p | save_480p | best_mkv
    only_audio: bool = False
    audio_format: str = "mp3"  # mp3 | m4a
    subtitle: bool = True
    fragment_threads: int = 8
    proxy_mode: str = "auto"  # auto | system | custom | direct
    proxy_url: str = ""
    browser_cookie: str = "none"
    cookie_file: str = ""


@dataclass
class ProgressEvent:
    status: str  # parsing | downloading | merging | completed
    progress: float = 0.0
    speed: str = ""
    eta: str = ""
    title: str = ""


@dataclass
class DownloadResult:
    file_path: str
    title: str
    engine_used: str
    subtitle_path: str = ""
    thumbnail: str = ""
    duration: float = 0.0
    uploader: str = ""
    description: str = ""
    trace: dict[str, Any] | None = None


ProgressCallback = Callable[[ProgressEvent], None]


def classify_exception(exc_or_msg: Exception | str, platform_id: str = "generic") -> tuple[ErrorCode, bool, str]:
    """Classify an engine exception into `(ErrorCode, retryable, actionable_user_hint)`."""
    if isinstance(exc_or_msg, DownloadCancelledError):
        return ErrorCode.CANCELLED, False, "下载任务已暂停或取消"
    if isinstance(exc_or_msg, OSError) and getattr(exc_or_msg, "errno", None) == 28:
        return ErrorCode.DISK_FULL, False, "目标磁盘空间不足，请清理空间或更换保存目录后重试"

    msg = str(exc_or_msg or "").strip()
    lower = msg.lower()

    if any(k in lower for k in ("no space left", "enospc", "disk full", "磁盘空间不足")):
        return ErrorCode.DISK_FULL, False, "目标磁盘空间不足，请清理空间或更换保存目录后重试"

    if any(k in lower for k in ("drm", "widevine", "playready", "protected")):
        return (
            ErrorCode.UNSUPPORTED,
            False,
            "该视频受平台 DRM 数字版权加密保护，不支持解析或下载",
        )

    if any(
        k in lower
        for k in (
            "not available in your country",
            "geo-restricted",
            "geo restricted",
            "geoblocked",
            "地区限制",
            "仅限台湾",
            "仅限港澳",
        )
    ):
        return (
            ErrorCode.GEO_BLOCKED,
            False,
            "该内容存在地区版权限制，请切换对应地区的代理节点后重试",
        )

    if any(
        k in lower
        for k in (
            "timed out",
            "timeout",
            "connection refused",
            "10060",
            "10061",
            "10054",
            "unreachable",
            "eof occurred",
            "reset by peer",
            "proxyerror",
            "cannot connect to proxy",
            "name or service not known",
            "getaddrinfo failed",
        )
    ):
        if platform_id in ("youtube", "tiktok", "twitter", "instagram", "facebook", "vimeo", "twitch"):
            return (
                ErrorCode.NETWORK_TIMEOUT,
                True,
                "境外平台网络连接超时，请确认本机代理已开启并在「设置 -> 代理策略」中启用智能分流",
            )
        return (
            ErrorCode.NETWORK_TIMEOUT,
            True,
            f"网络连接超时或中断，请检查网络连通性后重试 ({msg[:100]})",
        )

    if any(
        k in lower
        for k in (
            "sign in to confirm",
            "login required",
            "cookies are needed",
            "fresh cookies",
            "members-only",
            "members only",
            "private video",
            "requires authentication",
            "大会员",
            "付费",
            "登录后观看",
            "需要登录",
        )
    ):
        return (
            ErrorCode.AUTH_REQUIRED,
            False,
            "该视频需要账号登录或会员权限：请点击「设置 Cookie」选择浏览器或导入 cookies.txt 后重试",
        )

    if any(
        k in lower
        for k in (
            "not a bot",
            "captcha",
            "429",
            "too many requests",
            "risk",
            "verify",
            "风控",
            "验证码",
            "人机校验",
            "js 签名渲染",
            "403",
            "forbidden",
        )
    ):
        return (
            ErrorCode.RISK_CONTROL,
            True,
            "触发目标站点风控或频率限制：建议在设置中开启浏览器 Cookie，或稍等片刻后重试",
        )

    if any(k in lower for k in ("requested format is not available", "no video formats found", "html 页面")):
        return (
            ErrorCode.FORMAT_INVALID,
            False,
            f"目标画质流不可用或直链鉴权已过期，建议切换清晰度后重试 ({msg[:100]})",
        )

    if any(k in lower for k in ("unsupported url", "不在支持列表", "not a valid url")):
        return (
            ErrorCode.UNSUPPORTED,
            False,
            "暂不支持该链接格式，请确认输入的是包含视频的单页地址",
        )

    clean = redact_sensitive_text(msg)[:240] if msg else "未知解析或下载错误"
    return ErrorCode.UNKNOWN, True, clean


def humanize_download_error(raw_error: str, platform_id: str) -> str:
    """Backward-compatible wrapper returning the actionable Chinese hint for `raw_error`."""
    _code, _retryable, hint = classify_exception(raw_error, platform_id)
    return hint


class BaseVideoEngine(ABC):
    """Unified Strategy interface implemented by all 4 video parsing/downloading engines."""

    name: str = "base"

    @abstractmethod
    def can_handle(self, url: str, platform_id: str, prefer_fallback: bool = False) -> bool:
        """Return True if this engine should attempt handling `url`."""

    def skip_reason(self, url: str, platform_id: str, prefer_fallback: bool = False) -> str:
        """Human-readable explanation recorded in `ResolutionTrace` when `can_handle()` returns False."""
        if self.can_handle(url, platform_id, prefer_fallback=prefer_fallback):
            return ""
        if not prefer_fallback:
            return f"平台 {platform_id} 不在该引擎首选注册表"
        return f"平台 {platform_id} 已禁用该兜底策略"

    @abstractmethod
    def parse(self, url: str, platform_id: str, options: DownloadOptions) -> ParsedMedia:
        """Extract video metadata and available quality choices without downloading full media."""

    @abstractmethod
    def download(
        self,
        url: str,
        platform_id: str,
        options: DownloadOptions,
        stop_event: threading.Event,
        stop_reason_getter: Callable[[], str],
        on_progress: ProgressCallback | None = None,
    ) -> DownloadResult:
        """Download media to `options.save_dir`, emitting progress updates and honoring `stop_event`."""


class MultiEngineChain:
    """Explainable 4-Stage Chain-of-Responsibility router recording `ResolutionTrace` & structured `ResolutionError`."""

    def __init__(self, engines: list[BaseVideoEngine]) -> None:
        self.engines = engines
        self.telemetry: dict[str, dict[str, int]] = {
            e.name: {"attempts": 0, "successes": 0, "failures": 0} for e in engines
        }
        self._trace_lock = threading.Lock()
        self.recent_traces: deque[dict[str, Any]] = deque(maxlen=30)

    def _record_trace(self, trace: ResolutionTrace) -> None:
        with self._trace_lock:
            self.recent_traces.appendleft(trace.to_dict())

    def get_recent_traces(self, limit: int = 10) -> list[dict[str, Any]]:
        with self._trace_lock:
            return list(self.recent_traces)[:limit]

    def parse(self, url: str, platform_id: str, options: DownloadOptions) -> ParsedMedia:
        chain_t0 = time.perf_counter()
        stages: list[StageRecord] = []
        failures: list[EngineFailure] = []

        for idx, engine in enumerate(self.engines):
            stage_no = idx + 1
            prefer_fb = idx > 0 or bool(failures)
            if not engine.can_handle(url, platform_id, prefer_fallback=prefer_fb):
                stages.append(
                    StageRecord(
                        stage=stage_no,
                        engine=engine.name,
                        status="skipped",
                        reason=engine.skip_reason(url, platform_id, prefer_fallback=prefer_fb),
                    )
                )
                continue

            self.telemetry[engine.name]["attempts"] += 1
            t0 = time.perf_counter()
            try:
                media = engine.parse(url, platform_id, options)
                elapsed_ms = int((time.perf_counter() - t0) * 1000)
                media.engine_name = engine.name
                media.elapsed_ms = elapsed_ms
                self.telemetry[engine.name]["successes"] += 1

                fmt_count = len(media.formats or [])
                detail = f"解析到 {fmt_count} 个规格" if fmt_count else "获取无水印直链"
                stages.append(
                    StageRecord(
                        stage=stage_no,
                        engine=engine.name,
                        status="success",
                        elapsed_ms=elapsed_ms,
                        reason=detail,
                    )
                )
                for rem_idx in range(idx + 1, len(self.engines)):
                    stages.append(
                        StageRecord(
                            stage=rem_idx + 1,
                            engine=self.engines[rem_idx].name,
                            status="not_run",
                            reason="前序阶段已命中",
                        )
                    )

                trace = ResolutionTrace(
                    url=url,
                    platform_id=platform_id,
                    operation="parse",
                    stages=stages,
                    final_engine=engine.name,
                    total_ms=int((time.perf_counter() - chain_t0) * 1000),
                    succeeded=True,
                )
                media.trace = trace.to_dict()
                self._record_trace(trace)
                logger.info(
                    "Parse succeeded | platform=%s | engine=%s | elapsed=%dms | url=%s",
                    platform_id,
                    engine.name,
                    trace.total_ms,
                    url,
                )
                return media

            except Exception as exc:
                elapsed_ms = int((time.perf_counter() - t0) * 1000)
                self.telemetry[engine.name]["failures"] += 1
                code, retryable, hint = classify_exception(exc, platform_id)
                raw_short = redact_sensitive_text(str(exc)).strip()[:160]
                failures.append(
                    EngineFailure(
                        stage=stage_no,
                        engine=engine.name,
                        code=code,
                        retryable=retryable,
                        elapsed_ms=elapsed_ms,
                        raw_message=str(exc),
                        hint=hint,
                    )
                )
                stages.append(
                    StageRecord(
                        stage=stage_no,
                        engine=engine.name,
                        status="failed",
                        elapsed_ms=elapsed_ms,
                        reason=raw_short or hint,
                        error_code=str(code),
                    )
                )
                logger.warning(
                    "Parse stage %d (%s) failed in %dms [%s]: %s",
                    stage_no,
                    engine.name,
                    elapsed_ms,
                    code,
                    raw_short,
                )
                if code == ErrorCode.DISK_FULL:
                    break
                continue

        trace = ResolutionTrace(
            url=url,
            platform_id=platform_id,
            operation="parse",
            stages=stages,
            final_engine="",
            total_ms=int((time.perf_counter() - chain_t0) * 1000),
            succeeded=False,
        )
        self._record_trace(trace)
        raise ResolutionError(failures=failures, trace=trace, platform_id=platform_id)

    def download(
        self,
        url: str,
        platform_id: str,
        options: DownloadOptions,
        stop_event: threading.Event,
        stop_reason_getter: Callable[[], str],
        on_progress: ProgressCallback | None = None,
        skip_engines: set[str] | None = None,
    ) -> DownloadResult:
        chain_t0 = time.perf_counter()
        stages: list[StageRecord] = []
        failures: list[EngineFailure] = []
        skip = skip_engines or set()

        for idx, engine in enumerate(self.engines):
            stage_no = idx + 1
            if engine.name in skip:
                stages.append(
                    StageRecord(
                        stage=stage_no,
                        engine=engine.name,
                        status="skipped",
                        reason="由调用方显式跳过",
                    )
                )
                continue

            prefer_fb = idx > 0 or bool(failures)
            if not engine.can_handle(url, platform_id, prefer_fallback=prefer_fb):
                stages.append(
                    StageRecord(
                        stage=stage_no,
                        engine=engine.name,
                        status="skipped",
                        reason=engine.skip_reason(url, platform_id, prefer_fallback=prefer_fb),
                    )
                )
                continue

            self.telemetry[engine.name]["attempts"] += 1
            t0 = time.perf_counter()
            try:
                res = engine.download(
                    url=url,
                    platform_id=platform_id,
                    options=options,
                    stop_event=stop_event,
                    stop_reason_getter=stop_reason_getter,
                    on_progress=on_progress,
                )
                elapsed_ms = int((time.perf_counter() - t0) * 1000)
                if not res.engine_used:
                    res.engine_used = engine.name
                self.telemetry[engine.name]["successes"] += 1

                stages.append(
                    StageRecord(
                        stage=stage_no,
                        engine=engine.name,
                        status="success",
                        elapsed_ms=elapsed_ms,
                        reason=f"下载完成 ({res.engine_used})",
                    )
                )
                for rem_idx in range(idx + 1, len(self.engines)):
                    stages.append(
                        StageRecord(
                            stage=rem_idx + 1,
                            engine=self.engines[rem_idx].name,
                            status="not_run",
                            reason="前序阶段已成功下载",
                        )
                    )

                trace = ResolutionTrace(
                    url=url,
                    platform_id=platform_id,
                    operation="download",
                    stages=stages,
                    final_engine=res.engine_used,
                    total_ms=int((time.perf_counter() - chain_t0) * 1000),
                    succeeded=True,
                )
                res.trace = trace.to_dict()
                self._record_trace(trace)
                logger.info(
                    "Download succeeded | platform=%s | engine=%s | elapsed=%dms | file=%s",
                    platform_id,
                    res.engine_used,
                    trace.total_ms,
                    res.file_path,
                )
                return res

            except DownloadCancelledError:
                logger.info("Download cancelled/paused | platform=%s | engine=%s | url=%s", platform_id, engine.name, url)
                raise

            except Exception as exc:
                elapsed_ms = int((time.perf_counter() - t0) * 1000)
                self.telemetry[engine.name]["failures"] += 1
                code, retryable, hint = classify_exception(exc, platform_id)
                raw_short = redact_sensitive_text(str(exc)).strip()[:160]
                failures.append(
                    EngineFailure(
                        stage=stage_no,
                        engine=engine.name,
                        code=code,
                        retryable=retryable,
                        elapsed_ms=elapsed_ms,
                        raw_message=str(exc),
                        hint=hint,
                    )
                )
                stages.append(
                    StageRecord(
                        stage=stage_no,
                        engine=engine.name,
                        status="failed",
                        elapsed_ms=elapsed_ms,
                        reason=raw_short or hint,
                        error_code=str(code),
                    )
                )
                logger.warning(
                    "Download stage %d (%s) failed in %dms [%s]: %s",
                    stage_no,
                    engine.name,
                    elapsed_ms,
                    code,
                    raw_short,
                )
                if code == ErrorCode.DISK_FULL:
                    break
                continue

        trace = ResolutionTrace(
            url=url,
            platform_id=platform_id,
            operation="download",
            stages=stages,
            final_engine="",
            total_ms=int((time.perf_counter() - chain_t0) * 1000),
            succeeded=False,
        )
        self._record_trace(trace)
        raise ResolutionError(failures=failures, trace=trace, platform_id=platform_id)
