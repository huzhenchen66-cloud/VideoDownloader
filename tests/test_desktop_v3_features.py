"""Offline unit & GUI state-machine tests for v3.0 Desktop Essentials:
- Global Hotkey chord parser
- App Self-Updater SemVer comparison & mandatory SHA-256 verification
- Single-Instance IPC wakeup (`QLocalServer` / `QLocalSocket`)
- Speculative Prefetcher 2-level cache & Clipboard Sentinel 7 anti-annoyance rules
- Fluent MainWindow (`Enter`-to-download, pure brand title, subtle bottom-right `v3.0.0`)
- QuickCaptureDialog (`empty -> cached -> Ctrl+Enter paused queue / Tab quality cycling / Esc 24h suppression`)
- SystemTrayController dynamic `QPainter` progress ring rendering.
"""

from __future__ import annotations

import hashlib
import os
from pathlib import Path
import uuid

import pytest

if os.path.isdir(r"C:\Windows\Fonts"):
    os.environ.setdefault("QT_QPA_FONTDIR", r"C:\Windows\Fonts")
os.environ.setdefault("QT_QPA_PLATFORM", "offscreen")

from PyQt6.QtCore import Qt
from PyQt6.QtGui import QKeyEvent
from PyQt6.QtWidgets import QApplication

from videodownloader import APP_NAME, __version__
from videodownloader.core.app_updater import (
    download_verified_installer,
    is_newer_version,
    parse_semver,
    parse_sha256sums,
    verify_file_sha256,
)
from videodownloader.core.clipboard_sentinel import ClipboardSentinel
from videodownloader.core.db import Database
from videodownloader.core.hotkey import MOD_ALT, MOD_CONTROL, MOD_SHIFT, parse_hotkey_chord
from videodownloader.core.prefetch import SpeculativePrefetcher
from videodownloader.core.single_instance import SingleInstanceGuard
from videodownloader.core.task_queue import TaskQueueManager
from videodownloader.desktop_app import MainWindow
from videodownloader.engines.base import BaseVideoEngine, DownloadOptions, DownloadResult, MultiEngineChain, ParsedMedia


def _get_qapp() -> QApplication:
    return QApplication.instance() or QApplication([])


# -------------------- 1. Global Hotkey Parser --------------------


def test_parse_hotkey_chords():
    parsed = parse_hotkey_chord("Ctrl+Shift+D")
    assert parsed is not None
    mods, vk, pynput_spec = parsed
    assert mods == (MOD_CONTROL | MOD_SHIFT)
    assert vk == ord("D")
    assert pynput_spec == "<ctrl>+<shift>+d"

    parsed_f = parse_hotkey_chord("Alt+F9")
    assert parsed_f is not None
    assert parsed_f[0] == MOD_ALT
    assert parsed_f[2] == "<alt>+<f9>"

    # Single unmodified key MUST be rejected so normal typing can never be hijacked
    assert parse_hotkey_chord("D") is None
    assert parse_hotkey_chord("") is None


# -------------------- 2. Self-Updater SemVer & SHA256 Security --------------------


def test_semver_comparison_handles_multi_digit_versions_and_prereleases():
    assert parse_semver("v3.10.0") > parse_semver("v3.2.9")
    assert is_newer_version("v3.0.1", "3.0.0") is True
    assert is_newer_version("v3.0.0", "3.0.0") is False
    assert is_newer_version("v3.0.0-rc1", "3.0.0") is False


def test_sha256sums_parser_and_tampered_installer_rejection(tmp_path: Path, monkeypatch):
    payload = b"MZ_FAKE_INSTALLER_BINARY_CONTENT_FOR_HASH_TEST"
    good_hash = hashlib.sha256(payload).hexdigest()
    bad_hash = "0" * 64

    sums_txt = f"""
    {good_hash}  VideoDownloader-Setup-3.1.0-x64.exe
    {'a' * 64} *VideoDownloader-3.1.0-macOS-arm64.dmg
    """
    assert parse_sha256sums(sums_txt, "VideoDownloader-Setup-3.1.0-x64.exe") == good_hash

    sample_file = tmp_path / "Setup.exe"
    sample_file.write_bytes(payload)
    assert verify_file_sha256(sample_file, good_hash) is True
    assert verify_file_sha256(sample_file, bad_hash) is False

    # Mock `requests.get` to verify `download_verified_installer` destroys file & raises on hash mismatch!
    class _FakeResp:
        def __enter__(self):
            return self

        def __exit__(self, *args):
            return False

        headers = {"Content-Length": str(len(payload))}

        def raise_for_status(self):
            pass

        def iter_content(self, chunk_size=1024):
            yield payload

    monkeypatch.setattr("videodownloader.core.app_updater.requests.get", lambda *a, **kw: _FakeResp())

    with pytest.raises(ValueError, match="SHA256"):
        download_verified_installer(
            "https://example.com/VideoDownloader-Setup-3.1.0-x64.exe",
            "VideoDownloader-Setup-3.1.0-x64.exe",
            expected_sha256=bad_hash,
            dest_dir=tmp_path / "dl_bad",
        )
    assert not (tmp_path / "dl_bad" / "VideoDownloader-Setup-3.1.0-x64.exe").exists()

    ok_path = download_verified_installer(
        "https://example.com/VideoDownloader-Setup-3.1.0-x64.exe",
        "VideoDownloader-Setup-3.1.0-x64.exe",
        expected_sha256=good_hash,
        dest_dir=tmp_path / "dl_ok",
    )
    assert ok_path.is_file()


# -------------------- 3. Single-Instance Lock & Wakeup --------------------


def test_single_instance_guard_ipc_wakeup():
    app = _get_qapp()
    pipe_name = f"VD_TestPipe_{uuid.uuid4().hex[:10]}"
    primary = SingleInstanceGuard(server_name=pipe_name)
    secondary = SingleInstanceGuard(server_name=pipe_name)

    received: list[str] = []
    primary.message_received.connect(received.append)

    try:
        assert primary.try_acquire_or_notify("SHOW") is True
        assert secondary.try_acquire_or_notify("CAPTURE") is False
        app.processEvents()
        assert received == ["CAPTURE"]
    finally:
        primary.close()
        secondary.close()


# -------------------- 4. Speculative Prefetcher & Non-Intrusive Clipboard Sentinel --------------------


class _InstantOfflineEngine(BaseVideoEngine):
    name = "yt-dlp"

    def can_handle(self, url, platform_id, prefer_fallback=False) -> bool:
        return True

    def parse(self, url, platform_id, options) -> ParsedMedia:
        return ParsedMedia(
            url=url,
            title="Zero-Wait Prefetched Bilibili Video",
            platform_id=platform_id,
            platform_name="哔哩哔哩 Bilibili",
            uploader="TechCreator",
            duration=245.0,
        )

    def download(self, url, platform_id, options, stop_event, stop_reason_getter, on_progress=None) -> DownloadResult:
        p = Path(options.save_dir) / "offline.mp4"
        p.write_bytes(b"\x00\x00\x00\x18ftypmp42" + b"Z" * 4096)
        return DownloadResult(file_path=str(p), title="Offline Video", engine_used=self.name)


def test_clipboard_sentinel_anti_annoyance_rules_and_prefetch_cache(tmp_path: Path, monkeypatch):
    _get_qapp()
    monkeypatch.setenv("VIDEODOWNLOADER_HOME", str(tmp_path))
    db = Database(db_path=tmp_path / "sentinel.db")
    mgr = TaskQueueManager(db)
    mgr.engine_chain = MultiEngineChain([_InstantOfflineEngine()])
    prefetcher = SpeculativePrefetcher(db, mgr)
    sentinel = ClipboardSentinel(db=db, prefetcher=prefetcher, is_app_foreground=lambda: True)

    try:
        # Rule A: Generic non-video URLs (GitHub, Docs) MUST be ignored (zero false positives!)
        assert sentinel.evaluate_text("https://github.com/torvalds/linux/pull/123") is None
        assert sentinel.evaluate_text("Just normal copied paragraph text without links") is None

        # Rule B: Internal app copies MUST be ignored
        sentinel.mark_internal_copy("https://www.bilibili.com/video/BV1internal/")
        assert sentinel.evaluate_text("https://www.bilibili.com/video/BV1internal/") is None

        # Rule C: Valid Bilibili share text passes evaluation
        cand = sentinel.evaluate_text("【好片推荐】https://www.bilibili.com/video/BV1Js411v7Y3?spm_id_from=333.1")
        assert cand is not None
        assert cand.platform_id == "bilibili"

        # Rule D: 24h URL suppression blocks repeat triggers
        sentinel.suppress_url_24h(cand.normalized_url)
        sentinel._snooze_until = 0.0
        assert sentinel.evaluate_text(cand.normalized_url) is None

        # Rule E: Permanent domain blacklist blocks entire host
        sentinel.suppress_domain_forever("https://www.tiktok.com/@u/video/1")
        sentinel._snooze_until = 0.0
        assert sentinel.evaluate_text("https://www.tiktok.com/@other/video/999") is None

        # Speculative Prefetcher L2 -> L1 0ms cache lookup
        db.set_prefetch_cache(
            "https://www.youtube.com/watch?v=cached01",
            {
                "platform_id": "youtube",
                "platform_name": "YouTube",
                "media": {"title": "Prewarmed 4K Video", "duration": 90.0, "uploader": "ChannelX"},
            },
        )
        hit = prefetcher.get_cached("https://www.youtube.com/watch?v=cached01")
        assert hit is not None
        assert hit.ok is True
        assert hit.from_cache is True
        assert hit.media["title"] == "Prewarmed 4K Video"
    finally:
        sentinel.shutdown()
        mgr.stop()


# -------------------- 5. Fluent MainWindow, Enter-to-Download, Quick Capture & Tray Ring --------------------


def test_fluent_mainwindow_branding_enter_to_download_and_quick_capture(tmp_path: Path, monkeypatch):
    app = _get_qapp()
    monkeypatch.setenv("VIDEODOWNLOADER_HOME", str(tmp_path))

    win = MainWindow()
    win.queue_mgr.stop()  # Keep queued tasks deterministic for inspection
    try:
        # 1. User's explicit branding rule: pure `VideoDownloader` title & header, subtle `v3.0.0` badge in bottom-right
        assert win.windowTitle() == APP_NAME == "VideoDownloader"
        assert "3.0" not in win.windowTitle()
        assert win.brand_title_lbl.text() == "VideoDownloader"
        assert win.version_badge_lbl.text() == f"v{__version__}"

        # 2. Pressing Enter in URL input immediately submits download task & clears input!
        win.url_input.setText("https://www.bilibili.com/video/BV1enterTest/")
        win.url_input.returnPressed.emit()
        app.processEvents()

        tasks = win.db.list_tasks()
        assert len(tasks) == 1
        assert tasks[0]["platform_id"] == "bilibili"
        assert win.url_input.text() == ""

        # 3. Quick Capture 0ms cached hit + Tab quality cycling + Ctrl+Enter paused enqueue
        cached_url = "https://www.youtube.com/watch?v=qcZeroWait1"
        win.db.set_prefetch_cache(
            cached_url,
            {
                "platform_id": "youtube",
                "platform_name": "YouTube",
                "media": {"title": "Zero Wait Raycast Card", "duration": 120.0, "uploader": "Creator"},
            },
        )
        win.open_quick_capture(cached_url)
        app.processEvents()
        assert win.quick_capture.state == "cached"
        assert "Zero Wait Raycast Card" in win.quick_capture.lbl_title.text()

        # Cycle quality chip via Tab
        assert win.quick_capture._selected_chip_idx == 0
        tab_ev = QKeyEvent(QKeyEvent.Type.KeyPress, Qt.Key.Key_Tab, Qt.KeyboardModifier.NoModifier)
        win.quick_capture.keyPressEvent(tab_ev)
        assert win.quick_capture._selected_chip_idx == 1  # Switched to `best_4k`!

        # Press Ctrl+Enter -> enqueue paused
        ctrl_enter = QKeyEvent(
            QKeyEvent.Type.KeyPress,
            Qt.Key.Key_Return,
            Qt.KeyboardModifier.ControlModifier,
        )
        win.quick_capture.keyPressEvent(ctrl_enter)
        app.processEvents()

        all_tasks = win.db.list_tasks()
        yt_task = next(t for t in all_tasks if t["url"] == cached_url)
        assert yt_task["status"] == "paused"
        assert yt_task["quality"] == "best_4k"
        assert yt_task["title"] == "Zero Wait Raycast Card"

        # 4. System Tray dynamic progress ring rendering across all 3 visual states
        for state_name, pct in (("idle", 0.0), ("downloading", 64.0), ("error", 0.0)):
            pix = win.tray_ctrl.render_progress_pixmap(state=state_name, progress=pct)
            assert not pix.isNull()
            assert pix.width() == 64 and pix.height() == 64
    finally:
        win._force_quit = True
        win.close()
        app.processEvents()


# -------------------- 6. Anime Aesthetic Multi-Themes --------------------


def test_anime_themes_presets_and_runtime_switching(tmp_path: Path, monkeypatch):
    app = _get_qapp()
    monkeypatch.setenv("VIDEODOWNLOADER_HOME", str(tmp_path))

    from videodownloader.ui.tokens import ANIME_THEMES, get_anime_theme, get_palette

    # Verify all 5 anime themes exist and have contrasting light & dark accents
    for tid in ("miku", "sakura", "violet", "amber", "azure"):
        tinfo = get_anime_theme(tid)
        assert tinfo["id"] == tid
        assert tinfo["accent"].startswith("#")
        assert len(tinfo["gradient"]) == 2

        pal_light = get_palette(is_dark=False, theme_style=tid)
        pal_dark = get_palette(is_dark=True, theme_style=tid)
        assert pal_light["accent"] == tinfo["accent"]
        assert pal_dark["accent"] == tinfo["accent_light"]

    # Test runtime switching on MainWindow
    win = MainWindow()
    try:
        # Switch to Sakura Pink theme
        win.apply_theme(is_dark=False, theme_style="sakura")
        app.processEvents()
        assert win._theme_style == "sakura"

        # Switch to Neon Violet Dark theme
        win.apply_theme(is_dark=True, theme_style="violet")
        app.processEvents()
        assert win._theme_style == "violet"
        assert win._is_dark is True
    finally:
        win._force_quit = True
        win.close()
        app.processEvents()
