"""Cross-platform CLI & Desktop Launcher (`videodownloader/launcher.py` — v3.0):
- Default: Native Fluent Desktop Application (`VideoDownloader`) with single-instance lock & system tray
- `--minimized`: Start silently in the system tray (for Windows startup autostart)
- `--explain <url>`: Run the 4-stage engine resolution chain and print the full `ResolutionTrace` tree
- `--version`: Print application version
- `--web`: Run standalone headless FastAPI Web/PWA server
- `--mcp`: Run standard MCP stdio server for AI Agents (Claude / Cursor)
- `--smoke-test <json_path>`: Post-build automated verification of packaged GUI + engines + multimedia.
"""

from __future__ import annotations

import argparse
import os
import sys
import threading
import time
import webbrowser

from . import APP_NAME, __version__
from .ai.mcp_server import main as run_mcp_server
from .core.lan_helper import get_lan_ips
from .core.logging_setup import setup_logging
from .core.paths import get_logs_dir
from .core.web_server_manager import find_free_port, is_port_in_use


def _ensure_safe_stdio() -> None:
    """Prevent Uvicorn / library crashes when packaged with PyInstaller `--noconsole` (`sys.stdout` is None)."""
    log_file = get_logs_dir() / "stdio.log"
    if sys.stdout is None:
        sys.stdout = open(log_file, "a", encoding="utf-8", buffering=1)
    if sys.stderr is None:
        sys.stderr = open(log_file, "a", encoding="utf-8", buffering=1)


def _open_browser_when_ready(url: str, port: int) -> None:
    for _ in range(40):
        if is_port_in_use(port):
            time.sleep(0.15)
            try:
                webbrowser.open(url)
            except Exception:
                pass
            return
        time.sleep(0.2)


def run_explain_cli(raw_url: str) -> int:
    """Execute 4-stage resolution probe and print the explainable `ResolutionTrace` tree to stdout."""
    from .core.db import Database
    from .core.task_queue import TaskQueueManager
    from .core.url_cleaner import clean_share_input
    from .engines.base import ResolutionError, ResolutionTrace, StageRecord

    setup_logging()
    cleaned = clean_share_input(raw_url, resolve_short_links=True)
    if not cleaned:
        print(f"[ERROR] 未在输入中识别到有效链接: {raw_url}", file=sys.stderr)
        return 2

    target = cleaned[0]
    db = Database()
    mgr = TaskQueueManager(db)
    opts = mgr.build_download_options()

    print(f"{APP_NAME} v{__version__} · 四级多引擎解析轨迹诊断")
    print(f"目标地址 : {target.normalized_url}")
    print(f"识别平台 : {target.platform_name} ({target.platform_id})\n")

    try:
        media = mgr.engine_chain.parse(target.normalized_url, target.platform_id, opts)
        td = media.trace or {}
        rt = ResolutionTrace(
            url=target.normalized_url,
            platform_id=target.platform_id,
            operation="parse",
            stages=[StageRecord(**s) for s in (td.get("stages") or [])],
            final_engine=media.engine_name,
            total_ms=int(td.get("total_ms") or media.elapsed_ms),
            succeeded=True,
        )
        print(rt.format_tree())
        print(f"\n解析结果 : 成功 ({rt.total_ms}ms)")
        print(f"视频标题 : {media.title}")
        if media.uploader:
            print(f"作者频道 : {media.uploader}")
        if media.formats:
            print("可选规格 : " + " / ".join(f.label for f in media.formats[:6]))
        return 0
    except ResolutionError as exc:
        print(exc.trace.format_tree())
        print(f"\n解析结果 : 失败 [{exc.primary_code}] (可自动重试: {'是' if exc.retryable else '否'})")
        print(f"诊断建议 : {exc.hint}")
        return 1


def run_standalone_web_server(host: str, preferred_port: int, open_browser: bool) -> None:
    """Run headless FastAPI Web/PWA server (for `--web`, Docker, Linux servers, or macOS web mode)."""
    _ensure_safe_stdio()
    setup_logging()
    port = find_free_port(preferred_port)
    local_url = f"http://127.0.0.1:{port}"
    lan_ip = get_lan_ips()[0]
    lan_url = f"http://{lan_ip}:{port}"

    banner = (
        "\n"
        "====================================================================\n"
        f"  {APP_NAME} Web & Mobile Server (v{__version__})\n"
        "--------------------------------------------------------------------\n"
        f"  Local Browser : {local_url}\n"
        f"  iPhone / LAN  : {lan_url}\n"
        "  MCP Server    : videodownloader --mcp\n"
        "====================================================================\n"
    )
    try:
        print(banner, flush=True)
    except Exception:
        pass

    if open_browser:
        threading.Thread(target=_open_browser_when_ready, args=(local_url, port), daemon=True).start()

    import uvicorn
    from .main import create_app

    fastapi_app = create_app(server_port=port)
    config = uvicorn.Config(
        app=fastapi_app,
        host=host,
        port=port,
        log_level="warning",
        access_log=False,
    )
    uvicorn.Server(config).run()


def main(argv: list[str] | None = None) -> None:
    parser = argparse.ArgumentParser(description=f"{APP_NAME} Desktop, Explainable CLI & MCP Launcher")
    parser.add_argument(
        "--version",
        "-V",
        action="store_true",
        help="Print version and exit",
    )
    parser.add_argument(
        "--minimized",
        action="store_true",
        help="Start silently minimized to the system tray (used by OS autostart)",
    )
    parser.add_argument(
        "--explain",
        default="",
        metavar="URL",
        help="Probe URL across the 4-stage engine chain and print the full ResolutionTrace tree",
    )
    parser.add_argument(
        "--web",
        action="store_true",
        help="Run in standalone Web/Mobile server mode instead of launching the Native Desktop GUI",
    )
    parser.add_argument(
        "--host",
        default=os.environ.get("VD_HOST", "0.0.0.0"),
        help="Bind host for --web mode (default 0.0.0.0)",
    )
    parser.add_argument(
        "--port",
        type=int,
        default=int(os.environ.get("VD_PORT", "8765")),
        help="Bind port for --web mode (default 8765)",
    )
    parser.add_argument(
        "--open-browser",
        action="store_true",
        help="Open system browser automatically when running in --web mode",
    )
    parser.add_argument(
        "--no-browser",
        action="store_true",
        help="Do not open browser (kept for CLI compatibility)",
    )
    parser.add_argument(
        "--mcp",
        action="store_true",
        help="Run as standard MCP stdio server for AI Agents (Claude / Cursor)",
    )
    parser.add_argument(
        "--smoke-test",
        default="",
        help="Internal post-build verification: boot Fluent MainWindow + QuickCapture + engines and write JSON report",
    )
    args = parser.parse_args(argv)

    if args.version:
        print(f"{APP_NAME} {__version__}")
        return

    if args.explain:
        sys.exit(run_explain_cli(args.explain))

    if args.smoke_test:
        import json as _json
        from pathlib import Path as _Path
        import tempfile as _tempfile

        if sys.platform.startswith("win") and os.path.isdir(r"C:\Windows\Fonts"):
            os.environ.setdefault("QT_QPA_FONTDIR", r"C:\Windows\Fonts")
        os.environ.setdefault("QT_QPA_PLATFORM", "offscreen")

        # Isolate smoke test DB inside a temp folder so build verification never pollutes user's live DB
        tmp_home = _Path(_tempfile.mkdtemp(prefix="vd_smoke_"))
        os.environ["VIDEODOWNLOADER_HOME"] = str(tmp_home)

        _ensure_safe_stdio()
        setup_logging()

        from PyQt6.QtWidgets import QApplication as _QApp
        import yt_dlp as _ytdlp
        from yt_dlp.networking.impersonate import ImpersonateTarget as _Imp

        from .core.binary_updater import get_toolchain_status as _get_tc
        from .desktop_app import MainWindow as _MW

        app = _QApp.instance() or _QApp([])
        win = _MW()
        win.quick_capture.open_panel("")
        app.processEvents()
        tc = _get_tc()
        imp_ok = str(_Imp.from_str("chrome"))
        report = {
            "ok": True,
            "app_name": APP_NAME,
            "version": __version__,
            "window_title": win.windowTitle(),
            "version_badge": win.version_badge_lbl.text(),
            "quick_capture_state": win.quick_capture.state,
            "web_default_off": (not win.web_mgr.is_running),
            "ytdlp_version": _ytdlp.version.__version__,
            "impersonate_target": imp_ok,
            "toolchain": tc,
        }
        win._force_quit = True
        win.close()
        app.processEvents()
        _Path(args.smoke_test).write_text(_json.dumps(report, ensure_ascii=False, indent=2), encoding="utf-8")
        sys.exit(0)

    if args.mcp:
        run_mcp_server()
        return

    _ensure_safe_stdio()

    if args.web:
        run_standalone_web_server(
            host=args.host,
            preferred_port=args.port,
            open_browser=(args.open_browser and not args.no_browser),
        )
        return

    # Default behavior: Launch the Native Fluent Desktop Application
    try:
        from .desktop_app import run_desktop_gui
    except ImportError:
        run_standalone_web_server(
            host=args.host,
            preferred_port=args.port,
            open_browser=(not args.no_browser),
        )
        return

    run_desktop_gui(start_minimized=bool(args.minimized))


if __name__ == "__main__":
    main()
