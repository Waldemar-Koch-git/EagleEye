import json
import logging
import math
import os
import time
from dataclasses import asdict

from PyQt6 import QtCore, QtGui, QtWidgets

from .camera_manager_dialog import CameraManagerDialog
from .i18n import LANGUAGES, get_language, language_name, set_language, tr, translate_status

LANGUAGE_PATH = os.path.expanduser("~/.EagleEye/language.json")
from .frame_buffer import LatestFrameBuffer
from .settings_dialog import StreamSettingsDialog
from .stream_config import StreamConfig
from .stream_types import StreamType
from .stream_worker import StreamWorker
from .video_widget import VideoWidget

logger = logging.getLogger(__name__)

CONFIG_PATH = os.path.expanduser("~/.EagleEye/config.json")
RECORDINGS_DIR = os.path.expanduser("~/.EagleEye/recordings")
# Window geometry, position and compact mode are stored in a small dedicated
# file instead of config.json. This keeps the window state updated on every
# shutdown, independently from the explicit Save action for stream and layout
# configuration.
WINDOW_STATE_PATH = os.path.expanduser("~/.EagleEye/window_state.json")


class MainWindow(QtWidgets.QMainWindow):
    _status_signal = QtCore.pyqtSignal(str, str, dict)

    def __init__(self):
        super().__init__()
        self.setWindowTitle(tr("app.title"))
        self.resize(1280, 800)

        self.streams: dict[str, StreamConfig] = {}
        self.buffers: dict[str, LatestFrameBuffer] = {}
        self.workers: dict[str, StreamWorker] = {}
        self.widgets: dict[str, VideoWidget] = {}
        self.order: list[str] = []
        self._selected_id: str | None = None
        self._camera_manager_dialog: CameraManagerDialog | None = None
        # When set, only this tile is shown in the grid and fills all
        # available space. Double-clicking a tile toggles this state.
        self._fullscreen_id: str | None = None

        self.layout_mode = "dynamic"  # "dynamic" or "fixed"
        self.fixed_cols = 2
        self.fixed_rows = 2
        self.hide_disabled = False   # remove disabled tiles from the grid completely

        # Window position, size and compact mode are stored on shutdown and
        # restored on next launch. Set this to False in code to disable it.
        self.remember_window = True
        self._last_grid_cols = 0
        self._last_grid_rows = 0

        # Debounce dynamic grid recalculation during window resize.
        self._resize_relayout_timer = QtCore.QTimer(self)
        self._resize_relayout_timer.setSingleShot(True)
        self._resize_relayout_timer.timeout.connect(self._relayout)

        self._status_signal.connect(self._handle_status_on_gui_thread)

        self._build_ui()
        self._load_language()
        self._load_window_state()
        self._load_config()

    # ------------------------------------------------------------------
    def _build_ui(self):
        # Each category is its own toolbar so Qt can move, reorder, dock and
        # hide them independently via the built-in toolbar handling.
        def make_toolbar(title, object_name):
            tb = QtWidgets.QToolBar(title)
            tb.setObjectName(object_name)
            self.addToolBar(tb)
            return tb

        # --- Camera management ---
        self.toolbar_cameras = make_toolbar(tr("toolbar.cameras"), "toolbar_cameras")
        self.overview_action = QtGui.QAction(tr("action.overview"), self)
        self.overview_action.triggered.connect(self.open_camera_manager)
        self.toolbar_cameras.addAction(self.overview_action)
        self.add_action = QtGui.QAction(tr("action.add"), self)
        self.add_action.triggered.connect(self.add_stream_dialog)
        self.toolbar_cameras.addAction(self.add_action)

        # --- Tile grid layout ---
        self.toolbar_layout = make_toolbar(tr("toolbar.layout"), "toolbar_layout")
        self.mode_combo = QtWidgets.QComboBox()
        self.mode_combo.addItem(tr("layout.dynamic"), "dynamic")
        self.mode_combo.addItem(tr("layout.fixed"), "fixed")
        self.mode_combo.currentIndexChanged.connect(self._on_mode_changed)
        self.toolbar_layout.addWidget(self.mode_combo)

        self.cols_spin = QtWidgets.QSpinBox()
        self.cols_spin.setRange(1, 20)
        self.cols_spin.setValue(self.fixed_cols)
        self.cols_spin.setPrefix(tr("spin.columns"))
        self.cols_spin.valueChanged.connect(self._on_fixed_grid_changed)
        self.cols_spin.setEnabled(False)
        self.toolbar_layout.addWidget(self.cols_spin)

        self.rows_spin = QtWidgets.QSpinBox()
        self.rows_spin.setRange(1, 20)
        self.rows_spin.setValue(self.fixed_rows)
        self.rows_spin.setPrefix(tr("spin.rows"))
        self.rows_spin.valueChanged.connect(self._on_fixed_grid_changed)
        self.rows_spin.setEnabled(False)
        self.toolbar_layout.addWidget(self.rows_spin)

        self.hide_disabled_action = QtGui.QAction(tr("action.hide_disabled"), self)
        self.hide_disabled_action.setCheckable(True)
        self.hide_disabled_action.toggled.connect(self._toggle_hide_disabled)
        self.toolbar_layout.addAction(self.hide_disabled_action)

        # --- Main window behavior ---
        self.toolbar_window = make_toolbar(tr("toolbar.window"), "toolbar_window")
        self.always_on_top_action = QtGui.QAction(tr("action.always_on_top"), self)
        self.always_on_top_action.setCheckable(True)
        self.always_on_top_action.toggled.connect(self._toggle_always_on_top)
        self.toolbar_window.addAction(self.always_on_top_action)

        self.compact_action = QtGui.QAction(tr("action.compact"), self)
        self.compact_action.setCheckable(True)
        self.compact_action.setShortcut(QtGui.QKeySequence("F11"))
        self.compact_action.setToolTip(tr("compact.tooltip"))
        self.compact_action.toggled.connect(self._toggle_compact_mode)
        self.toolbar_window.addAction(self.compact_action)

        self.language_label = QtWidgets.QLabel(tr("language.label"))
        self.toolbar_window.addWidget(self.language_label)
        self.language_combo = QtWidgets.QComboBox()
        self.language_combo.setToolTip(tr("language.tooltip"))
        for code in LANGUAGES:
            self.language_combo.addItem(language_name(code), code)
        self.language_combo.setCurrentIndex(
            max(0, self.language_combo.findData(get_language())))
        self.language_combo.currentIndexChanged.connect(self._on_language_changed)
        self.toolbar_window.addWidget(self.language_combo)

        # Register the shortcut at window level so F11 keeps working when the
        # toolbars and their actions are currently hidden.
        self.addAction(self.compact_action)

        # --- Stream and layout configuration persistence. Window state is
        # persisted independently in _save_window_state(). ---
        self.toolbar_file = make_toolbar(tr("toolbar.file"), "toolbar_file")
        self.save_action = QtGui.QAction(tr("action.save"), self)
        self.save_action.triggered.connect(self._save_config)
        self.toolbar_file.addAction(self.save_action)

        # Used by compact mode and by saveState/restoreState.
        self.toolbars = [self.toolbar_cameras, self.toolbar_layout, self.toolbar_window, self.toolbar_file]

        central = QtWidgets.QWidget()
        self.grid_layout = QtWidgets.QGridLayout(central)
        self.grid_layout.setSpacing(4)
        self.grid_layout.setContentsMargins(4, 4, 4, 4)
        self.setCentralWidget(central)

        self.status_bar = self.statusBar()

    def _on_language_changed(self, index: int):
        """Switch the active language and refresh visible frontend labels."""
        code = self.language_combo.itemData(index)
        if not code:
            return
        set_language(code)
        self._save_language()
        self._retranslate_ui()

    def _retranslate_ui(self):
        """Refresh translatable main window labels after a language switch."""
        self.setWindowTitle(tr("app.title"))
        self.toolbar_cameras.setWindowTitle(tr("toolbar.cameras"))
        self.toolbar_layout.setWindowTitle(tr("toolbar.layout"))
        self.toolbar_window.setWindowTitle(tr("toolbar.window"))
        self.toolbar_file.setWindowTitle(tr("toolbar.file"))
        self.language_label.setText(tr("language.label"))
        self.language_combo.setToolTip(tr("language.tooltip"))
        self.overview_action.setText(tr("action.overview"))
        self.add_action.setText(tr("action.add"))
        self.hide_disabled_action.setText(tr("action.hide_disabled"))
        self.always_on_top_action.setText(tr("action.always_on_top"))
        self.compact_action.setText(tr("action.compact"))
        self.compact_action.setToolTip(tr("compact.tooltip"))
        self.save_action.setText(tr("action.save"))
        current_mode = self.mode_combo.currentData()
        self.mode_combo.blockSignals(True)
        self.mode_combo.setItemText(0, tr("layout.dynamic"))
        self.mode_combo.setItemText(1, tr("layout.fixed"))
        self.mode_combo.setCurrentIndex(0 if current_mode == "dynamic" else 1)
        self.mode_combo.blockSignals(False)
        self.cols_spin.setPrefix(tr("spin.columns"))
        self.rows_spin.setPrefix(tr("spin.rows"))
        current_language = get_language()
        self.language_combo.blockSignals(True)
        for idx in range(self.language_combo.count()):
            code = self.language_combo.itemData(idx)
            self.language_combo.setItemText(idx, language_name(code))
        self.language_combo.setCurrentIndex(
            max(0, self.language_combo.findData(current_language)))
        self.language_combo.blockSignals(False)
        for widget in self.widgets.values():
            widget.update()
        if self._camera_manager_dialog is not None:
            self._camera_manager_dialog.retranslate_ui()
            self._camera_manager_dialog.refresh()

    # ------------------------------------------------------------------
    # Stream management
    # ------------------------------------------------------------------
    def add_stream_dialog(self):
        dlg = StreamSettingsDialog(parent=self)
        if dlg.exec() == QtWidgets.QDialog.DialogCode.Accepted:
            cfg = dlg.get_config()
            if not cfg.url:
                QtWidgets.QMessageBox.warning(self, tr("dialog.error.title"), tr("dialog.url_required"))
                return
            self.add_stream(cfg)

    def add_stream(self, cfg: StreamConfig):
        self.streams[cfg.id] = cfg
        buf = LatestFrameBuffer()
        self.buffers[cfg.id] = buf

        widget = VideoWidget(cfg, buf)
        widget.doubleClicked.connect(self.toggle_fullscreen_for)
        widget.reorderRequested.connect(self._reorder_streams)
        widget.setContextMenuPolicy(QtCore.Qt.ContextMenuPolicy.CustomContextMenu)
        widget.customContextMenuRequested.connect(
            lambda pos, sid=cfg.id, w=widget: self._context_menu(sid, w, pos))
        widget.mousePressEvent = self._make_select_handler(cfg.id, widget)
        self.widgets[cfg.id] = widget
        self.order.append(cfg.id)
        self._selected_id = cfg.id

        if cfg.enabled:
            self._start_worker(cfg.id)

        self._relayout()

    def _start_worker(self, stream_id):
        """Start or restart the decode thread for a stream."""
        cfg = self.streams.get(stream_id)
        if not cfg:
            return
        old_worker = self.workers.pop(stream_id, None)
        if old_worker:
            old_worker.stop()
        worker = StreamWorker(cfg, self.buffers[stream_id], status_cb=self._on_worker_status)
        self.workers[stream_id] = worker
        worker.start()

    def _stop_worker(self, stream_id):
        worker = self.workers.pop(stream_id, None)
        if worker:
            worker.stop()

    def set_stream_enabled(self, stream_id, enabled: bool):
        """Enable or disable a stream and update its worker and tile."""
        cfg = self.streams.get(stream_id)
        if not cfg or cfg.enabled == enabled:
            return
        cfg.enabled = enabled
        if enabled:
            self._start_worker(stream_id)
        else:
            self._stop_worker(stream_id)
        widget = self.widgets.get(stream_id)
        if widget:
            widget.apply_enabled_state(enabled)
            if not enabled:
                widget.set_recording(False)
        if self.hide_disabled:
            self._relayout()
        self.status_bar.showMessage(
            tr("status.stream_enabled" if enabled else "status.stream_disabled", name=cfg.name), 3000)

    def switch_stream_variant(self, stream_id):
        """Switch between main and sub stream and restart the worker."""
        cfg = self.streams.get(stream_id)
        if not cfg or not cfg.has_sub_stream():
            return
        cfg.active_variant = cfg.other_variant()
        if cfg.enabled:
            self._start_worker(stream_id)
        widget = self.widgets.get(stream_id)
        if widget:
            widget.update()
        variant = tr("stream.sub" if cfg.active_variant == "sub" else "stream.main")
        self.status_bar.showMessage(
            tr("status.variant_active", name=cfg.name, variant=variant), 3000)

    def _reorder_streams(self, dragged_id, target_id):
        """Move a dragged tile before the target tile in the stream order."""
        if dragged_id == target_id:
            return
        if dragged_id not in self.order or target_id not in self.order:
            return
        self.order.remove(dragged_id)
        insert_at = self.order.index(target_id)
        self.order.insert(insert_at, dragged_id)
        self._relayout()

    def toggle_fullscreen_for(self, stream_id):
        """Toggle single-tile full-grid view for a stream."""
        if stream_id not in self.streams:
            return
        self._fullscreen_id = None if self._fullscreen_id == stream_id else stream_id
        self._relayout()

    def _make_select_handler(self, stream_id, widget):
        original = widget.mousePressEvent

        def handler(event):
            self._selected_id = stream_id
            self._highlight_selected()
            original(event)

        return handler

    def _highlight_selected(self):
        for sid, w in self.widgets.items():
            if sid == self._selected_id:
                w.setStyleSheet("background-color: black; border: 2px solid #2d8cf0;")
            else:
                w.setStyleSheet("background-color: black; border: none;")

    def remove_stream(self, stream_id):
        if self._fullscreen_id == stream_id:
            self._fullscreen_id = None
        self._stop_worker(stream_id)
        widget = self.widgets.pop(stream_id, None)
        if widget:
            self.grid_layout.removeWidget(widget)
            widget.deleteLater()
        self.buffers.pop(stream_id, None)
        self.streams.pop(stream_id, None)
        if stream_id in self.order:
            self.order.remove(stream_id)
        if self._selected_id == stream_id:
            self._selected_id = self.order[-1] if self.order else None
        self._relayout()

    def _confirm_and_remove(self, stream_id):
        """Ask for confirmation before removing a camera."""
        cfg = self.streams.get(stream_id)
        if not cfg:
            return
        reply = QtWidgets.QMessageBox.question(
            self, tr("dialog.remove_camera.title"),
            tr("dialog.remove_camera.message", name=cfg.name),
            QtWidgets.QMessageBox.StandardButton.Yes | QtWidgets.QMessageBox.StandardButton.No,
            QtWidgets.QMessageBox.StandardButton.No)
        if reply == QtWidgets.QMessageBox.StandardButton.Yes:
            self.remove_stream(stream_id)

    def remove_selected(self):
        if self._selected_id:
            self._confirm_and_remove(self._selected_id)
        elif self.order:
            self._confirm_and_remove(self.order[-1])

    def open_settings_for(self, stream_id):
        cfg = self.streams.get(stream_id)
        if not cfg:
            return
        dlg = StreamSettingsDialog(cfg, parent=self)
        if dlg.exec() == QtWidgets.QDialog.DialogCode.Accepted:
            new_cfg = dlg.get_config()
            self.streams[stream_id] = new_cfg
            widget = self.widgets[stream_id]
            widget.config = new_cfg
            widget.set_target_fps(new_cfg.max_fps)
            widget.apply_enabled_state(new_cfg.enabled)
            if new_cfg.enabled:
                self._start_worker(stream_id)
            else:
                self._stop_worker(stream_id)
            if self.hide_disabled:
                self._relayout()

    def _context_menu(self, stream_id, widget, pos):
        cfg = self.streams.get(stream_id)
        if not cfg:
            return
        menu = QtWidgets.QMenu(self)
        edit_act = menu.addAction(tr("menu.settings"))

        toggle_label = tr("menu.disable" if cfg.enabled else "menu.enable")
        toggle_act = menu.addAction(toggle_label)

        variant_act = None
        if cfg.has_sub_stream():
            variant_label = tr("menu.switch_main" if cfg.active_variant == "sub"
                               else "menu.switch_sub")
            variant_act = menu.addAction(variant_label)

        audio_act = menu.addAction(
            tr("menu.audio_disable" if cfg.audio_enabled else "menu.audio_enable"))

        fps_act = menu.addAction(
            tr("menu.fps_hide" if cfg.show_fps else "menu.fps_show"))

        zoom_reset_act = None
        if widget.is_zoomed():
            zoom_reset_act = menu.addAction(tr("menu.zoom_reset"))

        record_act = None
        if cfg.enabled:
            worker = self.workers.get(stream_id)
            is_recording = bool(worker and worker.is_recording())
            record_act = menu.addAction(
                tr("menu.record_stop" if is_recording else "menu.record_start"))

        menu.addSeparator()
        remove_act = menu.addAction(tr("menu.remove"))

        chosen = menu.exec(widget.mapToGlobal(pos))
        if chosen == edit_act:
            self.open_settings_for(stream_id)
        elif chosen == toggle_act:
            self.set_stream_enabled(stream_id, not cfg.enabled)
        elif variant_act is not None and chosen == variant_act:
            self.switch_stream_variant(stream_id)
        elif chosen == audio_act:
            self.toggle_audio(stream_id)
        elif chosen == fps_act:
            self.toggle_fps_display(stream_id)
        elif zoom_reset_act is not None and chosen == zoom_reset_act:
            widget.reset_zoom()
        elif record_act is not None and chosen == record_act:
            self.toggle_recording(stream_id)
        elif chosen == remove_act:
            self._confirm_and_remove(stream_id)

    def toggle_audio(self, stream_id):
        """Toggle audio and ensure only one stream plays audio at a time."""
        cfg = self.streams.get(stream_id)
        if not cfg:
            return
        turning_on = not cfg.audio_enabled

        if turning_on:
            for sid, other_cfg in self.streams.items():
                if sid != stream_id and other_cfg.audio_enabled:
                    other_cfg.audio_enabled = False
                    if other_cfg.enabled:
                        self._start_worker(sid)

        cfg.audio_enabled = turning_on
        if cfg.enabled:
            self._start_worker(stream_id)
        self.status_bar.showMessage(
            tr("status.audio_enabled" if turning_on else "status.audio_disabled", name=cfg.name), 3000)

    def toggle_fps_display(self, stream_id):
        """Toggle the FPS overlay on a single tile."""
        cfg = self.streams.get(stream_id)
        if not cfg:
            return
        cfg.show_fps = not cfg.show_fps
        widget = self.widgets.get(stream_id)
        if widget:
            widget.update()

    def toggle_recording(self, stream_id):
        worker = self.workers.get(stream_id)
        widget = self.widgets.get(stream_id)
        if not worker:
            QtWidgets.QMessageBox.information(
                self, tr("recording.title"), tr("recording.inactive"))
            return

        if worker.is_recording():
            worker.stop_recording()
            if widget:
                widget.set_recording(False)
            self.status_bar.showMessage(tr("status.recording_stopped"), 3000)
            return

        cfg = self.streams.get(stream_id)
        os.makedirs(RECORDINGS_DIR, exist_ok=True)
        safe_name = "".join(
            c if c.isalnum() or c in "-_" else "_"
            for c in (cfg.name if cfg else stream_id))
        filename = f"{safe_name}_{time.strftime('%Y%m%d_%H%M%S')}.mp4"
        path = os.path.join(RECORDINGS_DIR, filename)
        worker.start_recording(path)
        if widget:
            widget.set_recording(True)
        self.status_bar.showMessage(tr("status.recording_started", path=path), 5000)

    def _on_worker_status(self, stream_id, status, extra):
        # This is called from the worker thread. Emit only a Qt signal; the
        # actual GUI update happens on the GUI thread.
        self._status_signal.emit(stream_id, status, extra)

    def _handle_status_on_gui_thread(self, stream_id, status, extra):
        widget = self.widgets.get(stream_id)
        if widget:
            widget.set_status(status)
        self.status_bar.showMessage(
            tr("status.worker", id=stream_id[:8], status=translate_status(status), extra=extra),
            3000)

    # ------------------------------------------------------------------
    # Camera overview dialog
    # ------------------------------------------------------------------
    def open_camera_manager(self):
        if self._camera_manager_dialog is None:
            self._camera_manager_dialog = CameraManagerDialog(self, parent=self)
        self._camera_manager_dialog.refresh()
        self._camera_manager_dialog.show()
        self._camera_manager_dialog.raise_()
        self._camera_manager_dialog.activateWindow()

    # ------------------------------------------------------------------
    # Always on top
    # ------------------------------------------------------------------
    def _toggle_always_on_top(self, checked: bool):
        flags = self.windowFlags()
        if checked:
            flags |= QtCore.Qt.WindowType.WindowStaysOnTopHint
        else:
            flags &= ~QtCore.Qt.WindowType.WindowStaysOnTopHint
        self.setWindowFlags(flags)
        self.show()  # setWindowFlags requires calling show() again.

    # ------------------------------------------------------------------
    # Layout handling
    # ------------------------------------------------------------------
    def _toggle_hide_disabled(self, checked: bool):
        self.hide_disabled = checked
        self._relayout()

    def _toggle_compact_mode(self, checked: bool):
        """Toggle compact mode by hiding toolbars and the status bar."""
        for tb in self.toolbars:
            tb.setVisible(not checked)
        if checked:
            self.status_bar.showMessage(tr("status.compact_active"), 3000)
            # Hide the status bar only after the hint was visible.
            QtCore.QTimer.singleShot(
                3000,
                lambda: self.status_bar.setVisible(not self.compact_action.isChecked()))
        else:
            self.status_bar.setVisible(True)

    def _on_mode_changed(self, idx):
        self.layout_mode = self.mode_combo.itemData(idx) or "dynamic"
        self.cols_spin.setEnabled(self.layout_mode == "fixed")
        self.rows_spin.setEnabled(self.layout_mode == "fixed")
        self._relayout()

    def _on_fixed_grid_changed(self):
        self.fixed_cols = self.cols_spin.value()
        self.fixed_rows = self.rows_spin.value()
        self._relayout()

    def resizeEvent(self, event):
        super().resizeEvent(event)
        if self.layout_mode == "dynamic" and self.order:
            self._resize_relayout_timer.start(120)

    def _dynamic_grid_size(self, n):
        """Choose columns and rows that maximize available 16:9 tile size."""
        central = self.centralWidget()
        avail_w = central.width() if central else 0
        avail_h = central.height() if central else 0

        if avail_w <= 0 or avail_h <= 0:
            # Window is not sized yet, for example before the first show.
            cols = max(1, math.ceil(math.sqrt(n)))
            return cols, max(1, math.ceil(n / cols))

        best_cols, best_rows, best_area = 1, n, -1.0
        for cols in range(1, n + 1):
            rows = math.ceil(n / cols)
            cell_w = avail_w / cols
            cell_h = avail_h / rows
            # Largest possible 16:9 tile that still fits into the cell.
            scale = min(cell_w / 16.0, cell_h / 9.0)
            if scale <= 0:
                continue
            area = (scale * 16.0) * (scale * 9.0)
            if area > best_area:
                best_area = area
                best_cols, best_rows = cols, rows

        return best_cols, best_rows

    def _relayout(self):
        while self.grid_layout.count():
            item = self.grid_layout.takeAt(0)
            w = item.widget()
            if w:
                self.grid_layout.removeWidget(w)

        # Single-tile fullscreen mode hides all other tiles.
        if self._fullscreen_id is not None and self._fullscreen_id in self.widgets:
            for sid, w in self.widgets.items():
                if sid == self._fullscreen_id:
                    w.show()
                    self.grid_layout.addWidget(w, 0, 0)
                else:
                    w.hide()
            prev_cols = getattr(self, "_last_grid_cols", 0)
            prev_rows = getattr(self, "_last_grid_rows", 0)
            for c in range(prev_cols):
                self.grid_layout.setColumnStretch(c, 0)
            for r in range(prev_rows):
                self.grid_layout.setRowStretch(r, 0)
            self.grid_layout.setColumnStretch(0, 1)
            self.grid_layout.setRowStretch(0, 1)
            self._last_grid_cols = 1
            self._last_grid_rows = 1
            self._highlight_selected()
            return

        if self.hide_disabled:
            visible_order = [sid for sid in self.order if self.streams[sid].enabled]
        else:
            visible_order = list(self.order)

        # Hide tiles removed by the disabled-stream filter.
        for sid in self.order:
            if sid not in visible_order:
                self.widgets[sid].hide()

        # Reset stretch factors from previously used columns and rows.
        # QGridLayout keeps them after widgets are removed, which would leave
        # empty stretched areas when the grid shrinks.
        prev_cols = getattr(self, "_last_grid_cols", 0)
        prev_rows = getattr(self, "_last_grid_rows", 0)
        for c in range(prev_cols):
            self.grid_layout.setColumnStretch(c, 0)
        for r in range(prev_rows):
            self.grid_layout.setRowStretch(r, 0)

        n = len(visible_order)
        if n == 0:
            self._last_grid_cols = 0
            self._last_grid_rows = 0
            return

        if self.layout_mode == "dynamic":
            cols, rows = self._dynamic_grid_size(n)
        else:
            cols = max(1, self.fixed_cols)
            rows = max(1, self.fixed_rows)

        for i, sid in enumerate(visible_order):
            r, c = divmod(i, cols)
            if r >= rows:
                self.widgets[sid].hide()
                continue
            self.widgets[sid].show()
            self.grid_layout.addWidget(self.widgets[sid], r, c)

        for c in range(cols):
            self.grid_layout.setColumnStretch(c, 1)
        for r in range(rows):
            self.grid_layout.setRowStretch(r, 1)

        self._last_grid_cols = cols
        self._last_grid_rows = rows

        self._highlight_selected()

    # ------------------------------------------------------------------
    # Persistence
    # ------------------------------------------------------------------
    @staticmethod
    def _cfg_to_dict(cfg: StreamConfig) -> dict:
        d = asdict(cfg)
        d["stream_type"] = cfg.stream_type.value
        d["sub_stream_type"] = cfg.sub_stream_type.value
        return d

    def _save_config(self):
        os.makedirs(os.path.dirname(CONFIG_PATH), exist_ok=True)
        data = {
            "layout_mode": self.layout_mode,
            "fixed_cols": self.fixed_cols,
            "fixed_rows": self.fixed_rows,
            "hide_disabled": self.hide_disabled,
            "streams": [self._cfg_to_dict(self.streams[sid]) for sid in self.order],
        }
        with open(CONFIG_PATH, "w", encoding="utf-8") as f:
            json.dump(data, f, indent=2, ensure_ascii=False)
        self.status_bar.showMessage(tr("status.saved", path=CONFIG_PATH), 3000)

    def _load_language(self):
        if not os.path.exists(LANGUAGE_PATH):
            return
        try:
            with open(LANGUAGE_PATH, "r", encoding="utf-8") as f:
                data = json.load(f)
                language = data.get("language")
                if language in LANGUAGES:
                    set_language(language)
                    self.language_combo.setCurrentIndex(
                        max(0, self.language_combo.findData(language)))
        except Exception:
            return

    def _save_language(self):
        try:
            os.makedirs(os.path.dirname(LANGUAGE_PATH), exist_ok=True)
            data = {"language": get_language()}
            with open(LANGUAGE_PATH, "w", encoding="utf-8") as f:
                json.dump(data, f, indent=2, ensure_ascii=False)
        except OSError:
            logger.warning("Language preference could not be saved", exc_info=True)

    def _load_config(self):
        if not os.path.exists(CONFIG_PATH):
            return
        try:
            with open(CONFIG_PATH, "r", encoding="utf-8") as f:
                data = json.load(f)
        except Exception:
            return

        self.layout_mode = data.get("layout_mode", "dynamic")
        self.fixed_cols = data.get("fixed_cols", 2)
        self.fixed_rows = data.get("fixed_rows", 2)
        self.mode_combo.setCurrentIndex(
            max(0, self.mode_combo.findData(self.layout_mode)))
        self.cols_spin.setValue(self.fixed_cols)
        self.rows_spin.setValue(self.fixed_rows)
        self.hide_disabled_action.setChecked(data.get("hide_disabled", False))

        for s in data.get("streams", []):
            s["stream_type"] = StreamType(s["stream_type"])
            s["sub_stream_type"] = StreamType(s.get("sub_stream_type", "auto"))
            cfg = StreamConfig(**s)
            self.add_stream(cfg)

    # ------------------------------------------------------------------
    # Window state persistence is independent from the explicit Save action.
    # Geometry, toolbar layout and compact mode are stored on shutdown and
    # restored at startup.
    # ------------------------------------------------------------------
    def _save_window_state(self):
        if not self.remember_window:
            return
        try:
            os.makedirs(os.path.dirname(WINDOW_STATE_PATH), exist_ok=True)
            # saveGeometry() captures position, size, maximized state and
            # screen assignment. saveState() captures toolbar layout.
            data = {
                "window_geometry": bytes(self.saveGeometry()).hex(),
                "window_state": bytes(self.saveState()).hex(),
                "compact_mode": self.compact_action.isChecked(),
            }
            with open(WINDOW_STATE_PATH, "w", encoding="utf-8") as f:
                json.dump(data, f, indent=2, ensure_ascii=False)
        except OSError:
            logger.warning("Window state could not be saved", exc_info=True)

    def _load_window_state(self):
        if not self.remember_window or not os.path.exists(WINDOW_STATE_PATH):
            return
        try:
            with open(WINDOW_STATE_PATH, "r", encoding="utf-8") as f:
                data = json.load(f)
        except Exception:
            return

        geometry_hex = data.get("window_geometry")
        if geometry_hex:
            try:
                geometry = QtCore.QByteArray(bytes.fromhex(geometry_hex))
                self.restoreGeometry(geometry)
            except (ValueError, TypeError):
                logger.warning("Stored window geometry is invalid, ignoring it")

        state_hex = data.get("window_state")
        if state_hex:
            try:
                state = QtCore.QByteArray(bytes.fromhex(state_hex))
                self.restoreState(state)
            except (ValueError, TypeError):
                logger.warning("Stored toolbar layout is invalid, ignoring it")

        self.compact_action.setChecked(data.get("compact_mode", False))

    def closeEvent(self, event):
        self._save_window_state()
        for dlg in (self._camera_manager_dialog,):
            if dlg is not None:
                dlg.close()
        for worker in self.workers.values():
            worker.stop()
        super().closeEvent(event)
