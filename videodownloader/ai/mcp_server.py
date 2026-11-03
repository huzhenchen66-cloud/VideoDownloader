"""Standard Model Context Protocol (MCP) Server over stdio (JSON-RPC 2.0).

Allows AI Agents (Cursor, Claude Desktop, Windsurf, Cherry Studio) to invoke VideoDownloader tools:
  - `parse_video_url`: Clean messy share text and inspect video metadata/formats
  - `download_video`: Enqueue a video or audio download task
  - `get_video_transcript`: Extract timestamped subtitles/transcript from a video URL or task ID
  - `summarize_video`: Generate structured AI timeline summary and chapters for a video

Usage in Cursor / Claude Desktop `mcp.json`:
{
  "mcpServers": {
    "videodownloader": {
      "command": "python",
      "args": ["-m", "videodownloader.ai.mcp_server"]
    }
  }
}
"""

from __future__ import annotations

import json
import sys
from typing import Any

from .. import __version__
from ..core.db import Database
from ..core.task_queue import TaskQueueManager
from .llm_service import summarize_video_transcript
from .transcript import extract_transcript_for_task, fetch_remote_subtitles_via_ytdlp

MCP_TOOLS: list[dict[str, Any]] = [
    {
        "name": "parse_video_url",
        "description": "Clean mobile share text (Douyin/Bilibili/Xiaohongshu/YouTube/etc.) and parse video metadata, duration, and available formats.",
        "inputSchema": {
            "type": "object",
            "properties": {
                "url_or_text": {
                    "type": "string",
                    "description": "Video URL or messy clipboard share text containing a URL.",
                }
            },
            "required": ["url_or_text"],
        },
    },
    {
        "name": "download_video",
        "description": "Submit a video or audio-only download task to VideoDownloader's multi-engine queue.",
        "inputSchema": {
            "type": "object",
            "properties": {
                "url_or_text": {"type": "string", "description": "Video URL or share text."},
                "quality": {
                    "type": "string",
                    "enum": ["best_4k", "best_1080p", "fast_720p", "save_480p", "best_mkv"],
                    "default": "best_1080p",
                },
                "only_audio": {"type": "boolean", "default": False},
            },
            "required": ["url_or_text"],
        },
    },
    {
        "name": "get_video_transcript",
        "description": "Extract timestamped subtitles / transcript segments ([MM:SS] text) from a video URL or downloaded task_id.",
        "inputSchema": {
            "type": "object",
            "properties": {
                "url": {"type": "string", "description": "Direct video page URL (e.g. Bilibili / YouTube)."},
                "task_id": {"type": "string", "description": "Optional existing VideoDownloader task ID."},
            },
        },
    },
    {
        "name": "summarize_video",
        "description": "Extract video subtitles and generate a structured AI timeline summary with clickable chapter timestamps.",
        "inputSchema": {
            "type": "object",
            "properties": {
                "url": {"type": "string", "description": "Video URL to analyze."},
                "task_id": {"type": "string", "description": "Optional existing VideoDownloader task ID."},
            },
        },
    },
]


class VideoDownloaderMCPServer:
    """Dependency-free JSON-RPC 2.0 MCP Server compatible with MCP specification 2024-11-05."""

    def __init__(self, db: Database | None = None) -> None:
        self.db = db or Database()
        self.queue_mgr = TaskQueueManager(self.db)

    def handle_request(self, req: dict[str, Any]) -> dict[str, Any] | None:
        method = req.get("method", "")
        req_id = req.get("id")
        params = req.get("params") or {}

        # Notifications have no `id` and expect no response
        if req_id is None and method.startswith("notifications/"):
            return None

        try:
            if method == "initialize":
                result = {
                    "protocolVersion": "2024-11-05",
                    "capabilities": {"tools": {}},
                    "serverInfo": {"name": "videodownloader-mcp", "version": __version__},
                }
            elif method == "ping":
                result = {}
            elif method == "tools/list":
                result = {"tools": MCP_TOOLS}
            elif method == "tools/call":
                result = self._call_tool(params.get("name", ""), params.get("arguments") or {})
            else:
                return {
                    "jsonrpc": "2.0",
                    "id": req_id,
                    "error": {"code": -32601, "message": f"Method not found: {method}"},
                }
            return {"jsonrpc": "2.0", "id": req_id, "result": result}
        except Exception as exc:
            return {
                "jsonrpc": "2.0",
                "id": req_id,
                "result": {
                    "isError": True,
                    "content": [{"type": "text", "text": f"Error: {exc}"}],
                },
            }

    def _call_tool(self, name: str, args: dict[str, Any]) -> dict[str, Any]:
        settings = self.db.get_settings()

        if name == "parse_video_url":
            parsed = self.queue_mgr.parse_input(args["url_or_text"])
            return {"content": [{"type": "text", "text": json.dumps(parsed, ensure_ascii=False, indent=2)}]}

        if name == "download_video":
            tasks = self.queue_mgr.submit_tasks(
                args["url_or_text"],
                overrides={
                    "quality": args.get("quality", "best_1080p"),
                    "only_audio": bool(args.get("only_audio", False)),
                },
            )
            return {"content": [{"type": "text", "text": json.dumps({"submitted": tasks}, ensure_ascii=False, indent=2)}]}

        if name == "get_video_transcript":
            segs = self._resolve_segments(args, settings)
            formatted = "\n".join(f"[{s['time_str']}] {s['text']}" for s in segs[:300])
            return {"content": [{"type": "text", "text": formatted or "No subtitles available."}]}

        if name == "summarize_video":
            title = args.get("url") or args.get("task_id") or "Video"
            if args.get("task_id"):
                t = self.db.get_task(args["task_id"])
                if t:
                    title = t["title"]
            segs = self._resolve_segments(args, settings)
            summary = summarize_video_transcript(title=title, segments=segs, settings=settings)
            return {"content": [{"type": "text", "text": json.dumps(summary, ensure_ascii=False, indent=2)}]}

        raise ValueError(f"Unknown tool: {name}")

    def _resolve_segments(self, args: dict[str, Any], settings: dict[str, Any]) -> list[dict[str, Any]]:
        if args.get("task_id"):
            task = self.db.get_task(args["task_id"])
            if not task:
                raise ValueError(f"Task not found: {args['task_id']}")
            if task.get("transcript") and task["transcript"].get("segments"):
                return task["transcript"]["segments"]
            res = extract_transcript_for_task(task, settings)
            self.db.update_task(task["id"], transcript=res)
            return res["segments"]

        url = args.get("url") or ""
        if not url:
            raise ValueError("Provide either `url` or `task_id`.")
        segs = fetch_remote_subtitles_via_ytdlp(
            url,
            cookie_file=settings.get("cookie_file", ""),
            browser_cookie=settings.get("browser_cookie", "none"),
        )
        if not segs:
            raise RuntimeError("No native subtitles found for this URL.")
        return segs

    def serve_stdio(self) -> None:
        for line in sys.stdin:
            line = line.strip()
            if not line:
                continue
            try:
                req = json.loads(line)
            except json.JSONDecodeError:
                continue
            resp = self.handle_request(req)
            if resp is not None:
                sys.stdout.write(json.dumps(resp, ensure_ascii=False) + "\n")
                sys.stdout.flush()


def main() -> None:
    server = VideoDownloaderMCPServer()
    server.serve_stdio()


if __name__ == "__main__":
    main()
