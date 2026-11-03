"""Upgraded yt-dlp Engine (v2.0):
- Bundled yt-dlp 2026.10+ with `yt-dlp-ejs` (YouTube 4K JS solver) & `curl_cffi` (TikTok TLS impersonation)
- Hot-updatable `bin/yt-dlp.exe` CLI priority so compiled `.exe` stays up-to-date forever
- Smart Split Proxy Routing (YouTube/TikTok/X/Instagram via proxy, Bilibili/CN direct connection)
- H.264 (`avc1`) codec sorting for 1080p/720p/480p MP4 presets so downloaded files play on 100% of players
- Automatic browser-cookie lock recovery on Windows Chromium/Edge
"""

from __future__ import annotations

import json
import os
import re
import subprocess
import sys
import threading
import time
from pathlib import Path
from typing import Any, Callable

import yt_dlp

from ..core.bilibili_helper import ensure_bilibili_bootstrap_cookie
from ..core.binary_updater import HAS_CURL_CFFI, resolve_external_ytdlp_path, resolve_ffmpeg_path
from ..core.os_utils import resolve_proxy_for_platform, validate_media_file
from ..core.url_cleaner import detect_platform
from .base import (
    BaseVideoEngine,
    DownloadCancelledError,
    DownloadOptions,
    DownloadResult,
    FormatChoice,
    ParsedMedia,
    ProgressCallback,
    ProgressEvent,
    YTDLP_SEM,
)

BROWSER_UA = (
    "Mozilla/5.0 (Windows NT 10.0; Win64; x64) "
    "AppleWebKit/537.36 (KHTML, like Gecko) Chrome/131.0.0.0 Safari/537.36"
)

MEDIA_EXTENSIONS = {
    ".mp4", ".mkv", ".webm", ".mov", ".avi", ".flv", ".m4v", ".ts",
    ".mp3", ".m4a", ".aac", ".wav", ".flac", ".ogg",
}
SUBTITLE_EXTENSIONS = {".srt", ".vtt", ".ass"}
TEMP_EXTENSIONS = {".part", ".ytdl", ".tmp", ".download"}
ANSI_ESCAPE_RE = re.compile(r"\x1B(?:[@-Z\\-_]|\[[0-?]*[ -/]*[@-~])")

CLI_PROGRESS_RE = re.compile(
    r"\[download\]\s+(\d{1,3}(?:\.\d+)?)%"
    r"(?:.*?\s+at\s+([^\s]+/s))?"
    r"(?:.*?\s+ETA\s+([^\s]+))?",
    re.IGNORECASE,
)
FINAL_FILE_MARKER = "__VD_FINAL_FILE__:"


class _QuietLogger:
    def debug(self, msg: str) -> None:
        pass

    def warning(self, msg: str) -> None:
        pass

    def error(self, msg: str) -> None:
        pass


def _strip_ansi(s: str) -> str:
    return ANSI_ESCAPE_RE.sub("", s or "").strip()


def _format_bytes_speed(speed: float | None) -> str:
    if not speed or speed <= 0:
        return ""
    if speed >= 1024 * 1024:
        return f"{speed / (1024 * 1024):.1f} MB/s"
    if speed >= 1024:
        return f"{speed / 1024:.0f} KB/s"
    return f"{int(speed)} B/s"


def _format_eta(seconds: int | float | None) -> str:
    if seconds is None or seconds < 0:
        return ""
    sec = int(seconds)
    if sec >= 3600:
        return f"{sec // 3600}:{(sec % 3600) // 60:02d}:{sec % 60:02d}"
    return f"{sec // 60:02d}:{sec % 60:02d}"


def build_ytdlp_format_spec(
    quality: str,
    only_audio: bool,
    audio_format: str,
    has_ffmpeg: bool,
) -> tuple[str, str, list[str], list[dict[str, Any]]]:
    """Return `(format_selector, merge_ext, format_sort_list, postprocessors)`.

    Crucial v2.0 improvement:
      - Standard MP4 presets (`best_1080p`, `fast_720p`, `save_480p`) sort with `vcodec:h264` first!
        This prevents Bilibili/YouTube/TikTok from defaulting to `hev1` (HEVC) or `av01` (AV1) streams
        that show black screens in browsers, Windows Media Player, or editing software.
      - `best_4k` sorts by `res,fps,br` to unlock true 2160p 4K / 4320p 8K master streams.
    """
    postprocessors: list[dict[str, Any]] = []

    if only_audio:
        if audio_format == "m4a":
            fmt = "bestaudio[ext=m4a]/bestaudio/best"
            if has_ffmpeg:
                postprocessors.append({"key": "FFmpegExtractAudio", "preferredcodec": "m4a"})
            return fmt, "m4a", [], postprocessors
        fmt = "bestaudio/best"
        if has_ffmpeg:
            postprocessors.append({"key": "FFmpegExtractAudio", "preferredcodec": "mp3", "preferredquality": "192"})
        return fmt, "mp3", [], postprocessors

    if not has_ffmpeg:
        if quality == "fast_720p":
            return "best[height<=720][ext=mp4]/best[height<=720]/best", "mp4", ["vcodec:h264", "res:720"], postprocessors
        if quality == "save_480p":
            return "best[height<=480][ext=mp4]/best[height<=480]/best", "mp4", ["vcodec:h264", "res:480"], postprocessors
        return "best[ext=mp4]/best", "mp4", ["vcodec:h264"], postprocessors

    if quality == "best_4k":
        return "bv*+ba/bestvideo+bestaudio/best", "mp4", ["res", "fps", "br"], postprocessors
    if quality == "best_mkv":
        return "bv*+ba/best", "mkv", ["res", "fps", "br"], postprocessors
    if quality == "fast_720p":
        return (
            "bv*[height<=720]+ba/best[height<=720]/best",
            "mp4",
            ["vcodec:h264", "res:720", "acodec:m4a", "ext:mp4:m4a"],
            postprocessors,
        )
    if quality == "save_480p":
        return (
            "bv*[height<=480]+ba/best[height<=480]/best",
            "mp4",
            ["vcodec:h264", "res:480", "acodec:m4a", "ext:mp4:m4a"],
            postprocessors,
        )

    # Default: recommended 1080p MP4 (H.264 universal playback compatibility!)
    return (
        "bv*[height<=1080]+ba/best[height<=1080]/best",
        "mp4",
        ["vcodec:h264", "res:1080", "acodec:m4a", "ext:mp4:m4a"],
        postprocessors,
    )


def _is_cookie_lock_error(exc_msg: str) -> bool:
    lower = (exc_msg or "").lower()
    return any(
        token in lower
        for token in (
            "could not copy chrome cookie",
            "cookie database",
            "failed to decrypt with dpapi",
            "permission denied",
            "cookies-from-browser",
        )
    )


class YtdlpEngine(BaseVideoEngine):
    """Primary global engine supporting 1800+ sites (YouTube 4K/8K, Bilibili, TikTok, X, Weibo, etc.)."""

    name = "yt-dlp"

    def can_handle(self, url: str, platform_id: str, prefer_fallback: bool = False) -> bool:
        if platform_id == "direct":
            return prefer_fallback
        # Douyin / Xiaohongshu / Kuaishou / Xigua try fast native or headless Chrome CDP sniffer first;
        # yt-dlp acts as fallback if those don't match
        if platform_id in ("douyin", "xiaohongshu", "kuaishou") and not prefer_fallback:
            return False
        return True

    def skip_reason(self, url: str, platform_id: str, prefer_fallback: bool = False) -> str:
        if self.can_handle(url, platform_id, prefer_fallback=prefer_fallback):
            return ""
        if platform_id == "direct":
            return "媒体直链已由原生流式下载器处理"
        return f"平台 {platform_id} 优先尝试原生/浏览器反风控引擎"

    def _base_ydl_opts(
        self,
        url: str,
        platform_id: str,
        options: DownloadOptions,
        allow_browser_cookie: bool = True,
    ) -> dict[str, Any]:
        ffmpeg_path = resolve_ffmpeg_path()
        headers = {
            "User-Agent": BROWSER_UA,
            "Accept-Language": "zh-CN,zh;q=0.9,en;q=0.8",
        }
        lower_url = url.lower()
        if "bilibili.com" in lower_url or "b23.tv" in lower_url:
            headers["Referer"] = "https://www.bilibili.com/"
        elif "weibo.com" in lower_url or "weibo.cn" in lower_url:
            headers["Referer"] = "https://weibo.com/"

        opts: dict[str, Any] = {
            "quiet": True,
            "no_warnings": True,
            "logger": _QuietLogger(),
            "noplaylist": True,
            "nocheckcertificate": True,
            "geo_bypass": True,
            "retries": 10,
            "fragment_retries": 10,
            "extractor_retries": 5,
            "file_access_retries": 3,
            "socket_timeout": 15,
            "http_chunk_size": 10485760,  # 10 MiB chunks: breaks YouTube/Bilibili streaming bandwidth throttle!
            "buffersize": 1048576,  # 1 MiB socket buffer (1000x larger than default 1KB)
            "concurrent_fragment_downloads": max(2, min(32, int(options.fragment_threads or 8))),
            "windowsfilenames": True,
            "trim_file_name": 110,
            "http_headers": headers,
            "extractor_args": {
                "youtube": {
                    "player_client": ["android", "ios", "web"],
                    "player_skip": ["configs"],
                }
            },
        }

        if ffmpeg_path:
            opts["ffmpeg_location"] = ffmpeg_path

        # Smart Split Proxy Routing
        resolved_proxy = resolve_proxy_for_platform(platform_id, options.proxy_mode, options.proxy_url)
        if resolved_proxy is not None:
            # Note: `""` explicitly forces direct connection in yt-dlp (bypassing Windows system VPN for Bilibili!)
            opts["proxy"] = resolved_proxy

        # TikTok requires Chrome TLS fingerprint impersonation (`curl_cffi`) to avoid empty shell responses
        if platform_id == "tiktok" and HAS_CURL_CFFI:
            try:
                from yt_dlp.networking.impersonate import ImpersonateTarget
                opts["impersonate"] = ImpersonateTarget.from_str("chrome")
            except Exception:
                pass

        # Cookies injection
        if options.cookie_file and os.path.exists(options.cookie_file):
            opts["cookiefile"] = options.cookie_file
        elif (
            allow_browser_cookie
            and options.browser_cookie
            and options.browser_cookie.lower() not in ("none", "不使用浏览器cookies", "不使用 cookie", "")
        ):
            opts["cookiesfrombrowser"] = (options.browser_cookie.lower(),)
        elif platform_id == "bilibili" or "bilibili.com" in lower_url or "b23.tv" in lower_url:
            # Inject fresh official buvid3/buvid4 SPI bootstrap cookies to bypass 412 & unlock high-speed DASH
            try:
                spi_cookie = ensure_bilibili_bootstrap_cookie()
                if spi_cookie and os.path.exists(spi_cookie):
                    opts["cookiefile"] = spi_cookie
            except Exception:
                pass

        return opts

    # -------------------- Parse --------------------

    def parse(self, url: str, platform_id: str, options: DownloadOptions) -> ParsedMedia:
        ext_bin = resolve_external_ytdlp_path()
        if ext_bin:
            try:
                info = self._parse_via_cli(ext_bin, url, platform_id, options)
                return self._info_to_parsed_media(info, url, platform_id)
            except Exception:
                # Seamless fallback to bundled Python yt_dlp (which includes yt_dlp_ejs + curl_cffi)
                pass

        info = self._extract_info_python(url, platform_id, options)
        return self._info_to_parsed_media(info, url, platform_id)

    def _extract_info_python(self, url: str, platform_id: str, options: DownloadOptions) -> dict[str, Any]:
        opts = self._base_ydl_opts(url, platform_id, options, allow_browser_cookie=True)
        opts["skip_download"] = True
        try:
            with yt_dlp.YoutubeDL(opts) as ydl:
                info = ydl.extract_info(url, download=False)
                if info:
                    return info
        except Exception as exc:
            err_str = _strip_ansi(str(exc))
            if "cookiesfrombrowser" in opts and _is_cookie_lock_error(err_str):
                opts_no_bc = self._base_ydl_opts(url, platform_id, options, allow_browser_cookie=False)
                opts_no_bc["skip_download"] = True
                with yt_dlp.YoutubeDL(opts_no_bc) as ydl:
                    info = ydl.extract_info(url, download=False)
                    if info:
                        return info
            raise RuntimeError(err_str) from exc

        raise RuntimeError("yt-dlp 未返回有效视频信息")

    def _parse_via_cli(self, ytdlp_bin: str, url: str, platform_id: str, options: DownloadOptions) -> dict[str, Any]:
        cmd = [
            ytdlp_bin,
            "-J",
            "--no-playlist",
            "--no-warnings",
            "--no-check-certificates",
            "--encoding",
            "utf-8",
        ]
        resolved_proxy = resolve_proxy_for_platform(platform_id, options.proxy_mode, options.proxy_url)
        if resolved_proxy is not None:
            cmd += ["--proxy", resolved_proxy]

        if options.cookie_file and os.path.exists(options.cookie_file):
            cmd += ["--cookies", options.cookie_file]
        elif options.browser_cookie and options.browser_cookie.lower() not in ("none", "不使用浏览器cookies", ""):
            cmd += ["--cookies-from-browser", options.browser_cookie.lower()]
        elif platform_id == "bilibili" or "bilibili.com" in url.lower() or "b23.tv" in url.lower():
            try:
                spi_cookie = ensure_bilibili_bootstrap_cookie()
                if spi_cookie and os.path.exists(spi_cookie):
                    cmd += ["--cookies", spi_cookie]
            except Exception:
                pass

        if platform_id == "tiktok":
            cmd += ["--impersonate", "chrome"]

        cmd.append(url)
        creationflags = (
            subprocess.CREATE_NO_WINDOW
            if sys.platform.startswith("win") and hasattr(subprocess, "CREATE_NO_WINDOW")
            else 0
        )
        proc = subprocess.run(
            cmd,
            capture_output=True,
            text=True,
            encoding="utf-8",
            errors="replace",
            timeout=45,
            creationflags=creationflags,
        )
        if proc.returncode != 0:
            raise RuntimeError(_strip_ansi(proc.stderr or proc.stdout or f"yt-dlp CLI exit {proc.returncode}"))
        return json.loads(proc.stdout)

    @staticmethod
    def _info_to_parsed_media(info: dict[str, Any], url: str, platform_id: str) -> ParsedMedia:
        if "entries" in info and info["entries"]:
            entries = [e for e in info["entries"] if e]
            if not entries:
                raise RuntimeError("播放列表为空")
            info = entries[0]

        pid, pname, _ = detect_platform(info.get("webpage_url") or url)
        formats_out: list[FormatChoice] = []
        seen_heights: set[int] = set()
        for f in reversed(info.get("formats") or []):
            h = f.get("height")
            vcodec = str(f.get("vcodec") or "none")
            if h and vcodec != "none" and int(h) not in seen_heights:
                seen_heights.add(int(h))
                codec_tag = "H.264" if vcodec.startswith("avc") else vcodec.split(".")[0].upper()
                formats_out.append(
                    FormatChoice(
                        format_id=str(f.get("format_id") or h),
                        label=f"{h}p ({f.get('ext', 'mp4').upper()} · {codec_tag})",
                        resolution=f"{f.get('width', '?')}x{h}",
                        ext=str(f.get("ext") or "mp4"),
                        filesize=int(f.get("filesize") or f.get("filesize_approx") or 0),
                        has_audio=(f.get("acodec") not in (None, "none")),
                        has_video=True,
                    )
                )
        formats_out.sort(
            key=lambda x: int(x.label.split("p")[0]) if x.label.split("p")[0].isdigit() else 0,
            reverse=True,
        )

        title = (info.get("title") or "").strip() or "未命名视频"
        uploader = (info.get("uploader") or info.get("channel") or "").strip()
        if len(title.replace("#", "").strip()) < 4 and uploader:
            title = f"{uploader} - {title}"

        has_subs = bool(info.get("subtitles") or info.get("automatic_captions"))
        return ParsedMedia(
            url=info.get("webpage_url") or url,
            title=title,
            platform_id=pid if pid != "generic" else platform_id,
            platform_name=pname,
            uploader=uploader,
            thumbnail=info.get("thumbnail") or "",
            duration=float(info.get("duration") or 0),
            description=(info.get("description") or "")[:500],
            has_subtitles=has_subs,
            formats=formats_out[:8],
        )

    # -------------------- Download --------------------

    @staticmethod
    def _snapshot_files(folder: str) -> dict[str, tuple[float, int]]:
        snap: dict[str, tuple[float, int]] = {}
        if not os.path.isdir(folder):
            return snap
        for entry in os.scandir(folder):
            if not entry.is_file():
                continue
            lower = entry.name.lower()
            if any(lower.endswith(t) for t in TEMP_EXTENSIONS):
                continue
            try:
                st = entry.stat()
                snap[os.path.normpath(entry.path)] = (st.st_mtime, st.st_size)
            except Exception:
                pass
        return snap

    def _resolve_output_files(
        self,
        save_dir: str,
        captured_paths: list[str],
        before_snap: dict[str, tuple[float, int]],
        started_at: float,
    ) -> tuple[str, str]:
        after_snap = self._snapshot_files(save_dir)
        media_candidates: list[str] = []
        subtitle_candidates: list[str] = []

        for p in reversed(captured_paths):
            if p and os.path.exists(p):
                ext = Path(p).suffix.lower()
                if ext in MEDIA_EXTENSIONS and p not in media_candidates:
                    media_candidates.append(p)
                elif ext in SUBTITLE_EXTENSIONS and p not in subtitle_candidates:
                    subtitle_candidates.append(p)

        for path, meta in after_snap.items():
            old = before_snap.get(path)
            is_new_or_changed = (old is None) or (old != meta) or (meta[0] >= started_at - 3.0)
            if not is_new_or_changed or meta[1] <= 0:
                continue
            ext = Path(path).suffix.lower()
            if ext in MEDIA_EXTENSIONS and path not in media_candidates:
                media_candidates.append(path)
            elif ext in SUBTITLE_EXTENSIONS and path not in subtitle_candidates:
                subtitle_candidates.append(path)

        if not media_candidates:
            raise RuntimeError("下载流程结束，但未在目标目录检测到有效媒体文件")

        final_media = media_candidates[0]
        ok, err_reason = validate_media_file(final_media)
        if not ok:
            try:
                os.remove(final_media)
            except OSError:
                pass
            raise RuntimeError(err_reason)

        final_sub = subtitle_candidates[0] if subtitle_candidates else ""
        if not final_sub:
            stem = str(Path(final_media).with_suffix(""))
            if os.path.isdir(save_dir):
                for entry in os.scandir(save_dir):
                    if entry.is_file() and Path(entry.name).suffix.lower() in SUBTITLE_EXTENSIONS:
                        if os.path.normpath(entry.path).startswith(os.path.normpath(stem)):
                            final_sub = os.path.normpath(entry.path)
                            break

        return final_media, final_sub

    def download(
        self,
        url: str,
        platform_id: str,
        options: DownloadOptions,
        stop_event: threading.Event,
        stop_reason_getter: Callable[[], str],
        on_progress: ProgressCallback | None = None,
    ) -> DownloadResult:
        os.makedirs(options.save_dir, exist_ok=True)
        with YTDLP_SEM:
            ext_bin = resolve_external_ytdlp_path()
            if ext_bin:
                try:
                    return self._download_via_cli(
                        ext_bin, url, platform_id, options, stop_event, stop_reason_getter, on_progress
                    )
                except DownloadCancelledError:
                    raise
                except Exception:
                    # If external binary fails (e.g., TikTok needing bundled curl_cffi), fall back to Python core
                    pass

            return self._download_via_python(
                url, platform_id, options, stop_event, stop_reason_getter, on_progress, allow_browser_cookie=True
            )

    def _download_via_cli(
        self,
        ytdlp_bin: str,
        url: str,
        platform_id: str,
        options: DownloadOptions,
        stop_event: threading.Event,
        stop_reason_getter: Callable[[], str],
        on_progress: ProgressCallback | None,
        include_subtitles: bool | None = None,
    ) -> DownloadResult:
        want_subs = (bool(options.subtitle) and not options.only_audio) if include_subtitles is None else include_subtitles
        ffmpeg_path = resolve_ffmpeg_path()
        has_ffmpeg = bool(ffmpeg_path)
        fmt_spec, merge_ext, fmt_sort, _ = build_ytdlp_format_spec(
            options.quality, options.only_audio, options.audio_format, has_ffmpeg
        )
        out_tmpl = os.path.join(options.save_dir, "%(title).100s [%(id)s].%(ext)s")

        cmd = [
            ytdlp_bin,
            "-f", fmt_spec,
            "-o", out_tmpl,
            "--no-playlist",
            "--newline",
            "--no-colors",
            "--encoding", "utf-8",
            "--windows-filenames",
            "--trim-filenames", "110",
            "--continue",
            "--part",
            "--no-mtime",
            "--no-check-certificates",
            "--retries", "10",
            "--fragment-retries", "10",
            "--http-chunk-size", "10M",
            "--buffer-size", "1M",
            "-N", str(max(2, min(32, int(options.fragment_threads or 8)))),
            "--extractor-args", "youtube:player_client=android,ios,web;player_skip=configs",
            "--print", f"after_move:{FINAL_FILE_MARKER}%(filepath)s",
        ]

        if fmt_sort:
            cmd += ["-S", ",".join(fmt_sort)]

        if ffmpeg_path:
            cmd += ["--ffmpeg-location", ffmpeg_path]

        resolved_proxy = resolve_proxy_for_platform(platform_id, options.proxy_mode, options.proxy_url)
        if resolved_proxy is not None:
            cmd += ["--proxy", resolved_proxy]

        if "bilibili.com" in url.lower() or "b23.tv" in url.lower():
            cmd += ["--add-header", "Referer:https://www.bilibili.com/", "--add-header", f"User-Agent:{BROWSER_UA}"]

        if platform_id == "tiktok":
            cmd += ["--impersonate", "chrome"]

        if options.only_audio:
            cmd += ["-x", "--audio-format", options.audio_format, "--audio-quality", "0"]
        elif has_ffmpeg:
            cmd += ["--merge-output-format", merge_ext, "--ppa", "Merger:-movflags +faststart"]

        if want_subs:
            cmd += ["--write-sub", "--sub-lang", "zh-Hans,zh-CN,zh,en"]
            if has_ffmpeg:
                cmd += ["--convert-subs", "srt"]

        if options.cookie_file and os.path.exists(options.cookie_file):
            cmd += ["--cookies", options.cookie_file]
        elif options.browser_cookie and options.browser_cookie.lower() not in ("none", "不使用浏览器cookies", ""):
            cmd += ["--cookies-from-browser", options.browser_cookie.lower()]
        elif platform_id == "bilibili" or "bilibili.com" in url.lower() or "b23.tv" in url.lower():
            try:
                spi_cookie = ensure_bilibili_bootstrap_cookie()
                if spi_cookie and os.path.exists(spi_cookie):
                    cmd += ["--cookies", spi_cookie]
            except Exception:
                pass

        cmd.append(url)

        started_at = time.time()
        before_snap = self._snapshot_files(options.save_dir)
        captured_paths: list[str] = []
        last_err_line = ""

        creationflags = (
            subprocess.CREATE_NO_WINDOW
            if sys.platform.startswith("win") and hasattr(subprocess, "CREATE_NO_WINDOW")
            else 0
        )
        proc = subprocess.Popen(
            cmd,
            stdout=subprocess.PIPE,
            stderr=subprocess.STDOUT,
            text=True,
            encoding="utf-8",
            errors="replace",
            creationflags=creationflags,
        )

        try:
            assert proc.stdout is not None
            for raw_line in proc.stdout:
                if stop_event.is_set():
                    proc.terminate()
                    raise DownloadCancelledError(stop_reason_getter() or "cancel")

                line = _strip_ansi(raw_line)
                if not line:
                    continue
                if FINAL_FILE_MARKER in line:
                    fp = line.split(FINAL_FILE_MARKER, 1)[-1].strip().strip('"')
                    if fp:
                        captured_paths.append(os.path.normpath(fp))
                    continue
                if line.upper().startswith("ERROR:"):
                    last_err_line = line[6:].strip()
                    continue
                if "[Merger]" in line and on_progress:
                    on_progress(ProgressEvent(status="merging", progress=99.0))
                    continue

                m = CLI_PROGRESS_RE.search(line)
                if m and on_progress:
                    pct = min(99.5, max(0.0, float(m.group(1))))
                    spd = m.group(2) or ""
                    eta = m.group(3) or ""
                    on_progress(ProgressEvent(status="downloading", progress=round(pct, 1), speed=spd, eta=eta))

            rc = proc.wait(timeout=30)
        finally:
            if proc.poll() is None:
                try:
                    proc.kill()
                except Exception:
                    pass

        if stop_event.is_set():
            raise DownloadCancelledError(stop_reason_getter() or "cancel")
        if rc != 0:
            if want_subs and "subtitles" in last_err_line.lower():
                return self._download_via_cli(
                    ytdlp_bin, url, platform_id, options, stop_event, stop_reason_getter, on_progress, include_subtitles=False
                )
            raise RuntimeError(last_err_line or f"yt-dlp 进程返回错误码 {rc}")

        final_media, final_sub = self._resolve_output_files(
            options.save_dir, captured_paths, before_snap, started_at
        )
        return DownloadResult(
            file_path=final_media,
            title=Path(final_media).stem,
            engine_used="yt-dlp (bin)",
            subtitle_path=final_sub,
        )

    def _download_via_python(
        self,
        url: str,
        platform_id: str,
        options: DownloadOptions,
        stop_event: threading.Event,
        stop_reason_getter: Callable[[], str],
        on_progress: ProgressCallback | None,
        allow_browser_cookie: bool = True,
        include_subtitles: bool | None = None,
    ) -> DownloadResult:
        want_subs = (bool(options.subtitle) and not options.only_audio) if include_subtitles is None else include_subtitles
        ffmpeg_path = resolve_ffmpeg_path()
        has_ffmpeg = bool(ffmpeg_path)
        fmt_spec, merge_ext, fmt_sort, postprocessors = build_ytdlp_format_spec(
            options.quality, options.only_audio, options.audio_format, has_ffmpeg
        )

        opts = self._base_ydl_opts(url, platform_id, options, allow_browser_cookie=allow_browser_cookie)
        opts.update(
            {
                "format": fmt_spec,
                "outtmpl": os.path.join(options.save_dir, "%(title).100s [%(id)s].%(ext)s"),
                "continuedl": True,
                "nopart": False,
                "updatetime": False,
            }
        )
        if fmt_sort:
            opts["format_sort"] = fmt_sort
        if not options.only_audio and has_ffmpeg:
            opts["merge_output_format"] = merge_ext
            opts["postprocessor_args"] = {"merger": ["-movflags", "+faststart"]}
        if postprocessors:
            opts["postprocessors"] = postprocessors

        if want_subs:
            opts["writesubtitles"] = True
            opts["writeautomaticsub"] = False
            opts["subtitleslangs"] = ["zh-Hans", "zh-CN", "zh", "en"]
            if has_ffmpeg:
                opts["convertsubtitles"] = "srt"

        captured_paths: list[str] = []
        current_title = [""]
        started_at = time.time()
        before_snap = self._snapshot_files(options.save_dir)

        def _progress_hook(d: dict[str, Any]) -> None:
            if stop_event.is_set():
                raise DownloadCancelledError(stop_reason_getter() or "cancel")

            status = d.get("status")
            info_dict = d.get("info_dict") or {}
            if info_dict.get("title"):
                current_title[0] = info_dict["title"]

            if status == "downloading":
                downloaded = float(d.get("downloaded_bytes") or 0)
                total = float(d.get("total_bytes") or d.get("total_bytes_estimate") or 0)
                pct = 0.0
                if total > 0:
                    pct = min(99.5, max(0.0, downloaded * 100.0 / total))
                elif d.get("_percent_str"):
                    raw_pct = _strip_ansi(str(d["_percent_str"])).replace("%", "").strip()
                    try:
                        pct = min(99.5, max(0.0, float(raw_pct)))
                    except ValueError:
                        pct = 0.0

                if on_progress:
                    on_progress(
                        ProgressEvent(
                            status="downloading",
                            progress=round(pct, 1),
                            speed=_format_bytes_speed(d.get("speed")),
                            eta=_format_eta(d.get("eta")),
                            title=current_title[0],
                        )
                    )
            elif status == "finished":
                fn = d.get("filename")
                if fn:
                    captured_paths.append(os.path.normpath(fn))
                if on_progress:
                    on_progress(
                        ProgressEvent(
                            status="merging" if has_ffmpeg else "downloading",
                            progress=99.0,
                            title=current_title[0],
                        )
                    )

        def _postprocessor_hook(d: dict[str, Any]) -> None:
            if stop_event.is_set():
                raise DownloadCancelledError(stop_reason_getter() or "cancel")
            if d.get("status") == "finished":
                fp = (d.get("info_dict") or {}).get("filepath")
                if fp:
                    captured_paths.append(os.path.normpath(fp))

        opts["progress_hooks"] = [_progress_hook]
        opts["postprocessor_hooks"] = [_postprocessor_hook]

        if on_progress:
            on_progress(ProgressEvent(status="parsing", progress=0.0))

        try:
            with yt_dlp.YoutubeDL(opts) as ydl:
                info = ydl.extract_info(url, download=True)
                if info and "entries" in info and info["entries"]:
                    info = next((e for e in info["entries"] if e), info)
                if info:
                    current_title[0] = info.get("title") or current_title[0]
                    prepared = ydl.prepare_filename(info)
                    if prepared:
                        base_no_ext = os.path.splitext(prepared)[0]
                        expected_ext = (options.audio_format if options.only_audio else merge_ext).lstrip(".")
                        candidate = f"{base_no_ext}.{expected_ext}"
                        captured_paths.append(os.path.normpath(candidate))
                        captured_paths.append(os.path.normpath(prepared))
        except DownloadCancelledError:
            raise
        except Exception as exc:
            err_msg = _strip_ansi(str(exc))
            if "paused" in err_msg.lower() or "cancel" in err_msg.lower() or stop_event.is_set():
                raise DownloadCancelledError(stop_reason_getter() or "cancel") from exc
            if want_subs and "subtitles" in err_msg.lower():
                return self._download_via_python(
                    url,
                    platform_id,
                    options,
                    stop_event,
                    stop_reason_getter,
                    on_progress,
                    allow_browser_cookie=allow_browser_cookie,
                    include_subtitles=False,
                )
            if allow_browser_cookie and "cookiesfrombrowser" in opts and _is_cookie_lock_error(err_msg):
                return self._download_via_python(
                    url,
                    platform_id,
                    options,
                    stop_event,
                    stop_reason_getter,
                    on_progress,
                    allow_browser_cookie=False,
                    include_subtitles=want_subs,
                )
            raise RuntimeError(err_msg) from exc

        final_media, final_sub = self._resolve_output_files(
            options.save_dir, captured_paths, before_snap, started_at
        )
        return DownloadResult(
            file_path=final_media,
            title=current_title[0] or Path(final_media).stem,
            engine_used=self.name,
            subtitle_path=final_sub,
            thumbnail=(info or {}).get("thumbnail") or "" if "info" in locals() and isinstance(info, dict) else "",
            duration=float((info or {}).get("duration") or 0) if "info" in locals() and isinstance(info, dict) else 0.0,
            uploader=((info or {}).get("uploader") or (info or {}).get("channel") or "")
            if "info" in locals() and isinstance(info, dict)
            else "",
            description=((info or {}).get("description") or "")
            if "info" in locals() and isinstance(info, dict)
            else "",
        )
