"""Startable / stoppable background Web & iPhone LAN server controller for the Native Desktop App."""

from __future__ import annotations

import socket
import threading
import time
from typing import Any

import uvicorn

from ..main import create_app
from .db import Database
from .lan_helper import build_lan_pairing_info
from .task_queue import TaskQueueManager


def is_port_in_use(port: int) -> bool:
    with socket.socket(socket.AF_INET, socket.SOCK_STREAM) as s:
        s.settimeout(0.25)
        return s.connect_ex(("127.0.0.1", port)) == 0


def find_free_port(preferred: int = 8765) -> int:
    if not is_port_in_use(preferred):
        return preferred
    for p in range(preferred + 1, preferred + 40):
        if not is_port_in_use(p):
            return p
    return preferred


class EmbeddedWebServerManager:
    """Manages the optional FastAPI/Uvicorn Web & iPhone interconnection server in a daemon thread."""

    def __init__(self, db: Database, queue_mgr: TaskQueueManager) -> None:
        self.db = db
        self.queue_mgr = queue_mgr
        self._server: uvicorn.Server | None = None
        self._thread: threading.Thread | None = None
        self._port: int = int(self.db.get_settings().get("web_server_port", 8765) or 8765)
        self._lock = threading.Lock()

    @property
    def is_running(self) -> bool:
        return bool(self._server and self._thread and self._thread.is_alive() and not self._server.should_exit)

    @property
    def port(self) -> int:
        return self._port

    @property
    def local_url(self) -> str:
        return f"http://127.0.0.1:{self._port}"

    def get_lan_info(self) -> dict[str, Any]:
        return build_lan_pairing_info(self._port)

    def start(self, preferred_port: int | None = None) -> str:
        with self._lock:
            if self.is_running:
                return self.local_url

            base_port = preferred_port or int(self.db.get_settings().get("web_server_port", 8765) or 8765)
            self._port = find_free_port(base_port)
            fastapi_app = create_app(
                server_port=self._port,
                db=self.db,
                queue_mgr=self.queue_mgr,
            )
            config = uvicorn.Config(
                app=fastapi_app,
                host="0.0.0.0",
                port=self._port,
                log_level="warning",
                access_log=False,
            )
            server = uvicorn.Server(config)
            # Disable signal handlers so Uvicorn runs cleanly inside a background thread alongside PyQt6
            server.install_signal_handlers = lambda: None  # type: ignore[method-assign]
            self._server = server

            t = threading.Thread(target=server.run, daemon=True, name="vd-optional-webserver")
            self._thread = t
            t.start()

        for _ in range(30):
            if is_port_in_use(self._port):
                break
            time.sleep(0.1)
        return self.local_url

    def stop(self) -> None:
        with self._lock:
            server = self._server
            thread = self._thread
            if not server:
                return
            server.should_exit = True

        if thread and thread.is_alive():
            thread.join(timeout=3.0)

        with self._lock:
            self._server = None
            self._thread = None
