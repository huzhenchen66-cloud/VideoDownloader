"""OS-native helpers: Win32 Shell folder reveal, system player, smart proxy routing, and media validation."""

from __future__ import annotations

import os
import shutil
import subprocess
import sys
import urllib.request
from pathlib import Path

from .paths import get_downloads_dir

# Overseas platforms that require proxy in Mainland China when `proxy_mode == "auto"`
OVERSEAS_PLATFORMS = {
    "youtube",
    "tiktok",
    "twitter",
    "instagram",
    "facebook",
    "vimeo",
    "twitch",
    "reddit",
    "pinterest",
    "dailymotion",
}

# Domestic platforms that should connect DIRECTLY (`proxy=""`) when `proxy_mode == "auto"`
DOMESTIC_CN_PLATFORMS = {
    "bilibili",
    "douyin",
    "xiaohongshu",
    "kuaishou",
    "weibo",
    "xigua",
    "youku",
    "tencent",
    "iqiyi",
    "acfun",
    "zhihu",
}

# Signatures that indicate an HTML webpage or JSON error payload was erroneously saved as a media file
INVALID_MEDIA_PREFIXES = (
    b"<!doctype html",
    b"<html",
    b"<head",
    b"<body",
    b"<script",
    b"<meta ",
    b'{"error"',
    b'{"code":',
    b"accessdenied",
)


def detect_system_proxy() -> str:
    """Detect active HTTP/HTTPS/SOCKS proxy from OS settings or environment variables."""
    try:
        proxies = urllib.request.getproxies()
    except Exception:
        proxies = {}

    for key in ("https", "http", "all", "socks", "socks5"):
        val = (proxies.get(key) or "").strip()
        if val:
            if "://" not in val:
                val = f"http://{val}"
            return val
    return ""


def resolve_proxy_for_platform(platform_id: str, proxy_mode: str = "auto", custom_proxy: str = "") -> str | None:
    """Resolve the exact proxy setting for a given platform.

    Returns:
      - `str` (non-empty): explicit proxy URL to use (e.g. `http://127.0.0.1:15715`)
      - `""` (empty string): force DIRECT connection (bypassing OS system proxy!)
      - `None`: let underlying library use default environment behavior
    """
    mode = (proxy_mode or "auto").strip().lower()
    custom = (custom_proxy or "").strip()
    if custom and "://" not in custom:
        custom = f"http://{custom}"

    if mode == "direct":
        return ""
    if mode == "custom":
        return custom or detect_system_proxy() or None
    if mode == "system":
        return detect_system_proxy() or custom or None

    # Default `auto` mode (Smart Split Routing inspired by qingsong-downloader):
    # - Domestic CN sites connect directly so overseas VPN nodes never slow down or geo-block Bilibili/Douyin
    # - Overseas sites (YouTube/TikTok/X/Instagram) automatically use custom proxy or detected OS proxy
    if platform_id in DOMESTIC_CN_PLATFORMS:
        return ""
    if platform_id in OVERSEAS_PLATFORMS:
        return custom or detect_system_proxy() or None
    return None


def resolve_task_media_path(stored_path: str, current_save_dir: str = "") -> str:
    """Resolve a task's media file path resiliently across app restarts and directory moves."""
    if not stored_path:
        return ""

    norm = os.path.normpath(stored_path.strip().strip('"'))
    if os.path.isfile(norm):
        return os.path.abspath(norm)

    basename = os.path.basename(norm)
    if not basename:
        return ""

    search_dirs: list[str] = []
    if current_save_dir:
        search_dirs.append(current_save_dir)
    default_dl = str(get_downloads_dir())
    if default_dl not in search_dirs:
        search_dirs.append(default_dl)

    for folder in search_dirs:
        candidate = os.path.normpath(os.path.join(folder, basename))
        if os.path.isfile(candidate):
            return os.path.abspath(candidate)

    return ""


def validate_media_file(file_path: str, min_bytes: int = 4096) -> tuple[bool, str]:
    """Verify downloaded file is a real binary media file and NOT an HTML error/embed page."""
    if not file_path or not os.path.isfile(file_path):
        return False, "未找到输出媒体文件"

    try:
        size = os.path.getsize(file_path)
    except OSError as exc:
        return False, f"无法读取媒体文件大小: {exc}"

    if size < min_bytes:
        return False, f"下载文件体积异常过小 ({size} B)，疑似被风控拦截或空流"

    try:
        with open(file_path, "rb") as fp:
            header = fp.read(512).lstrip().lower()
    except OSError as exc:
        return False, f"无法校验媒体文件头: {exc}"

    for bad_prefix in INVALID_MEDIA_PREFIXES:
        if header.startswith(bad_prefix):
            return False, "下载结果实为网页 HTML 而非视频流（已拦截伪造文件）"

    if b"<html" in header[:256] or b"<!doctype html" in header[:256]:
        return False, "下载结果包含 HTML 页面内容而非有效音视频流"

    return True, ""


def reveal_in_file_manager(target_path: str, select_file: bool = True) -> str:
    """Open system file manager and highlight the exact target file (100% reliable on Windows).

    Uses native Win32 Shell API `SHOpenFolderAndSelectItems` on Windows so paths containing
    Chinese characters, spaces, commas, brackets `[BV...]`, or emojis `👀` never fail due to
    `explorer.exe /select,` command-line quoting bugs.
    """
    if not target_path:
        raise FileNotFoundError("路径为空")

    norm = os.path.abspath(os.path.normpath(target_path))
    exists = os.path.exists(norm)
    is_file = os.path.isfile(norm)

    if not exists:
        parent = os.path.dirname(norm)
        if parent and os.path.isdir(parent):
            norm = parent
            is_file = False
            select_file = False
        else:
            raise FileNotFoundError(f"目标路径不存在: {target_path}")

    if sys.platform.startswith("win"):
        if select_file and is_file:
            if _win32_select_file_in_explorer(norm):
                return norm
            # Fallback if COM Shell call ever returns non-zero
            subprocess.Popen(f'explorer.exe /select,"{norm}"', shell=True)
            return norm
        folder = os.path.dirname(norm) if is_file else norm
        os.startfile(folder)  # type: ignore[attr-defined]
        return folder

    if sys.platform == "darwin":
        if select_file and is_file:
            subprocess.Popen(["open", "-R", norm])
        else:
            subprocess.Popen(["open", os.path.dirname(norm) if is_file else norm])
        return norm

    folder = os.path.dirname(norm) if is_file else norm
    subprocess.Popen(["xdg-open", folder])
    return folder


def _win32_select_file_in_explorer(file_path: str) -> bool:
    """Invoke `shell32.SHOpenFolderAndSelectItems` with 64-bit safe ctypes pointer signatures."""
    try:
        import ctypes
        from ctypes import wintypes

        ole32 = ctypes.windll.ole32
        shell32 = ctypes.windll.shell32

        ole32.CoInitialize.argtypes = [ctypes.c_void_p]
        ole32.CoInitialize.restype = ctypes.c_long
        ole32.CoInitialize(None)

        shell32.ILCreateFromPathW.argtypes = [wintypes.LPCWSTR]
        shell32.ILCreateFromPathW.restype = ctypes.c_void_p

        shell32.SHOpenFolderAndSelectItems.argtypes = [
            ctypes.c_void_p,
            wintypes.UINT,
            ctypes.c_void_p,
            wintypes.DWORD,
        ]
        shell32.SHOpenFolderAndSelectItems.restype = ctypes.c_long

        shell32.ILFree.argtypes = [ctypes.c_void_p]
        shell32.ILFree.restype = None

        pidl = shell32.ILCreateFromPathW(file_path)
        if not pidl:
            return False
        try:
            hr = shell32.SHOpenFolderAndSelectItems(pidl, 0, None, 0)
            return hr == 0
        finally:
            shell32.ILFree(pidl)
    except Exception:
        return False


def open_with_system_player(file_path: str) -> None:
    """Launch the OS default media player (Windows Media Player / PotPlayer / VLC / QuickTime)."""
    norm = os.path.abspath(os.path.normpath(file_path))
    if not os.path.isfile(norm):
        raise FileNotFoundError(f"媒体文件不存在: {norm}")

    if sys.platform.startswith("win"):
        os.startfile(norm)  # type: ignore[attr-defined]
    elif sys.platform == "darwin":
        subprocess.Popen(["open", norm])
    else:
        subprocess.Popen(["xdg-open", norm])


def find_local_chromium_browser() -> str:
    """Locate local Chrome / Edge / Chromium binary for headless CDP anti-captcha stream sniffing."""
    env_override = os.environ.get("VD_BROWSER_PATH", "").strip()
    if env_override and os.path.isfile(env_override):
        return env_override

    candidates: list[str] = []
    if sys.platform.startswith("win"):
        local_app = os.environ.get("LOCALAPPDATA", "")
        candidates.extend(
            [
                r"C:\Program Files\Google\Chrome\Application\chrome.exe",
                r"C:\Program Files (x86)\Google\Chrome\Application\chrome.exe",
                os.path.join(local_app, r"Google\Chrome\Application\chrome.exe") if local_app else "",
                r"C:\Program Files (x86)\Microsoft\Edge\Application\msedge.exe",
                r"C:\Program Files\Microsoft\Edge\Application\msedge.exe",
                os.path.join(local_app, r"Microsoft\Edge\Application\msedge.exe") if local_app else "",
            ]
        )
    elif sys.platform == "darwin":
        candidates.extend(
            [
                "/Applications/Google Chrome.app/Contents/MacOS/Google Chrome",
                "/Applications/Microsoft Edge.app/Contents/MacOS/Microsoft Edge",
                "/Applications/Chromium.app/Contents/MacOS/Chromium",
                "/Applications/Brave Browser.app/Contents/MacOS/Brave Browser",
            ]
        )
    else:
        for name in ("google-chrome", "chromium", "chromium-browser", "microsoft-edge"):
            found = shutil.which(name)
            if found:
                candidates.append(found)

    for path in candidates:
        if path and os.path.isfile(path):
            return path
    return ""
