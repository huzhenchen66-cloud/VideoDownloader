"""Generate real offline screenshot assets and animated demo GIF for documentation and GitHub README.

Outputs:
  - `docs/assets/screenshot-light.png`: MainWindow in Light mode (Miku Aurora Cyan theme, expanded 4-stage ResolutionTrace)
  - `docs/assets/screenshot-dark.png`: MainWindow in Dark mode (Neon Violet cyberpunk theme)
  - `docs/assets/screenshot-quick-capture.png`: Raycast-style 0ms Quick Capture floating palette
  - `docs/assets/demo.gif`: Multi-frame workflow animation (0ms prefetch -> Enter to download -> ResolutionTrace)
"""

from __future__ import annotations

import io
import os
from pathlib import Path
import sys
import tempfile
import time

if sys.platform.startswith("win") and os.path.isdir(r"C:\Windows\Fonts"):
    os.environ.setdefault("QT_QPA_FONTDIR", r"C:\Windows\Fonts")
os.environ.setdefault("QT_QPA_PLATFORM", "offscreen")

from PIL import Image
from PyQt6.QtCore import QBuffer, QIODevice, Qt
from PyQt6.QtWidgets import QApplication

ROOT = Path(__file__).resolve().parent.parent
sys.path.insert(0, str(ROOT))

from videodownloader.core.db import Database
from videodownloader.desktop_app import MainWindow


def qpixmap_to_pil(pixmap) -> Image.Image:
    buf = QBuffer()
    buf.open(QIODevice.OpenModeFlag.ReadWrite)
    pixmap.save(buf, "PNG")
    data = bytes(buf.data())
    buf.close()
    return Image.open(io.BytesIO(data))

SAMPLE_TASKS = [
    {
        "id": "demo-task-01",
        "url": "https://www.bilibili.com/video/BV1Js411v7Y3/",
        "platform_id": "bilibili",
        "platform_name": "哔哩哔哩 Bilibili",
        "title": "【4K60FPS】2026 经典动画巡礼 · 超高清视听盛宴",
        "uploader": "AnimeMaster",
        "quality": "best_1080p",
        "status": "completed",
        "progress": 100.0,
        "file_size": 248_500_000,
        "duration": 348.0,
        "file_path": r"C:\Users\User\Downloads\VideoDownloader\2026经典动画巡礼.mp4",
        "error": "",
        "error_code": "",
        "trace": {
            "url": "https://www.bilibili.com/video/BV1Js411v7Y3/",
            "platform_id": "bilibili",
            "operation": "download",
            "final_engine": "yt-dlp",
            "total_ms": 1420,
            "succeeded": True,
            "stages": [
                {
                    "stage": 1,
                    "engine": "cn-native-sniffer",
                    "status": "skipped",
                    "reason": "平台 bilibili 不在移动端直连注册表",
                    "elapsed_ms": 0,
                    "error_code": "",
                },
                {
                    "stage": 2,
                    "engine": "browser-cdp-sniffer",
                    "status": "skipped",
                    "reason": "平台 bilibili 无需无头浏览器嗅探",
                    "elapsed_ms": 0,
                    "error_code": "",
                },
                {
                    "stage": 3,
                    "engine": "yt-dlp",
                    "status": "success",
                    "reason": "解析到 14 个清晰度规格并完成 H.264+AAC 混流",
                    "elapsed_ms": 1420,
                    "error_code": "",
                },
                {
                    "stage": 4,
                    "engine": "web-media-sniffer",
                    "status": "not_run",
                    "reason": "前序阶段已命中",
                    "elapsed_ms": 0,
                    "error_code": "",
                },
            ],
        },
        "created_at": time.time() - 300,
        "updated_at": time.time() - 100,
    },
    {
        "id": "demo-task-02",
        "url": "https://www.youtube.com/watch?v=dQw4w9WgXcQ",
        "platform_id": "youtube",
        "platform_name": "YouTube",
        "title": "Rick Astley - Never Gonna Give You Up (Official Music Video)",
        "uploader": "Rick Astley",
        "quality": "best_4k",
        "status": "downloading",
        "progress": 72.5,
        "speed": "24.6 MB/s",
        "eta": "00:08",
        "file_size": 189_000_000,
        "duration": 213.0,
        "file_path": "",
        "error": "",
        "error_code": "",
        "created_at": time.time() - 60,
        "updated_at": time.time() - 5,
    },
    {
        "id": "demo-task-03",
        "url": "https://www.xiaohongshu.com/explore/648a12bc0000000013028",
        "platform_id": "xiaohongshu",
        "platform_name": "小红书 RED",
        "title": "京都樱花季巡游 Vlog · 绝美粉色隧道",
        "uploader": "二次元旅行家",
        "quality": "best_1080p",
        "status": "completed",
        "progress": 100.0,
        "file_size": 68_400_000,
        "duration": 85.0,
        "file_path": r"C:\Users\User\Downloads\VideoDownloader\京都樱花季巡游.mp4",
        "error": "",
        "error_code": "",
        "created_at": time.time() - 600,
        "updated_at": time.time() - 500,
    },
]


def generate_all() -> None:
    docs_assets = ROOT / "docs" / "assets"
    docs_assets.mkdir(parents=True, exist_ok=True)

    app = QApplication.instance() or QApplication(sys.argv)

    tmp_dir = Path(tempfile.mkdtemp(prefix="vd_media_"))
    os.environ["VIDEODOWNLOADER_HOME"] = str(tmp_dir)

    import json
    db = Database(db_path=tmp_dir / "data" / "videodownloader.db")
    for t in SAMPLE_TASKS:
        t_copy = dict(t)
        t_copy["raw_input"] = t["url"]
        t_copy["trace_json"] = json.dumps(t.get("trace") or {}, ensure_ascii=False)
        db.create_task(t_copy)

    win = MainWindow(db=db)
    win.queue_mgr.stop()  # Stop worker threads during static grab

    # Expand the first task's ResolutionTrace tree
    win._expanded_traces.add("demo-task-01")

    # 1. Render Light Mode with Miku Aurora Cyan Theme
    print("[1/4] Rendering docs/assets/screenshot-light.png (Miku Theme)...")
    win.apply_theme(is_dark=False, theme_style="miku")
    win.resize(1160, 780)
    win.show()
    app.processEvents()

    pix_light = win.grab()
    light_path = docs_assets / "screenshot-light.png"
    pix_light.save(str(light_path), "PNG")
    print(f" -> Saved {light_path.name} ({light_path.stat().st_size // 1024} KB)")

    # 2. Render Dark Mode with Neon Violet Cyberpunk Theme
    print("[2/4] Rendering docs/assets/screenshot-dark.png (Neon Violet Theme)...")
    win.apply_theme(is_dark=True, theme_style="violet")
    app.processEvents()

    pix_dark = win.grab()
    dark_path = docs_assets / "screenshot-dark.png"
    pix_dark.save(str(dark_path), "PNG")
    print(f" -> Saved {dark_path.name} ({dark_path.stat().st_size // 1024} KB)")

    # 3. Render Quick Capture 0ms Raycast Palette
    print("[3/4] Rendering docs/assets/screenshot-quick-capture.png...")
    win.quick_capture.apply_theme(is_dark=True, theme_style="miku")
    win.quick_capture.open_panel("https://www.bilibili.com/video/BV1Js411v7Y3/")
    # Populate preview
    win.quick_capture.state = "cached"
    win.quick_capture.lbl_title.setText("【4K60FPS】2026 经典动画巡礼 · 超高清视听盛宴")
    win.quick_capture.lbl_submeta.setText("AnimeMaster  ·  05:48  ·  哔哩哔哩")
    win.quick_capture.lbl_platform_badge.setText("哔哩哔哩 Bilibili")
    win.quick_capture.lbl_cache_badge.setText("预探测 0ms 命中")
    win.quick_capture.lbl_cache_badge.setVisible(True)
    win.quick_capture.stack.setCurrentIndex(1)
    app.processEvents()

    qc_pix = win.quick_capture.grab()
    qc_path = docs_assets / "screenshot-quick-capture.png"
    qc_pix.save(str(qc_path), "PNG")
    win.quick_capture.hide()
    print(f" -> Saved {qc_path.name} ({qc_path.stat().st_size // 1024} KB)")

    # 4. Render Viral Social Stats & Achievements Card
    print("[4/6] Rendering docs/assets/screenshot-stats.png...")
    from videodownloader.ui.stats_dialog import StatsDialog
    stats_dlg = StatsDialog(db=db, is_dark=False, theme_style="miku")
    stats_dlg._compute_and_render()
    stats_dlg.show()
    app.processEvents()
    stats_pix = stats_dlg.grab()
    stats_path = docs_assets / "screenshot-stats.png"
    stats_pix.save(str(stats_path), "PNG")
    stats_dlg.hide()
    print(f" -> Saved {stats_path.name} ({stats_path.stat().st_size // 1024} KB)")

    # 5. Render Command Palette (Ctrl+K)
    print("[5/6] Rendering docs/assets/screenshot-command-palette.png...")
    win.command_palette.apply_theme(is_dark=False, theme_style="miku")
    win.command_palette.open_palette()
    win.command_palette.search_input.setText("主题")
    win.command_palette._filter_commands("主题")
    app.processEvents()
    cp_pix = win.command_palette.grab()
    cp_path = docs_assets / "screenshot-command-palette.png"
    cp_pix.save(str(cp_path), "PNG")
    win.command_palette.hide()
    print(f" -> Saved {cp_path.name} ({cp_path.stat().st_size // 1024} KB)")

    # 6. Synthesize Animated Demo GIF
    print("[6/6] Generating animated docs/assets/demo.gif...")
    gif_frames: list[Image.Image] = []

    # Frame sequence simulating: Empty MainWindow -> Quick Capture popup -> Enter download -> Done & Trace tree
    # Step A: MainWindow waiting for input (Miku Teal Light)
    win.apply_theme(is_dark=False, theme_style="miku")
    win._expanded_traces.clear()
    win.url_input.setText("")
    db.update_task("demo-task-02", status="queued", progress=0.0)
    win.refresh_all_task_views()
    app.processEvents()
    img_a = qpixmap_to_pil(win.grab()).resize((870, 585), Image.Resampling.LANCZOS)
    for _ in range(8):  # 800ms
        gif_frames.append(img_a.convert("RGB"))

    # Step B: URL typed into Quick Capture
    win.url_input.setText("https://www.bilibili.com/video/BV1Js411v7Y3/")
    win.refresh_all_task_views()
    app.processEvents()
    img_b = qpixmap_to_pil(win.grab()).resize((870, 585), Image.Resampling.LANCZOS)
    for _ in range(6):  # 600ms
        gif_frames.append(img_b.convert("RGB"))

    # Step C: Downloading animation (30%, 65%, 90%)
    for pct, eta in ((32.0, "00:15"), (68.0, "00:07"), (94.0, "00:02")):
        db.update_task("demo-task-02", status="downloading", progress=pct, speed="28.4 MB/s", eta=eta)
        win.refresh_all_task_views()
        app.processEvents()
        frame_img = qpixmap_to_pil(win.grab()).resize((870, 585), Image.Resampling.LANCZOS)
        for _ in range(4):
            gif_frames.append(frame_img.convert("RGB"))

    # Step D: Completed and expanded 4-Stage ResolutionTrace Tree!
    db.update_task("demo-task-02", status="completed", progress=100.0, speed="", eta="")
    win._expanded_traces.add("demo-task-01")
    win.refresh_all_task_views()
    app.processEvents()
    img_d = qpixmap_to_pil(win.grab()).resize((870, 585), Image.Resampling.LANCZOS)
    for _ in range(15):  # 1.5s freeze on completed state
        gif_frames.append(img_d.convert("RGB"))

    gif_path = docs_assets / "demo.gif"
    if gif_frames:
        gif_frames[0].save(
            str(gif_path),
            save_all=True,
            append_images=gif_frames[1:],
            duration=100,  # 10 fps
            loop=0,
            optimize=True,
        )
        print(f" -> Saved {gif_path.name} ({gif_path.stat().st_size // 1024} KB, {len(gif_frames)} frames)")

    win._force_quit = True
    win.close()
    app.processEvents()
    print("[SUCCESS] All demo media generated successfully!")


if __name__ == "__main__":
    generate_all()
