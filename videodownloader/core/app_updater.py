"""Application Self-Updater (`core/app_updater.py`):
- Strict SemVer tuple comparison (`parse_semver`)
- GitHub Releases `/releases/latest` inspection (silent failure on offline/rate-limit)
- Mandatory `SHA256SUMS.txt` cryptographic verification before executing `Setup.exe`
- Detached `/VERYSILENT` Inno Setup upgrade handoff.
"""

from __future__ import annotations

from dataclasses import dataclass
import hashlib
import hmac
from pathlib import Path
import re
import subprocess
import sys
import tempfile
from typing import Callable

import requests

from .. import __version__
from .logging_setup import get_logger

logger = get_logger("updater")

SEMVER_RE = re.compile(r"v?(\d+)\.(\d+)\.(\d+)(?:[-+._]([0-9A-Za-z.-]+))?")


def parse_semver(version_str: str) -> tuple[int, int, int, int]:
    """Parse `'v3.10.2'` into `(3, 10, 2, 1)` (pre-release gets `0`, stable release gets `1`)."""
    m = SEMVER_RE.search((version_str or "").strip())
    if not m:
        return (0, 0, 0, 0)
    major, minor, patch = int(m.group(1)), int(m.group(2)), int(m.group(3))
    is_stable = 0 if m.group(4) else 1
    return (major, minor, patch, is_stable)


def is_newer_version(candidate: str, current: str = __version__) -> bool:
    return parse_semver(candidate) > parse_semver(current)


def parse_sha256sums(sums_text: str, target_filename: str) -> str:
    """Extract 64-char hex SHA-256 for `target_filename` from `SHA256SUMS.txt` content."""
    target_lower = (target_filename or "").strip().lower()
    if not target_lower:
        return ""
    for line in (sums_text or "").splitlines():
        clean = line.strip()
        if not clean or clean.startswith("#"):
            continue
        parts = clean.split(None, 1)
        if len(parts) != 2:
            continue
        digest, fname = parts[0].strip().lower(), parts[1].strip().lstrip("*").strip()
        if len(digest) == 64 and all(c in "0123456789abcdef" for c in digest):
            if Path(fname).name.lower() == target_lower:
                return digest
    return ""


def compute_file_sha256(file_path: Path) -> str:
    h = hashlib.sha256()
    with open(file_path, "rb") as fp:
        while True:
            chunk = fp.read(512 * 1024)
            if not chunk:
                break
            h.update(chunk)
    return h.hexdigest().lower()


def verify_file_sha256(file_path: Path, expected_sha256: str) -> bool:
    expected = (expected_sha256 or "").strip().lower()
    if len(expected) != 64:
        return False
    actual = compute_file_sha256(file_path)
    return hmac.compare_digest(actual, expected)


@dataclass
class UpdateCheckResult:
    checked: bool
    has_update: bool
    current_version: str
    latest_version: str
    release_url: str = ""
    installer_url: str = ""
    installer_name: str = ""
    expected_sha256: str = ""
    changelog: str = ""
    message: str = ""


def check_github_release(
    github_repo: str,
    current_version: str = __version__,
    timeout: float = 8.0,
) -> UpdateCheckResult:
    """Query GitHub Releases API for a newer version. Never raises on network error during background check."""
    repo = (github_repo or "").strip().strip("/")
    if not repo or "/" not in repo:
        return UpdateCheckResult(
            checked=False,
            has_update=False,
            current_version=current_version,
            latest_version=current_version,
            message="当前为本地构建版本（在设置中配置 GitHub 仓库 owner/repo 后即可在线检测新版本）",
        )

    api_url = f"https://api.github.com/repos/{repo}/releases/latest"
    headers = {
        "Accept": "application/vnd.github+json",
        "User-Agent": f"VideoDownloader/{current_version}",
    }
    try:
        resp = requests.get(api_url, headers=headers, timeout=timeout)
        if resp.status_code == 404:
            return UpdateCheckResult(
                checked=True,
                has_update=False,
                current_version=current_version,
                latest_version=current_version,
                message="目标仓库尚未发布 Release 版本",
            )
        resp.raise_for_status()
        data = resp.json()
    except Exception as exc:
        logger.debug("Silent update check skipped due to network error: %s", exc)
        return UpdateCheckResult(
            checked=False,
            has_update=False,
            current_version=current_version,
            latest_version=current_version,
            message=f"暂无法连接更新服务器 ({exc})",
        )

    tag = str(data.get("tag_name") or "").strip()
    latest_clean = tag.lstrip("vV") or current_version
    if not is_newer_version(tag, current_version):
        return UpdateCheckResult(
            checked=True,
            has_update=False,
            current_version=current_version,
            latest_version=latest_clean,
            release_url=str(data.get("html_url") or ""),
            message=f"当前已是最新版本 ({current_version})",
        )

    assets = data.get("assets") or []
    installer_url = ""
    installer_name = ""
    sums_url = ""

    for asset in assets:
        name = str(asset.get("name") or "")
        dl_url = str(asset.get("browser_download_url") or "")
        lower_name = name.lower()
        if lower_name == "sha256sums.txt":
            sums_url = dl_url
        elif sys.platform.startswith("win") and lower_name.endswith(".exe") and "setup" in lower_name:
            installer_url = dl_url
            installer_name = name
        elif sys.platform == "darwin" and lower_name.endswith(".dmg"):
            installer_url = dl_url
            installer_name = name

    expected_hash = ""
    if sums_url and installer_name:
        try:
            s_resp = requests.get(sums_url, headers=headers, timeout=timeout)
            if s_resp.ok:
                expected_hash = parse_sha256sums(s_resp.text, installer_name)
        except Exception:
            pass

    return UpdateCheckResult(
        checked=True,
        has_update=True,
        current_version=current_version,
        latest_version=latest_clean,
        release_url=str(data.get("html_url") or ""),
        installer_url=installer_url,
        installer_name=installer_name,
        expected_sha256=expected_hash,
        changelog=str(data.get("body") or "")[:1200],
        message=f"发现新版本 v{latest_clean}",
    )


def download_verified_installer(
    installer_url: str,
    installer_name: str,
    expected_sha256: str,
    dest_dir: Path | None = None,
    on_progress: Callable[[float], None] | None = None,
) -> Path:
    """Download `Setup.exe` and enforce SHA-256 verification before allowing execution."""
    if not expected_sha256 or len(expected_sha256.strip()) != 64:
        raise ValueError("缺少官方 SHA256SUMS 签名摘要，出于安全保护已阻止自动执行安装包")

    target_dir = dest_dir or Path(tempfile.mkdtemp(prefix="vd_update_"))
    target_dir.mkdir(parents=True, exist_ok=True)
    safe_name = Path(installer_name or "VideoDownloader-Setup.exe").name
    out_file = target_dir / safe_name

    with requests.get(installer_url, stream=True, timeout=30) as resp:
        resp.raise_for_status()
        total = int(resp.headers.get("Content-Length") or 0)
        downloaded = 0
        with open(out_file, "wb") as fp:
            for chunk in resp.iter_content(chunk_size=256 * 1024):
                if not chunk:
                    continue
                fp.write(chunk)
                downloaded += len(chunk)
                if on_progress and total > 0:
                    on_progress(min(99.0, downloaded * 100.0 / total))

    if not verify_file_sha256(out_file, expected_sha256):
        try:
            out_file.unlink(missing_ok=True)
        except OSError:
            pass
        raise ValueError("安装包 SHA256 哈希校验不匹配，文件可能已损坏或被篡改，已自动销毁")

    if on_progress:
        on_progress(100.0)
    logger.info("Downloaded & SHA256-verified update installer: %s", out_file)
    return out_file


def launch_silent_windows_installer(installer_path: Path) -> None:
    """Spawn Inno Setup installer with `/VERYSILENT` right before exiting the running app."""
    if not sys.platform.startswith("win"):
        raise RuntimeError("静默安装器仅适用于 Windows Setup.exe")
    cmd = [
        str(installer_path),
        "/VERYSILENT",
        "/SUPPRESSMSGBOXES",
        "/NORESTART",
        "/CLOSEAPPLICATIONS",
    ]
    subprocess.Popen(cmd, close_fds=True)
