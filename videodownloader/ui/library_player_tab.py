"""Page 2: Persistent Media Library & Native Hardware-Accelerated Player (Fluent UI v3.0, zero emoji)."""

from __future__ import annotations

import contextlib
import io
import os
from typing import Any, Callable

with contextlib.redirect_stdout(io.StringIO()):
    from qfluentwidgets import (
        CardWidget,
        FluentIcon as FIF,
        InfoBar,
        InfoBarPosition,
        PrimaryPushButton,
        PushButton,
        SearchLineEdit,
        ToolButton,
    )

from PyQt6.QtCore import Qt, QUrl
from PyQt6.QtMultimedia import QAudioOutput, QMediaPlayer
from PyQt6.QtMultimediaWidgets import QVideoWidget
from PyQt6.QtWidgets import (
    QAbstractItemView,
    QHBoxLayout,
    QHeaderView,
    QLabel,
    QMessageBox,
    QSlider,
    QSplitter,
    QTableWidget,
    QTableWidgetItem,
    QVBoxLayout,
    QWidget,
)

from ..core.db import Database
from ..core.os_utils import open_with_system_player, resolve_task_media_path, reveal_in_file_manager
from ..core.task_queue import TaskQueueManager
from .desktop_theme import format_mmss, format_size
from .tokens import get_palette


class LibraryPlayerTab(QWidget):
    """Persistent downloaded media library paired with an embedded hardware-accelerated Qt6 media player."""

    def __init__(
        self,
        db: Database,
        queue_mgr: TaskQueueManager,
        on_open_ai: Callable[[str], None],
        on_library_changed: Callable[[], None],
        parent: QWidget | None = None,
    ) -> None:
        super().__init__(parent)
        self.setObjectName("libraryPage")
        self.db = db
        self.queue_mgr = queue_mgr
        self.on_open_ai = on_open_ai
        self.on_library_changed = on_library_changed

        self._current_playing_task_id: str = ""
        self._current_playing_path: str = ""
        self._slider_dragging: bool = False
        self._completed_cache: list[dict[str, Any]] = []
        self._is_dark: bool = self.db.get_settings().get("theme_mode") == "dark"

        self._build_ui()
        self._setup_player()
        self.apply_theme(self._is_dark)

    def _build_ui(self) -> None:
        layout = QVBoxLayout(self)
        layout.setContentsMargins(24, 20, 24, 16)
        layout.setSpacing(16)

        splitter = QSplitter(Qt.Orientation.Horizontal)

        # Left Pane: Media Library Card
        self.left_card = CardWidget(self)
        left_lay = QVBoxLayout(self.left_card)
        left_lay.setContentsMargins(16, 16, 16, 16)
        left_lay.setSpacing(12)

        top_bar = QHBoxLayout()
        top_bar.setSpacing(8)
        self.search_edit = SearchLineEdit(self.left_card)
        self.search_edit.setPlaceholderText("搜索已下载视频标题或平台...")
        self.search_edit.textChanged.connect(self._render_table)
        top_bar.addWidget(self.search_edit, 1)

        btn_scan = PushButton(FIF.SYNC, "同步目录", self.left_card)
        btn_scan.clicked.connect(self._scan_folder)
        top_bar.addWidget(btn_scan)

        btn_clean = PushButton(FIF.BROOM, "清理失效", self.left_card)
        btn_clean.clicked.connect(self._clean_invalid)
        top_bar.addWidget(btn_clean)

        btn_open_root = PushButton(FIF.FOLDER, "打开目录", self.left_card)
        btn_open_root.clicked.connect(self._open_root_save_dir)
        top_bar.addWidget(btn_open_root)
        left_lay.addLayout(top_bar)

        self.table = QTableWidget(0, 5)
        self.table.setHorizontalHeaderLabels(["媒体标题 (双击直接播放)", "平台", "大小", "状态", "操作"])
        self.table.setAlternatingRowColors(True)
        self.table.setSelectionBehavior(QAbstractItemView.SelectionBehavior.SelectRows)
        self.table.setEditTriggers(QAbstractItemView.EditTrigger.NoEditTriggers)
        self.table.verticalHeader().setVisible(False)
        self.table.verticalHeader().setDefaultSectionSize(44)
        hdr = self.table.horizontalHeader()
        hdr.setSectionResizeMode(0, QHeaderView.ResizeMode.Stretch)
        hdr.setSectionResizeMode(1, QHeaderView.ResizeMode.ResizeToContents)
        hdr.setSectionResizeMode(2, QHeaderView.ResizeMode.ResizeToContents)
        hdr.setSectionResizeMode(3, QHeaderView.ResizeMode.ResizeToContents)
        hdr.setSectionResizeMode(4, QHeaderView.ResizeMode.Fixed)
        self.table.setColumnWidth(4, 200)
        self.table.cellDoubleClicked.connect(self._on_row_double_clicked)
        left_lay.addWidget(self.table, 1)
        splitter.addWidget(self.left_card)

        # Right Pane: Embedded Native Video/Audio Player
        self.right_card = CardWidget(self)
        right_lay = QVBoxLayout(self.right_card)
        right_lay.setContentsMargins(16, 16, 16, 16)
        right_lay.setSpacing(10)

        self.now_playing_label = QLabel("内置播放器就绪 — 双击左侧媒体或点击播放图标即可观看")
        self.now_playing_label.setWordWrap(True)
        right_lay.addWidget(self.now_playing_label)

        self.video_widget = QVideoWidget()
        self.video_widget.setMinimumSize(360, 240)
        self.video_widget.setStyleSheet("background-color:#0B0F19;border-radius:10px;")
        right_lay.addWidget(self.video_widget, 1)

        seek_row = QHBoxLayout()
        self.seek_slider = QSlider(Qt.Orientation.Horizontal)
        self.seek_slider.setRange(0, 0)
        self.seek_slider.sliderPressed.connect(lambda: setattr(self, "_slider_dragging", True))
        self.seek_slider.sliderReleased.connect(self._on_seek_released)
        seek_row.addWidget(self.seek_slider, 1)

        self.time_label = QLabel("00:00 / 00:00")
        seek_row.addWidget(self.time_label)
        right_lay.addLayout(seek_row)

        ctrl_row = QHBoxLayout()
        ctrl_row.setSpacing(8)
        self.btn_play_pause = PrimaryPushButton(FIF.PLAY, "播放", self.right_card)
        self.btn_play_pause.clicked.connect(self.toggle_play_pause)
        ctrl_row.addWidget(self.btn_play_pause)

        btn_stop = PushButton(FIF.CLOSE, "停止", self.right_card)
        btn_stop.clicked.connect(lambda: self.stop_playback(release_file=False))
        ctrl_row.addWidget(btn_stop)

        vol_btn = ToolButton(FIF.VOLUME, self.right_card)
        vol_btn.setEnabled(False)
        ctrl_row.addWidget(vol_btn)

        self.vol_slider = QSlider(Qt.Orientation.Horizontal)
        self.vol_slider.setRange(0, 100)
        self.vol_slider.setValue(85)
        self.vol_slider.setFixedWidth(84)
        ctrl_row.addWidget(self.vol_slider)

        ctrl_row.addStretch(1)

        btn_reveal_cur = ToolButton(FIF.FOLDER, self.right_card)
        btn_reveal_cur.setToolTip("在资源管理器中定位当前文件")
        btn_reveal_cur.clicked.connect(self._reveal_current_playing)
        ctrl_row.addWidget(btn_reveal_cur)

        btn_ext_cur = PushButton(FIF.SHARE, "系统播放器", self.right_card)
        btn_ext_cur.clicked.connect(self._open_current_external)
        ctrl_row.addWidget(btn_ext_cur)
        right_lay.addLayout(ctrl_row)

        splitter.addWidget(self.right_card)
        splitter.setStretchFactor(0, 3)
        splitter.setStretchFactor(1, 2)
        layout.addWidget(splitter, 1)

    def apply_theme(self, is_dark: bool) -> None:
        self._is_dark = is_dark
        pal = get_palette(is_dark)
        self.now_playing_label.setStyleSheet(f"font-weight: 600; font-size: 13px; color: {pal['text_primary']};")
        self.time_label.setStyleSheet(f"color: {pal['text_secondary']}; font-family: Consolas; font-size: 12px;")
        self.table.setStyleSheet(
            f"""
            QTableWidget {{
                background-color: {pal['bg_card']};
                alternate-background-color: {pal['bg_trace']};
                color: {pal['text_primary']};
                border: 1px solid {pal['border']};
                border-radius: 8px;
                gridline-color: {pal['border']};
                selection-background-color: {pal['accent_bg']};
                selection-color: {pal['text_primary']};
            }}
            QHeaderView::section {{
                background-color: {pal['bg_subtle']};
                color: {pal['text_secondary']};
                font-weight: 600;
                border: none;
                border-bottom: 1px solid {pal['border']};
                padding: 8px;
            }}
            """
        )

    def _setup_player(self) -> None:
        self.audio_output = QAudioOutput(self)
        self.audio_output.setVolume(0.85)
        self.media_player = QMediaPlayer(self)
        self.media_player.setAudioOutput(self.audio_output)
        self.media_player.setVideoOutput(self.video_widget)

        self.vol_slider.valueChanged.connect(lambda v: self.audio_output.setVolume(max(0.0, min(1.0, v / 100.0))))
        self.media_player.positionChanged.connect(self._on_position_changed)
        self.media_player.durationChanged.connect(self._on_duration_changed)
        self.media_player.playbackStateChanged.connect(self._on_state_changed)
        self.media_player.errorOccurred.connect(self._on_player_error)

    # -------------------- Library Data & Actions --------------------

    def update_tasks(self, completed_tasks: list[dict[str, Any]]) -> None:
        self._completed_cache = completed_tasks
        self._render_table()

    def _filtered_tasks(self) -> list[dict[str, Any]]:
        kw = self.search_edit.text().strip().lower()
        if not kw:
            return self._completed_cache
        return [
            t
            for t in self._completed_cache
            if kw in (t.get("title") or "").lower() or kw in (t.get("platform_name") or "").lower()
        ]

    def _render_table(self) -> None:
        rows = self._filtered_tasks()
        save_dir = self.db.get_settings().get("save_path", "")
        self.table.setRowCount(len(rows))

        for row_idx, task in enumerate(rows):
            tid = task["id"]
            resolved = resolve_task_media_path(task.get("file_path") or "", save_dir)
            exists = bool(resolved and os.path.isfile(resolved))

            title_item = QTableWidgetItem(task.get("title") or "未命名媒体")
            title_item.setToolTip(resolved or task.get("file_path") or "")
            title_item.setData(Qt.ItemDataRole.UserRole, tid)
            self.table.setItem(row_idx, 0, title_item)

            plat_item = QTableWidgetItem(task.get("platform_name") or "-")
            self.table.setItem(row_idx, 1, plat_item)

            sz = os.path.getsize(resolved) if exists else (task.get("file_size") or 0)
            size_item = QTableWidgetItem(format_size(sz))
            self.table.setItem(row_idx, 2, size_item)

            status_item = QTableWidgetItem("本地就绪" if exists else "文件缺失")
            self.table.setItem(row_idx, 3, status_item)

            cell_widget = QWidget()
            cell_lay = QHBoxLayout(cell_widget)
            cell_lay.setContentsMargins(4, 2, 4, 2)
            cell_lay.setSpacing(4)

            b_play = ToolButton(FIF.PLAY, cell_widget)
            b_play.setToolTip("在右侧内置播放器直接播放")
            b_play.setEnabled(exists)
            b_play.clicked.connect(lambda _, x=tid: self.play_task(x))
            cell_lay.addWidget(b_play)

            b_dir = ToolButton(FIF.FOLDER, cell_widget)
            b_dir.setToolTip("打开所在文件夹并选中该视频文件")
            b_dir.clicked.connect(lambda _, x=tid: self.reveal_task_in_folder(x))
            cell_lay.addWidget(b_dir)

            b_ext = ToolButton(FIF.SHARE, cell_widget)
            b_ext.setToolTip("调用系统默认播放器打开")
            b_ext.setEnabled(exists)
            b_ext.clicked.connect(lambda _, x=tid: self.open_task_external(x))
            cell_lay.addWidget(b_ext)

            b_ai = ToolButton(FIF.CHAT, cell_widget)
            b_ai.setToolTip("送入 AI 视频精读")
            b_ai.clicked.connect(lambda _, x=tid: self.on_open_ai(x))
            cell_lay.addWidget(b_ai)

            b_del = ToolButton(FIF.DELETE, cell_widget)
            b_del.setToolTip("移除记录或本地文件")
            b_del.clicked.connect(lambda _, x=tid: self._delete_task_prompt(x))
            cell_lay.addWidget(b_del)

            self.table.setCellWidget(row_idx, 4, cell_widget)

    def _on_row_double_clicked(self, row: int, _col: int) -> None:
        item = self.table.item(row, 0)
        if item:
            tid = item.data(Qt.ItemDataRole.UserRole)
            if tid:
                self.play_task(tid)

    # -------------------- Playback & Folder Jump --------------------

    def play_task(self, task_id: str, seek_seconds: float | None = None) -> None:
        task = self.db.get_task(task_id)
        if not task:
            return
        save_dir = self.db.get_settings().get("save_path", "")
        resolved = resolve_task_media_path(task.get("file_path") or "", save_dir)
        if not resolved:
            InfoBar.warning(
                title="文件不存在",
                content="该媒体文件已被移动或删除，可点击「同步目录」或「清理失效」。",
                position=InfoBarPosition.TOP,
                duration=3500,
                parent=self,
            )
            return

        if resolved != self._current_playing_path:
            self._current_playing_task_id = task_id
            self._current_playing_path = resolved
            self.now_playing_label.setText(f"正在播放：{task.get('title', os.path.basename(resolved))}")
            self.media_player.setSource(QUrl.fromLocalFile(resolved))

        self.media_player.play()
        if seek_seconds is not None and seek_seconds > 0:
            self.media_player.setPosition(int(seek_seconds * 1000))

    def stop_playback(self, release_file: bool = False) -> None:
        self.media_player.stop()
        if release_file:
            self.media_player.setSource(QUrl())
            self._current_playing_path = ""
            self._current_playing_task_id = ""
            self.now_playing_label.setText("内置播放器已停止")

    def toggle_play_pause(self) -> None:
        if self.media_player.playbackState() == QMediaPlayer.PlaybackState.PlayingState:
            self.media_player.pause()
        elif self._current_playing_path:
            self.media_player.play()
        else:
            rows = self._filtered_tasks()
            if rows:
                self.play_task(rows[0]["id"])

    def reveal_task_in_folder(self, task_id: str) -> None:
        task = self.db.get_task(task_id)
        save_dir = self.db.get_settings().get("save_path", "")
        resolved = resolve_task_media_path((task or {}).get("file_path") or "", save_dir)
        target = resolved or save_dir
        try:
            reveal_in_file_manager(target, select_file=bool(resolved))
        except Exception as exc:
            QMessageBox.warning(self, "打开文件夹失败", str(exc))

    def open_task_external(self, task_id: str) -> None:
        task = self.db.get_task(task_id)
        save_dir = self.db.get_settings().get("save_path", "")
        resolved = resolve_task_media_path((task or {}).get("file_path") or "", save_dir)
        if not resolved:
            return
        try:
            open_with_system_player(resolved)
        except Exception as exc:
            QMessageBox.warning(self, "打开失败", str(exc))

    def _reveal_current_playing(self) -> None:
        if self._current_playing_path and os.path.exists(self._current_playing_path):
            reveal_in_file_manager(self._current_playing_path, select_file=True)
        else:
            self._open_root_save_dir()

    def _open_current_external(self) -> None:
        if self._current_playing_path and os.path.isfile(self._current_playing_path):
            self.media_player.pause()
            open_with_system_player(self._current_playing_path)

    def _open_root_save_dir(self) -> None:
        save_dir = self.db.get_settings().get("save_path", "")
        os.makedirs(save_dir, exist_ok=True)
        reveal_in_file_manager(save_dir, select_file=False)

    def _scan_folder(self) -> None:
        res = self.queue_mgr.scan_local_folder()
        self.on_library_changed()
        InfoBar.success(
            title="目录同步完成",
            content=f"已扫描保存目录，新增导入 {res.get('added', 0)} 个本地音视频文件。",
            position=InfoBarPosition.TOP,
            duration=2800,
            parent=self,
        )

    def _clean_invalid(self) -> None:
        removed = self.queue_mgr.clean_invalid_records()
        self.on_library_changed()
        InfoBar.info(
            title="清理完成",
            content=f"已清理 {removed} 条本地文件缺失的记录。" if removed else "当前媒体库所有文件均完好有效。",
            position=InfoBarPosition.TOP,
            duration=2800,
            parent=self,
        )

    def _delete_task_prompt(self, task_id: str) -> None:
        task = self.db.get_task(task_id)
        if not task:
            return
        box = QMessageBox(self)
        box.setWindowTitle("移除媒体")
        box.setText(f"如何移除《{task.get('title', '')[:45]}》？")
        btn_only_rec = box.addButton("仅移除列表记录 (保留硬盘文件)", QMessageBox.ButtonRole.AcceptRole)
        btn_with_file = box.addButton("连同硬盘文件彻底删除", QMessageBox.ButtonRole.DestructiveRole)
        box.addButton("取消", QMessageBox.ButtonRole.RejectRole)
        box.exec()

        clicked = box.clickedButton()
        if clicked not in (btn_only_rec, btn_with_file):
            return
        delete_disk = clicked == btn_with_file

        save_dir = self.db.get_settings().get("save_path", "")
        resolved = resolve_task_media_path(task.get("file_path") or "", save_dir)
        if (
            resolved
            and self._current_playing_path
            and os.path.normcase(resolved) == os.path.normcase(self._current_playing_path)
        ):
            self.stop_playback(release_file=True)

        self.queue_mgr.delete_task(task_id, delete_file=delete_disk)
        self.on_library_changed()

    # -------------------- Player Callbacks --------------------

    def _on_position_changed(self, pos_ms: int) -> None:
        if not self._slider_dragging:
            self.seek_slider.setValue(pos_ms)
        dur = self.media_player.duration()
        self.time_label.setText(f"{format_mmss(pos_ms, is_ms=True)} / {format_mmss(dur, is_ms=True)}")

    def _on_duration_changed(self, dur_ms: int) -> None:
        self.seek_slider.setRange(0, max(0, dur_ms))

    def _on_seek_released(self) -> None:
        self._slider_dragging = False
        self.media_player.setPosition(self.seek_slider.value())

    def _on_state_changed(self, state: QMediaPlayer.PlaybackState) -> None:
        if state == QMediaPlayer.PlaybackState.PlayingState:
            self.btn_play_pause.setIcon(FIF.PAUSE)
            self.btn_play_pause.setText("暂停")
        else:
            self.btn_play_pause.setIcon(FIF.PLAY)
            self.btn_play_pause.setText("播放")

    def _on_player_error(self, _error: QMediaPlayer.Error, error_string: str) -> None:
        if not self._current_playing_path:
            return
        ans = QMessageBox.question(
            self,
            "编码兼容提示",
            f"Windows 内置解码器无法直接渲染该编码 ({error_string or 'HEVC/AV1'})。\n是否立即调用系统默认播放器打开观看？",
        )
        if ans == QMessageBox.StandardButton.Yes and os.path.isfile(self._current_playing_path):
            open_with_system_player(self._current_playing_path)
