"""Top-level bootstrapper for double-click execution and PyInstaller `.exe` packaging.

1. Sets up trusted DLL search directories on Windows to prevent DLL hijacking / 32-bit architecture mismatch.
2. Enables `faulthandler` BEFORE importing any native extension to capture hard crashes.
3. Fast-paths directly to `run_desktop_gui` for normal desktop launches (bypassing heavy CLI server modules).
"""

import faulthandler
import os
from pathlib import Path
import sys

# Defense-in-depth: prioritize application directory and PyInstaller _internal for DLL loading
if sys.platform.startswith("win"):
    exe_dir = Path(sys.executable).parent if getattr(sys, "frozen", False) else Path(__file__).resolve().parent
    if hasattr(os, "add_dll_directory"):
        for candidate in (exe_dir, exe_dir / "_internal"):
            if candidate.is_dir():
                try:
                    os.add_dll_directory(str(candidate))
                except Exception:
                    pass


def _enable_crash_log() -> None:
    try:
        from videodownloader.core.paths import get_logs_dir

        crash_fp = open(get_logs_dir() / "crash.log", "a", encoding="utf-8", buffering=1)
        crash_fp.write(f"\n==== boot argv={sys.argv[1:]} frozen={getattr(sys, 'frozen', False)} ====\n")
        faulthandler.enable(file=crash_fp, all_threads=True)
        # Keep a strong reference so the file handle stays open for the process lifetime
        sys._vd_crash_fp = crash_fp  # type: ignore[attr-defined]
    except Exception:
        pass


_enable_crash_log()


def bootstrap() -> None:
    argv = sys.argv[1:]
    cli_flags = {"--explain", "--web", "--mcp", "--smoke-test", "-V", "--version", "-h", "--help"}

    # If launched with CLI subcommands, route to launcher parser
    if any(arg in cli_flags for arg in argv):
        from videodownloader.launcher import main
        main(argv)
        return

    # Normal desktop launch (double-click, shortcut, or autostart with --minimized)
    from videodownloader.desktop_app import run_desktop_gui
    run_desktop_gui(start_minimized="--minimized" in argv)


if __name__ == "__main__":
    bootstrap()
