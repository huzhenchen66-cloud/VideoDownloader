"""Custom `.vdtheme` Package Importer & Exporter (`core/theme_package.py`).

Format specification:
A `.vdtheme` file is a ZIP archive containing:
  - `theme.json` (Required metadata: id, name, accent, gradient, etc.)
  - `backdrop.png` (Optional custom wallpaper)
  - `complete.wav` (Optional custom success sound effect)
"""

from __future__ import annotations

import json
from pathlib import Path
import shutil
from typing import Any
import zipfile

from .paths import get_runtime_root


def get_custom_themes_dir() -> Path:
    d = get_runtime_root() / "custom_themes"
    d.mkdir(parents=True, exist_ok=True)
    return d


def import_vdtheme_package(vdtheme_path: Path) -> dict[str, Any]:
    """Validate and extract `.vdtheme` package into runtime custom_themes folder."""
    if not vdtheme_path.is_file():
        raise FileNotFoundError(f"Theme file not found: {vdtheme_path}")

    with zipfile.ZipFile(vdtheme_path, "r") as zf:
        names = zf.namelist()
        if "theme.json" not in names:
            raise ValueError("Invalid .vdtheme: missing 'theme.json'")

        meta_bytes = zf.read("theme.json")
        meta = json.loads(meta_bytes.decode("utf-8"))

        tid = str(meta.get("id") or "").strip().lower()
        if not tid or not tid.isalnum():
            raise ValueError(f"Invalid theme id '{tid}': must be alphanumeric")

        dest_dir = get_custom_themes_dir() / tid
        dest_dir.mkdir(parents=True, exist_ok=True)

        zf.extractall(dest_dir)

    return meta


def export_vdtheme_package(
    theme_id: str,
    name: str,
    accent: str,
    gradient: tuple[str, str],
    out_vdtheme_path: Path,
    custom_bg_path: Path | None = None,
    custom_sound_path: Path | None = None,
) -> Path:
    """Export an anime theme as a shareable `.vdtheme` archive."""
    meta = {
        "id": theme_id,
        "name": name,
        "accent": accent,
        "gradient": list(gradient),
        "version": "1.0.0",
    }
    with zipfile.ZipFile(out_vdtheme_path, "w", compression=zipfile.ZIP_DEFLATED) as zf:
        zf.writestr("theme.json", json.dumps(meta, ensure_ascii=False, indent=2))
        if custom_bg_path and custom_bg_path.is_file():
            zf.write(custom_bg_path, "backdrop.png")
        if custom_sound_path and custom_sound_path.is_file():
            zf.write(custom_sound_path, "complete.wav")

    return out_vdtheme_path
