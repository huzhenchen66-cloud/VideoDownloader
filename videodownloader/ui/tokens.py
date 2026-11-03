"""Design System Tokens for VideoDownloader v3.0 (8px Grid, Radii, Typography & Light/Dark Palettes)."""

from __future__ import annotations

from typing import Any

# Strict 8px spatial grid
SPACE: dict[int, int] = {
    1: 8,
    2: 16,
    3: 24,
    4: 32,
}

RADIUS: dict[str, int] = {
    "sm": 6,
    "md": 10,
    "lg": 14,
}

BRAND_ACCENT = "#0EA5E9"  # Refreshing Ice-Sky Cyan (pairs cleanly with Indigo #6366F1)
BRAND_INDIGO = "#4F46E5"

FONT_STACK = '"Segoe UI", "Microsoft YaHei UI", "PingFang SC", sans-serif'
MONO_STACK = '"Cascadia Mono", "Consolas", "SF Mono", monospace'

# Pure text status labels (zero emoji characters anywhere in UI controls!)
STATUS_TEXT: dict[str, str] = {
    "queued": "排队中",
    "parsing": "解析中",
    "downloading": "下载中",
    "merging": "混流封装",
    "retrying": "自动重试",
    "paused": "已暂停",
    "interrupted": "上次未完成",
    "completed": "已完成",
    "failed": "下载失败",
    "canceled": "已取消",
}

QUALITY_PRESETS: list[tuple[str, str, bool, str]] = [
    ("1080P 高清 MP4 (H.264 全兼容)", "best_1080p", False, "mp3"),
    ("4K / 8K 原画母带 (MP4)", "best_4k", False, "mp3"),
    ("720P 极速高清 (H.264)", "fast_720p", False, "mp3"),
    ("480P 省流标清 (H.264)", "save_480p", False, "mp3"),
    ("无损母带封装 (MKV)", "best_mkv", False, "mp3"),
    ("仅提取音频 (MP3)", "best_1080p", True, "mp3"),
    ("仅提取音频 (M4A)", "best_1080p", True, "m4a"),
]

QUICK_QUALITY_CHIPS: list[tuple[str, str, bool, str]] = [
    ("1080P H.264", "best_1080p", False, "mp3"),
    ("4K 原画", "best_4k", False, "mp3"),
    ("720P 极速", "fast_720p", False, "mp3"),
    ("仅音频 MP3", "best_1080p", True, "mp3"),
]

# -------------------- Anime & Aesthetic Theme Presets --------------------
ANIME_THEMES: dict[str, dict[str, Any]] = {
    "miku": {
        "id": "miku",
        "name": "初音未来 (Miku Teal)",
        "badge": "极光青",
        "accent": "#39C5BB",        # Iconic Hatsune Miku Teal
        "accent_light": "#00E5FF",  # Neon Cyan
        "accent_bg_light": "#E6FAF8",
        "accent_bg_dark": "#0B2D2B",
        "gradient": ("#39C5BB", "#00E5FF"),
    },
    "sakura": {
        "id": "sakura",
        "name": "樱花粉 (Sakura Pink)",
        "badge": "少女粉",
        "accent": "#FF6584",        # Vibrant Blossom Pink
        "accent_light": "#F472B6",
        "accent_bg_light": "#FFF1F3",
        "accent_bg_dark": "#3B111D",
        "gradient": ("#FF6584", "#F472B6"),
    },
    "violet": {
        "id": "violet",
        "name": "星空幻境 (Neon Violet)",
        "badge": "霓虹紫",
        "accent": "#8B5CF6",        # Cyber Anime Violet
        "accent_light": "#A855F7",
        "accent_bg_light": "#F5F3FF",
        "accent_bg_dark": "#27104E",
        "gradient": ("#8B5CF6", "#C084FC"),
    },
    "amber": {
        "id": "amber",
        "name": "元气暖阳 (Amber Gold)",
        "badge": "元气金",
        "accent": "#F59E0B",        # Warm Sunshine Gold
        "accent_light": "#FBBF24",
        "accent_bg_light": "#FEF3C7",
        "accent_bg_dark": "#3E2908",
        "gradient": ("#F59E0B", "#FBBF24"),
    },
    "azure": {
        "id": "azure",
        "name": "琉璃晴空 (Azure Sky)",
        "badge": "冰空蓝",
        "accent": "#0EA5E9",        # Classic Crisp Azure
        "accent_light": "#38BDF8",
        "accent_bg_light": "#E0F2FE",
        "accent_bg_dark": "#0C2D48",
        "gradient": ("#0EA5E9", "#38BDF8"),
    },
}

DEFAULT_ANIME_THEME = "miku"

LIGHT_PALETTE: dict[str, str] = {
    "bg_window": "#F7F9FC",
    "bg_card": "#FFFFFF",
    "bg_subtle": "#F1F5F9",
    "bg_trace": "#F8FAFC",
    "border": "#E2E8F0",
    "border_strong": "#CBD5E1",
    "text_primary": "#0F172A",
    "text_secondary": "#475569",
    "text_muted": "#94A3B8",
    "accent": "#39C5BB",
    "accent_bg": "#E6FAF8",
    "success": "#059669",
    "success_bg": "#DCFCE7",
    "warning": "#D97706",
    "warning_bg": "#FEF3C7",
    "danger": "#DC2626",
    "danger_bg": "#FEE2E2",
}

DARK_PALETTE: dict[str, str] = {
    "bg_window": "#11141C",
    "bg_card": "#1A1F2C",
    "bg_subtle": "#232A3B",
    "bg_trace": "#151924",
    "border": "#2B3346",
    "border_strong": "#3B455E",
    "text_primary": "#F1F5F9",
    "text_secondary": "#94A3B8",
    "text_muted": "#64748B",
    "accent": "#00E5FF",
    "accent_bg": "#0B2D2B",
    "success": "#34D399",
    "success_bg": "#063729",
    "warning": "#FBBF24",
    "warning_bg": "#3E2C0A",
    "danger": "#F87171",
    "danger_bg": "#3F1519",
}


def get_anime_theme(theme_style: str | None = None) -> dict[str, Any]:
    key = str(theme_style or "").lower().strip()
    return ANIME_THEMES.get(key, ANIME_THEMES[DEFAULT_ANIME_THEME])


def get_palette(is_dark: bool, theme_style: str | None = None) -> dict[str, str]:
    pal = dict(DARK_PALETTE if is_dark else LIGHT_PALETTE)
    theme = get_anime_theme(theme_style)
    pal["accent"] = theme["accent_light"] if is_dark else theme["accent"]
    pal["accent_bg"] = theme["accent_bg_dark"] if is_dark else theme["accent_bg_light"]
    return pal


def status_badge_colors(status: str, is_dark: bool, theme_style: str | None = None) -> tuple[str, str]:
    """Return `(foreground_hex, background_hex)` for a task status pill."""
    pal = get_palette(is_dark, theme_style)
    if status == "completed":
        return pal["success"], pal["success_bg"]
    if status == "failed":
        return pal["danger"], pal["danger_bg"]
    if status in ("retrying", "interrupted", "paused"):
        return pal["warning"], pal["warning_bg"]
    if status in ("downloading", "parsing", "merging", "queued"):
        return pal["accent"], pal["accent_bg"]
    return pal["text_secondary"], pal["bg_subtle"]
