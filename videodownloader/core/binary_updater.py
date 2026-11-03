"""Cross-platform FFmpeg / yt-dlp / Browser CDP resolver and multi-threaded HTTP Range hot-updater."""

from __future__ import annotations

import os
import shutil
import stat
import subprocess
import sys
import tempfile
import threading
import urllib.request
from typing import Any, Callable

from .os_utils import detect_system_proxy, find_local_chromium_browser
from .paths import get_bin_dir, get_package_root

try:
    import imageio_ffmpeg
except Exception:
    imageio_ffmpeg = None

try:
    import yt_dlp
except Exception:
    yt_dlp = None

try:
    import curl_cffi  # noqa: F401
    HAS_CURL_CFFI = True
except Exception:
    HAS_CURL_CFFI = False

try:
    import yt_dlp_ejs  # noqa: F401
    HAS_YTDLP_EJS = True
except Exception:
    HAS_YTDLP_EJS = False

BROWSER_UA = (
    "Mozilla/5.0 (Windows NT 10.0; Win64; x64) "
    "AppleWebKit/537.36 (KHTML, like Gecko) Chrome/131.0.0.0 Safari/537.36"
)

YTDLP_RELEASE_URLS = {
    "win": "https://github.com/yt-dlp/yt-dlp/releases/latest/download/yt-dlp.exe",
    "mac": "https://github.com/yt-dlp/yt-dlp/releases/latest/download/yt-dlp_macos",
    "linux": "https://github.com/yt-dlp/yt-dlp/releases/latest/download/yt-dlp_linux",
}


def _platform_key() -> str:
    if sys.platform.startswith("win"):
        return "win"
    if sys.platform == "darwin":
        return "mac"
    return "linux"


def resolve_ffmpeg_path() -> str:
    """Locate an executable `ffmpeg` binary across local `bin/`, `imageio_ffmpeg`, or system PATH."""
    bin_dir = get_bin_dir()
    exe_name = "ffmpeg.exe" if sys.platform.startswith("win") else "ffmpeg"

    candidates = [
        bin_dir / exe_name,
        bin_dir / "bin" / exe_name,
        get_package_root() / exe_name,
    ]
    for candidate in candidates:
        if candidate.exists() and candidate.is_file():
            return str(candidate)

    if imageio_ffmpeg is not None:
        try:
            bundled_ffmpeg = imageio_ffmpeg.get_ffmpeg_exe()
            if bundled_ffmpeg and os.path.exists(bundled_ffmpeg):
                return bundled_ffmpeg
        except Exception:
            pass

    sys_ffmpeg = shutil.which("ffmpeg")
    return sys_ffmpeg or ""


def resolve_external_ytdlp_path() -> str:
    """Return hot-updated standalone `yt-dlp` binary in `bin/` if present, else empty string."""
    bin_dir = get_bin_dir()
    exe_name = "yt-dlp.exe" if sys.platform.startswith("win") else "yt-dlp"
    local_bin = bin_dir / exe_name
    if local_bin.exists() and local_bin.is_file():
        return str(local_bin)
    return ""


def get_toolchain_status() -> dict[str, Any]:
    """Inspect versions and availability of `yt-dlp`, `ffmpeg`, `Chrome/Edge CDP`, `curl_cffi`, and `proxy`."""
    creationflags = (
        subprocess.CREATE_NO_WINDOW
        if sys.platform.startswith("win") and hasattr(subprocess, "CREATE_NO_WINDOW")
        else 0
    )

    ffmpeg_path = resolve_ffmpeg_path()
    ffmpeg_version = "未检测到"
    if ffmpeg_path:
        try:
            out = subprocess.check_output(
                [ffmpeg_path, "-version"],
                stderr=subprocess.STDOUT,
                text=True,
                encoding="utf-8",
                errors="ignore",
                timeout=6,
                creationflags=creationflags,
            )
            first_line = out.strip().splitlines()[0] if out.strip() else ""
            if first_line.startswith("ffmpeg version"):
                ffmpeg_version = first_line.replace("ffmpeg version ", "").split()[0]
            elif first_line:
                ffmpeg_version = first_line[:40]
        except Exception:
            ffmpeg_version = "可用"

    py_ytdlp_ver = getattr(getattr(yt_dlp, "version", None), "__version__", None) if yt_dlp else None
    ext_ytdlp = resolve_external_ytdlp_path()
    ext_ytdlp_ver = None
    if ext_ytdlp:
        try:
            out = subprocess.check_output(
                [ext_ytdlp, "--version"],
                stderr=subprocess.STDOUT,
                text=True,
                encoding="utf-8",
                errors="ignore",
                timeout=6,
                creationflags=creationflags,
            )
            ext_ytdlp_ver = out.strip().splitlines()[0] if out.strip() else None
        except Exception:
            pass

    browser_path = find_local_chromium_browser()
    sys_proxy = detect_system_proxy()

    try:
        import videodl  # type: ignore
        videodl_ver = getattr(videodl, "__version__", "installed")
    except Exception:
        videodl_ver = None

    active_ytdlp_ver = ext_ytdlp_ver or py_ytdlp_ver or "未安装"

    return {
        "platform": sys.platform,
        "ytdlp_active_version": active_ytdlp_ver,
        "ytdlp_python_version": py_ytdlp_ver or "未安装",
        "ytdlp_binary_path": ext_ytdlp or None,
        "ytdlp_binary_version": ext_ytdlp_ver,
        "ytdlp_ejs_ready": HAS_YTDLP_EJS,
        "curl_cffi_ready": HAS_CURL_CFFI,
        "ytdlp_ready": bool(py_ytdlp_ver or ext_ytdlp),
        "ffmpeg_path": ffmpeg_path or None,
        "ffmpeg_version": ffmpeg_version,
        "ffmpeg_ready": bool(ffmpeg_path),
        "browser_cdp_path": browser_path or None,
        "browser_cdp_ready": bool(browser_path),
        "system_proxy": sys_proxy or None,
        "videofetch_plugin": videodl_ver,
    }


def download_file_parallel(
    url: str,
    target_path: str,
    threads: int = 8,
    timeout: int = 120,
    on_progress: Callable[[float], None] | None = None,
) -> str:
    """Multi-threaded HTTP Range downloader inherited from the original 0.25 desktop tool."""
    os.makedirs(os.path.dirname(target_path), exist_ok=True)
    tmp_target = target_path + ".download"

    total_size = 0
    accept_ranges = False
    try:
        head_req = urllib.request.Request(url, headers={"User-Agent": BROWSER_UA}, method="HEAD")
        with urllib.request.urlopen(head_req, timeout=20) as resp:
            total_size = int(resp.headers.get("Content-Length") or 0)
            accept_ranges = "bytes" in (resp.headers.get("Accept-Ranges") or "").lower()
    except Exception:
        pass

    if total_size > 4 * 1024 * 1024 and accept_ranges:
        part_size = (total_size + threads - 1) // threads
        done_bytes = [0]
        lock = threading.Lock()
        errors: list[str] = []

        def _worker(idx: int, start: int, end: int) -> None:
            part_file = f"{tmp_target}.part{idx}"
            headers = {
                "User-Agent": BROWSER_UA,
                "Range": f"bytes={start}-{end}",
            }
            req = urllib.request.Request(url, headers=headers)
            try:
                with urllib.request.urlopen(req, timeout=timeout) as resp, open(part_file, "wb") as fp:
                    if resp.status not in (200, 206):
                        raise RuntimeError(f"HTTP {resp.status}")
                    while True:
                        chunk = resp.read(512 * 1024)
                        if not chunk:
                            break
                        fp.write(chunk)
                        with lock:
                            done_bytes[0] += len(chunk)
                            if on_progress:
                                on_progress(done_bytes[0] * 100.0 / total_size)
            except Exception as exc:
                errors.append(str(exc))

        workers: list[threading.Thread] = []
        for i in range(threads):
            s = i * part_size
            e = min(total_size - 1, s + part_size - 1)
            if s > e:
                continue
            t = threading.Thread(target=_worker, args=(i, s, e), daemon=True)
            workers.append(t)
            t.start()

        for t in workers:
            t.join()

        if not errors:
            with open(tmp_target, "wb") as out_fp:
                for i in range(threads):
                    pf = f"{tmp_target}.part{i}"
                    if os.path.exists(pf):
                        with open(pf, "rb") as in_fp:
                            shutil.copyfileobj(in_fp, out_fp, length=1024 * 1024)
                        try:
                            os.remove(pf)
                        except OSError:
                            pass
            if os.path.exists(tmp_target) and os.path.getsize(tmp_target) >= total_size:
                os.replace(tmp_target, target_path)
                return target_path
        else:
            for i in range(threads):
                pf = f"{tmp_target}.part{i}"
                if os.path.exists(pf):
                    try:
                        os.remove(pf)
                    except OSError:
                        pass

    # Fallback: single-stream download
    req = urllib.request.Request(url, headers={"User-Agent": BROWSER_UA})
    with urllib.request.urlopen(req, timeout=timeout) as resp, open(tmp_target, "wb") as fp:
        total = int(resp.headers.get("Content-Length") or 0)
        done = 0
        while True:
            chunk = resp.read(512 * 1024)
            if not chunk:
                break
            fp.write(chunk)
            done += len(chunk)
            if total and on_progress:
                on_progress(done * 100.0 / total)

    os.replace(tmp_target, target_path)
    return target_path


def hot_update_ytdlp_binary(on_progress: Callable[[float], None] | None = None) -> dict[str, Any]:
    """Download or update standalone `yt-dlp` binary into `bin/` so frozen `.exe` stays up to date forever."""
    pkey = _platform_key()
    url = YTDLP_RELEASE_URLS[pkey]
    exe_name = "yt-dlp.exe" if pkey == "win" else "yt-dlp"
    final_path = get_bin_dir() / exe_name

    # If external binary already exists, try `yt-dlp -U` first
    if final_path.exists():
        try:
            creationflags = (
                subprocess.CREATE_NO_WINDOW
                if sys.platform.startswith("win") and hasattr(subprocess, "CREATE_NO_WINDOW")
                else 0
            )
            proc = subprocess.run(
                [str(final_path), "-U"],
                capture_output=True,
                text=True,
                encoding="utf-8",
                errors="ignore",
                timeout=60,
                creationflags=creationflags,
            )
            if proc.returncode == 0:
                return get_toolchain_status()
        except Exception:
            pass

    with tempfile.TemporaryDirectory(prefix="vd_ytdlp_") as tmp_dir:
        tmp_bin = os.path.join(tmp_dir, exe_name)
        download_file_parallel(url, tmp_bin, threads=8, timeout=120, on_progress=on_progress)
        if pkey != "win":
            st = os.stat(tmp_bin)
            os.chmod(tmp_bin, st.st_mode | stat.S_IEXEC)
        shutil.copy2(tmp_bin, str(final_path))
        if pkey != "win":
            st = os.stat(final_path)
            os.chmod(final_path, st.st_mode | stat.S_IEXEC)

    return get_toolchain_status()
