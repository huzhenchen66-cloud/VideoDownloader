"""Multi-source timestamped video transcript extractor (Local SRT/VTT + Remote yt-dlp + Cloud Whisper/SenseVoice ASR)."""

from __future__ import annotations

import html
import json
import os
import re
import subprocess
import sys
import tempfile
from pathlib import Path
from typing import Any

import requests
import yt_dlp

from ..core.binary_updater import resolve_ffmpeg_path
from ..core.os_utils import resolve_proxy_for_platform, resolve_task_media_path
from ..core.url_cleaner import detect_platform

TIMECODE_RE = re.compile(
    r"(?:(\d{1,2}):)?(\d{2}):(\d{2})[.,](\d{1,3})\s*-->\s*(?:(\d{1,2}):)?(\d{2}):(\d{2})[.,](\d{1,3})"
)
HTML_TAG_RE = re.compile(r"<[^>]+>")
ASS_BRACE_RE = re.compile(r"\{[^}]+\}")


def format_mmss(seconds: float) -> str:
    sec = max(0, int(seconds))
    if sec >= 3600:
        return f"{sec // 3600:02d}:{(sec % 3600) // 60:02d}:{sec % 60:02d}"
    return f"{sec // 60:02d}:{sec % 60:02d}"


def _tc_to_seconds(h: str | None, m: str, s: str, ms: str) -> float:
    hours = int(h) if h else 0
    mins = int(m)
    secs = int(s)
    millis = int(ms.ljust(3, "0")[:3])
    return hours * 3600 + mins * 60 + secs + millis / 1000.0


def parse_subtitle_text(content: str) -> list[dict[str, Any]]:
    """Parse SRT, WebVTT, or JSON3 subtitle text into deduplicated timestamped segments."""
    raw = (content or "").strip()
    if not raw:
        return []

    # Check JSON3 / Bilibili JSON subtitle formats
    if raw.startswith("{"):
        try:
            data = json.loads(raw)
            if "events" in data:  # YouTube json3
                segs: list[dict[str, Any]] = []
                for ev in data.get("events") or []:
                    t_start = float(ev.get("tStartMs") or 0) / 1000.0
                    dur = float(ev.get("dDurationMs") or 3000) / 1000.0
                    txt = "".join(s.get("utf8", "") for s in (ev.get("segs") or [])).strip()
                    txt = re.sub(r"\s+", " ", txt)
                    if txt and (not segs or segs[-1]["text"] != txt):
                        segs.append(
                            {
                                "start": round(t_start, 2),
                                "end": round(t_start + dur, 2),
                                "time_str": format_mmss(t_start),
                                "text": txt,
                            }
                        )
                if segs:
                    return segs
            if "body" in data and isinstance(data["body"], list):  # Bilibili JSON subtitle
                segs = []
                for item in data["body"]:
                    t_start = float(item.get("from") or 0)
                    t_end = float(item.get("to") or (t_start + 3))
                    txt = str(item.get("content") or "").strip()
                    if txt:
                        segs.append(
                            {
                                "start": round(t_start, 2),
                                "end": round(t_end, 2),
                                "time_str": format_mmss(t_start),
                                "text": txt,
                            }
                        )
                if segs:
                    return segs
        except Exception:
            pass

    # Standard SRT / WebVTT parser
    lines = raw.splitlines()
    segments: list[dict[str, Any]] = []
    cur_start: float | None = None
    cur_end: float = 0.0
    cur_lines: list[str] = []

    def _flush() -> None:
        nonlocal cur_start, cur_lines
        if cur_start is None or not cur_lines:
            cur_start = None
            cur_lines = []
            return
        joined = " ".join(cur_lines)
        joined = HTML_TAG_RE.sub("", joined)
        joined = ASS_BRACE_RE.sub("", joined)
        joined = html.unescape(re.sub(r"\s+", " ", joined)).strip()
        if joined:
            if not segments or segments[-1]["text"] != joined:
                segments.append(
                    {
                        "start": round(cur_start, 2),
                        "end": round(cur_end, 2),
                        "time_str": format_mmss(cur_start),
                        "text": joined,
                    }
                )
        cur_start = None
        cur_lines = []

    for line in lines:
        s = line.strip()
        if not s:
            _flush()
            continue
        if s.startswith("WEBVTT") or s.startswith("NOTE") or s.startswith("Kind:") or s.startswith("Language:"):
            continue
        m = TIMECODE_RE.search(s)
        if m:
            _flush()
            cur_start = _tc_to_seconds(m.group(1), m.group(2), m.group(3), m.group(4))
            cur_end = _tc_to_seconds(m.group(5), m.group(6), m.group(7), m.group(8))
            continue
        if cur_start is not None and not s.isdigit():
            cur_lines.append(s)

    _flush()
    return segments


def parse_subtitle_file(path: str) -> list[dict[str, Any]]:
    if not path or not os.path.exists(path):
        return []
    for enc in ("utf-8-sig", "utf-8", "gbk"):
        try:
            with open(path, "r", encoding=enc) as fp:
                return parse_subtitle_text(fp.read())
        except UnicodeDecodeError:
            continue
        except Exception:
            break
    return []


def _sec_to_srt_time(sec_float: float) -> str:
    sec = max(0.0, float(sec_float))
    total_secs = int(sec)
    millis = int(round((sec - total_secs) * 1000))
    if millis >= 1000:
        total_secs += 1
        millis = 0
    hours = total_secs // 3600
    mins = (total_secs % 3600) // 60
    secs = total_secs % 60
    return f"{hours:02d}:{mins:02d}:{secs:02d},{millis:03d}"


def segments_to_srt_text(segments: list[dict[str, Any]]) -> str:
    """Format timestamped subtitle segments into standard SRT file format."""
    lines = []
    for idx, s in enumerate(segments, 1):
        st = float(s.get("start") or 0.0)
        ed = float(s.get("end") or (st + 3.0))
        txt = str(s.get("text") or "").strip()
        lines.append(str(idx))
        lines.append(f"{_sec_to_srt_time(st)} --> {_sec_to_srt_time(ed)}")
        lines.append(txt)
        lines.append("")
    return "\n".join(lines)


def fetch_bilibili_subtitles(
    url: str,
    cookie_file: str = "",
) -> tuple[list[dict[str, Any]], str, str]:
    """Fetch Bilibili subtitles (both human CC and AI auto-generated ai-zh) directly from Bilibili API.

    Returns:
        tuple[list[dict[str, Any]], str, str]: (segments, description, raw_title)
    """
    if not url:
        return [], "", ""

    bv_match = re.search(r"(?:BV|bv)([0-9a-zA-Z]{10})", url)
    av_match = re.search(r"(?:av|AV)(\d+)", url)
    if not bv_match and not av_match:
        return [], "", ""

    query_param = f"bvid=BV{bv_match.group(1)}" if bv_match else f"aid={av_match.group(1)}"

    headers = {
        "User-Agent": (
            "Mozilla/5.0 (Windows NT 10.0; Win64; x64) "
            "AppleWebKit/537.36 (KHTML, like Gecko) Chrome/131.0.0.0 Safari/537.36"
        ),
        "Referer": "https://www.bilibili.com/",
    }

    try:
        from ..core.bilibili_helper import ensure_bilibili_bootstrap_cookie
        spi_cookie = ensure_bilibili_bootstrap_cookie()
        if spi_cookie and os.path.exists(spi_cookie):
            with open(spi_cookie, "r", encoding="utf-8", errors="ignore") as fp:
                c_lines = []
                for line in fp:
                    if line.startswith("#") or not line.strip():
                        continue
                    parts = line.strip().split("\t")
                    if len(parts) >= 7:
                        c_lines.append(f"{parts[5]}={parts[6]}")
                if c_lines:
                    headers["Cookie"] = "; ".join(c_lines)
    except Exception:
        pass

    session = requests.Session()
    session.headers.update(headers)

    try:
        view_url = f"https://api.bilibili.com/x/web-interface/view?{query_param}"
        resp = session.get(view_url, timeout=10)
        resp.raise_for_status()
        view_data = resp.json().get("data") or {}

        aid = view_data.get("aid")
        cid = view_data.get("cid")
        title = str(view_data.get("title") or "").strip()
        desc = str(view_data.get("desc") or "").strip()

        if not aid or not cid:
            return [], desc, title

        dm_url = f"https://api.bilibili.com/x/v2/dm/view?type=1&oid={cid}&pid={aid}"
        resp_dm = session.get(dm_url, timeout=10)
        resp_dm.raise_for_status()
        dm_data = resp_dm.json().get("data") or {}

        subs = dm_data.get("subtitle", {}).get("subtitles") or []
        if not subs:
            return [], desc, title

        def _sub_priority(s: dict[str, Any]) -> int:
            lan = str(s.get("lan") or "").lower()
            if lan in ("zh-hans", "zh-cn", "zh"):
                return 0
            if "ai-zh" in lan:
                return 1
            if lan.startswith("zh"):
                return 2
            if lan.startswith("ai-"):
                return 3
            return 4

        sorted_subs = sorted(subs, key=_sub_priority)
        chosen_sub = sorted_subs[0]
        sub_url = str(chosen_sub.get("subtitle_url") or "").strip()
        if not sub_url:
            return [], desc, title
        if sub_url.startswith("//"):
            sub_url = "https:" + sub_url

        sub_resp = session.get(sub_url, timeout=12)
        sub_resp.raise_for_status()
        sub_payload = sub_resp.json()

        segments: list[dict[str, Any]] = []
        for item in sub_payload.get("body", []):
            t_start = float(item.get("from") or 0.0)
            t_end = float(item.get("to") or (t_start + 3.0))
            txt = str(item.get("content") or "").strip()
            if txt:
                segments.append(
                    {
                        "start": round(t_start, 2),
                        "end": round(t_end, 2),
                        "time_str": format_mmss(t_start),
                        "text": txt,
                    }
                )

        return segments, desc, title

    except Exception:
        return [], "", ""


def fetch_remote_subtitles_via_ytdlp(
    url: str,
    cookie_file: str = "",
    browser_cookie: str = "none",
    proxy_mode: str = "auto",
    proxy_url: str = "",
) -> list[dict[str, Any]]:
    """Extract native or auto-generated subtitles directly from URL without downloading the video."""
    if not url or url.startswith("file://"):
        return []

    pid, _, _ = detect_platform(url)
    resolved_proxy = resolve_proxy_for_platform(pid, proxy_mode, proxy_url)

    with tempfile.TemporaryDirectory(prefix="vd_sub_") as tmpdir:
        # Attempt 1: Native/manual subtitles (zh/en); Attempt 2: Original-language auto-captions (avoids YouTube tlang 429)
        attempts = [
            (False, ["zh-Hans", "zh-CN", "zh", "zh-TW", "en", "en-US"]),
            (True, ["en", "zh-Hans", "zh"]),
        ]
        for auto_sub, langs in attempts:
            opts: dict[str, Any] = {
                "quiet": True,
                "no_warnings": True,
                "ignoreerrors": True,
                "skip_download": True,
                "writesubtitles": not auto_sub,
                "writeautomaticsub": auto_sub,
                "subtitleslangs": langs,
                "subtitlesformat": "srt/vtt/json3/best",
                "outtmpl": os.path.join(tmpdir, "sub.%(ext)s"),
                "noplaylist": True,
                "nocheckcertificate": True,
            }
            if resolved_proxy is not None:
                opts["proxy"] = resolved_proxy
            if cookie_file and os.path.exists(cookie_file):
                opts["cookiefile"] = cookie_file
            elif browser_cookie and browser_cookie.lower() not in ("none", ""):
                opts["cookiesfrombrowser"] = (browser_cookie.lower(),)

            try:
                with yt_dlp.YoutubeDL(opts) as ydl:
                    ydl.extract_info(url, download=True)
            except Exception:
                pass

            files = sorted(Path(tmpdir).glob("sub*"), key=lambda p: p.stat().st_size, reverse=True)
            for f in files:
                segs = parse_subtitle_file(str(f))
                if segs:
                    return segs
    return []


def transcribe_local_media_via_asr(
    media_path: str,
    asr_base_url: str,
    asr_api_key: str,
    asr_model: str = "FunAudioLLM/SenseVoiceSmall",
) -> list[dict[str, Any]]:
    """Extract lightweight 16kHz audio via FFmpeg and call OpenAI-compatible `/audio/transcriptions` ASR."""
    if not media_path or not os.path.exists(media_path):
        raise ValueError("本地媒体文件不存在，无法进行语音转写")
    if not asr_api_key:
        raise ValueError("该视频无内置字幕，如需 AI 语音转写请在【设置】中配置 ASR API Key（如硅基流动免费 SenseVoice / OpenAI Whisper）")

    ffmpeg_path = resolve_ffmpeg_path()
    if not ffmpeg_path:
        raise RuntimeError("未检测到 FFmpeg，无法提取音轨进行 ASR 转写")

    with tempfile.TemporaryDirectory(prefix="vd_asr_") as tmpdir:
        audio_clip = os.path.join(tmpdir, "speech.mp3")
        creationflags = subprocess.CREATE_NO_WINDOW if sys.platform.startswith("win") and hasattr(subprocess, "CREATE_NO_WINDOW") else 0
        # Compress to 16kHz mono 32kbps MP3 (keeps 30-min video under ~7MB for fast API upload!)
        cmd = [
            ffmpeg_path, "-y", "-i", media_path,
            "-vn", "-ac", "1", "-ar", "16000", "-b:a", "32k",
            audio_clip,
        ]
        res = subprocess.run(cmd, stdout=subprocess.PIPE, stderr=subprocess.STDOUT, creationflags=creationflags)
        if res.returncode != 0 or not os.path.exists(audio_clip):
            raise RuntimeError("FFmpeg 提取音轨失败")

        endpoint = asr_base_url.rstrip("/") + "/audio/transcriptions"
        headers = {"Authorization": f"Bearer {asr_api_key}"}

        with open(audio_clip, "rb") as fp:
            resp = requests.post(
                endpoint,
                headers=headers,
                files={"file": ("speech.mp3", fp, "audio/mpeg")},
                data={"model": asr_model, "response_format": "verbose_json"},
                timeout=180,
            )

        if resp.status_code >= 400:
            # Retry with standard `json` format (some providers like SenseVoice only accept `json`)
            with open(audio_clip, "rb") as fp:
                resp = requests.post(
                    endpoint,
                    headers=headers,
                    files={"file": ("speech.mp3", fp, "audio/mpeg")},
                    data={"model": asr_model},
                    timeout=180,
                )
        resp.raise_for_status()
        payload = resp.json()

    if isinstance(payload.get("segments"), list) and payload["segments"]:
        out: list[dict[str, Any]] = []
        for s in payload["segments"]:
            st = float(s.get("start") or 0)
            ed = float(s.get("end") or (st + 4))
            txt = str(s.get("text") or "").strip()
            if txt:
                out.append({"start": round(st, 2), "end": round(ed, 2), "time_str": format_mmss(st), "text": txt})
        if out:
            return out

    full_text = str(payload.get("text") or "").strip()
    if not full_text:
        raise RuntimeError("ASR 接口未返回有效文本")

    # Split plain ASR text into readable sentence segments
    sentences = [p.strip() for p in re.split(r"[。！？.!?;\n]+", full_text) if p.strip()]
    return [
        {
            "start": float(idx * 5),
            "end": float((idx + 1) * 5),
            "time_str": format_mmss(idx * 5),
            "text": sent,
        }
        for idx, sent in enumerate(sentences)
    ]


def parse_raw_text_to_segments(raw_text: str, total_duration: float = 0.0) -> list[dict[str, Any]]:
    """Parse unstructured plain text (e.g. video copy / narration) into timestamped segments."""
    cleaned = (raw_text or "").strip()
    if not cleaned:
        return []
    # If it's already in SRT/VTT format, parse directly
    if "-->" in cleaned or (cleaned.startswith("{") and ("events" in cleaned or "body" in cleaned)):
        parsed = parse_subtitle_text(cleaned)
        if parsed:
            return parsed

    # Check for [00:15] or 01:23 timestamped line format
    lines = [ln.strip() for ln in cleaned.splitlines() if ln.strip()]
    ts_pattern = re.compile(r"^\[?(\d{1,2}):(\d{2})(?::(\d{2}))?\]?\s*(.*)$")
    custom_segs: list[dict[str, Any]] = []
    for ln in lines:
        m = ts_pattern.match(ln)
        if m:
            h = m.group(1) if m.group(3) else None
            mins = m.group(1) if not m.group(3) else m.group(2)
            secs = m.group(2) if not m.group(3) else m.group(3)
            txt = m.group(4).strip()
            sec_val = (int(h) * 3600 if h else 0) + int(mins) * 60 + int(secs)
            if txt:
                custom_segs.append({
                    "start": float(sec_val),
                    "end": float(sec_val + 5),
                    "time_str": format_mmss(sec_val),
                    "text": txt,
                })
    if len(custom_segs) >= 2:
        for i in range(len(custom_segs) - 1):
            custom_segs[i]["end"] = max(custom_segs[i]["start"] + 1.0, custom_segs[i + 1]["start"])
        return custom_segs

    # Free-form narrative paragraphs / sentences
    sentences = [p.strip() for p in re.split(r"[。！？!?\n\r]+", cleaned) if p.strip()]
    if not sentences:
        sentences = [cleaned[:120]]

    dur = max(float(total_duration or 0), float(len(sentences) * 5))
    step = dur / max(1, len(sentences))
    return [
        {
            "start": round(idx * step, 2),
            "end": round(min(dur, (idx + 1) * step), 2),
            "time_str": format_mmss(idx * step),
            "text": s,
        }
        for idx, s in enumerate(sentences)
    ]


def extract_transcript_for_task(task: dict[str, Any], settings: dict[str, Any]) -> dict[str, Any]:
    """Unified 5-tier transcript pipeline: User Custom -> Local Subtitle -> Companion File -> Remote yt-dlp -> Cloud ASR -> Metadata Fallback."""
    # Tier 0: Custom user-provided script or pasted transcription
    custom_script = task.get("custom_script") or task.get("manual_transcript") or ""
    if custom_script.strip():
        segs = parse_raw_text_to_segments(custom_script, float(task.get("duration") or 0.0))
        if segs:
            return {"source": "user_custom", "segments": segs, "note": "用户自定义/导入文案台词"}

    # Tier 1: Already saved subtitle path
    sub_path = task.get("subtitle_path") or ""
    if sub_path and os.path.exists(sub_path):
        segs = parse_subtitle_file(sub_path)
        if segs:
            return {"source": "local_subtitle", "segments": segs}

    # Tier 2: Companion subtitle next to downloaded media file
    save_dir = settings.get("save_path", "")
    file_path = resolve_task_media_path(task.get("file_path") or "", save_dir) or (task.get("file_path") or "")
    if file_path and os.path.exists(file_path):
        p = Path(file_path)
        for cand in p.parent.glob(f"{p.stem[:30]}*"):
            if cand.suffix.lower() in (".srt", ".vtt"):
                segs = parse_subtitle_file(str(cand))
                if segs:
                    return {"source": "companion_subtitle", "segments": segs}

    url = task.get("url") or ""

    # Tier 2.5: Direct Bilibili AI & CC Captions (Handles BV1nYx86pEeA and all Bilibili AI auto-generated subtitles)
    if url and ("bilibili.com" in url or "b23.tv" in url or re.search(r"(?:BV|bv)[0-9a-zA-Z]{10}", url)):
        try:
            bili_segs, bili_desc, bili_title = fetch_bilibili_subtitles(url, cookie_file=settings.get("cookie_file", ""))
            if bili_desc and not task.get("description"):
                task["description"] = bili_desc
            if bili_segs:
                if file_path and os.path.exists(file_path):
                    srt_file = Path(file_path).with_suffix(".srt")
                    if not srt_file.exists():
                        try:
                            srt_file.write_text(segments_to_srt_text(bili_segs), encoding="utf-8")
                            task["subtitle_path"] = str(srt_file)
                        except Exception:
                            pass
                return {
                    "source": "remote_subtitle",
                    "segments": bili_segs,
                    "note": f"B站官方/AI字幕 (共 {len(bili_segs)} 条台词)",
                }
        except Exception:
            pass

    # Tier 3: Remote zero-download subtitle fetch via yt-dlp
    if url.startswith("http"):
        segs = fetch_remote_subtitles_via_ytdlp(
            url,
            cookie_file=settings.get("cookie_file", ""),
            browser_cookie=settings.get("browser_cookie", "none"),
            proxy_mode=settings.get("proxy_mode", "auto"),
            proxy_url=settings.get("proxy_url", ""),
        )
        if segs:
            if file_path and os.path.exists(file_path):
                srt_file = Path(file_path).with_suffix(".srt")
                if not srt_file.exists():
                    try:
                        srt_file.write_text(segments_to_srt_text(segs), encoding="utf-8")
                        task["subtitle_path"] = str(srt_file)
                    except Exception:
                        pass
            return {"source": "remote_subtitle", "segments": segs}

    # Tier 4: Cloud Whisper / SenseVoice ASR on downloaded local media
    if file_path and os.path.exists(file_path) and settings.get("asr_api_key"):
        try:
            segs = transcribe_local_media_via_asr(
                media_path=file_path,
                asr_base_url=settings.get("asr_base_url", "https://api.siliconflow.cn/v1"),
                asr_api_key=settings["asr_api_key"],
                asr_model=settings.get("asr_model", "FunAudioLLM/SenseVoiceSmall"),
            )
            if segs:
                return {"source": "cloud_asr", "segments": segs}
        except Exception:
            pass

    # Tier 5: Metadata & Description Narrative Fallback (Guaranteed to succeed for short-form videos with no CC captions)
    desc = (task.get("description") or "").strip()
    title = (task.get("title") or "短视频").strip()
    duration = float(task.get("duration") or 0.0)

    if desc and len(desc) >= 8:
        segs = parse_raw_text_to_segments(desc, duration)
        if segs:
            return {
                "source": "metadata_description",
                "segments": segs,
                "is_fallback": True,
                "note": "该视频无独立字幕轨道，已自动提取视频简介文案生成分镜时间轴。",
            }

    # Fallback to Title & Creator info
    uploader = task.get("uploader") or ""
    plat = task.get("platform_name") or ""
    synth_text = f"《{title}》"
    if uploader:
        synth_text += f" - 作者: {uploader}"
    if plat:
        synth_text += f" [{plat}]"

    fallback_segs = [
        {
            "start": 0.0,
            "end": max(5.0, duration or 30.0),
            "time_str": "00:00",
            "text": synth_text,
        }
    ]
    return {
        "source": "metadata_title",
        "segments": fallback_segs,
        "is_fallback": True,
        "note": "该视频无独立字幕文件，已载入视频主题。可直接点击【拆解分镜脚本】或【补充台词文案】。",
    }
