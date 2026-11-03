"""System-wide Global Hotkeys:
- Windows: Win32 `RegisterHotKey(real_hwnd, ...)` + `QAbstractNativeEventFilter` (`WM_HOTKEY = 0x0312`)
  with `ERROR_HOTKEY_ALREADY_REGISTERED (1409)` conflict detection.
- macOS / Linux: `pynput.keyboard.GlobalHotKeys` fallback marshaled cleanly onto the Qt main thread.
"""

from __future__ import annotations

import ctypes
from ctypes import wintypes
import sys
from typing import Any

from PyQt6.QtCore import QAbstractNativeEventFilter, QObject, Qt, pyqtSignal
from PyQt6.QtGui import QGuiApplication, QKeyEvent, QKeySequence
from PyQt6.QtWidgets import QApplication, QLineEdit, QWidget

from .logging_setup import get_logger

logger = get_logger("hotkey")

WM_HOTKEY = 0x0312
MOD_ALT = 0x0001
MOD_CONTROL = 0x0002
MOD_SHIFT = 0x0004
MOD_WIN = 0x0008
MOD_NOREPEAT = 0x4000
ERROR_HOTKEY_ALREADY_REGISTERED = 1409

SPECIAL_VK_MAP: dict[str, int] = {
    "SPACE": 0x20,
    "RETURN": 0x0D,
    "ENTER": 0x0D,
    "TAB": 0x09,
    "ESC": 0x1B,
    "ESCAPE": 0x1B,
    "UP": 0x26,
    "DOWN": 0x28,
    "LEFT": 0x25,
    "RIGHT": 0x27,
    "INSERT": 0x2D,
    "DELETE": 0x2E,
    "HOME": 0x24,
    "END": 0x23,
    "PGUP": 0x21,
    "PGDOWN": 0x22,
    **{f"F{i}": 0x6F + i for i in range(1, 13)},
}


def parse_hotkey_chord(chord: str) -> tuple[int, int, str] | None:
    """Parse `'Ctrl+Shift+D'` into `(win32_modifiers, win32_vk, pynput_spec)` or `None` if invalid."""
    raw = (chord or "").strip()
    if not raw:
        return None
    parts = [p.strip() for p in raw.split("+") if p.strip()]
    if len(parts) < 2:
        return None

    mods = 0
    pynput_parts: list[str] = []
    key_token = ""

    for token in parts:
        up = token.upper()
        if up in ("CTRL", "CONTROL"):
            mods |= MOD_CONTROL
            pynput_parts.append("<ctrl>")
        elif up == "SHIFT":
            mods |= MOD_SHIFT
            pynput_parts.append("<shift>")
        elif up in ("ALT", "OPTION"):
            mods |= MOD_ALT
            pynput_parts.append("<alt>")
        elif up in ("WIN", "META", "CMD", "SUPER"):
            mods |= MOD_WIN
            pynput_parts.append("<cmd>")
        else:
            key_token = up

    if not mods or not key_token:
        return None

    if len(key_token) == 1 and ("A" <= key_token <= "Z" or "0" <= key_token <= "9"):
        vk = ord(key_token)
        pynput_parts.append(key_token.lower())
    elif key_token in SPECIAL_VK_MAP:
        vk = SPECIAL_VK_MAP[key_token]
        pynput_parts.append(f"<{key_token.lower()}>")
    else:
        return None

    return mods, vk, "+".join(pynput_parts)


class _Win32HotkeyEventFilter(QAbstractNativeEventFilter):
    """Intercept `WM_HOTKEY (0x0312)` delivered to our bound `HWND`."""

    def __init__(self, manager: GlobalHotkeyManager) -> None:
        super().__init__()
        self._manager = manager

    def nativeEventFilter(self, eventType: Any, message: Any) -> tuple[bool, int]:  # noqa: N802, N803
        try:
            ev_bytes = bytes(eventType)
        except Exception:
            ev_bytes = b""
        if ev_bytes in (b"windows_generic_MSG", b"windows_dispatcher_MSG") and message:
            msg = wintypes.MSG.from_address(int(message))
            if msg.message == WM_HOTKEY:
                hk_id = int(msg.wParam)
                if self._manager._dispatch_id(hk_id):
                    return True, 0
        return False, 0


class GlobalHotkeyManager(QObject):
    """Cross-platform system-wide hotkey manager emitting `activated(action_name)` on the Qt UI thread."""

    activated = pyqtSignal(str)

    def __init__(self, window: QWidget, parent: QObject | None = None) -> None:
        super().__init__(parent or window)
        self._window = window
        self._id_to_action: dict[int, str] = {}
        self._registered_hwnd: int = 0
        self._conflicts: dict[str, str] = {}
        self._win_filter: _Win32HotkeyEventFilter | None = None
        self._pynput_listener: Any = None
        self._user32 = (
            ctypes.WinDLL("user32", use_last_error=True) if sys.platform.startswith("win") else None
        )

        if self._user32 is not None:
            app = QApplication.instance()
            if app is not None:
                self._win_filter = _Win32HotkeyEventFilter(self)
                app.installNativeEventFilter(self._win_filter)

    @property
    def conflicts(self) -> dict[str, str]:
        return dict(self._conflicts)

    def _dispatch_id(self, hk_id: int) -> bool:
        action = self._id_to_action.get(hk_id)
        if action:
            self.activated.emit(action)
            return True
        return False

    def register_all(self, bindings: dict[str, str], enabled: bool = True) -> dict[str, str]:
        """Register `{action: chord}` map. Returns `{action: conflict_reason}` for any occupied chords."""
        self.unregister_all()
        self._conflicts.clear()
        if not enabled:
            return {}

        platform_name = (QGuiApplication.platformName() or "").lower()
        if platform_name == "offscreen":
            # Offscreen test QPA has no OS desktop window station; skip native OS hook cleanly
            return {}

        if self._user32 is not None:
            return self._register_win32(bindings)
        return self._register_pynput(bindings)

    def _register_win32(self, bindings: dict[str, str]) -> dict[str, str]:
        assert self._user32 is not None
        hwnd = int(self._window.winId())
        self._registered_hwnd = hwnd

        next_id = 0x5644  # 'VD'
        for action, chord in bindings.items():
            if not (chord or "").strip():
                continue
            parsed = parse_hotkey_chord(chord)
            if not parsed:
                self._conflicts[action] = f"快捷键格式无效: {chord}"
                continue
            mods, vk, _ = parsed
            hk_id = next_id
            next_id += 1

            ctypes.set_last_error(0)
            ok = self._user32.RegisterHotKey(wintypes.HWND(hwnd), hk_id, mods | MOD_NOREPEAT, vk)
            if ok:
                self._id_to_action[hk_id] = action
                logger.info("Registered Win32 global hotkey: %s -> %s (id=%d)", action, chord, hk_id)
            else:
                err = ctypes.get_last_error()
                if err == ERROR_HOTKEY_ALREADY_REGISTERED:
                    reason = f"{chord} 已被系统中其他软件占用 (Win32 Error 1409)"
                else:
                    reason = f"{chord} 注册失败 (Win32 Error {err})"
                self._conflicts[action] = reason
                logger.warning("Global hotkey conflict for %s (%s): %s", action, chord, reason)

        return dict(self._conflicts)

    def _register_pynput(self, bindings: dict[str, str]) -> dict[str, str]:
        try:
            from pynput import keyboard as pynput_kb
        except Exception as exc:
            logger.warning("pynput unavailable for non-Windows global hotkeys: %s", exc)
            return {}

        mapping: dict[str, Any] = {}
        for action, chord in bindings.items():
            parsed = parse_hotkey_chord(chord)
            if not parsed:
                if (chord or "").strip():
                    self._conflicts[action] = f"快捷键格式无效: {chord}"
                continue
            _, _, spec = parsed
            mapping[spec] = lambda act=action: self.activated.emit(act)

        if mapping:
            try:
                self._pynput_listener = pynput_kb.GlobalHotKeys(mapping)
                self._pynput_listener.daemon = True
                self._pynput_listener.start()
                logger.info("Registered %d global hotkeys via pynput", len(mapping))
            except Exception as exc:
                self._conflicts["global"] = f"系统辅助功能权限未开启或监听器异常: {exc}"
        return dict(self._conflicts)

    def unregister_all(self) -> None:
        if self._user32 is not None and self._registered_hwnd and self._id_to_action:
            for hk_id in list(self._id_to_action.keys()):
                try:
                    self._user32.UnregisterHotKey(wintypes.HWND(self._registered_hwnd), hk_id)
                except Exception:
                    pass
        self._id_to_action.clear()

        if self._pynput_listener is not None:
            try:
                self._pynput_listener.stop()
            except Exception:
                pass
            self._pynput_listener = None

    def shutdown(self) -> None:
        self.unregister_all()
        if self._win_filter is not None:
            app = QApplication.instance()
            if app is not None:
                try:
                    app.removeNativeEventFilter(self._win_filter)
                except Exception:
                    pass
            self._win_filter = None


class HotkeyRecorderEdit(QLineEdit):
    """Interactive shortcut recorder input: click & press `Ctrl+Shift+X` (or `Backspace` to clear)."""

    chord_changed = pyqtSignal(str)

    def __init__(self, initial_chord: str = "", parent: QWidget | None = None) -> None:
        super().__init__(parent)
        self.setReadOnly(True)
        self.setText(initial_chord)
        self.setPlaceholderText("点击此处并按下组合键 (如 Ctrl+Shift+D)")
        self.setCursor(Qt.CursorShape.PointingHandCursor)

    def keyPressEvent(self, event: QKeyEvent) -> None:  # noqa: N802
        key = event.key()
        if key in (Qt.Key.Key_Backspace, Qt.Key.Key_Delete, Qt.Key.Key_Escape):
            self.setText("")
            self.chord_changed.emit("")
            event.accept()
            return

        if key in (
            Qt.Key.Key_Control,
            Qt.Key.Key_Shift,
            Qt.Key.Key_Alt,
            Qt.Key.Key_Meta,
        ):
            event.accept()
            return

        mods = event.modifiers()
        parts: list[str] = []
        if mods & Qt.KeyboardModifier.ControlModifier:
            parts.append("Ctrl")
        if mods & Qt.KeyboardModifier.AltModifier:
            parts.append("Alt")
        if mods & Qt.KeyboardModifier.ShiftModifier:
            parts.append("Shift")
        if mods & Qt.KeyboardModifier.MetaModifier:
            parts.append("Win")

        if not parts:
            event.accept()
            return

        seq_str = QKeySequence(key).toString(QKeySequence.SequenceFormat.PortableText).upper()
        if seq_str and parse_hotkey_chord("+".join([*parts, seq_str])):
            chord = "+".join([*parts, seq_str])
            self.setText(chord)
            self.chord_changed.emit(chord)
        event.accept()
