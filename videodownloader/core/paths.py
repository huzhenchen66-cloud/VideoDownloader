"""Cross-platform runtime, user-data, and bundled asset path resolution (supports Installed & Portable modes)."""

from __future__ import annotations

import os
import sys
from pathlib import Path


def is_frozen() -> bool:
    """Return True when running inside a PyInstaller bundle (`onedir` or `onefile`)."""
    return bool(getattr(sys, "frozen", False))


def get_package_root() -> Path:
    """Return the package directory containing static assets (`assets/`, `web_static/`)."""
    if is_frozen():
        if hasattr(sys, "_MEIPASS"):
            meipass = Path(getattr(sys, "_MEIPASS"))
            bundled = meipass / "videodownloader"
            if bundled.exists():
                return bundled
            return meipass
        exe_dir = Path(sys.executable).resolve().parent
        for candidate in (exe_dir / "_internal" / "videodownloader", exe_dir / "videodownloader", exe_dir):
            if (candidate / "assets").exists() or (candidate / "web_static").exists():
                return candidate
    return Path(__file__).resolve().parent.parent


def get_assets_dir() -> Path:
    """Return path to bundled vector icons and brand `.ico`/`.png` files."""
    return get_package_root() / "assets"


def get_web_static_dir() -> Path:
    """Return path to bundled Web/PWA frontend assets."""
    return get_package_root() / "web_static"


def is_installed_mode() -> bool:
    """Return True when running from an installed directory (e.g. Inno Setup `{localappdata}\\Programs\\VideoDownloader`)."""
    if os.environ.get("VIDEODOWNLOADER_HOME"):
        return False
    if not is_frozen():
        return False
    exe_dir = Path(sys.executable).resolve().parent
    if (exe_dir / "portable.flag").exists():
        return False
    # If installed by Inno Setup (`unins000.exe` present) or placed under `Programs` / `Program Files` / `.app`
    lower_str = str(exe_dir).lower()
    if (
        any(exe_dir.glob("unins*.exe"))
        or "\\programs\\" in lower_str
        or "program files" in lower_str
        or ".app/contents/" in lower_str.replace("\\", "/")
    ):
        return True
    return False


def _get_os_appdata_dir() -> Path:
    if sys.platform.startswith("win"):
        base = os.environ.get("LOCALAPPDATA") or os.environ.get("APPDATA") or str(Path.home() / "AppData" / "Local")
        return Path(base) / "VideoDownloader"
    if sys.platform == "darwin":
        return Path.home() / "Library" / "Application Support" / "VideoDownloader"
    xdg = os.environ.get("XDG_DATA_HOME") or str(Path.home() / ".local" / "share")
    return Path(xdg) / "videodownloader"


def get_runtime_root() -> Path:
    """Return deterministic mutable runtime directory for SQLite DB, logs, cookies, and hot-updated binaries."""
    env_override = os.environ.get("VIDEODOWNLOADER_HOME")
    if env_override:
        root = Path(env_override).expanduser().resolve()
        root.mkdir(parents=True, exist_ok=True)
        return root

    if is_installed_mode():
        root = _get_os_appdata_dir()
    elif is_frozen():
        root = Path(sys.executable).resolve().parent
    else:
        root = Path(__file__).resolve().parent.parent.parent

    root.mkdir(parents=True, exist_ok=True)
    return root


def get_data_dir() -> Path:
    path = get_runtime_root() / "data"
    path.mkdir(parents=True, exist_ok=True)
    return path


def get_logs_dir() -> Path:
    path = get_runtime_root() / "logs"
    path.mkdir(parents=True, exist_ok=True)
    return path


def get_downloads_dir() -> Path:
    """Return default video download folder.

    In installed desktop mode, defaults to `~/Downloads/VideoDownloader` so user videos sit right in their
    system Downloads folder and are untouched across software upgrades or uninstallation.
    """
    if os.environ.get("VIDEODOWNLOADER_HOME"):
        path = get_runtime_root() / "downloads"
        path.mkdir(parents=True, exist_ok=True)
        return path

    if is_installed_mode():
        user_dl = Path.home() / "Downloads" / "VideoDownloader"
        try:
            user_dl.mkdir(parents=True, exist_ok=True)
            return user_dl
        except OSError:
            pass

    path = get_runtime_root() / "downloads"
    path.mkdir(parents=True, exist_ok=True)
    return path


def get_bin_dir() -> Path:
    path = get_runtime_root() / "bin"
    path.mkdir(parents=True, exist_ok=True)
    return path


def get_db_path() -> Path:
    return get_data_dir() / "videodownloader.db"
