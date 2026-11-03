"""Single-instance lock & secondary-launch IPC wakeup via `QLocalServer` / `QLocalSocket`."""

from __future__ import annotations

import time

from PyQt6.QtCore import QCoreApplication, QObject, pyqtSignal
from PyQt6.QtNetwork import QLocalServer, QLocalSocket

from .logging_setup import get_logger

logger = get_logger("single_instance")

DEFAULT_SERVER_NAME = "VideoDownloader_SingleInstance_v3"


class SingleInstanceGuard(QObject):
    """Ensure only one `VideoDownloader` instance runs per user session and forward wakeup commands."""

    message_received = pyqtSignal(str)

    def __init__(self, server_name: str = DEFAULT_SERVER_NAME, parent: QObject | None = None) -> None:
        super().__init__(parent)
        self.server_name = server_name
        self._server: QLocalServer | None = None

    def try_acquire_or_notify(self, payload: str = "SHOW") -> bool:
        """Return `True` if this process owns the lock, or `False` after waking the existing instance."""
        probe = QLocalSocket()
        probe.connectToServer(self.server_name)
        if probe.waitForConnected(250):
            try:
                data = (payload or "SHOW").encode("utf-8")
                probe.write(data)
                probe.flush()
                probe.waitForBytesWritten(250)

                # Wait up to 400ms for primary instance ACK so fast `sys.exit(0)` never aborts unread pipe bytes
                app = QCoreApplication.instance()
                deadline = time.monotonic() + 0.4
                while time.monotonic() < deadline:
                    if app is not None:
                        app.processEvents()
                    if probe.bytesAvailable() > 0 or probe.waitForReadyRead(25):
                        _ = probe.readAll()
                        break

                probe.disconnectFromServer()
            except Exception:
                pass
            logger.info("Secondary instance forwarded '%s' to running primary instance", payload)
            return False
        probe.abort()

        # Clean up any stale pipe/socket left by a prior crash
        QLocalServer.removeServer(self.server_name)
        server = QLocalServer(self)
        server.newConnection.connect(self._on_new_connection)
        if not server.listen(self.server_name):
            QLocalServer.removeServer(self.server_name)
            if not server.listen(self.server_name):
                logger.warning("QLocalServer failed to listen on %s: %s", self.server_name, server.errorString())
                return True
        self._server = server
        return True

    def _on_new_connection(self) -> None:
        if not self._server:
            return
        while self._server.hasPendingConnections():
            conn = self._server.nextPendingConnection()
            if not conn:
                continue
            if conn.bytesAvailable() <= 0:
                conn.waitForReadyRead(250)
            raw = bytes(conn.readAll()).decode("utf-8", errors="replace").strip()
            try:
                conn.write(b"OK")
                conn.flush()
                conn.waitForBytesWritten(100)
            except Exception:
                pass
            conn.disconnectFromServer()
            conn.deleteLater()
            self.message_received.emit(raw or "SHOW")

    def close(self) -> None:
        if self._server:
            try:
                self._server.close()
                QLocalServer.removeServer(self.server_name)
            except Exception:
                pass
            self._server = None
