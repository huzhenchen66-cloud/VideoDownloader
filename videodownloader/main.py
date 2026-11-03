"""FastAPI application factory mounting REST API, WebSocket telemetry, and Web/iOS PWA static UI."""

from __future__ import annotations

from contextlib import asynccontextmanager
from pathlib import Path

from fastapi import FastAPI
from fastapi.middleware.cors import CORSMiddleware
from fastapi.responses import FileResponse, HTMLResponse
from fastapi.staticfiles import StaticFiles

from . import __version__
from .api.routes import router as api_router
from .api.ws import ws_router
from .core.db import Database
from .core.paths import get_web_static_dir
from .core.task_queue import TaskQueueManager


def create_app(
    db_path: Path | None = None,
    server_port: int = 8765,
    db: Database | None = None,
    queue_mgr: TaskQueueManager | None = None,
) -> FastAPI:
    owns_queue = queue_mgr is None
    shared_db = db or Database(db_path=db_path)
    shared_mgr = queue_mgr or TaskQueueManager(db=shared_db)

    @asynccontextmanager
    async def lifespan(app: FastAPI):
        shared_mgr.start()
        yield
        if owns_queue:
            shared_mgr.stop()

    app = FastAPI(
        title="VideoDownloader AI Workspace",
        description="Multi-Engine Cross-Platform Video Downloader, Native Desktop + Optional Web/Mobile & MCP Server.",
        version=__version__,
        lifespan=lifespan,
    )
    app.state.db = shared_db
    app.state.queue_mgr = shared_mgr
    app.state.server_port = server_port

    app.add_middleware(
        CORSMiddleware,
        allow_origins=["*"],
        allow_credentials=True,
        allow_methods=["*"],
        allow_headers=["*"],
    )

    app.include_router(api_router)
    app.include_router(ws_router)

    static_dir = get_web_static_dir()
    static_dir.mkdir(parents=True, exist_ok=True)
    app.mount("/static", StaticFiles(directory=str(static_dir)), name="static")

    @app.get("/manifest.json", include_in_schema=False)
    async def pwa_manifest():
        mf = static_dir / "manifest.json"
        return FileResponse(str(mf), media_type="application/manifest+json")

    @app.get("/sw.js", include_in_schema=False)
    async def pwa_sw():
        sw = static_dir / "sw.js"
        return FileResponse(str(sw), media_type="application/javascript")

    @app.get("/", include_in_schema=False)
    async def index_page():
        index_html = static_dir / "index.html"
        if index_html.exists():
            return FileResponse(str(index_html), media_type="text/html; charset=utf-8")
        return HTMLResponse("<h1>VideoDownloader Backend Running</h1>")

    return app


def get_default_app() -> FastAPI:
    return create_app()
