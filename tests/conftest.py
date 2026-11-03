"""Pytest session configuration keeping a single persistent QApplication alive across all GUI tests."""

from __future__ import annotations

import os

import pytest

if os.path.isdir(r"C:\Windows\Fonts"):
    os.environ.setdefault("QT_QPA_FONTDIR", r"C:\Windows\Fonts")
os.environ.setdefault("QT_QPA_PLATFORM", "offscreen")

_SESSION_QAPP = None


@pytest.fixture(scope="session", autouse=True)
def persistent_qapplication():
    """Keep a strong Python reference to QApplication for the entire pytest run so PyQt6 SIP GC never deletes Qt singletons."""
    global _SESSION_QAPP
    try:
        from PyQt6.QtWidgets import QApplication
    except ImportError:
        yield None
        return

    if _SESSION_QAPP is None:
        _SESSION_QAPP = QApplication.instance() or QApplication([])
    yield _SESSION_QAPP
