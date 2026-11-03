"""Unit and integration tests for Sensory Layer (`sound_manager`, `backdrop`, `stats_dialog`, `command_palette`, `theme_package`)."""

from __future__ import annotations

import io
from pathlib import Path
import wave
import zipfile

import pytest
from PyQt6.QtCore import Qt
from PyQt6.QtGui import QColor, QPixmap
from PyQt6.QtWidgets import QApplication

from videodownloader.core.db import Database
from videodownloader.core.sound_manager import (
    SAMPLE_RATE,
    VOICE_SPECS,
    SoundEffectManager,
    synthesize_arpeggio_wav,
    synthesize_failure_wav,
)
from videodownloader.core.theme_package import (
    export_vdtheme_package,
    get_custom_themes_dir,
    import_vdtheme_package,
)
from videodownloader.ui.backdrop import render_procedural_backdrop
from videodownloader.ui.command_palette import CommandPaletteDialog
from videodownloader.ui.stats_dialog import StatsDialog, compute_download_stats
from videodownloader.ui.tokens import ANIME_THEMES, get_anime_theme, get_palette


def _get_qapp() -> QApplication:
    app = QApplication.instance()
    if app is None:
        app = QApplication([])
    return app


def test_sound_synthesis_wav_format_and_chords(tmp_path: Path):
    """Verify all 5 themes synthesize valid 16-bit 44.1kHz mono WAV files with crisp chords."""
    for theme_key in VOICE_SPECS:
        wav_path = tmp_path / f"test_{theme_key}.wav"
        out = synthesize_arpeggio_wav(theme_key, wav_path, is_batch=False)
        assert out.is_file()
        assert out.stat().st_size > 5000

        with wave.open(str(out), "rb") as wf:
            assert wf.getnchannels() == 1
            assert wf.getsampwidth() == 2  # 16-bit PCM
            assert wf.getframerate() == SAMPLE_RATE
            nframes = wf.getnframes()
            dur = nframes / float(SAMPLE_RATE)
            assert 0.25 <= dur <= 0.85

    # Test failure chord
    fail_path = tmp_path / "test_failed.wav"
    synthesize_failure_wav(fail_path)
    assert fail_path.is_file()
    with wave.open(str(fail_path), "rb") as wf:
        assert wf.getnchannels() == 1
        assert wf.getsampwidth() == 2
        assert wf.getframerate() == SAMPLE_RATE


def test_sound_manager_configuration_and_anti_annoyance(tmp_path: Path, monkeypatch):
    """Verify SoundEffectManager volume scaling, debounce and theme binding."""
    _get_qapp()
    monkeypatch.setenv("VIDEODOWNLOADER_HOME", str(tmp_path))

    mgr = SoundEffectManager()
    mgr.configure(enabled=True, volume=65, theme_style="sakura")
    assert mgr._enabled is True
    assert abs(mgr._volume - 0.65) < 0.01
    assert mgr._active_theme == "sakura"

    # Volume decimal scaling test
    mgr.configure(enabled=False, volume=0.45, theme_style="miku")
    assert mgr._enabled is False
    assert abs(mgr._volume - 0.45) < 0.01
    assert mgr._active_theme == "miku"


def test_procedural_backdrop_rendering_for_all_themes():
    """Verify procedural vector backdrop generates valid non-null high-DPI pixmaps."""
    _get_qapp()
    for tid in ("miku", "sakura", "violet", "amber", "azure"):
        for dark in (False, True):
            pix = render_procedural_backdrop(width=400, height=300, is_dark=dark, theme_style=tid)
            assert isinstance(pix, QPixmap)
            assert not pix.isNull()
            assert pix.width() == 400
            assert pix.height() == 300


def test_vdtheme_package_export_and_import(tmp_path: Path, monkeypatch):
    """Verify .vdtheme ZIP packaging, metadata validation, and unpacking."""
    monkeypatch.setenv("VIDEODOWNLOADER_HOME", str(tmp_path))

    out_pkg = tmp_path / "custom_cyber.vdtheme"
    res_path = export_vdtheme_package(
        theme_id="cyber",
        name="赛博幻境 Cyberpunk",
        accent="#00F0FF",
        gradient=("#00F0FF", "#7928CA"),
        out_vdtheme_path=out_pkg,
    )
    assert res_path.is_file()
    assert zipfile.is_zipfile(str(res_path))

    # Test import
    meta = import_vdtheme_package(res_path)
    assert meta["id"] == "cyber"
    assert meta["name"] == "赛博幻境 Cyberpunk"
    assert meta["accent"] == "#00F0FF"

    installed_dir = get_custom_themes_dir() / "cyber"
    assert installed_dir.is_dir()
    assert (installed_dir / "theme.json").is_file()

    # Invalid package without theme.json should raise ValueError
    bad_zip = tmp_path / "corrupted.vdtheme"
    with zipfile.ZipFile(bad_zip, "w") as zf:
        zf.writestr("dummy.txt", "hello")
    with pytest.raises(ValueError, match="missing 'theme.json'"):
        import_vdtheme_package(bad_zip)


def test_stats_computation_and_dialog(tmp_path: Path, monkeypatch):
    """Verify download stats aggregation from SQLite database and dialog instantiation."""
    _get_qapp()
    monkeypatch.setenv("VIDEODOWNLOADER_HOME", str(tmp_path))

    db = Database()

    # 1. Empty database stats
    stats_empty = compute_download_stats(db)
    assert stats_empty["total_tasks"] == 0
    assert stats_empty["total_completed"] == 0
    assert stats_empty["total_bytes"] == 0
    assert stats_empty["top_platform"] == "无"

    # 2. Populate mock completed tasks
    db.create_task(
        {
            "id": "t1",
            "url": "https://www.bilibili.com/video/BV1xx411c7mD",
            "title": "B站高质量科普视频",
            "status": "completed",
            "file_size": 150 * 1024 * 1024,
            "created_at": "2026-11-01T12:00:00",
        }
    )
    db.create_task(
        {
            "id": "t2",
            "url": "https://www.youtube.com/watch?v=dQw4w9WgXcQ",
            "title": "YouTube 4K 音乐精选",
            "status": "completed",
            "file_size": 250 * 1024 * 1024,
            "created_at": "2026-11-01T12:05:00",
        }
    )
    db.create_task(
        {
            "id": "t3",
            "url": "https://www.bilibili.com/video/BV2yy411c7mE",
            "title": "B站二创动画",
            "status": "completed",
            "file_size": 100 * 1024 * 1024,
            "created_at": "2026-11-01T12:10:00",
        }
    )

    stats = compute_download_stats(db)
    assert stats["total_tasks"] == 3
    assert stats["total_completed"] == 3
    assert stats["total_bytes"] == 500 * 1024 * 1024
    assert stats["top_platform"] == "bilibili"
    assert stats["platform_counts"]["bilibili"] == 2
    assert stats["platform_counts"]["youtube"] == 1

    # Verify StatsDialog renders high-resolution card pixmap
    dlg = StatsDialog(db=db, is_dark=False, theme_style="miku")
    dlg._compute_and_render()
    assert dlg._cached_card_pixmap is not None
    assert not dlg._cached_card_pixmap.isNull()
    assert dlg._cached_card_pixmap.width() >= 500
    assert dlg._cached_card_pixmap.height() >= 600


def test_command_palette_fuzzy_filter():
    """Verify CommandPaletteDialog search input filters commands accurately."""
    _get_qapp()
    triggered = []

    palette = CommandPaletteDialog(
        on_switch_theme=lambda s: triggered.append(f"theme_{s}"),
        on_toggle_dark=lambda: triggered.append("toggle_dark"),
        on_show_stats=lambda: triggered.append("show_stats"),
    )

    # Empty search query shows all items
    palette._filter_commands("")
    assert palette.cmd_list.count() == len(palette._all_commands)

    # Filter by keyword "miku"
    palette._filter_commands("miku")
    assert palette.cmd_list.count() >= 1
    top_item = palette.cmd_list.item(0)
    assert "初音未来" in top_item.text()

    # Filter by keyword "战报"
    palette._filter_commands("战报")
    assert palette.cmd_list.count() >= 1
    assert "战报" in palette.cmd_list.item(0).text()
