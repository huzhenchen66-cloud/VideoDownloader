"""System Tray Controller with `QPainter` dynamic circular progress ring (throttled `>= 2%` delta)."""

from __future__ import annotations

from typing import Any, Callable

from PyQt6.QtCore import QObject, QRectF, Qt, pyqtSignal
from PyQt6.QtGui import QAction, QColor, QGuiApplication, QIcon, QLinearGradient, QPainter, QPen, QPixmap
from PyQt6.QtWidgets import QMenu, QSystemTrayIcon, QWidget

from .. import APP_NAME
from ..core.os_utils import reveal_in_file_manager
from ..core.paths import get_assets_dir
from ..core.task_queue import TaskQueueManager


class SystemTrayController(QObject):
    """Resident system tray icon subscribed directly to `TaskQueueManager` progress events."""

    _queue_signal = pyqtSignal(str, object)

    def __init__(
        self,
        parent_window: QWidget,
        queue_mgr: TaskQueueManager,
        on_show_main: Callable[[], None],
        on_quick_capture: Callable[[], None],
        on_paste_download: Callable[[], None],
        on_toggle_pause_all: Callable[[], None],
        on_open_downloads: Callable[[], None],
        on_quit_app: Callable[[], None],
    ) -> None:
        super().__init__(parent_window)
        self._window = parent_window
        self._queue_mgr = queue_mgr
        self._on_show_main = on_show_main
        self._on_quick_capture = on_quick_capture
        self._on_paste_download = on_paste_download
        self._on_toggle_pause_all = on_toggle_pause_all
        self._on_open_downloads = on_open_downloads
        self._on_quit_app = on_quit_app

        self._last_rendered_pct: float = -10.0
        self._last_rendered_state: str = ""
        self._last_completed_file: str = ""

        icon_path = get_assets_dir() / "icon.png"
        self._base_pixmap = QPixmap(str(icon_path)) if icon_path.exists() else QPixmap(64, 64)
        if self._base_pixmap.isNull():
            self._base_pixmap = QPixmap(64, 64)
            self._base_pixmap.fill(QColor("#3B82F6"))

        self.tray = QSystemTrayIcon(QIcon(self._base_pixmap), parent_window)
        self.tray.setToolTip(APP_NAME)
        self._build_menu()

        self.tray.activated.connect(self._on_activated)
        self.tray.messageClicked.connect(self._on_notification_clicked)

        self._queue_signal.connect(self._on_queue_event)
        self._queue_mgr.subscribe_sync(lambda ev, payload: self._queue_signal.emit(ev, payload))

        platform_name = (QGuiApplication.platformName() or "").lower()
        if platform_name != "offscreen" and QSystemTrayIcon.isSystemTrayAvailable():
            self.tray.show()

        self.refresh_ring(force=True)

    def _build_menu(self) -> None:
        menu = QMenu()
        act_show = QAction(f"打开 {APP_NAME}", menu)
        act_show.triggered.connect(self._on_show_main)
        menu.addAction(act_show)

        act_cap = QAction("快速捕获链接 (Ctrl+Shift+D)", menu)
        act_cap.triggered.connect(self._on_quick_capture)
        menu.addAction(act_cap)

        act_paste = QAction("粘贴剪贴板并立即下载", menu)
        act_paste.triggered.connect(self._on_paste_download)
        menu.addAction(act_paste)

        menu.addSeparator()

        self._act_pause = QAction("暂停 / 恢复全部任务 (Ctrl+Shift+P)", menu)
        self._act_pause.triggered.connect(self._on_toggle_pause_all)
        menu.addAction(self._act_pause)

        act_dir = QAction("打开下载文件夹", menu)
        act_dir.triggered.connect(self._on_open_downloads)
        menu.addAction(act_dir)

        menu.addSeparator()

        act_quit = QAction("退出", menu)
        act_quit.triggered.connect(self._on_quit_app)
        menu.addAction(act_quit)

        self.tray.setContextMenu(menu)

    def _on_activated(self, reason: QSystemTrayIcon.ActivationReason) -> None:
        if reason in (
            QSystemTrayIcon.ActivationReason.Trigger,
            QSystemTrayIcon.ActivationReason.DoubleClick,
        ):
            self._on_show_main()

    def _on_notification_clicked(self) -> None:
        if self._last_completed_file:
            try:
                reveal_in_file_manager(self._last_completed_file, select_file=True)
                return
            except Exception:
                pass
        self._on_show_main()

    def _on_queue_event(self, event_type: str, payload: object) -> None:
        if event_type == "task_completed" and isinstance(payload, dict):
            self._last_completed_file = str(payload.get("file_path") or "")
            title = str(payload.get("title") or "视频")[:48]
            self.notify("下载完成", f"《{title}》已保存，点击可在文件夹中定位。")
            self.refresh_ring(force=True)
            return
        self.refresh_ring(force=(event_type in ("task_created", "task_deleted")))

    def notify(self, title: str, message: str, ms: int = 3500) -> None:
        if self.tray.isVisible():
            self.tray.showMessage(title, message, QIcon(self._base_pixmap), ms)

    def render_progress_pixmap(self, state: str, progress: float = 0.0) -> QPixmap:
        """Draw 64x64 tray icon with live circular progress arc (`downloading`), error dot, or clean idle."""
        canvas = QPixmap(64, 64)
        canvas.fill(Qt.GlobalColor.transparent)

        p = QPainter(canvas)
        p.setRenderHint(QPainter.RenderHint.Antialiasing, True)
        p.setRenderHint(QPainter.RenderHint.SmoothPixmapTransform, True)

        if state == "downloading":
            # Inset base icon slightly to give room for crisp outer ring
            inner = self._base_pixmap.scaled(
                46, 46, Qt.AspectRatioMode.KeepAspectRatio, Qt.TransformationMode.SmoothTransformation
            )
            p.setOpacity(0.88)
            p.drawPixmap(9, 9, inner)
            p.setOpacity(1.0)

            ring_rect = QRectF(4.0, 4.0, 56.0, 56.0)
            track_pen = QPen(QColor(148, 163, 184, 90), 5.2)
            p.setPen(track_pen)
            p.drawEllipse(ring_rect)

            grad = QLinearGradient(0, 0, 64, 64)
            grad.setColorAt(0.0, QColor("#22D3EE"))
            grad.setColorAt(1.0, QColor("#3B82F6"))
            arc_pen = QPen(grad, 5.6, Qt.PenStyle.SolidLine, Qt.PenCapStyle.RoundCap)
            p.setPen(arc_pen)
            clamped = max(4.0, min(100.0, progress))
            span_angle = int(-clamped * 3.6 * 16)
            p.drawArc(ring_rect, 90 * 16, span_angle)
        else:
            full = self._base_pixmap.scaled(
                58, 58, Qt.AspectRatioMode.KeepAspectRatio, Qt.TransformationMode.SmoothTransformation
            )
            p.drawPixmap(3, 3, full)
            if state == "error":
                p.setPen(QPen(QColor("#FFFFFF"), 2.0))
                p.setBrush(QColor("#EF4444"))
                p.drawEllipse(QRectF(44.0, 44.0, 16.0, 16.0))

        p.end()
        return canvas

    def refresh_ring(self, force: bool = False) -> None:
        agg: dict[str, Any] = self._queue_mgr.get_aggregate_progress()
        active_cnt = int(agg["active_count"])
        pct = float(agg["progress"])
        has_err = bool(agg["has_error"])

        state = "downloading" if active_cnt > 0 else ("error" if has_err else "idle")

        # 2% delta throttle so tray icon updates never burn CPU during high-frequency progress callbacks
        if not force and state == self._last_rendered_state:
            if state != "downloading" or abs(pct - self._last_rendered_pct) < 2.0:
                return

        self._last_rendered_state = state
        self._last_rendered_pct = pct

        pix = self.render_progress_pixmap(state=state, progress=pct)
        self.tray.setIcon(QIcon(pix))
        if state == "downloading":
            self.tray.setToolTip(f"{APP_NAME} — {active_cnt} 个任务下载中 ({pct:.0f}%)")
        else:
            self.tray.setToolTip(APP_NAME)

    def shutdown(self) -> None:
        self.tray.hide()
