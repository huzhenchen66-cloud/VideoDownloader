"""Chinese Short-Video Watermark-Free Mobile Extractor, Direct/M3U8 Downloader & Strict Media Validator.

Hardened in v2.0:
  - Domestic requests bypass system VPN proxies automatically (`proxies={"http": None, "https": None}`)
  - Generic OpenGraph `<meta property="og:video">` sniffing NEVER runs on `youtube`, `bilibili`, `tiktok`,
    `twitter`, `instagram`, `facebook` (preventing `/embed/` HTML iframes from ever being mistaken for MP4!)
  - Strict Content-Type & binary header validation (`validate_media_file`) prevents saving corrupt HTML files.
"""

from __future__ import annotations

import html
import json
import os
import re
import subprocess
import sys
import threading
import time
from pathlib import Path
from typing import Any, Callable
from urllib.parse import urlparse

import requests

from ..core.binary_updater import resolve_ffmpeg_path
from ..core.fast_downloader import (
    MIN_CHUNK_ACCELERATION_BYTES,
    download_stream_multi_chunk,
    download_stream_sequential,
    probe_range_support,
)
from ..core.os_utils import resolve_proxy_for_platform, validate_media_file
from ..core.url_cleaner import MOBILE_UA, detect_platform
from .base import (
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

DESKTOP_UA = (
    "Mozilla/5.0 (Windows NT 10.0; Win64; x64) "
    "AppleWebKit/537.36 (KHTML, like Gecko) Chrome/131.0.0.0 Safari/537.36"
)

# Platforms where generic `<meta property="og:video">` points to an HTML `/embed/` player rather than a raw stream
NO_GENERIC_OG_PLATFORMS = {
    "youtube",
    "bilibili",
    "tiktok",
    "twitter",
    "instagram",
    "facebook",
    "vimeo",
    "twitch",
    "douyin",
    "xiaohongshu",
    "kuaishou",
    "xigua",
}

SAFE_FILENAME_RE = re.compile(r'[<>:"/\\|?*\x00-\x1f]+')
ROUTER_DATA_RE = re.compile(r"window\._ROUTER_DATA\s*=\s*(\{.+?\})\s*</script>", re.DOTALL)
XHS_INITIAL_STATE_RE = re.compile(r"window\.__INITIAL_STATE__\s*=\s*(\{.+?\})\s*</script>", re.DOTALL)
OG_VIDEO_RE = re.compile(
    r'<meta[^>]+(?:property|name)=["\']og:video(?::url|:secure_url)?["\'][^>]+content=["\']([^"\']+)["\']',
    re.IGNORECASE,
)
OG_TITLE_RE = re.compile(
    r'<meta[^>]+(?:property|name)=["\']og:title["\'][^>]+content=["\']([^"\']+)["\']',
    re.IGNORECASE,
)
OG_IMAGE_RE = re.compile(
    r'<meta[^>]+(?:property|name)=["\']og:image["\'][^>]+content=["\']([^"\']+)["\']',
    re.IGNORECASE,
)
HTML_TITLE_RE = re.compile(r"<title[^>]*>(.*?)</title>", re.IGNORECASE | re.DOTALL)
VIDEO_SRC_RE = re.compile(r'<video[^>]+src=["\']([^"\']+)["\']', re.IGNORECASE)


def safe_filename(name: str, max_len: int = 90) -> str:
    cleaned = SAFE_FILENAME_RE.sub("_", (name or "").strip())
    cleaned = re.sub(r"\s+", " ", cleaned).strip(" ._")
    return (cleaned[:max_len] or f"video_{int(time.time())}").strip()


def _format_speed(bps: float) -> str:
    if bps <= 0:
        return ""
    if bps >= 1024 * 1024:
        return f"{bps / (1024 * 1024):.1f} MB/s"
    if bps >= 1024:
        return f"{bps / 1024:.0f} KB/s"
    return f"{int(bps)} B/s"


def _requests_proxies_for(platform_id: str, options: DownloadOptions) -> dict[str, str | None] | None:
    resolved = resolve_proxy_for_platform(platform_id, options.proxy_mode, options.proxy_url)
    if resolved == "":
        return {"http": None, "https": None}
    if resolved:
        return {"http": resolved, "https": resolved}
    return None


class NativeCNAndSnifferEngine(BaseVideoEngine):
    """Handles Douyin/Xiaohongshu mobile share links, optional videofetch bridge, and direct/M3U8 streams."""

    name = "cn-native-sniffer"

    def can_handle(self, url: str, platform_id: str, prefer_fallback: bool = False) -> bool:
        if platform_id in ("douyin", "xiaohongshu", "kuaishou", "weibo", "zhihu", "direct"):
            return True
        if platform_id in NO_GENERIC_OG_PLATFORMS:
            return False
        return prefer_fallback

    def skip_reason(self, url: str, platform_id: str, prefer_fallback: bool = False) -> str:
        if self.can_handle(url, platform_id, prefer_fallback=prefer_fallback):
            return ""
        if platform_id in NO_GENERIC_OG_PLATFORMS:
            return f"平台 {platform_id} 不在移动端直连注册表"
        return "非短视频直连域名，交由后续引擎处理"

    # -------------------- Platform Parsers --------------------

    def _parse_douyin(self, url: str, options: DownloadOptions) -> ParsedMedia:
        headers = {
            "User-Agent": MOBILE_UA,
            "Accept": "text/html,application/xhtml+xml,application/xml;q=0.9,*/*;q=0.8",
        }
        resp = requests.get(
            url,
            headers=headers,
            proxies=_requests_proxies_for("douyin", options),
            allow_redirects=True,
            timeout=12,
        )
        resp.raise_for_status()
        final_url = resp.url
        page = resp.text

        m = ROUTER_DATA_RE.search(page)
        if not m:
            raise RuntimeError("抖音移动端分享页需 JS 签名渲染，自动切换至无头浏览器引擎")

        data = json.loads(m.group(1))
        loader = data.get("loaderData") or {}
        item_info: dict[str, Any] | None = None
        for val in loader.values():
            if isinstance(val, dict) and "videoInfoRes" in val:
                items = (val.get("videoInfoRes") or {}).get("item_list") or []
                if items:
                    item_info = items[0]
                    break
        if not item_info:
            raise RuntimeError("未在抖音分享数据中找到视频流信息")

        desc = item_info.get("desc") or "抖音视频"
        author = (item_info.get("author") or {}).get("nickname") or ""
        video_obj = item_info.get("video") or {}
        play_addr = (video_obj.get("play_addr") or {}).get("url_list") or []
        if not play_addr:
            raise RuntimeError("抖音视频播放地址为空（可能是图文笔记）")

        wm_free_url = play_addr[0].replace("playwm", "play")
        cover_list = ((video_obj.get("cover") or {}).get("url_list")) or []
        duration_ms = float(video_obj.get("duration") or 0)
        duration = duration_ms / 1000.0 if duration_ms > 1000 else duration_ms

        return ParsedMedia(
            url=final_url,
            title=desc,
            platform_id="douyin",
            platform_name="抖音 Douyin (无水印直解)",
            uploader=author,
            thumbnail=cover_list[0] if cover_list else "",
            duration=duration,
            direct_video_url=wm_free_url,
            direct_headers={"User-Agent": MOBILE_UA, "Referer": "https://www.iesdouyin.com/"},
            formats=[FormatChoice(format_id="nowm", label="原画无水印 MP4", ext="mp4")],
        )

    def _parse_xiaohongshu(self, url: str, options: DownloadOptions) -> ParsedMedia:
        headers = {
            "User-Agent": DESKTOP_UA,
            "Accept": "text/html,application/xhtml+xml,application/xml;q=0.9,*/*;q=0.8",
        }
        resp = requests.get(
            url,
            headers=headers,
            proxies=_requests_proxies_for("xiaohongshu", options),
            allow_redirects=True,
            timeout=12,
        )
        resp.raise_for_status()
        final_url = resp.url
        page = resp.text

        og_vid = OG_VIDEO_RE.search(page)
        og_title = OG_TITLE_RE.search(page)
        og_img = OG_IMAGE_RE.search(page)

        video_url = html.unescape(og_vid.group(1)) if og_vid else ""
        title = html.unescape(og_title.group(1)).replace(" - 小红书", "").strip() if og_title else ""
        thumb = html.unescape(og_img.group(1)) if og_img else ""
        author = ""

        if not video_url:
            m = XHS_INITIAL_STATE_RE.search(page)
            if m:
                raw_state = m.group(1).replace(":undefined", ":null")
                try:
                    state = json.loads(raw_state)
                    note_map = ((state.get("note") or {}).get("noteDetailMap")) or {}
                    for entry in note_map.values():
                        note = (entry or {}).get("note") or {}
                        title = title or note.get("title") or note.get("desc") or ""
                        author = ((note.get("user") or {}).get("nickname")) or ""
                        streams = (((note.get("video") or {}).get("media") or {}).get("stream") or {}).get("h264") or []
                        if streams and streams[0].get("masterUrl"):
                            video_url = streams[0]["masterUrl"]
                            break
                except Exception:
                    pass

        if not video_url:
            raise RuntimeError("小红书静态页未提取到视频直链，自动切换至下一引擎")

        return ParsedMedia(
            url=final_url,
            title=title or "小红书视频",
            platform_id="xiaohongshu",
            platform_name="小红书 RED (无水印直解)",
            uploader=author,
            thumbnail=thumb,
            direct_video_url=video_url,
            direct_headers={"User-Agent": DESKTOP_UA, "Referer": "https://www.xiaohongshu.com/"},
            formats=[FormatChoice(format_id="nowm", label="原画无水印 MP4", ext="mp4")],
        )

    def _parse_kuaishou(self, url: str, options: DownloadOptions) -> ParsedMedia:
        headers = {
            "User-Agent": DESKTOP_UA,
            "Accept": "text/html,application/xhtml+xml,application/xml;q=0.9,*/*;q=0.8",
            "Referer": "https://v.kuaishou.com/",
        }
        resp = requests.get(
            url,
            headers=headers,
            proxies=_requests_proxies_for("kuaishou", options),
            allow_redirects=True,
            timeout=12,
        )
        resp.raise_for_status()
        page = resp.text

        video_url = ""
        title = ""
        thumb = ""

        # Search for photoUrl / photoH265Url / mainMvcUrl
        m_url = (
            re.search(r'"photoUrl"\s*:\s*"([^"]+)"', page)
            or re.search(r'"photoH265Url"\s*:\s*"([^"]+)"', page)
            or re.search(r'"url"\s*:\s*"(https?://[^"]+\.mp4[^"]*)"', page)
        )
        if m_url:
            video_url = m_url.group(1).encode("utf-8").decode("unicode_escape").strip()
            video_url = video_url.replace(r"\/", "/")

        m_title = re.search(r'<meta[^>]+(?:name|property)="og:title"[^>]+content="([^"]+)"', page) or re.search(r"<title>(.*?)</title>", page)
        if m_title:
            title = html.unescape(m_title.group(1)).replace(" - 快手", "").replace("-快手", "").strip()

        m_img = re.search(r'<meta[^>]+(?:name|property)="og:image"[^>]+content="([^"]+)"', page) or re.search(r'"coverUrl"\s*:\s*"([^"]+)"', page)
        if m_img:
            thumb = m_img.group(1).replace(r"\/", "/")

        if not video_url:
            m_v = VIDEO_SRC_RE.search(page)
            if m_v:
                video_url = html.unescape(m_v.group(1))

        if not video_url:
            raise RuntimeError("快手页面未直接提取到视频直链，自动切换至 yt-dlp 引擎解析")

        return ParsedMedia(
            url=resp.url,
            title=title or "快手短视频",
            platform_id="kuaishou",
            platform_name="快手 Kuaishou (无水印直解)",
            thumbnail=thumb,
            direct_video_url=video_url,
            direct_headers={"User-Agent": DESKTOP_UA, "Referer": "https://www.kuaishou.com/"},
            formats=[FormatChoice(format_id="nowm", label="原画无水印 MP4", ext="mp4")],
        )

    def _parse_weibo(self, url: str, options: DownloadOptions) -> ParsedMedia:
        headers = {
            "User-Agent": DESKTOP_UA,
            "Accept": "text/html,application/xhtml+xml,application/xml;q=0.9,*/*;q=0.8",
            "Referer": "https://weibo.com/",
        }
        resp = requests.get(
            url,
            headers=headers,
            proxies=_requests_proxies_for("weibo", options),
            allow_redirects=True,
            timeout=12,
        )
        resp.raise_for_status()
        final_url = resp.url
        page = resp.text

        video_url = ""
        title = ""
        thumb = ""

        # Check direct status / fid for mobile API
        m_vid = re.search(r"/(?:detail|status)/([A-Za-z0-9]+)", final_url)
        if m_vid:
            vid = m_vid.group(1)
            try:
                api_url = f"https://m.weibo.cn/statuses/show?id={vid}"
                api_resp = requests.get(
                    api_url,
                    headers={"User-Agent": MOBILE_UA, "Referer": f"https://m.weibo.cn/detail/{vid}"},
                    timeout=8,
                )
                if api_resp.status_code == 200:
                    data = api_resp.json().get("data", {})
                    page_info = data.get("page_info") or {}
                    media_info = page_info.get("media_info") or {}
                    urls_map = page_info.get("urls") or {}
                    video_url = (
                        urls_map.get("mp4_720p_mp4")
                        or urls_map.get("mp4_hd_mp4")
                        or media_info.get("stream_url_hd")
                        or media_info.get("stream_url")
                    )
                    title = page_info.get("content2") or data.get("text_raw") or ""
                    thumb = (
                        page_info.get("page_pic", {}).get("url")
                        if isinstance(page_info.get("page_pic"), dict)
                        else ""
                    )
            except Exception:
                pass

        if not video_url:
            m_stream = re.search(r'"stream_url(?:_hd)?"\s*:\s*"(https?://[^"]+\.mp4[^"]*)"', page) or OG_VIDEO_RE.search(page)
            if m_stream:
                video_url = m_stream.group(1).replace(r"\/", "/")

        if not title:
            m_t = OG_TITLE_RE.search(page) or HTML_TITLE_RE.search(page)
            if m_t:
                title = html.unescape(m_t.group(1)).replace(" - 微博", "").replace("-微博", "").strip()

        if not thumb:
            m_im = OG_IMAGE_RE.search(page)
            if m_im:
                thumb = m_im.group(1).replace(r"\/", "/")

        if not video_url:
            raise RuntimeError("微博页面未提取到有效视频流，自动切换至 yt-dlp 引擎")

        return ParsedMedia(
            url=final_url,
            title=title or "微博视频",
            platform_id="weibo",
            platform_name="微博 Weibo (高清直解)",
            thumbnail=thumb,
            direct_video_url=video_url,
            direct_headers={"User-Agent": DESKTOP_UA, "Referer": "https://weibo.com/"},
            formats=[FormatChoice(format_id="hd", label="高清 MP4", ext="mp4")],
        )

    def _parse_zhihu(self, url: str, options: DownloadOptions) -> ParsedMedia:
        m = re.search(r"/(?:zvideo|video)/(\d+)", url)
        if not m:
            raise RuntimeError("非标准知乎视频链接，交由 yt-dlp 引擎解析")
        vid = m.group(1)
        api_url = f"https://www.zhihu.com/api/v4/zvideos/{vid}"
        headers = {"User-Agent": DESKTOP_UA, "Referer": f"https://www.zhihu.com/zvideo/{vid}"}
        resp = requests.get(
            api_url,
            headers=headers,
            proxies=_requests_proxies_for("zhihu", options),
            timeout=10,
        )
        resp.raise_for_status()
        data = resp.json()
        title = data.get("title") or "知乎视频"
        video_info = data.get("video") or {}
        thumb = video_info.get("thumbnail") or ""
        playlist = video_info.get("playlist") or {}

        best_url = ""
        best_res = 0
        for item in playlist.values():
            if isinstance(item, dict):
                w = int(item.get("width") or 0)
                h = int(item.get("height") or 0)
                u = item.get("play_url") or item.get("url") or ""
                if w * h > best_res and u:
                    best_res = w * h
                    best_url = u

        if not best_url:
            raise RuntimeError("未在知乎 API 中提取到播放地址")

        return ParsedMedia(
            url=url,
            title=title,
            platform_id="zhihu",
            platform_name="知乎 Zhihu (高清直解)",
            thumbnail=thumb,
            direct_video_url=best_url,
            direct_headers={"User-Agent": DESKTOP_UA, "Referer": "https://www.zhihu.com/"},
            formats=[FormatChoice(format_id="best", label="原画高清 MP4", ext="mp4")],
        )

    def _try_optional_videofetch(self, url: str) -> ParsedMedia | None:
        """Optional dynamic plugin adapter if `videofetch` (`videodl`) is installed in Python env."""
        try:
            from videodl import videodl as videodl_mod  # type: ignore
        except Exception:
            return None

        try:
            client = videodl_mod.VideoClient()
            infos = client.parsefromurl(url)
            for info in infos or []:
                dl_url = getattr(info, "download_url", "") or (info.get("download_url") if isinstance(info, dict) else "")
                if dl_url and str(dl_url).startswith("http"):
                    pid, pname, _ = detect_platform(url)
                    return ParsedMedia(
                        url=url,
                        title=getattr(info, "title", "") or "视频下载",
                        platform_id=pid,
                        platform_name=f"{pname} (videofetch驱动)",
                        thumbnail=getattr(info, "cover_url", "") or "",
                        direct_video_url=str(dl_url),
                        direct_headers=getattr(info, "default_download_headers", None) or {"User-Agent": DESKTOP_UA},
                        formats=[FormatChoice(format_id="default", label="最佳直链", ext=getattr(info, "ext", "mp4") or "mp4")],
                    )
        except Exception:
            return None
        return None

    def _parse_direct_or_opengraph(self, url: str, platform_id: str, options: DownloadOptions) -> ParsedMedia:
        parsed = urlparse(url)
        path_lower = (parsed.path or "").lower()
        is_m3u8 = ".m3u8" in url.lower()
        is_direct = is_m3u8 or any(
            path_lower.endswith(ext) for ext in (".mp4", ".mkv", ".webm", ".mov", ".mp3", ".m4a", ".flv")
        )

        if is_direct:
            stem = Path(parsed.path).stem or f"media_{int(time.time())}"
            ext = "mp4" if is_m3u8 else (Path(parsed.path).suffix.lstrip(".") or "mp4")
            return ParsedMedia(
                url=url,
                title=stem,
                platform_id="direct",
                platform_name="M3U8 流媒体" if is_m3u8 else "直链媒体",
                direct_video_url=url,
                direct_headers={"User-Agent": DESKTOP_UA},
                formats=[FormatChoice(format_id="direct", label=f"原始流 ({ext.upper()})", ext=ext)],
            )

        if platform_id in NO_GENERIC_OG_PLATFORMS:
            raise RuntimeError("该平台不使用通用 HTML 嗅探")

        vf_media = self._try_optional_videofetch(url)
        if vf_media:
            return vf_media

        resp = requests.get(
            url,
            headers={"User-Agent": DESKTOP_UA},
            proxies=_requests_proxies_for(platform_id, options),
            allow_redirects=True,
            timeout=12,
        )
        resp.raise_for_status()
        content_type = (resp.headers.get("Content-Type") or "").lower()
        if content_type.startswith("video/") or content_type.startswith("audio/"):
            ext = "mp3" if "audio" in content_type else "mp4"
            return ParsedMedia(
                url=resp.url,
                title=Path(urlparse(resp.url).path).stem or "直链媒体",
                platform_id="direct",
                platform_name="直链媒体",
                direct_video_url=resp.url,
                direct_headers={"User-Agent": DESKTOP_UA},
                formats=[FormatChoice(format_id="direct", label=f"直链 ({ext.upper()})", ext=ext)],
            )

        page = resp.text
        vid_match = VIDEO_SRC_RE.search(page) or OG_VIDEO_RE.search(page)
        if not vid_match:
            raise RuntimeError("通用网页嗅探未发现直接可下载的媒体文件流")

        media_url = html.unescape(vid_match.group(1))
        if media_url.startswith("//"):
            media_url = f"{parsed.scheme or 'https'}:{media_url}"

        # Reject iframe/embed HTML player URLs masquerading as `og:video`
        lower_media = media_url.lower()
        if "/embed/" in lower_media or lower_media.endswith((".html", ".htm", ".php", ".swf")):
            raise RuntimeError("og:video 为网页嵌入播放器而非直链视频流")

        title_match = OG_TITLE_RE.search(page) or HTML_TITLE_RE.search(page)
        title = html.unescape(re.sub(r"\s+", " ", title_match.group(1))).strip() if title_match else "网页视频"
        img_match = OG_IMAGE_RE.search(page)

        return ParsedMedia(
            url=resp.url,
            title=title,
            platform_id=platform_id,
            platform_name="网页媒体嗅探",
            thumbnail=html.unescape(img_match.group(1)) if img_match else "",
            direct_video_url=media_url,
            direct_headers={"User-Agent": DESKTOP_UA, "Referer": resp.url},
            formats=[FormatChoice(format_id="sniffed", label="嗅探直链 MP4", ext="mp4")],
        )

    def parse(self, url: str, platform_id: str, options: DownloadOptions) -> ParsedMedia:
        if platform_id == "douyin":
            return self._parse_douyin(url, options)
        if platform_id == "xiaohongshu":
            return self._parse_xiaohongshu(url, options)
        if platform_id == "kuaishou":
            return self._parse_kuaishou(url, options)
        if platform_id == "weibo":
            return self._parse_weibo(url, options)
        if platform_id == "zhihu":
            return self._parse_zhihu(url, options)
        return self._parse_direct_or_opengraph(url, platform_id, options)

    # -------------------- Stream / HTTP Downloader --------------------

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
        stream_url = media.direct_video_url
        if not stream_url:
            raise RuntimeError("未获取到有效直链地址")

        base_name = safe_filename(media.title)
        ffmpeg_path = resolve_ffmpeg_path()

        if ".m3u8" in stream_url.lower():
            return self._download_m3u8_via_ffmpeg(
                stream_url=stream_url,
                base_name=base_name,
                media=media,
                options=options,
                ffmpeg_path=ffmpeg_path,
                stop_event=stop_event,
                stop_reason_getter=stop_reason_getter,
                on_progress=on_progress,
            )

        ext = "mp4"
        if options.only_audio and not ffmpeg_path:
            ext = "m4a"
        raw_target = os.path.join(options.save_dir, f"{base_name}.{ext}")
        if os.path.exists(raw_target):
            raw_target = os.path.join(options.save_dir, f"{base_name}_{int(time.time()) % 10000}.{ext}")
        part_target = raw_target + ".part"

        headers = dict(media.direct_headers or {"User-Agent": DESKTOP_UA})
        proxies = _requests_proxies_for(platform_id, options)
        threads = max(2, min(16, int(options.fragment_threads or 8)))

        supports_range, total_bytes, resolved_url = probe_range_support(
            stream_url, headers=headers, proxies=proxies
        )

        if supports_range and total_bytes >= MIN_CHUNK_ACCELERATION_BYTES:
            download_stream_multi_chunk(
                url=resolved_url,
                target_path=raw_target,
                headers=headers,
                total_bytes=total_bytes,
                num_workers=threads,
                proxies=proxies,
                stop_event=stop_event,
                stop_reason_getter=stop_reason_getter,
                on_progress=on_progress,
                title=media.title,
            )
        else:
            download_stream_sequential(
                url=resolved_url,
                target_path=raw_target,
                headers=headers,
                total_bytes=total_bytes,
                proxies=proxies,
                stop_event=stop_event,
                stop_reason_getter=stop_reason_getter,
                on_progress=on_progress,
                title=media.title,
            )

        ok, err_reason = validate_media_file(raw_target)
        if not ok:
            try:
                os.remove(raw_target)
            except OSError:
                pass
            raise RuntimeError(err_reason)

        final_path = raw_target
        if options.only_audio and ffmpeg_path:
            if on_progress:
                on_progress(ProgressEvent(status="merging", progress=99.0, title=media.title))
            audio_ext = options.audio_format.lstrip(".")
            audio_target = os.path.splitext(raw_target)[0] + f".{audio_ext}"
            creationflags = (
                subprocess.CREATE_NO_WINDOW
                if sys.platform.startswith("win") and hasattr(subprocess, "CREATE_NO_WINDOW")
                else 0
            )
            cmd = [ffmpeg_path, "-y", "-i", raw_target, "-vn"]
            if audio_ext == "mp3":
                cmd += ["-acodec", "libmp3lame", "-b:a", "192k", audio_target]
            else:
                cmd += ["-c:a", "aac", audio_target]
            with FFMPEG_SEM:
                subprocess.run(cmd, check=True, stdout=subprocess.DEVNULL, stderr=subprocess.DEVNULL, creationflags=creationflags)
            try:
                os.remove(raw_target)
            except OSError:
                pass
            final_path = audio_target

        return DownloadResult(
            file_path=final_path,
            title=media.title,
            engine_used=self.name,
            thumbnail=media.thumbnail,
            duration=media.duration,
            uploader=media.uploader,
        )

    def _download_m3u8_via_ffmpeg(
        self,
        stream_url: str,
        base_name: str,
        media: ParsedMedia,
        options: DownloadOptions,
        ffmpeg_path: str,
        stop_event: threading.Event,
        stop_reason_getter: Callable[[], str],
        on_progress: ProgressCallback | None,
    ) -> DownloadResult:
        if not ffmpeg_path:
            raise RuntimeError("下载 M3U8 流媒体需要 FFmpeg，当前环境未检测到 FFmpeg")

        out_ext = options.audio_format if options.only_audio else "mp4"
        final_path = os.path.join(options.save_dir, f"{base_name}.{out_ext}")
        creationflags = (
            subprocess.CREATE_NO_WINDOW
            if sys.platform.startswith("win") and hasattr(subprocess, "CREATE_NO_WINDOW")
            else 0
        )

        if options.only_audio:
            cmd = [ffmpeg_path, "-y", "-i", stream_url, "-vn", final_path]
        else:
            cmd = [ffmpeg_path, "-y", "-i", stream_url, "-c", "copy", "-bsf:a", "aac_adtstoasc", final_path]

        proc = subprocess.Popen(cmd, stdout=subprocess.DEVNULL, stderr=subprocess.PIPE, creationflags=creationflags)
        while proc.poll() is None:
            if stop_event.is_set():
                proc.terminate()
                raise DownloadCancelledError(stop_reason_getter() or "cancel")
            if on_progress:
                on_progress(ProgressEvent(status="downloading", progress=60.0, title=media.title))
            time.sleep(0.4)

        if proc.returncode != 0:
            raise RuntimeError(f"FFmpeg 合并 M3U8 流失败 (exit={proc.returncode})")

        ok, err_reason = validate_media_file(final_path)
        if not ok:
            try:
                os.remove(final_path)
            except OSError:
                pass
            raise RuntimeError(err_reason)

        return DownloadResult(
            file_path=final_path,
            title=media.title,
            engine_used=f"{self.name}+ffmpeg",
            thumbnail=media.thumbnail,
            duration=media.duration,
            uploader=media.uploader,
        )
