from PyQt6 import QtCore, QtGui, QtWidgets

from .frame_buffer import LatestFrameBuffer
from .i18n import tr
from .stream_config import StreamConfig


class VideoWidget(QtWidgets.QWidget):
    """
    Display the current frame of a stream. Poll via QTimer instead of firing
    a Qt signal for every frame, which keeps the widget lightweight with many
    simultaneous streams and lets the display frame rate stay independent from
    the decode frame rate.

    Enable and disable is handled exclusively through the right-click context
    menu or the camera overview. There is intentionally no button on the tile
    itself. A double-click on the tile switches to the fullscreen view of that
    stream; a second double-click switches back to the grid.
    """

    doubleClicked = QtCore.pyqtSignal(str)
    reorderRequested = QtCore.pyqtSignal(str, str)  # (dragged_id, target_id)

    # Digital zoom via Ctrl+mouse wheel (see wheelEvent/_compute_src_rect).
    # Display-only: the already decoded frame is cropped and scaled without
    # affecting decoding or network load.
    MIN_ZOOM = 1.0
    MAX_ZOOM = 6.0
    ZOOM_STEP_FACTOR = 1.2

    def __init__(self, config: StreamConfig, buffer: LatestFrameBuffer, parent=None):
        super().__init__(parent)
        self.config = config
        self.buffer = buffer
        self._last_frame_id = -1
        self._qimage = None
        self._fps = 0.0
        self._status = "connecting"
        self._recording = False
        self._drag_start_pos = None

        # Zoom and pan state (see wheelEvent). pan_x/pan_y are normalized
        # coordinates in the original image that mark the center of the
        # visible crop.
        self._zoom = 1.0
        self._pan_x = 0.5
        self._pan_y = 0.5
        # Updated on every paintEvent so wheelEvent can correctly map mouse
        # position to image coordinates (zoom around cursor instead of always
        # around image center).
        self._last_draw_rect = None
        self._last_src_rect = None
        self._last_img_size = None

        # Move the visible crop while zoomed by holding Ctrl and dragging with
        # the left mouse button. This is intentionally bound to Ctrl so it does
        # not conflict with the normal left-button drag used for tile reordering.
        self._panning = False
        self._pan_last_pos = None

        self.setMinimumSize(160, 90)
        self.setStyleSheet("background-color: black;")
        self.setAcceptDrops(True)

        self._timer = QtCore.QTimer(self)
        self._timer.timeout.connect(self._refresh)
        self.set_target_fps(config.max_fps)

        self.apply_enabled_state(config.enabled)

    def apply_enabled_state(self, enabled: bool):
        """Called from the main window when the stream enabled state changes.
        The timer is started or stopped accordingly and the widget is refreshed.
        """
        self.config.enabled = enabled
        if enabled:
            if not self._timer.isActive():
                self._timer.start()
        else:
            self._timer.stop()
            self._qimage = None
            self._last_frame_id = -1
            self._last_draw_rect = None
            self._last_src_rect = None
            self._last_img_size = None
            self._panning = False
            self._pan_last_pos = None
        self.update()

    def set_target_fps(self, fps: int):
        fps = max(1, min(fps, 60))
        self._timer.setInterval(int(1000 / fps))

    @QtCore.pyqtSlot(str)
    def set_status(self, status: str):
        self._status = status
        self.update()

    def set_recording(self, recording: bool):
        self._recording = recording
        self.update()

    def _refresh(self):
        frame, fid, fps = self.buffer.get(self._last_frame_id)
        if frame is None:
            return
        self._last_frame_id = fid
        self._fps = fps
        h, w = frame.shape[:2]
        bytes_per_line = 3 * w
        qimg = QtGui.QImage(frame.data, w, h, bytes_per_line,
                             QtGui.QImage.Format.Format_BGR888)
        self._qimage = qimg.copy()  # vom numpy-Buffer loskoppeln
        self.update()

    def paintEvent(self, event):
        painter = QtGui.QPainter(self)
        painter.fillRect(self.rect(), QtCore.Qt.GlobalColor.black)

        if not self.config.enabled:
            painter.setPen(QtCore.Qt.GlobalColor.darkGray)
            painter.drawText(self.rect(), QtCore.Qt.AlignmentFlag.AlignCenter,
                              tr("video.disabled"))
        elif self._qimage is not None:
            img_w, img_h = self._qimage.width(), self._qimage.height()
            src_rect = self._compute_src_rect(img_w, img_h)
            if self._zoom <= 1.0001:
                source_img = self._qimage
            else:
                source_img = self._qimage.copy(src_rect)
            scaled = source_img.scaled(
                self.size(),
                QtCore.Qt.AspectRatioMode.KeepAspectRatio,
                QtCore.Qt.TransformationMode.FastTransformation,
            )
            x = (self.width() - scaled.width()) // 2
            y = (self.height() - scaled.height()) // 2
            painter.drawImage(x, y, scaled)

            # Remember image placement for wheelEvent to enable zoom around cursor
            # on next scroll.
            self._last_img_size = (img_w, img_h)
            self._last_draw_rect = QtCore.QRect(x, y, scaled.width(), scaled.height())
            self._last_src_rect = src_rect
        else:
            painter.setPen(QtCore.Qt.GlobalColor.gray)
            text = tr("video.error") if self._status == "error" else tr("video.no_signal")
            painter.drawText(self.rect(), QtCore.Qt.AlignmentFlag.AlignCenter, text)

        painter.setPen(QtCore.Qt.GlobalColor.white)
        label = self.config.name
        if self.config.has_sub_stream():
            variant_label = tr("video.sub_short") if self.config.active_variant == 'sub' else tr('video.main_short')
            label += f"  [{variant_label}]"
        painter.drawText(6, 16, label)
        if self.config.enabled and self.config.show_fps:
            painter.drawText(6, self.height() - 6, f"{self._fps:.1f} {tr('display.fps')}")

        if self._zoom > 1.0001:
            painter.setPen(QtCore.Qt.GlobalColor.white)
            painter.drawText(self.width() - 46, self.height() - 6, f"{self._zoom:.1f}{tr('display.zoom')}")

        if self._recording:
            painter.setBrush(QtGui.QBrush(QtCore.Qt.GlobalColor.red))
            painter.setPen(QtCore.Qt.PenStyle.NoPen)
            painter.drawEllipse(self.width() - 76, 8, 8, 8)
            painter.setPen(QtCore.Qt.GlobalColor.white)
            painter.drawText(self.width() - 64, 16, tr("video.rec"))

    def mouseDoubleClickEvent(self, event):
        self.doubleClicked.emit(self.config.id)
        super().mouseDoubleClickEvent(event)

    # Digital zoom: Ctrl+mouse wheel over a tile increases or decreases the
    # visible crop, centered around the mouse position. This only affects
    # painting; decoding, network load and recording stay unchanged.
    def _compute_src_rect(self, img_w, img_h):
        """Returns the image crop region (in original image pixel coordinates)
        to display at the current zoom/pan level."""
        if self._zoom <= 1.0001:
            return QtCore.QRect(0, 0, img_w, img_h)
        src_w = max(1, int(img_w / self._zoom))
        src_h = max(1, int(img_h / self._zoom))
        cx = self._pan_x * img_w
        cy = self._pan_y * img_h
        src_x = int(cx - src_w / 2)
        src_y = int(cy - src_h / 2)
        src_x = max(0, min(src_x, img_w - src_w))
        src_y = max(0, min(src_y, img_h - src_h))
        return QtCore.QRect(src_x, src_y, src_w, src_h)

    def wheelEvent(self, event):
        if not (event.modifiers() & QtCore.Qt.KeyboardModifier.ControlModifier):
            # Without Ctrl: no special behavior, pass event through normally
            super().wheelEvent(event)
            return

        delta = event.angleDelta().y()
        if delta == 0 or self._last_img_size is None:
            event.accept()
            return

        factor = self.ZOOM_STEP_FACTOR if delta > 0 else (1.0 / self.ZOOM_STEP_FACTOR)
        new_zoom = max(self.MIN_ZOOM, min(self.MAX_ZOOM, self._zoom * factor))
        if abs(new_zoom - self._zoom) < 1e-6:
            event.accept()
            return

        # Before zooming: which image point is currently under the cursor?
        # This point should stay at the same location after zoom (zoom around cursor,
        # not image center).
        if self._last_draw_rect is not None and self._last_src_rect is not None:
            draw_rect = self._last_draw_rect
            if draw_rect.width() > 0 and draw_rect.height() > 0:
                pos = event.position()
                fx = (pos.x() - draw_rect.x()) / draw_rect.width()
                fy = (pos.y() - draw_rect.y()) / draw_rect.height()
                fx = min(1.0, max(0.0, fx))
                fy = min(1.0, max(0.0, fy))
                src = self._last_src_rect
                img_w, img_h = self._last_img_size
                img_x = src.x() + fx * src.width()
                img_y = src.y() + fy * src.height()
                self._pan_x = img_x / img_w
                self._pan_y = img_y / img_h

        self._zoom = new_zoom
        self.update()
        event.accept()

    def reset_zoom(self):
        self._zoom = 1.0
        self._pan_x = 0.5
        self._pan_y = 0.5
        self.update()

    def is_zoomed(self) -> bool:
        return self._zoom > 1.0001

    def _pan_by_widget_delta(self, dx_widget, dy_widget):
        """Pan the visible crop region by a delta measured in widget pixels
        (from mouseMoveEvent during drag), converted to image coordinates using
        the last drawn crop."""
        if not (self._last_draw_rect and self._last_src_rect and self._last_img_size):
            return
        draw_rect = self._last_draw_rect
        if draw_rect.width() <= 0 or draw_rect.height() <= 0:
            return
        src = self._last_src_rect
        img_w, img_h = self._last_img_size
        dx_img = dx_widget * (src.width() / draw_rect.width())
        dy_img = dy_widget * (src.height() / draw_rect.height())
        # "Grab and drag image": crop pans opposite to mouse drag direction
        cx = self._pan_x * img_w - dx_img
        cy = self._pan_y * img_h - dy_img
        self._pan_x = min(1.0, max(0.0, cx / img_w))
        self._pan_y = min(1.0, max(0.0, cy / img_h))
        self.update()

    def sizeHint(self):
        return QtCore.QSize(320, 180)

    # Drag & Drop to reorder tiles in the grid. MainWindow._make_select_handler
    # patches mousePressEvent additionally for selection highlighting, but always
    # calls the method defined here (as "original") so drag detection is preserved.
    def mousePressEvent(self, event):
        if (event.button() == QtCore.Qt.MouseButton.LeftButton
                and event.modifiers() & QtCore.Qt.KeyboardModifier.ControlModifier
                and self.is_zoomed()):
            # Ctrl+left click while zoomed -> start panning instead of tile reordering
            self._panning = True
            self._pan_last_pos = event.position()
            self.setCursor(QtCore.Qt.CursorShape.ClosedHandCursor)
            event.accept()
            return
        if event.button() == QtCore.Qt.MouseButton.LeftButton:
            self._drag_start_pos = event.position().toPoint()
        super().mousePressEvent(event)

    def mouseMoveEvent(self, event):
        if self._panning and (event.buttons() & QtCore.Qt.MouseButton.LeftButton):
            pos = event.position()
            if self._pan_last_pos is not None:
                self._pan_by_widget_delta(
                    pos.x() - self._pan_last_pos.x(), pos.y() - self._pan_last_pos.y())
            self._pan_last_pos = pos
            event.accept()
            return
        if (self._drag_start_pos is not None
                and event.buttons() & QtCore.Qt.MouseButton.LeftButton
                and (event.position().toPoint() - self._drag_start_pos).manhattanLength()
                >= QtWidgets.QApplication.startDragDistance()):
            self._start_drag()
        super().mouseMoveEvent(event)

    def mouseReleaseEvent(self, event):
        if self._panning and event.button() == QtCore.Qt.MouseButton.LeftButton:
            self._panning = False
            self._pan_last_pos = None
            self.unsetCursor()
            event.accept()
            return
        super().mouseReleaseEvent(event)

    def _start_drag(self):
        self._drag_start_pos = None
        drag = QtGui.QDrag(self)
        mime = QtCore.QMimeData()
        mime.setData("application/x-EagleEye-stream-id", self.config.id.encode("utf-8"))
        drag.setMimeData(mime)
        thumb = self.grab().scaled(
            120, 68, QtCore.Qt.AspectRatioMode.KeepAspectRatio,
            QtCore.Qt.TransformationMode.SmoothTransformation)
        drag.setPixmap(thumb)
        drag.setHotSpot(QtCore.QPoint(thumb.width() // 2, thumb.height() // 2))
        drag.exec(QtCore.Qt.DropAction.MoveAction)

    def dragEnterEvent(self, event):
        if event.mimeData().hasFormat("application/x-EagleEye-stream-id"):
            event.acceptProposedAction()

    def dropEvent(self, event):
        data = event.mimeData().data("application/x-EagleEye-stream-id")
        dragged_id = bytes(data).decode("utf-8")
        if dragged_id and dragged_id != self.config.id:
            self.reorderRequested.emit(dragged_id, self.config.id)
        event.acceptProposedAction()
