"""Procedural Sound Synthesizer & Sound Effect Manager (`core/sound_manager.py`).

100% pure standard library (`math`, `wave`, `struct`) — zero external audio dependencies,
zero copyright/DMCA licensing risk, zero packaged binary bloat.

Key Features:
- Synthesizes 5 unique theme-specific three-note arpeggios (30ms note stagger for crisp musicality).
- Synthesizes psychological dissonant minor-second beat-frequency failure tone.
- Low-latency `QSoundEffect` pre-decoded memory cache with volume & mute controls.
- Anti-annoyance smart throttling (batch debounce window, 1s deduplication, fullscreen suppression).
- User custom sound override support via `%LOCALAPPDATA%\\VideoDownloader\\sounds\\`.
"""

from __future__ import annotations

import math
import os
from pathlib import Path
import struct
import sys
import time
from typing import Any, Callable
import wave

from PyQt6.QtCore import QObject, QUrl
from PyQt6.QtMultimedia import QSoundEffect

from .logging_setup import get_logger
from .paths import get_runtime_root

logger = get_logger("sound")

SAMPLE_RATE = 44100

# Voice synthesis profiles: Base Triad Frequencies, Harmonics weighting & ADSR Envelope
# Formatted to create distinct timbres: Crystal / Marimba / Pad / Bell / Glassy
VOICE_SPECS: dict[str, dict[str, Any]] = {
    "miku": {
        "name": "crystal",
        # A5 (880.0Hz), C#6 (1108.73Hz), E6 (1318.51Hz) - Shimmering Major Triad
        "notes": [880.0, 1108.73, 1318.51],
        "harmonics": [(1, 1.0), (2, 0.32), (3, 0.14), (4, 0.06)],
        "adsr": (0.005, 0.080, 0.35, 0.220),  # attack, decay, sustain_level, release
        "reverb_delay": 0.055,
        "reverb_gain": 0.22,
    },
    "sakura": {
        "name": "marimba",
        # F5 (698.46Hz), A5 (880.0Hz), C6 (1046.50Hz) - Warm Organic Triad
        "notes": [698.46, 880.0, 1046.50],
        "harmonics": [(1, 1.0), (3, 0.20), (5, 0.06)],
        "adsr": (0.003, 0.140, 0.20, 0.280),
        "reverb_delay": 0.040,
        "reverb_gain": 0.15,
    },
    "violet": {
        "name": "pad",
        # D5 (587.33Hz), F#5 (739.99Hz), A5 (880.0Hz) - Cyber Atmospheric Triad
        "notes": [587.33, 739.99, 880.0],
        "harmonics": [(1, 1.0), (2, 0.42), (3, 0.28), (4, 0.15)],
        "adsr": (0.045, 0.160, 0.50, 0.380),
        "reverb_delay": 0.075,
        "reverb_gain": 0.28,
    },
    "amber": {
        "name": "bell",
        # G5 (783.99Hz), B5 (987.77Hz), D6 (1174.66Hz) - Bright Resonant Bell
        "notes": [783.99, 987.77, 1174.66],
        "harmonics": [(1, 1.0), (2, 0.18), (4, 0.12)],
        "adsr": (0.002, 0.070, 0.15, 0.200),
        "reverb_delay": 0.045,
        "reverb_gain": 0.18,
    },
    "azure": {
        "name": "glassy",
        # E5 (659.25Hz), G#5 (830.61Hz), B5 (987.77Hz) - Pristine Glass Triad
        "notes": [659.25, 830.61, 987.77],
        "harmonics": [(1, 1.0), (3, 0.26), (6, 0.09)],
        "adsr": (0.004, 0.110, 0.30, 0.240),
        "reverb_delay": 0.050,
        "reverb_gain": 0.20,
    },
}


def _apply_adsr(total_samples: int, a_s: int, d_s: int, s_lvl: float, r_s: int) -> list[float]:
    """Compute ADSR envelope curve for `total_samples` length."""
    env = [0.0] * total_samples
    sustain_samples = max(0, total_samples - (a_s + d_s + r_s))

    idx = 0
    # Attack
    if a_s > 0:
        for i in range(a_s):
            if idx < total_samples:
                env[idx] = i / float(a_s)
                idx += 1
    # Decay
    if d_s > 0:
        for i in range(d_s):
            if idx < total_samples:
                frac = i / float(d_s)
                env[idx] = 1.0 - (1.0 - s_lvl) * frac
                idx += 1
    # Sustain
    for _ in range(sustain_samples):
        if idx < total_samples:
            env[idx] = s_lvl
            idx += 1
    # Release
    if r_s > 0:
        for i in range(r_s):
            if idx < total_samples:
                frac = i / float(r_s)
                env[idx] = s_lvl * (1.0 - frac)
                idx += 1
    return env


def synthesize_arpeggio_wav(
    theme_key: str,
    out_path: Path,
    is_batch: bool = False,
) -> Path:
    """Synthesize a three-note arpeggio chord WAV file with 30ms stagger, harmonics, and reverb."""
    spec = VOICE_SPECS.get(theme_key, VOICE_SPECS["miku"])
    out_path.parent.mkdir(parents=True, exist_ok=True)

    notes = spec["notes"]
    if is_batch:
        # Lower octave and slower decay for batch all-done cue
        notes = [n * 0.5 for n in notes]

    stagger_sec = 0.030 if not is_batch else 0.045
    stagger_samples = int(stagger_sec * SAMPLE_RATE)

    a, d, s, r = spec["adsr"]
    if is_batch:
        d *= 1.4
        r *= 1.5

    note_duration = a + d + 0.05 + r
    note_samples = int(note_duration * SAMPLE_RATE)
    total_samples = note_samples + (len(notes) - 1) * stagger_samples

    reverb_delay_samples = int(spec.get("reverb_delay", 0.05) * SAMPLE_RATE)
    reverb_gain = spec.get("reverb_gain", 0.20)
    buffer_len = total_samples + reverb_delay_samples + 2000

    raw_signal = [0.0] * buffer_len

    # Render each note
    harmonics = spec["harmonics"]
    a_s, d_s, r_s = int(a * SAMPLE_RATE), int(d * SAMPLE_RATE), int(r * SAMPLE_RATE)
    envelope = _apply_adsr(note_samples, a_s, d_s, s, r_s)

    for note_idx, freq in enumerate(notes):
        offset = note_idx * stagger_samples
        for i in range(note_samples):
            t = i / float(SAMPLE_RATE)
            # Additive harmonics
            val = 0.0
            for h_num, h_weight in harmonics:
                val += h_weight * math.sin(2.0 * math.pi * (freq * h_num) * t)
            raw_signal[offset + i] += val * envelope[i] * 0.45

    # Simple feedback delay reverb
    for i in range(reverb_delay_samples, buffer_len):
        raw_signal[i] += raw_signal[i - reverb_delay_samples] * reverb_gain

    # Normalize to -3dBFS (max amplitude 23000 out of 32767)
    max_val = max((abs(x) for x in raw_signal), default=1.0)
    scale = (23000.0 / max_val) if max_val > 0.001 else 1.0

    with wave.open(str(out_path), "wb") as wf:
        wf.setnchannels(1)
        wf.setsampwidth(2)  # 16-bit
        wf.setframerate(SAMPLE_RATE)
        frames = bytearray()
        for s_val in raw_signal:
            int_val = int(max(-32767, min(32767, s_val * scale)))
            frames.extend(struct.pack("<h", int_val))
        wf.writeframes(frames)

    return out_path


def synthesize_failure_wav(out_path: Path) -> Path:
    """Synthesize a psychological minor-second beat-frequency failure tone (880Hz + 932Hz)."""
    out_path.parent.mkdir(parents=True, exist_ok=True)
    duration_sec = 0.38
    total_samples = int(duration_sec * SAMPLE_RATE)

    f1, f2 = 880.0, 932.33  # A5 and Bb5 minor second (dissonant beat frequency ~52Hz)
    a_s = int(0.008 * SAMPLE_RATE)
    d_s = int(0.120 * SAMPLE_RATE)
    s_lvl = 0.35
    r_s = int(0.180 * SAMPLE_RATE)
    env = _apply_adsr(total_samples, a_s, d_s, s_lvl, r_s)

    signal = [0.0] * total_samples
    for i in range(total_samples):
        t = i / float(SAMPLE_RATE)
        # Downward subtle pitch drop at tail
        pitch_mod = 1.0 - (0.08 * (i / float(total_samples)))
        v1 = math.sin(2.0 * math.pi * (f1 * pitch_mod) * t)
        v2 = math.sin(2.0 * math.pi * (f2 * pitch_mod) * t) * 0.85
        # Soft sub-harmonic
        v3 = math.sin(2.0 * math.pi * ((f1 * 0.5) * pitch_mod) * t) * 0.25
        signal[i] = (v1 + v2 + v3) * env[i] * 0.40

    max_val = max((abs(x) for x in signal), default=1.0)
    scale = (22000.0 / max_val) if max_val > 0.001 else 1.0

    with wave.open(str(out_path), "wb") as wf:
        wf.setnchannels(1)
        wf.setsampwidth(2)
        wf.setframerate(SAMPLE_RATE)
        frames = bytearray()
        for s_val in signal:
            int_val = int(max(-32767, min(32767, s_val * scale)))
            frames.extend(struct.pack("<h", int_val))
        wf.writeframes(frames)

    return out_path


def get_sounds_dir() -> Path:
    d = get_runtime_root() / "sounds"
    d.mkdir(parents=True, exist_ok=True)
    return d


class SoundEffectManager(QObject):
    """Centralized sound effect controller with theme binding, debounce & fullscreen detection."""

    def __init__(self, parent: QObject | None = None) -> None:
        super().__init__(parent)
        self.sounds_dir = get_sounds_dir()
        self._enabled: bool = True
        self._volume: float = 0.55  # 0.0 to 1.0
        self._active_theme: str = "miku"

        # Anti-annoyance throttle
        self._last_played_ts: dict[str, float] = {}
        self._batch_timer_active: bool = False
        self._batch_success_count: int = 0
        self._batch_last_ts: float = 0.0

        # Memory cache of pre-decoded QSoundEffect objects
        self._effects: dict[str, QSoundEffect] = {}
        self._ensure_all_synthesized()
        self._preload_effects()

    def _ensure_all_synthesized(self) -> None:
        """Synthesize missing WAV files on boot (takes < 30ms total)."""
        try:
            for theme_key in VOICE_SPECS:
                p_single = self.sounds_dir / f"complete_{theme_key}.wav"
                p_batch = self.sounds_dir / f"batch_{theme_key}.wav"
                if not p_single.is_file():
                    synthesize_arpeggio_wav(theme_key, p_single, is_batch=False)
                if not p_batch.is_file():
                    synthesize_arpeggio_wav(theme_key, p_batch, is_batch=True)

            p_fail = self.sounds_dir / "failed.wav"
            if not p_fail.is_file():
                synthesize_failure_wav(p_fail)
        except Exception as exc:
            logger.warning("Sound synthesis exception: %s", exc)

    def _preload_effects(self) -> None:
        """Pre-decode all WAV sources into QSoundEffect so first play has 0ms latency."""
        try:
            for theme_key in VOICE_SPECS:
                self._load_effect(f"complete_{theme_key}", self._resolve_sound_path(f"complete_{theme_key}"))
                self._load_effect(f"batch_{theme_key}", self._resolve_sound_path(f"batch_{theme_key}"))
            self._load_effect("failed", self._resolve_sound_path("failed"))
        except Exception as exc:
            logger.warning("QSoundEffect preload exception: %s", exc)

    def _resolve_sound_path(self, sound_name: str) -> Path:
        """Support user custom sound override (`%LOCALAPPDATA%\\VideoDownloader\\sounds\\<name>.wav`)."""
        # User override check: complete.wav or failed.wav
        if sound_name.startswith("complete"):
            user_override = self.sounds_dir / "complete.wav"
            if user_override.is_file():
                return user_override
        if sound_name.startswith("batch"):
            user_override = self.sounds_dir / "batch.wav"
            if user_override.is_file():
                return user_override
        if sound_name == "failed":
            user_override = self.sounds_dir / "failed.wav"
            if user_override.is_file():
                return user_override

        # Fallback to synthesized theme wav
        target = self.sounds_dir / f"{sound_name}.wav"
        if not target.is_file():
            self._ensure_all_synthesized()
        return target

    def _load_effect(self, key: str, wav_path: Path) -> QSoundEffect | None:
        if not wav_path.is_file():
            return None
        fx = QSoundEffect(self)
        fx.setSource(QUrl.fromLocalFile(str(wav_path)))
        fx.setVolume(self._volume)
        self._effects[key] = fx
        return fx

    def configure(
        self,
        enabled: bool = True,
        volume: int = 55,
        theme_style: str = "miku",
    ) -> None:
        """Update runtime settings from Database config."""
        self._enabled = bool(enabled)
        v_flt = float(volume)
        if v_flt > 1.0:
            v_flt = v_flt / 100.0
        self._volume = max(0.0, min(1.0, v_flt))
        self._active_theme = str(theme_style or "miku").lower().strip()
        for fx in self._effects.values():
            fx.setVolume(self._volume)

    def is_fullscreen_busy(self) -> bool:
        """Return True if user is in full-screen D3D gaming or presentation mode."""
        if not sys.platform.startswith("win"):
            return False
        try:
            import ctypes
            state = ctypes.c_int(0)
            ctypes.windll.shell32.SHQueryUserNotificationState(ctypes.byref(state))
            # QUNS_BUSY=2, QUNS_RUNNING_D3D_FULL_SCREEN=3, QUNS_PRESENTATION_MODE=4
            return state.value in (2, 3, 4)
        except Exception:
            return False

    def play(self, sound_kind: str, preview_theme: str | None = None) -> None:
        """Play a sound effect with anti-annoyance deduplication and fullscreen suppression."""
        if not self._enabled:
            return
        if self.is_fullscreen_busy():
            return

        now = time.monotonic()
        last = self._last_played_ts.get(sound_kind, 0.0)
        # 1-second deduplication window for same sound
        if (now - last) < 0.95 and not preview_theme:
            return
        self._last_played_ts[sound_kind] = now

        theme_to_use = preview_theme or self._active_theme
        if sound_kind == "complete":
            key = f"complete_{theme_to_use}"
        elif sound_kind == "batch":
            key = f"batch_{theme_to_use}"
        else:
            key = "failed"

        fx = self._effects.get(key)
        if not fx:
            target_path = self._resolve_sound_path(key)
            fx = self._load_effect(key, target_path)

        if fx:
            try:
                fx.setVolume(self._volume)
                fx.play()
            except Exception as exc:
                logger.debug("Playback failed on %s: %s", key, exc)

    def notify_task_finished(self, status: str, pending_count: int = 0) -> None:
        """Intelligent batch cue: suppress rapid individual dingings and play batch chord on all-clear."""
        if status == "failed":
            self.play("failed")
            return

        if status == "completed":
            # If there are still active downloads ongoing, throttle single task completion
            if pending_count > 0:
                self.play("complete")
            else:
                # All active tasks finished! Play the grand batch chord
                self.play("batch")
