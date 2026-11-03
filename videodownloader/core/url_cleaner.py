"""Smart URL cleaner for messy mobile share text (Douyin, Xiaohongshu, Bilibili, YouTube, Kuaishou, etc.)."""

from __future__ import annotations

import re
from dataclasses import dataclass
from urllib.parse import parse_qs, urlencode, urlparse, urlunparse

import requests

MOBILE_UA = (
    "Mozilla/5.0 (iPhone; CPU iPhone OS 17_5 like Mac OS X) "
    "AppleWebKit/605.1.15 (KHTML, like Gecko) Version/17.5 Mobile/15E148 Safari/604.1"
)

# Match http/https URLs while stopping before Chinese characters, whitespace, or closing brackets/quotes
URL_EXTRACT_RE = re.compile(
    r"https?://[A-Za-z0-9\-._~:/?#\[\]@!$&'()*+,;=%]+",
    re.IGNORECASE,
)

TRAILING_JUNK_CHARS = ".,;:!?)'\"】」》>）】！？，。；：、"

SHORT_LINK_DOMAINS = (
    "v.douyin.com",
    "b23.tv",
    "xhslink.com",
    "xhslink.cn",
    "v.kuaishou.com",
    "vt.tiktok.com",
    "t.cn",
    "dwz.cn",
)

CN_SHORT_DOMAINS = (
    "v.douyin.com",
    "b23.tv",
    "xhslink.com",
    "xhslink.cn",
    "v.kuaishou.com",
    "t.cn",
    "dwz.cn",
)

PLATFORM_RULES: list[tuple[str, str, tuple[str, ...]]] = [
    ("bilibili", "哔哩哔哩 Bilibili", ("bilibili.com", "b23.tv", "bilivideo.com", "acgvideo.com")),
    ("youtube", "YouTube", ("youtube.com", "youtu.be", "youtube-nocookie.com")),
    ("douyin", "抖音 Douyin", ("douyin.com", "iesdouyin.com", "v.douyin.com")),
    ("tiktok", "TikTok", ("tiktok.com", "vt.tiktok.com")),
    ("xiaohongshu", "小红书 RED", ("xiaohongshu.com", "xhslink.com", "xhslink.cn")),
    ("kuaishou", "快手 Kuaishou", ("kuaishou.com", "v.kuaishou.com", "gifshow.com", "chenzhongtech.com")),
    ("xigua", "西瓜视频 Xigua", ("ixigua.com",)),
    ("twitter", "X / Twitter", ("twitter.com", "x.com")),
    ("instagram", "Instagram", ("instagram.com",)),
    ("facebook", "Facebook", ("facebook.com", "fb.watch")),
    ("weibo", "微博 Weibo", ("weibo.com", "weibo.cn", "m.weibo.cn", "t.cn")),
    ("vimeo", "Vimeo", ("vimeo.com",)),
    ("twitch", "Twitch", ("twitch.tv",)),
    ("reddit", "Reddit", ("reddit.com", "redd.it")),
    ("pinterest", "Pinterest", ("pinterest.com", "pin.it")),
    ("dailymotion", "Dailymotion", ("dailymotion.com", "dai.ly")),
    ("youku", "优酷 Youku", ("youku.com",)),
    ("tencent", "腾讯视频", ("v.qq.com",)),
    ("iqiyi", "爱奇艺", ("iqiyi.com",)),
    ("acfun", "AcFun", ("acfun.cn",)),
    ("zhihu", "知乎视频", ("zhihu.com",)),
]

DIRECT_MEDIA_EXTS = (".mp4", ".m3u8", ".mkv", ".webm", ".mov", ".flv", ".mp3", ".m4a", ".wav")


@dataclass
class CleanedUrlResult:
    raw_input: str
    extracted_url: str
    normalized_url: str
    platform_id: str
    platform_name: str
    is_direct_media: bool = False


def extract_urls_from_text(text: str) -> list[str]:
    """Extract one or multiple clean URLs from dirty clipboard share text or batch input."""
    if not text or not text.strip():
        return []

    found: list[str] = []
    seen: set[str] = set()
    for match in URL_EXTRACT_RE.findall(text):
        cleaned = match.rstrip(TRAILING_JUNK_CHARS)
        if not cleaned or len(cleaned) < 10:
            continue
        if cleaned not in seen:
            seen.add(cleaned)
            found.append(cleaned)
    return found


def detect_platform(url: str) -> tuple[str, str, bool]:
    """Return `(platform_id, platform_name, is_direct_media)` for a URL."""
    parsed = urlparse(url)
    host = (parsed.netloc or "").lower()
    path_lower = (parsed.path or "").lower()

    if any(path_lower.endswith(ext) for ext in DIRECT_MEDIA_EXTS) or ".m3u8" in url.lower():
        return "direct", "直链媒体 / M3U8", True

    for pid, label, domains in PLATFORM_RULES:
        if any(host == d or host.endswith("." + d) for d in domains):
            return pid, label, False

    return "generic", host or "通用网页视频", False


def normalize_single_url(url: str, resolve_short_links: bool = False, timeout: float = 6.0) -> str:
    """Clean tracking parameters while preserving essential query args like Bilibili `?p=` or YouTube `?v=`."""
    url = url.strip().rstrip(TRAILING_JUNK_CHARS)
    parsed = urlparse(url)
    host = (parsed.netloc or "").lower()

    if resolve_short_links and any(host == d or host.endswith("." + d) for d in SHORT_LINK_DOMAINS):
        is_cn_short = any(host == d or host.endswith("." + d) for d in CN_SHORT_DOMAINS)
        req_kwargs: dict = {
            "headers": {"User-Agent": MOBILE_UA},
            "allow_redirects": True,
            "timeout": timeout,
            "stream": True,
        }
        if is_cn_short:
            # Domestic short links should bypass system VPN proxy for fast & reliable 302 expansion
            req_kwargs["proxies"] = {"http": None, "https": None}
        try:
            resp = requests.get(url, **req_kwargs)
            resolved = resp.url
            resp.close()
            if resolved and resolved.startswith("http"):
                url = resolved
                parsed = urlparse(url)
                host = (parsed.netloc or "").lower()
        except Exception:
            pass

    # Clean Bilibili tracking params while keeping multi-part `p` parameter
    if "bilibili.com" in host and "/video/" in parsed.path:
        qs = parse_qs(parsed.query)
        keep_query = {}
        if "p" in qs and qs["p"]:
            keep_query["p"] = qs["p"][0]
        clean_path = parsed.path if parsed.path.endswith("/") else parsed.path + "/"
        new_query = urlencode(keep_query)
        return urlunparse((parsed.scheme or "https", parsed.netloc, clean_path, "", new_query, ""))

    return url


def clean_share_input(raw_text: str, resolve_short_links: bool = False) -> list[CleanedUrlResult]:
    """Parse arbitrary user paste (single dirty share text or multi-line batch) into structured URLs."""
    urls = extract_urls_from_text(raw_text)
    results: list[CleanedUrlResult] = []
    seen_norm: set[str] = set()

    for extracted in urls:
        normalized = normalize_single_url(extracted, resolve_short_links=resolve_short_links)
        if normalized in seen_norm:
            continue
        seen_norm.add(normalized)
        pid, pname, is_direct = detect_platform(normalized)
        if pid == "generic":
            pid, pname, is_direct = detect_platform(extracted)
        results.append(
            CleanedUrlResult(
                raw_input=raw_text.strip(),
                extracted_url=extracted,
                normalized_url=normalized,
                platform_id=pid,
                platform_name=pname,
                is_direct_media=is_direct,
            )
        )
    return results
