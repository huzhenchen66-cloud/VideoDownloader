"""Create comprehensive zero-loss project archive for Shadow Defender persistence & handover."""

from __future__ import annotations

import hashlib
import os
from pathlib import Path
import shutil
import sys
import time
import zipfile

ROOT = Path(__file__).resolve().parent.parent

EXCLUDE_DIRS = {
    ".git",
    ".pytest_cache",
    "build",
    "__pycache__",
    ".idea",
    ".vscode",
}

EXCLUDE_FILE_EXTENSIONS = {
    ".pyc",
    ".pyo",
    ".tmp",
}


def should_exclude(path: Path) -> bool:
    for part in path.parts:
        if part in EXCLUDE_DIRS:
            return True
    if path.suffix.lower() in EXCLUDE_FILE_EXTENSIONS:
        return True
    if path.name.startswith("VideoDownloader_Complete_Project_Backup"):
        return True
    return False


def create_archive() -> None:
    print(f"[Archive] Root directory: {ROOT}")
    t0 = time.time()

    # Destination paths (Prioritize D:\视频下载器 as requested by user)
    destinations = [
        Path(r"D:\视频下载器\VideoDownloader_Complete_Project_Backup.zip"),
        Path(r"D:\google\VideoDownloader_Complete_Project_Backup.zip"),
        Path(r"D:\VideoDownloader_Complete_Project_Backup.zip"),
        ROOT / "VideoDownloader_Complete_Project_Backup.zip",
    ]

    primary_zip = destinations[0]
    primary_zip.parent.mkdir(parents=True, exist_ok=True)

    print(f"[Archive] Compressing to primary target: {primary_zip} ...")
    total_files = 0
    total_uncompressed_bytes = 0

    with zipfile.ZipFile(primary_zip, "w", compression=zipfile.ZIP_DEFLATED, compresslevel=6) as zf:
        for root, dirs, files in os.walk(ROOT):
            dirs[:] = [d for d in dirs if d not in EXCLUDE_DIRS]
            for f in files:
                fpath = Path(root) / f
                if should_exclude(fpath):
                    continue
                rel_path = fpath.relative_to(ROOT)
                zf.write(fpath, arcname=str(rel_path))
                total_files += 1
                try:
                    total_uncompressed_bytes += fpath.stat().st_size
                except Exception:
                    pass

    zip_size = primary_zip.stat().st_size
    elapsed = time.time() - t0

    h = hashlib.sha256()
    with open(primary_zip, "rb") as fp:
        for chunk in iter(lambda: fp.read(1024 * 1024), b""):
            h.update(chunk)
    sha256 = h.hexdigest().lower()

    print(f"[Archive] Primary ZIP created successfully in {elapsed:.1f}s!")
    print(f"[Archive] Total files: {total_files}")
    print(f"[Archive] Uncompressed size: {total_uncompressed_bytes / (1024*1024):.1f} MB")
    print(f"[Archive] ZIP Archive size: {zip_size / (1024*1024):.1f} MB")
    print(f"[Archive] SHA256: {sha256}")

    # Mirror to other destinations for redundancy
    for target in destinations[1:]:
        try:
            target.parent.mkdir(parents=True, exist_ok=True)
            shutil.copy2(primary_zip, target)
            print(f"[Archive] Mirrored backup to: {target}")
        except Exception as exc:
            print(f"[Archive] Warning mirroring to {target}: {exc}")


if __name__ == "__main__":
    create_archive()
