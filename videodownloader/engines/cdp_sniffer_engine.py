"""Headless Chrome/Edge CDP Media Stream Sniffer Engine (inspired by `dsh-everything-down`).

Solves JS-signed short-video anti-captcha pages (Douyin, Kuaishou, Xiaohongshu, Xigua, etc.)
by running the user's local Chrome/Edge in hidden headless mode over WebSocket CDP,
intercepting real CDN `.mp4` / `.m3u8` / `.m4s` media streams, and merging via FFmpeg.
"""

from __future__ import annotations

import asyncio
import json
import os
import re
import shutil
import socket
import subprocess
import sys
import tempfile
import threading
import time
from pathlib import Path
from typing import Any, Callable

import requests
import websockets

from ..core.binary_updater import resolve_ffmpeg_path
from ..core.os_utils import find_local_chromium_browser, resolve_proxy_for_platform, validate_media_file
from ..core.url_cleaner import detect_platform
from .base import (
    BROWSER_SEM,
    BaseVideoEngine,
    DownloadCancelledError,
    DownloadOptions,
    DownloadResult,
    FFMPEG_SEM,
    FormatChoice,
    ParsedMedia,
    ProgressCallback,
    ProgressEvent,
)
from .cn_short_engine import safe_filename

DESKTOP_UA = (
    "Mozilla/5.0 (Windows NT 10.0; Win64; x64) "
    "AppleWebKit/537.36 (KHTML, like Gecko) Chrome/131.0.0.0 Safari/537.36"
)
WECHAT_MOBILE_UA = (
    "Mozilla/5.0 (iPhone; CPU iPhone OS 17_5 like Mac OS X) "
    "AppleWebKit/605.1.15 (KHTML, like Gecko) Mobile/15E148 "
    "MicroMessenger/8.0.49(0x18003128) NetType/WIFI Language/zh_CN"
)

JUNK_MEDIA_URL_RE = re.compile(
    r"douyinstatic\.com|/obj/douyin-pc-web/|uuu_265|/aweme/v1/web/|blob:",
    re.IGNORECASE,
)


def _pick_free_port() -> int:
    with socket.socket(socket.AF_INET, socket.SOCK_STREAM) as s:
        s.bind(("127.0.0.1", 0))
        return int(s.getsockname()[1])


def _classify_stream(url: str, mime: str) -> str | None:
    m = (mime or "").lower()
    u = (url or "").lower()
    if not u.startswith(("http://", "https://")) or JUNK_MEDIA_URL_RE.search(u):
        return None
    if "mpegurl" in m or re.search(r"\.m3u8(\?|$)", u):
        return "hls"
    if m.startswith("audio/") or re.search(r"media-audio|-1-302\d{2}\.m4s|\.(m4a|mp3|aac)(\?|$)", u):
        return "audio"
    if re.search(r"\.ts(\?|$)", u) or "mp2t" in m:
        return None
    if m.startswith("video/") or re.search(r"media-video|-1-\d+\.m4s|\.(mp4|m4v|mov|flv)(\?|$)", u):
        return "video"
    return None


def _clean_page_title(raw_title: str, platform_name: str) -> str:
    t = re.sub(r"\s*-\s*(抖音|快手|小红书|哔哩哔哩|微博|西瓜视频).*$", "", (raw_title or "").strip())
    t = re.sub(r"\s+", " ", t).strip()
    return t or f"{platform_name}视频"


class BrowserCDPSnifferEngine(BaseVideoEngine):
    """Stage-2 anti-captcha browser CDP stream sniffer for Douyin, Kuaishou, Xiaohongshu, and Xigua."""

    name = "browser-cdp-sniffer"
    _sniff_lock = BROWSER_SEM

    def can_handle(self, url: str, platform_id: str, prefer_fallback: bool = False) -> bool:
        if not find_local_chromium_browser():
            return False
        if platform_id in ("kuaishou", "xigua"):
            return True
        if platform_id in ("douyin", "xiaohongshu"):
            return prefer_fallback
        return False

    def skip_reason(self, url: str, platform_id: str, prefer_fallback: bool = False) -> str:
        if self.can_handle(url, platform_id, prefer_fallback=prefer_fallback):
            return ""
        if not find_local_chromium_browser():
            return "未检测到本地 Chrome 或 Edge 浏览器"
        if platform_id in ("douyin", "xiaohongshu") and not prefer_fallback:
            return "Stage 1 移动端直连优先，仅在风控时启用无头嗅探"
        return f"平台 {platform_id} 无需无头浏览器嗅探"

    def _sniff_media_streams(self, url: str, platform_id: str) -> dict[str, Any]:
        browser = find_local_chromium_browser()
        if not browser:
            raise RuntimeError("未检测到本地 Chrome 或 Edge 浏览器")

        use_mobile = platform_id == "kuaishou"
        ua = WECHAT_MOBILE_UA if use_mobile else DESKTOP_UA
        port = _pick_free_port()
        profile_dir = tempfile.mkdtemp(prefix="vd_cdp_")
        creationflags = (
            subprocess.CREATE_NO_WINDOW
            if sys.platform.startswith("win") and hasattr(subprocess, "CREATE_NO_WINDOW")
            else 0
        )

        args = [
            browser,
            f"--remote-debugging-port={port}",
            f"--user-data-dir={profile_dir}",
            "--headless=new",
            "--disable-gpu",
            "--mute-audio",
            "--autoplay-policy=no-user-gesture-required",
            "--disable-blink-features=AutomationControlled",
            "--disable-background-networking",
            "--no-first-run",
            "--no-default-browser-check",
            "about:blank",
        ]

        with self._sniff_lock:
            proc = subprocess.Popen(
                args,
                stdout=subprocess.DEVNULL,
                stderr=subprocess.DEVNULL,
                creationflags=creationflags,
            )
            try:
                ws_url = ""
                for _ in range(50):
                    try:
                        r = requests.get(
                            f"http://127.0.0.1:{port}/json/version",
                            timeout=0.5,
                            proxies={"http": None, "https": None},
                        )
                        if r.ok:
                            ws_url = r.json().get("webSocketDebuggerUrl") or ""
                            if ws_url:
                                break
                    except Exception:
                        pass
                    time.sleep(0.15)

                if not ws_url:
                    raise RuntimeError("无头浏览器调试端口未能就绪")

                return asyncio.run(self._cdp_session(ws_url, url, ua, use_mobile))
            finally:
                try:
                    proc.kill()
                    proc.wait(timeout=3)
                except Exception:
                    pass
                shutil.rmtree(profile_dir, ignore_errors=True)

    async def _cdp_session(self, ws_url: str, target_url: str, ua: str, mobile: bool) -> dict[str, Any]:
        found: dict[str, dict[str, str]] = {}
        first_media_ts = 0.0
        early_src = ""
        page_title = ""
        duration = 0.0
        poster = ""

        async with websockets.connect(ws_url, max_size=8 * 1024 * 1024) as ws:
            seq = 0
            pending: dict[int, asyncio.Future[Any]] = {}

            async def _reader() -> None:
                nonlocal first_media_ts
                try:
                    async for raw in ws:
                        msg = json.loads(raw)
                        mid = msg.get("id")
                        if mid and mid in pending:
                            fut = pending.pop(mid)
                            if not fut.done():
                                if "error" in msg:
                                    fut.set_exception(RuntimeError(str(msg["error"])))
                                else:
                                    fut.set_result(msg.get("result") or {})
                            continue

                        if msg.get("method") == "Network.responseReceived":
                            resp = (msg.get("params") or {}).get("response") or {}
                            r_url = resp.get("url") or ""
                            r_mime = resp.get("mimeType") or ""
                            kind = _classify_stream(r_url, r_mime)
                            if kind and r_url not in found:
                                found[r_url] = {"kind": kind, "url": r_url, "mime": r_mime}
                                if kind in ("video", "hls") and not first_media_ts:
                                    first_media_ts = time.monotonic()
                except Exception:
                    pass

            reader_task = asyncio.create_task(_reader())

            async def _send(method: str, params: dict[str, Any] | None = None, sid: str = "") -> dict[str, Any]:
                nonlocal seq
                seq += 1
                req_id = seq
                fut: asyncio.Future[Any] = asyncio.get_running_loop().create_future()
                pending[req_id] = fut
                payload: dict[str, Any] = {"id": req_id, "method": method, "params": params or {}}
                if sid:
                    payload["sessionId"] = sid
                await ws.send(json.dumps(payload))
                return await asyncio.wait_for(fut, timeout=12.0)

            try:
                created = await _send("Target.createTarget", {"url": "about:blank"})
                target_id = created["targetId"]
                attached = await _send("Target.attachToTarget", {"targetId": target_id, "flatten": True})
                sid = attached["sessionId"]

                await _send("Network.enable", {}, sid)
                await _send("Page.enable", {}, sid)
                await _send(
                    "Page.addScriptToEvaluateOnNewDocument",
                    {"source": 'Object.defineProperty(navigator,"webdriver",{get:()=>undefined});'},
                    sid,
                )
                if mobile:
                    await _send(
                        "Emulation.setDeviceMetricsOverride",
                        {"width": 390, "height": 844, "deviceScaleFactor": 3, "mobile": True},
                        sid,
                    )
                await _send(
                    "Emulation.setUserAgentOverride",
                    {"userAgent": ua, "platform": "iPhone" if mobile else "Win32"},
                    sid,
                )
                await _send("Page.navigate", {"url": target_url}, sid)

                t0 = time.monotonic()
                max_wait = 13.0 if mobile else 15.0
                play_triggered = False

                while (time.monotonic() - t0) < max_wait:
                    await asyncio.sleep(0.35)
                    if not early_src:
                        try:
                            ev = await _send(
                                "Runtime.evaluate",
                                {
                                    "expression": (
                                        'JSON.stringify({src:((document.querySelector("video")||{}).currentSrc)||"",'
                                        "t:document.title})"
                                    ),
                                    "returnByValue": True,
                                },
                                sid,
                            )
                            val = json.loads((ev.get("result") or {}).get("value") or "{}")
                            cand_src = str(val.get("src") or "")
                            if cand_src.startswith(("http://", "https://")) and not JUNK_MEDIA_URL_RE.search(cand_src):
                                early_src = cand_src
                                page_title = str(val.get("t") or page_title)
                        except Exception:
                            pass

                    elapsed = time.monotonic() - t0
                    if not play_triggered and elapsed > (2.2 if mobile else 3.5):
                        play_triggered = True
                        try:
                            await _send(
                                "Runtime.evaluate",
                                {
                                    "expression": (
                                        'document.querySelectorAll("video").forEach(v=>{try{v.muted=true;v.play().catch(()=>{})}catch(e){}});'
                                    ),
                                    "returnByValue": True,
                                },
                                sid,
                            )
                        except Exception:
                            pass

                    if mobile and early_src:
                        break
                    has_video = any(m["kind"] in ("video", "hls") for m in found.values())
                    has_audio = any(m["kind"] == "audio" for m in found.values())
                    if has_video and (has_audio or early_src) and first_media_ts and (time.monotonic() - first_media_ts) > 1.2:
                        break
                    if has_video and first_media_ts and (time.monotonic() - first_media_ts) > 2.4:
                        break

                try:
                    meta_ev = await _send(
                        "Runtime.evaluate",
                        {
                            "expression": (
                                'JSON.stringify({t:document.title,'
                                'd:(document.querySelector("video")||{}).duration||0,'
                                'p:(document.querySelector("video")||{}).poster||""})'
                            ),
                            "returnByValue": True,
                        },
                        sid,
                    )
                    mv = json.loads((meta_ev.get("result") or {}).get("value") or "{}")
                    page_title = page_title or str(mv.get("t") or "")
                    duration = float(mv.get("d") or 0.0)
                    poster = str(mv.get("p") or "")
                except Exception:
                    pass
            finally:
                reader_task.cancel()

        videos = [m["url"] for m in found.values() if m["kind"] == "video"]
        audios = [m["url"] for m in found.values() if m["kind"] == "audio"]
        hls_list = [m["url"] for m in found.values() if m["kind"] == "hls"]

        # Prioritize well-known video CDNs
        cdn_re = re.compile(r"douyinvod|kwaicdn|xhscdn|ndcimgs|yximgs|bilivideo", re.IGNORECASE)
        videos.sort(key=lambda u: 0 if cdn_re.search(u) else 1)

        video_url = early_src or (videos[0] if videos else (hls_list[0] if hls_list else ""))
        audio_url = "" if early_src else (audios[0] if audios else "")

        if not video_url:
            raise RuntimeError("无头浏览器页面嗅探未捕获到可播放视频流")

        return {
            "title": page_title,
            "duration": duration,
            "poster": poster,
            "video_url": video_url,
            "audio_url": audio_url,
            "ua": ua,
        }

    def parse(self, url: str, platform_id: str, options: DownloadOptions) -> ParsedMedia:
        pid, pname, _ = detect_platform(url)
        res = self._sniff_media_streams(url, platform_id)
        title = _clean_page_title(res["title"], pname)
        referer_map = {
            "douyin": "https://www.douyin.com/",
            "kuaishou": "https://www.kuaishou.com/",
            "xiaohongshu": "https://www.xiaohongshu.com/",
            "xigua": "https://www.ixigua.com/",
        }
        return ParsedMedia(
            url=url,
            title=title,
            platform_id=pid if pid != "generic" else platform_id,
            platform_name=f"{pname} (浏览器无头嗅探)",
            thumbnail=res["poster"],
            duration=res["duration"],
            direct_video_url=res["video_url"],
            direct_audio_url=res["audio_url"],
            direct_headers={
                "User-Agent": res["ua"],
                "Referer": referer_map.get(platform_id, url),
                "Accept": "*/*",
            },
            formats=[FormatChoice(format_id="cdp", label="原画无水印直链 (MP4)", ext="mp4")],
        )

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
        if on_progress:
            on_progress(ProgressEvent(status="parsing", progress=0.0))

        media = self.parse(url, platform_id, options)
        if stop_event.is_set():
            raise DownloadCancelledError(stop_reason_getter() or "cancel")

        base_name = safe_filename(media.title)
        ffmpeg_path = resolve_ffmpeg_path()
        resolved_proxy = resolve_proxy_for_platform(platform_id, options.proxy_mode, options.proxy_url)
        req_proxies = {"http": None, "https": None} if resolved_proxy == "" else (
            {"http": resolved_proxy, "https": resolved_proxy} if resolved_proxy else None
        )

        # HLS stream -> mux via FFmpeg
        if ".m3u8" in media.direct_video_url.lower():
            if not ffmpeg_path:
                raise RuntimeError("下载 M3U8 流需要 FFmpeg 组件")
            out_path = os.path.join(options.save_dir, f"{base_name}.mp4")
            creationflags = (
                subprocess.CREATE_NO_WINDOW
                if sys.platform.startswith("win") and hasattr(subprocess, "CREATE_NO_WINDOW")
                else 0
            )
            proc = subprocess.Popen(
                [ffmpeg_path, "-y", "-i", media.direct_video_url, "-c", "copy", "-movflags", "+faststart", out_path],
                stdout=subprocess.DEVNULL,
                stderr=subprocess.DEVNULL,
                creationflags=creationflags,
            )
            while proc.poll() is None:
                if stop_event.is_set():
                    proc.terminate()
                    raise DownloadCancelledError(stop_reason_getter() or "cancel")
                time.sleep(0.3)
            if proc.returncode != 0:
                raise RuntimeError("FFmpeg 拉取 M3U8 流失败")
            ok, reason = validate_media_file(out_path)
            if not ok:
                raise RuntimeError(reason)
            return DownloadResult(
                file_path=out_path,
                title=media.title,
                engine_used=self.name,
                thumbnail=media.thumbnail,
                duration=media.duration,
            )

        out_ext = options.audio_format if options.only_audio else "mp4"
        final_path = os.path.join(options.save_dir, f"{base_name}.{out_ext}")
        if os.path.exists(final_path):
            final_path = os.path.join(options.save_dir, f"{base_name}_{int(time.time()) % 10000}.{out_ext}")

        tmp_video = final_path + ".v.part"
        self._stream_http_file(
            media.direct_video_url,
            tmp_video,
            media.direct_headers,
            req_proxies,
            stop_event,
            stop_reason_getter,
            on_progress,
            media.title,
        )

        if media.direct_audio_url and ffmpeg_path and not options.only_audio:
            tmp_audio = final_path + ".a.part"
            try:
                self._stream_http_file(
                    media.direct_audio_url,
                    tmp_audio,
                    media.direct_headers,
                    req_proxies,
                    stop_event,
                    stop_reason_getter,
                    None,
                    media.title,
                )
                if on_progress:
                    on_progress(ProgressEvent(status="merging", progress=99.0, title=media.title))
                creationflags = (
                    subprocess.CREATE_NO_WINDOW
                    if sys.platform.startswith("win") and hasattr(subprocess, "CREATE_NO_WINDOW")
                    else 0
                )
                with FFMPEG_SEM:
                    subprocess.run(
                        [
                            ffmpeg_path, "-y", "-i", tmp_video, "-i", tmp_audio,
                            "-c", "copy", "-movflags", "+faststart", final_path,
                        ],
                        check=True,
                        stdout=subprocess.DEVNULL,
                        stderr=subprocess.DEVNULL,
                        creationflags=creationflags,
                    )
            finally:
                for p in (tmp_video, tmp_audio):
                    if os.path.exists(p):
                        try:
                            os.remove(p)
                        except OSError:
                            pass
        elif options.only_audio and ffmpeg_path:
            try:
                creationflags = (
                    subprocess.CREATE_NO_WINDOW
                    if sys.platform.startswith("win") and hasattr(subprocess, "CREATE_NO_WINDOW")
                    else 0
                )
                codec_args = ["-vn", "-acodec", "libmp3lame", "-b:a", "192k"] if out_ext == "mp3" else ["-vn", "-c:a", "aac"]
                subprocess.run(
                    [ffmpeg_path, "-y", "-i", tmp_video, *codec_args, final_path],
                    check=True,
                    stdout=subprocess.DEVNULL,
                    stderr=subprocess.DEVNULL,
                    creationflags=creationflags,
                )
            finally:
                if os.path.exists(tmp_video):
                    try:
                        os.remove(tmp_video)
                    except OSError:
                        pass
        else:
            os.replace(tmp_video, final_path)

        ok, reason = validate_media_file(final_path)
        if not ok:
            try:
                os.remove(final_path)
            except OSError:
                pass
            raise RuntimeError(reason)

        return DownloadResult(
            file_path=final_path,
            title=media.title,
            engine_used=self.name,
            thumbnail=media.thumbnail,
            duration=media.duration,
        )

    @staticmethod
    def _stream_http_file(
        stream_url: str,
        dest_file: str,
        headers: dict[str, str],
        proxies: dict[str, str | None] | None,
        stop_event: threading.Event,
        stop_reason_getter: Callable[[], str],
        on_progress: ProgressCallback | None,
        title: str,
    ) -> None:
        req_headers = dict(headers)
        existing = os.path.getsize(dest_file) if os.path.exists(dest_file) else 0
        if existing > 0:
            req_headers["Range"] = f"bytes={existing}-"

        with requests.get(
            stream_url,
            headers=req_headers,
            proxies=proxies,
            stream=True,
            allow_redirects=True,
            timeout=25,
        ) as resp:
            if resp.status_code == 416 and existing > 0:
                return
            resp.raise_for_status()
            ctype = (resp.headers.get("Content-Type") or "").lower()
            if "text/html" in ctype:
                raise RuntimeError("媒体直链返回了 HTML 页面，疑似直链鉴权失效")

            is_partial = resp.status_code == 206
            mode = "ab" if (is_partial and existing > 0) else "wb"
            downloaded = existing if (is_partial and existing > 0) else 0
            clen = int(resp.headers.get("Content-Length") or 0)
            total = (downloaded + clen) if clen > 0 else 0
            t0 = time.perf_counter()

            with open(dest_file, mode) as fp:
                for chunk in resp.iter_content(chunk_size=256 * 1024):
                    if stop_event.is_set():
                        raise DownloadCancelledError(stop_reason_getter() or "cancel")
                    if not chunk:
                        continue
                    fp.write(chunk)
                    downloaded += len(chunk)
                    if on_progress:
                        elapsed = max(0.05, time.perf_counter() - t0)
                        bps = downloaded / elapsed
                        spd = f"{bps / (1024 * 1024):.1f} MB/s" if bps >= 1024 * 1024 else f"{bps / 1024:.0f} KB/s"
                        pct = min(98.5, downloaded * 100.0 / total) if total > 0 else 50.0
                        on_progress(ProgressEvent(status="downloading", progress=round(pct, 1), speed=spd, title=title))
