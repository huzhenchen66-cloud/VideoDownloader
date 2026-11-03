"""RESTful API endpoints for multi-engine downloading, iOS Safari Range streaming, and AI Video Copilot."""

from __future__ import annotations

import asyncio
import mimetypes
import os
import subprocess
import sys
from pathlib import Path
from typing import Any
from urllib.parse import quote, urlparse

import requests
from fastapi import APIRouter, File, HTTPException, Query, Request, Response, UploadFile
from fastapi.responses import StreamingResponse
from pydantic import BaseModel, Field

from .. import __version__
from ..ai.llm_service import (
    LLM_PROVIDER_PRESETS,
    ask_video_question,
    summarize_video_transcript,
)
from ..ai.transcript import extract_transcript_for_task, parse_subtitle_text
from ..core.binary_updater import get_toolchain_status, hot_update_ytdlp_binary
from ..core.lan_helper import build_lan_pairing_info
from ..core.os_utils import open_with_system_player, resolve_task_media_path, reveal_in_file_manager
from ..core.paths import get_data_dir

router = APIRouter(prefix="/api")


# -------------------- Request Schemas --------------------


class ParseRequest(BaseModel):
    text: str = Field(..., min_length=3, description="Video URL or dirty clipboard share text")


class CreateTaskRequest(BaseModel):
    text: str = Field(..., min_length=3)
    quality: str | None = None
    only_audio: bool | None = None
    audio_format: str | None = None
    subtitle: bool | None = None
    prefetched_title: str = ""
    prefetched_thumbnail: str = ""
    prefetched_duration: float = 0.0
    prefetched_uploader: str = ""


class OpenPathRequest(BaseModel):
    task_id: str | None = None
    reveal_file: bool = True


class AIAskRequest(BaseModel):
    question: str = Field(..., min_length=1)
    history: list[dict[str, str]] = Field(default_factory=list)


class ShortcutRequest(BaseModel):
    text: str
    quality: str | None = None
    only_audio: bool | None = None


# -------------------- Helpers --------------------


def _get_mgr(request: Request):
    return request.app.state.queue_mgr


def _get_db(request: Request):
    return request.app.state.db


def _guess_media_mime(path: str) -> str:
    ext = Path(path).suffix.lower()
    mapping = {
        ".mp4": "video/mp4",
        ".m4v": "video/mp4",
        ".webm": "video/webm",
        ".mkv": "video/x-matroska",
        ".mov": "video/quicktime",
        ".mp3": "audio/mpeg",
        ".m4a": "audio/mp4",
        ".wav": "audio/wav",
        ".flac": "audio/flac",
        ".ogg": "audio/ogg",
    }
    return mapping.get(ext) or mimetypes.guess_type(path)[0] or "application/octet-stream"


# -------------------- System & LAN Pairing --------------------


@router.get("/system/info")
async def api_system_info(request: Request) -> dict[str, Any]:
    port = int(getattr(request.app.state, "server_port", 8765))
    mgr = _get_mgr(request)
    toolchain = await asyncio.to_thread(get_toolchain_status)
    lan = await asyncio.to_thread(build_lan_pairing_info, port)
    return {
        "version": __version__,
        "toolchain": toolchain,
        "lan": lan,
        "engine_telemetry": mgr.engine_chain.telemetry,
        "llm_presets": LLM_PROVIDER_PRESETS,
    }


@router.post("/system/update-ytdlp")
async def api_update_ytdlp() -> dict[str, Any]:
    try:
        status = await asyncio.to_thread(hot_update_ytdlp_binary)
        return {"ok": True, "toolchain": status}
    except Exception as exc:
        raise HTTPException(status_code=500, detail=f"热更新 yt-dlp 失败: {exc}") from exc


@router.post("/system/open-folder")
async def api_open_folder(payload: OpenPathRequest, request: Request) -> dict[str, Any]:
    db = _get_db(request)
    save_dir = db.get_settings()["save_path"]
    target = save_dir
    select_file = False

    if payload.task_id:
        task = db.get_task(payload.task_id)
        if task and task.get("file_path"):
            resolved = resolve_task_media_path(task["file_path"], save_dir)
            if resolved:
                target = resolved if payload.reveal_file else os.path.dirname(resolved)
                select_file = payload.reveal_file

    try:
        opened = await asyncio.to_thread(reveal_in_file_manager, target, select_file)
    except FileNotFoundError as exc:
        raise HTTPException(status_code=404, detail=str(exc)) from exc
    except Exception as exc:
        raise HTTPException(status_code=500, detail=f"无法在宿主系统打开目录: {exc}") from exc

    return {"ok": True, "opened": opened}


@router.post("/system/play-external")
async def api_play_external(payload: OpenPathRequest, request: Request) -> dict[str, Any]:
    if not payload.task_id:
        raise HTTPException(status_code=400, detail="缺少 task_id")
    db = _get_db(request)
    save_dir = db.get_settings()["save_path"]
    task = db.get_task(payload.task_id)
    resolved = resolve_task_media_path((task or {}).get("file_path") or "", save_dir)
    if not resolved:
        raise HTTPException(status_code=404, detail="本地媒体文件不存在")
    try:
        await asyncio.to_thread(open_with_system_player, resolved)
    except Exception as exc:
        raise HTTPException(status_code=500, detail=f"调用系统播放器失败: {exc}") from exc
    return {"ok": True, "played": resolved}


# -------------------- Settings & Cookies --------------------


@router.get("/settings")
async def api_get_settings(request: Request) -> dict[str, Any]:
    return _get_db(request).get_settings()


@router.put("/settings")
async def api_update_settings(patch: dict[str, Any], request: Request) -> dict[str, Any]:
    db = _get_db(request)
    if "save_path" in patch and patch["save_path"]:
        try:
            os.makedirs(patch["save_path"], exist_ok=True)
        except Exception as exc:
            raise HTTPException(status_code=400, detail=f"保存目录无效或无写入权限: {exc}") from exc
    return db.update_settings(patch)


@router.post("/settings/upload-cookie")
async def api_upload_cookie(request: Request, file: UploadFile = File(...)) -> dict[str, Any]:
    content = await file.read()
    if not content:
        raise HTTPException(status_code=400, detail="上传的 cookies.txt 文件为空")
    cookie_dest = get_data_dir() / "cookies.txt"
    cookie_dest.write_bytes(content)
    updated = _get_db(request).update_settings({"cookie_file": str(cookie_dest)})
    return {"ok": True, "cookie_file": str(cookie_dest), "settings": updated}


# -------------------- Parse & Download Queue --------------------


@router.post("/parse")
async def api_parse_url(payload: ParseRequest, request: Request) -> dict[str, Any]:
    mgr = _get_mgr(request)
    try:
        return await asyncio.to_thread(mgr.parse_input, payload.text)
    except ValueError as exc:
        raise HTTPException(status_code=400, detail=str(exc)) from exc
    except Exception as exc:
        raise HTTPException(status_code=422, detail=str(exc)) from exc


@router.get("/tasks")
async def api_list_tasks(request: Request, limit: int = Query(200, ge=1, le=1000)) -> dict[str, Any]:
    tasks = _get_db(request).list_tasks(limit=limit)
    return {"tasks": tasks}


@router.post("/tasks")
async def api_create_tasks(payload: CreateTaskRequest, request: Request) -> dict[str, Any]:
    mgr = _get_mgr(request)
    overrides = {
        "quality": payload.quality,
        "only_audio": payload.only_audio,
        "audio_format": payload.audio_format,
        "subtitle": payload.subtitle,
    }
    try:
        created = await asyncio.to_thread(
            mgr.submit_tasks,
            payload.text,
            overrides,
            payload.prefetched_title,
            payload.prefetched_thumbnail,
            payload.prefetched_duration,
            payload.prefetched_uploader,
        )
        return {"ok": True, "tasks": created}
    except ValueError as exc:
        raise HTTPException(status_code=400, detail=str(exc)) from exc


@router.post("/tasks/scan-local")
async def api_scan_local_folder(request: Request) -> dict[str, Any]:
    mgr = _get_mgr(request)
    return await asyncio.to_thread(mgr.scan_local_folder)


@router.post("/tasks/{task_id}/pause")
async def api_pause_task(task_id: str, request: Request) -> dict[str, Any]:
    res = await asyncio.to_thread(_get_mgr(request).pause_task, task_id)
    if not res:
        raise HTTPException(status_code=404, detail="任务不存在")
    return {"ok": True, "task": res}


@router.post("/tasks/{task_id}/resume")
async def api_resume_task(task_id: str, request: Request) -> dict[str, Any]:
    res = await asyncio.to_thread(_get_mgr(request).resume_task, task_id)
    if not res:
        raise HTTPException(status_code=404, detail="任务不存在")
    return {"ok": True, "task": res}


@router.post("/tasks/{task_id}/cancel")
async def api_cancel_task(task_id: str, request: Request) -> dict[str, Any]:
    res = await asyncio.to_thread(_get_mgr(request).cancel_task, task_id)
    if not res:
        raise HTTPException(status_code=404, detail="任务不存在")
    return {"ok": True, "task": res}


@router.delete("/tasks/{task_id}")
async def api_delete_task(
    task_id: str,
    request: Request,
    delete_file: bool = Query(False, description="Also delete local downloaded file from disk"),
) -> dict[str, Any]:
    ok = await asyncio.to_thread(_get_mgr(request).delete_task, task_id, delete_file)
    if not ok:
        raise HTTPException(status_code=404, detail="任务不存在")
    return {"ok": True}


# -------------------- iOS Shortcuts 1-Tap Delivery --------------------


@router.post("/shortcut")
async def api_ios_shortcut_post(payload: ShortcutRequest, request: Request) -> dict[str, Any]:
    mgr = _get_mgr(request)
    tasks = await asyncio.to_thread(
        mgr.submit_tasks,
        payload.text,
        {"quality": payload.quality, "only_audio": payload.only_audio},
    )
    return {"ok": True, "message": f"已成功投递 {len(tasks)} 个任务至电脑队列", "tasks": tasks}


@router.get("/shortcut")
async def api_ios_shortcut_get(text: str = Query(...), request: Request = None) -> dict[str, Any]:  # type: ignore[assignment]
    mgr = _get_mgr(request)
    tasks = await asyncio.to_thread(mgr.submit_tasks, text)
    return {"ok": True, "message": f"已成功投递 {len(tasks)} 个任务至电脑队列", "tasks": tasks}


# -------------------- iPhone Safari Range Streaming & Direct Attachment Download --------------------


@router.get("/media/{task_id}/stream")
async def api_stream_media(task_id: str, request: Request):
    """RFC 7233 `206 Partial Content` byte-range streaming (mandatory for iOS Safari `<video>` playback & seeking)."""
    db = _get_db(request)
    task = db.get_task(task_id)
    save_dir = db.get_settings()["save_path"]
    file_path = resolve_task_media_path((task or {}).get("file_path") or "", save_dir)
    if not task or not file_path:
        raise HTTPException(status_code=404, detail="本地媒体文件不存在或尚未下载完成")
    file_size = os.path.getsize(file_path)
    mime_type = _guess_media_mime(file_path)
    range_header = request.headers.get("range") or request.headers.get("Range")

    start = 0
    end = file_size - 1
    status_code = 200

    if range_header and range_header.strip().lower().startswith("bytes="):
        status_code = 206
        range_spec = range_header.strip()[6:].split(",")[0].strip()
        if "-" in range_spec:
            s_part, e_part = range_spec.split("-", 1)
            if s_part == "" and e_part.isdigit():
                suffix_len = int(e_part)
                start = max(0, file_size - suffix_len)
            else:
                if s_part.isdigit():
                    start = int(s_part)
                if e_part.isdigit():
                    end = min(file_size - 1, int(e_part))
        if start >= file_size or start > end:
            return Response(
                status_code=416,
                headers={"Content-Range": f"bytes */{file_size}"},
            )

    content_length = end - start + 1

    def _file_iterator(path: str, offset: int, length: int, chunk_size: int = 256 * 1024):
        remaining = length
        with open(path, "rb") as fp:
            fp.seek(offset)
            while remaining > 0:
                data = fp.read(min(chunk_size, remaining))
                if not data:
                    break
                remaining -= len(data)
                yield data

    headers = {
        "Accept-Ranges": "bytes",
        "Content-Length": str(content_length),
        "Cache-Control": "no-cache",
    }
    if status_code == 206:
        headers["Content-Range"] = f"bytes {start}-{end}/{file_size}"

    return StreamingResponse(
        _file_iterator(file_path, start, content_length),
        status_code=status_code,
        media_type=mime_type,
        headers=headers,
    )


@router.get("/media/{task_id}/download")
async def api_download_to_device(task_id: str, request: Request):
    """Force browser / iPhone Safari file download sheet (`Content-Disposition: attachment`) to save into iPhone Files/Photos."""
    db = _get_db(request)
    task = db.get_task(task_id)
    save_dir = db.get_settings()["save_path"]
    file_path = resolve_task_media_path((task or {}).get("file_path") or "", save_dir)
    if not task or not file_path:
        raise HTTPException(status_code=404, detail="文件尚未就绪或已被移除")
    filename = os.path.basename(file_path)
    ascii_fallback = f"media_{task_id}{Path(filename).suffix}"
    encoded_utf8 = quote(filename)

    def _iter_full(path: str):
        with open(path, "rb") as fp:
            while chunk := fp.read(512 * 1024):
                yield chunk

    headers = {
        "Content-Length": str(os.path.getsize(file_path)),
        "Content-Disposition": f"attachment; filename=\"{ascii_fallback}\"; filename*=UTF-8''{encoded_utf8}",
    }
    return StreamingResponse(
        _iter_full(file_path),
        media_type="application/octet-stream",
        headers=headers,
    )


# -------------------- Thumbnail Anti-Hotlink Proxy --------------------


@router.get("/proxy/image")
async def api_proxy_image(url: str = Query(..., min_length=8)):
    """Proxy Bilibili / Douyin / Xiaohongshu cover images to prevent browser Referer 403 blocks."""
    if not url.startswith(("http://", "https://")):
        raise HTTPException(status_code=400, detail="Invalid image URL")

    host = (urlparse(url).netloc or "").lower()
    referer = ""
    if "hdslb.com" in host or "bilibili.com" in host:
        referer = "https://www.bilibili.com/"
    elif "xhscdn.com" in host or "xiaohongshu.com" in host:
        referer = "https://www.xiaohongshu.com/"
    elif "douyinpic.com" in host or "byteimg.com" in host:
        referer = "https://www.douyin.com/"

    def _fetch() -> tuple[bytes, str]:
        headers = {
            "User-Agent": "Mozilla/5.0 (Windows NT 10.0; Win64; x64) AppleWebKit/537.36 Chrome/131.0.0.0 Safari/537.36"
        }
        if referer:
            headers["Referer"] = referer
        r = requests.get(url, headers=headers, timeout=10)
        r.raise_for_status()
        return r.content, r.headers.get("Content-Type", "image/jpeg")

    try:
        data, ctype = await asyncio.to_thread(_fetch)
        return Response(content=data, media_type=ctype, headers={"Cache-Control": "public, max-age=86400"})
    except Exception as exc:
        raise HTTPException(status_code=502, detail=f"Image proxy error: {exc}") from exc


# -------------------- AI Video Knowledge Copilot --------------------


@router.post("/tasks/{task_id}/ai/transcript")
async def api_extract_transcript(task_id: str, request: Request, force: bool = Query(False)) -> dict[str, Any]:
    db = _get_db(request)
    task = db.get_task(task_id)
    if not task:
        raise HTTPException(status_code=404, detail="任务不存在")

    if not force and task.get("transcript") and task["transcript"].get("segments"):
        return {"ok": True, "transcript": task["transcript"]}

    settings = db.get_settings()
    try:
        transcript = await asyncio.to_thread(extract_transcript_for_task, task, settings)
        updated = db.update_task(task_id, transcript=transcript)
        if updated:
            _get_mgr(request).broadcast("task_updated", updated)
        return {"ok": True, "transcript": transcript}
    except Exception as exc:
        raise HTTPException(status_code=422, detail=str(exc)) from exc


@router.post("/tasks/{task_id}/ai/upload-subtitle")
async def api_upload_subtitle(task_id: str, request: Request, file: UploadFile = File(...)) -> dict[str, Any]:
    db = _get_db(request)
    task = db.get_task(task_id)
    if not task:
        raise HTTPException(status_code=404, detail="任务不存在")

    raw_bytes = await file.read()
    text = ""
    for enc in ("utf-8-sig", "utf-8", "gbk"):
        try:
            text = raw_bytes.decode(enc)
            break
        except UnicodeDecodeError:
            continue
    segments = parse_subtitle_text(text)
    if not segments:
        raise HTTPException(status_code=400, detail="未能从上传的字幕文件中解析出有效时间轴文本（支持 .srt / .vtt / .json）")

    transcript = {"source": "uploaded_subtitle", "segments": segments}
    updated = db.update_task(task_id, transcript=transcript)
    if updated:
        _get_mgr(request).broadcast("task_updated", updated)
    return {"ok": True, "transcript": transcript}


@router.post("/tasks/{task_id}/ai/summarize")
async def api_ai_summarize(task_id: str, request: Request, force: bool = Query(False)) -> dict[str, Any]:
    db = _get_db(request)
    task = db.get_task(task_id)
    if not task:
        raise HTTPException(status_code=404, detail="任务不存在")

    settings = db.get_settings()
    transcript = task.get("transcript")
    if not transcript or not transcript.get("segments"):
        try:
            transcript = await asyncio.to_thread(extract_transcript_for_task, task, settings)
            db.update_task(task_id, transcript=transcript)
        except Exception as exc:
            raise HTTPException(status_code=422, detail=str(exc)) from exc

    if not force and task.get("ai_summary") and (
        task["ai_summary"].get("mode") == "llm" or not settings.get("llm_api_key")
    ):
        return {"ok": True, "summary": task["ai_summary"], "transcript": transcript}

    try:
        summary = await asyncio.to_thread(
            summarize_video_transcript,
            task["title"],
            transcript["segments"],
            settings,
        )
        updated = db.update_task(task_id, ai_summary=summary)
        if updated:
            _get_mgr(request).broadcast("task_updated", updated)
        return {"ok": True, "summary": summary, "transcript": transcript}
    except Exception as exc:
        raise HTTPException(status_code=500, detail=f"AI 总结生成失败: {exc}") from exc


@router.post("/tasks/{task_id}/ai/ask")
async def api_ai_ask(task_id: str, payload: AIAskRequest, request: Request) -> dict[str, Any]:
    db = _get_db(request)
    task = db.get_task(task_id)
    if not task:
        raise HTTPException(status_code=404, detail="任务不存在")

    settings = db.get_settings()
    transcript = task.get("transcript")
    if not transcript or not transcript.get("segments"):
        try:
            transcript = await asyncio.to_thread(extract_transcript_for_task, task, settings)
            db.update_task(task_id, transcript=transcript)
        except Exception as exc:
            raise HTTPException(status_code=422, detail=str(exc)) from exc

    try:
        reply = await asyncio.to_thread(
            ask_video_question,
            task["title"],
            transcript["segments"],
            payload.question,
            settings,
            payload.history,
        )
        return {"ok": True, **reply}
    except Exception as exc:
        raise HTTPException(status_code=500, detail=f"AI 问答请求失败: {exc}") from exc
