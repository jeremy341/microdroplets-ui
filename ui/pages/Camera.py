"""Dino-Lite camera page and Qt-side camera workers.

Hardware access and DNX64/OpenCV capability handling live in
``backend.camera_service``.  This module owns Qt worker threads, preview,
recording coordination, and camera controls. See ``docs/DEVELOPER_GUIDE.md``.
"""

from __future__ import annotations

from datetime import datetime
from pathlib import Path
import ctypes
import ctypes.wintypes
import platform
import queue
import threading
import time

from collections import OrderedDict

from PyQt6.QtCore import (
    QAbstractNativeEventFilter,
    QMutex,
    QMutexLocker,
    QThread,
    QTimer,
    QUrl,
    Qt,
    QSize,
    QRectF,
    QPointF,
    pyqtSignal,
)
from PyQt6.QtGui import (
    QColor,
    QDesktopServices,
    QIcon,
    QImage,
    QPainter,
    QPainterPath,
    QPen,
    QPixmap,
)
from PyQt6.QtWidgets import (
    QAbstractButton, QApplication, QComboBox, QFrame, QGridLayout, QGroupBox, QHBoxLayout, QLabel, QPushButton, QSlider,
    QSizePolicy, QVBoxLayout, QWidget,
)

try:
    import cv2
except ImportError:
    cv2 = None

from backend.camera_profiles import CameraProfileBuilder, CameraProfileStore
from backend.application_paths import CAPTURES_DIR
from backend.camera_service import (
    HARDWARE_EXPOSURE_MAX_RAW,
    CameraDevice,
    OpenCVCamera,
    am4113t_fps_options,
    enumerate_cameras,
    practical_exposure_max,
)


# Internal queue keys for DNX64-only controls. They are never forwarded as
# OpenCV property IDs; CameraWorker intercepts them before that path.
CAMERA_LED_CONTROL = -10001


def camera_frame_pixmap(frame, target_size):
    """Render one BGR camera frame as a center-cropped preview pixmap.

    Both the full Camera page and Workspace use this helper so their preview
    policy stays identical: preserve aspect ratio, fill the complete preview
    rectangle, and crop only the overflow.
    """

    if frame is None or cv2 is None or target_size.width() <= 0 or target_size.height() <= 0:
        return None
    rgb = cv2.cvtColor(frame, cv2.COLOR_BGR2RGB)
    image = QImage(
        rgb.data,
        rgb.shape[1],
        rgb.shape[0],
        rgb.strides[0],
        QImage.Format.Format_RGB888,
    )
    scaled = QPixmap.fromImage(image).scaled(
        target_size,
        Qt.AspectRatioMode.KeepAspectRatioByExpanding,
        Qt.TransformationMode.FastTransformation,
    )
    x = max(0, (scaled.width() - target_size.width()) // 2)
    y = max(0, (scaled.height() - target_size.height()) // 2)
    return scaled.copy(x, y, target_size.width(), target_size.height())


def make_folder_icon(size=18):
    """Create the thin outline folder icon used by the target toolbar."""

    pixmap = QPixmap(size, size)
    pixmap.fill(Qt.GlobalColor.transparent)
    painter = QPainter(pixmap)
    painter.setRenderHint(QPainter.RenderHint.Antialiasing)
    pen = QPen(QColor("#16232D"), 1.35)
    pen.setJoinStyle(Qt.PenJoinStyle.RoundJoin)
    painter.setPen(pen)
    painter.setBrush(Qt.BrushStyle.NoBrush)

    folder = QPainterPath()
    folder.moveTo(1.5, 5.2)
    folder.lineTo(1.5, 3.8)
    folder.quadTo(1.5, 2.5, 2.8, 2.5)
    folder.lineTo(7.1, 2.5)
    folder.lineTo(9.0, 4.7)
    folder.lineTo(size - 2.7, 4.7)
    folder.quadTo(size - 1.5, 4.7, size - 1.5, 6.0)
    folder.lineTo(size - 1.5, size - 2.4)
    folder.quadTo(size - 1.5, size - 1.3, size - 2.7, size - 1.3)
    folder.lineTo(2.7, size - 1.3)
    folder.quadTo(1.5, size - 1.3, 1.5, size - 2.5)
    folder.closeSubpath()
    painter.drawPath(folder)
    painter.end()
    return QIcon(pixmap)


def make_camera_icon(size=20):
    pixmap = QPixmap(size, size)
    pixmap.fill(Qt.GlobalColor.transparent)
    painter = QPainter(pixmap)
    painter.setRenderHint(QPainter.RenderHint.Antialiasing)
    pen = QPen(QColor("#16232D"), 1.5)
    pen.setJoinStyle(Qt.PenJoinStyle.RoundJoin)
    painter.setPen(pen)
    painter.setBrush(Qt.BrushStyle.NoBrush)
    painter.drawRoundedRect(QRectF(2, 6, 16, 11), 2, 2)
    painter.drawLine(QPointF(6, 6), QPointF(8, 3.5))
    painter.drawLine(QPointF(8, 3.5), QPointF(12, 3.5))
    painter.drawEllipse(QRectF(7, 8, 6, 6))
    painter.end()
    return QIcon(pixmap)


def make_record_icon(size=20):
    pixmap = QPixmap(size, size)
    pixmap.fill(Qt.GlobalColor.transparent)
    painter = QPainter(pixmap)
    painter.setRenderHint(QPainter.RenderHint.Antialiasing)
    pen = QPen(QColor("#16232D"), 1.7)
    painter.setPen(pen)
    painter.setBrush(Qt.BrushStyle.NoBrush)
    painter.drawEllipse(QRectF(2.5, 2.5, 15, 15))
    painter.setBrush(QColor("#16232D"))
    painter.drawEllipse(QRectF(7, 7, 6, 6))
    painter.end()
    return QIcon(pixmap)


def make_stop_icon(size=20):
    pixmap = QPixmap(size, size)
    pixmap.fill(Qt.GlobalColor.transparent)
    painter = QPainter(pixmap)
    painter.setRenderHint(QPainter.RenderHint.Antialiasing)
    painter.setPen(Qt.PenStyle.NoPen)
    painter.setBrush(QColor("#16232D"))
    painter.drawRoundedRect(QRectF(4, 4, 12, 12), 1.5, 1.5)
    painter.end()
    return QIcon(pixmap)


def make_info_icon(size=20):
    pixmap = QPixmap(size, size)
    pixmap.fill(Qt.GlobalColor.transparent)
    painter = QPainter(pixmap)
    painter.setRenderHint(QPainter.RenderHint.Antialiasing)
    pen = QPen(QColor("#31424B"), 1.35)
    painter.setPen(pen)
    painter.setBrush(Qt.BrushStyle.NoBrush)
    painter.drawEllipse(QRectF(2.5, 2.5, 15, 15))
    painter.drawLine(QPointF(10, 8.5), QPointF(10, 14))
    painter.setPen(Qt.PenStyle.NoPen)
    painter.setBrush(QColor("#31424B"))
    painter.drawEllipse(QRectF(9.1, 5.3, 1.8, 1.8))
    painter.end()
    return QIcon(pixmap)


class CameraComboBox(QComboBox):
    """Reference-style combo with a stable chevron and optional helper text."""

    def __init__(self, trailing_text="", parent=None):
        super().__init__(parent)
        self.trailing_text = trailing_text

    def paintEvent(self, event):
        super().paintEvent(event)
        painter = QPainter(self)
        painter.setRenderHint(QPainter.RenderHint.Antialiasing)
        if self.trailing_text:
            helper_font = painter.font()
            helper_font.setPointSizeF(max(8.0, helper_font.pointSizeF() - 1.0))
            painter.setFont(helper_font)
            painter.setPen(QColor("#829098"))
            painter.drawText(
                QRectF(self.width() - 142, 0, 108, self.height()),
                Qt.AlignmentFlag.AlignRight | Qt.AlignmentFlag.AlignVCenter,
                self.trailing_text,
            )

        painter.setPen(QPen(QColor("#14242D"), 1.35))
        center_x = self.width() - 16
        center_y = self.height() / 2
        painter.drawLine(QPointF(center_x - 4, center_y - 2), QPointF(center_x, center_y + 2))
        painter.drawLine(QPointF(center_x, center_y + 2), QPointF(center_x + 4, center_y - 2))


class CameraSwitch(QAbstractButton):
    def __init__(self, checked=False, parent=None):
        super().__init__(parent)
        self.setCheckable(True)
        self.setChecked(bool(checked))
        self.setFixedSize(44, 24)
        self.setCursor(Qt.CursorShape.PointingHandCursor)
        self.setFocusPolicy(Qt.FocusPolicy.StrongFocus)

    def paintEvent(self, event):
        painter = QPainter(self)
        painter.setRenderHint(QPainter.RenderHint.Antialiasing)
        painter.setPen(Qt.PenStyle.NoPen)
        track = "#1A9A72" if self.isChecked() else "#D7E3DE"
        if not self.isEnabled():
            track = "#DDE4E1"
        painter.setBrush(QColor(track))
        painter.drawRoundedRect(QRectF(0, 0, 44, 24), 12, 12)
        painter.setBrush(QColor("#FFFFFF"))
        painter.drawEllipse(QRectF(23 if self.isChecked() else 3, 3, 18, 18))
        if self.hasFocus():
            painter.setBrush(Qt.BrushStyle.NoBrush)
            painter.setPen(QPen(QColor("#0A7F60"), 1))
            painter.drawRoundedRect(QRectF(0.5, 0.5, 43, 23), 11.5, 11.5)


class CameraSlider(QSlider):
    """Native slider interaction with a deterministic instrument-style paint."""

    def __init__(self, orientation, parent=None):
        super().__init__(orientation, parent)
        self.setMouseTracking(True)
        self.setTracking(True)
        self.setSingleStep(1)
        self._pointer_dragging = False

    def _value_from_position(self, x):
        """Map the painted handle geometry to a slider value exactly."""
        handle_radius = 9.0
        usable_width = max(1.0, self.width() - (handle_radius * 2.0))
        position = max(0.0, min(usable_width, float(x) - handle_radius))
        ratio = position / usable_width
        return round(self.minimum() + ratio * (self.maximum() - self.minimum()))

    def mousePressEvent(self, event):
        if event.button() == Qt.MouseButton.LeftButton and self.isEnabled():
            self._pointer_dragging = True
            self.setSliderDown(True)
            value = self._value_from_position(event.position().x())
            self._set_pointer_value(value)
            event.accept()
            return
        super().mousePressEvent(event)

    def mouseMoveEvent(self, event):
        if self._pointer_dragging and self.isEnabled():
            self._set_pointer_value(self._value_from_position(event.position().x()))
            event.accept()
            return
        super().mouseMoveEvent(event)

    def mouseReleaseEvent(self, event):
        if self._pointer_dragging and event.button() == Qt.MouseButton.LeftButton:
            self._set_pointer_value(self._value_from_position(event.position().x()))
            self._pointer_dragging = False
            # setSliderDown(False) emits the one canonical sliderReleased()
            # signal.  The previous implementation emitted it a second time
            # manually, which queued the final exposure command twice.
            self.setSliderDown(False)
            event.accept()
            return
        super().mouseReleaseEvent(event)

    def _set_pointer_value(self, value):
        """Move the handle without forcing a valueChanged storm when needed."""

        if self.hasTracking():
            self.setValue(value)
        else:
            # With tracking disabled QSlider still emits sliderMoved while the
            # handle follows the pointer, but it postpones valueChanged until
            # the final commit.  This is the standard separation between a
            # visual drag and an expensive device write.
            self.setSliderPosition(value)

    def paintEvent(self, event):
        painter = QPainter(self)
        painter.setRenderHint(QPainter.RenderHint.Antialiasing)

        width = max(1, self.width())
        height = self.height()
        handle_radius = 9.0
        track_height = 4.0
        track_y = (height - track_height) / 2.0
        usable_width = max(0.0, width - (handle_radius * 2))
        value_range = self.maximum() - self.minimum()
        ratio = 0.0 if value_range <= 0 else (
            (self.sliderPosition() - self.minimum()) / value_range
        )
        handle_center = handle_radius + (usable_width * ratio)

        if self.isEnabled():
            rail = QColor("#D4DDDA")
            active = QColor("#239A73")
            handle_fill = QColor("#FFFFFF")
            handle_border = QColor("#B9CEC5")
        else:
            rail = QColor("#E7ECEA")
            active = rail
            handle_fill = QColor("#F7F9F8")
            handle_border = QColor("#D8E0DD")

        painter.setPen(Qt.PenStyle.NoPen)
        painter.setBrush(rail)
        painter.drawRoundedRect(QRectF(0, track_y, width, track_height), 2, 2)

        if self.isEnabled():
            painter.setBrush(active)
            painter.drawRoundedRect(
                QRectF(0, track_y, handle_center, track_height),
                2,
                2,
            )

        painter.setBrush(handle_fill)
        painter.setPen(QPen(handle_border, 1))
        painter.drawEllipse(
            QRectF(
                handle_center - handle_radius,
                height / 2.0 - handle_radius,
                handle_radius * 2,
                handle_radius * 2,
            )
        )

        if self.isEnabled() and (self.underMouse() or self.hasFocus()):
            painter.setBrush(Qt.BrushStyle.NoBrush)
            painter.setPen(QPen(QColor("#239A73"), 1))
            painter.drawEllipse(
                QRectF(
                    handle_center - handle_radius - 1,
                    height / 2.0 - handle_radius - 1,
                    (handle_radius + 1) * 2,
                    (handle_radius + 1) * 2,
                )
            )


class CameraDiscoveryWorker(QThread):
    devices_ready = pyqtSignal(object)
    failed = pyqtSignal(str)

    def run(self):
        try:
            self.devices_ready.emit(enumerate_cameras(probe_video=False))
        except Exception as exc:  # hardware/driver failures must not kill Qt
            self.failed.emit(str(exc))


class CameraOpenWorker(QThread):
    opened = pyqtSignal()
    failed = pyqtSignal(str)

    def __init__(self, camera, open_arguments):
        super().__init__()
        self.camera = camera
        self.open_arguments = open_arguments

    def run(self):
        try:
            self.camera.open(**self.open_arguments)
            self.opened.emit()
        except Exception as exc:
            self.failed.emit(str(exc))


class PhotoSaveWorker(QThread):
    saved = pyqtSignal(str)
    failed = pyqtSignal(str)

    def __init__(self, path, frame):
        super().__init__()
        self.path = Path(path)
        self.frame = frame

    def run(self):
        try:
            if cv2 is None or not cv2.imwrite(str(self.path), self.frame):
                raise RuntimeError("Could not write the image file")
            self.saved.emit(str(self.path))
        except Exception as exc:
            self.failed.emit(str(exc))
        finally:
            self.frame = None


class RecorderWorker(QThread):
    recording_ready = pyqtSignal()
    recording_error = pyqtSignal(str)
    recording_stats = pyqtSignal(int)

    def __init__(self, path, fps, size, queue_size=6):
        super().__init__()
        self.path = Path(path)
        # VideoWriter accepts fractional FPS. Keeping the measured value avoids
        # systematic playback-speed errors from rounding 29.7 -> 30, etc.
        self.fps = max(1.0, float(fps))
        self.size = (int(size[0]), int(size[1]))
        self.frames = queue.Queue(maxsize=max(2, int(queue_size)))
        self._stop_requested = threading.Event()
        self._timeline_lock = threading.Lock()
        self._recording_start_timestamp = None
        self._stop_timestamp = None
        self.dropped_frames = 0

    def enqueue(self, frame, timestamp=None):
        """Queue a frame together with its capture time.

        If encoding falls behind, the oldest queued frame is discarded to
        preserve low latency. The remaining timestamps let the writer fill the
        missing time slots by duplicating the latest encoded frame, so queue
        drops do not shorten the saved video's wall-clock duration.
        """

        if self._stop_requested.is_set():
            return
        captured_at = time.monotonic() if timestamp is None else float(timestamp)
        with self._timeline_lock:
            if self._recording_start_timestamp is None:
                self._recording_start_timestamp = captured_at
        item = (captured_at, frame)
        try:
            self.frames.put_nowait(item)
        except queue.Full:
            try:
                self.frames.get_nowait()
                self.frames.task_done()
            except queue.Empty:
                pass
            self.dropped_frames += 1
            try:
                self.frames.put_nowait(item)
            except queue.Full:
                self.dropped_frames += 1

    def request_stop(self):
        with self._timeline_lock:
            if self._stop_timestamp is None:
                self._stop_timestamp = time.monotonic()
        self._stop_requested.set()

    def run(self):
        writer = None
        first_timestamp = None
        last_frame = None
        last_output_index = -1
        try:
            writer = cv2.VideoWriter(
                str(self.path),
                cv2.VideoWriter_fourcc(*"mp4v"),
                self.fps,
                self.size,
            )
            if not writer.isOpened():
                raise RuntimeError("Could not start the MP4 encoder")
            self.recording_ready.emit()
            while not self._stop_requested.is_set() or not self.frames.empty():
                try:
                    captured_at, frame = self.frames.get(timeout=0.10)
                except queue.Empty:
                    continue
                try:
                    if frame is None or frame.shape[1::-1] != self.size:
                        # A mode-change race must never feed differently sized
                        # frames into an already-open VideoWriter.
                        self.dropped_frames += 1
                        continue
                    if first_timestamp is None:
                        with self._timeline_lock:
                            first_timestamp = self._recording_start_timestamp
                        if first_timestamp is None:
                            first_timestamp = float(captured_at)
                        # If the queue dropped the earliest frame(s) before the
                        # encoder thread consumed them, backfill those initial
                        # time slots with the first available frame instead of
                        # silently shortening the beginning of the video.
                        initial_index = max(
                            0,
                            int(((float(captured_at) - first_timestamp) * self.fps) + 0.5),
                        )
                        for _ in range(initial_index + 1):
                            writer.write(frame)
                        last_frame = frame
                        last_output_index = initial_index
                        continue

                    elapsed = max(0.0, float(captured_at) - first_timestamp)
                    output_index = max(0, int((elapsed * self.fps) + 0.5))
                    if output_index <= last_output_index:
                        # More source frames than the fixed-rate file needs.
                        # Keep the freshest frame for any future gap fill.
                        last_frame = frame
                        continue

                    while last_output_index + 1 < output_index:
                        writer.write(last_frame)
                        last_output_index += 1
                    writer.write(frame)
                    last_frame = frame
                    last_output_index = output_index
                finally:
                    self.frames.task_done()

            # Preserve recording wall-clock duration even if the final source
            # frame arrived before the user pressed Stop. At most a short tail
            # of the last valid frame is duplicated.
            if (
                writer is not None
                and first_timestamp is not None
                and last_frame is not None
                and self._stop_timestamp is not None
            ):
                with self._timeline_lock:
                    stop_timestamp = self._stop_timestamp
                elapsed = max(0.0, stop_timestamp - first_timestamp)
                target_last_index = max(0, int((elapsed * self.fps) + 0.5) - 1)
                while last_output_index < target_last_index:
                    writer.write(last_frame)
                    last_output_index += 1
        except Exception as exc:
            self.recording_error.emit(str(exc))
        finally:
            self._stop_requested.set()
            if writer is not None:
                writer.release()
            self.recording_stats.emit(self.dropped_frames)


class WindowsDeviceChangeFilter(QAbstractNativeEventFilter):
    """Translate Windows USB notifications into one delayed camera refresh."""

    WM_DEVICECHANGE = 0x0219
    RELEVANT_EVENTS = {0x0007, 0x8000, 0x8004}  # nodes changed, arrival, removal

    def __init__(self, callback):
        super().__init__()
        self.callback = callback

    def nativeEventFilter(self, event_type, message):
        if platform.system() != "Windows":
            return False, 0
        try:
            msg = ctypes.wintypes.MSG.from_address(int(message))
            if msg.message == self.WM_DEVICECHANGE and int(msg.wParam) in self.RELEVANT_EVENTS:
                QTimer.singleShot(350, self.callback)
        except (TypeError, ValueError, OSError):
            pass
        return False, 0


class CameraWorker(QThread):
    camera_error = pyqtSignal(str)
    property_result = pyqtSignal(int, object)
    mode_result = pyqtSignal(object)

    def __init__(self, camera: OpenCVCamera, width, height, target_fps, profile_store):
        super().__init__()
        self.camera = camera
        self.running = False
        self._property_mutex = QMutex()
        self._pending_properties = OrderedDict()
        self._mode_mutex = threading.Lock()
        self._pending_mode = None
        self._frame_mutex = threading.Lock()
        self._latest_frame = None
        self._recording_mutex = threading.Lock()
        self._recorder = None
        self._discard_frames = 0
        self._width = int(width)
        self._height = int(height)
        self._target_fps = int(target_fps)
        self._profile_store = profile_store
        self._profile_builder = None
        self._frame_interval = 1.0 / max(1, self._target_fps)
        self._next_frame_deadline = 0.0
        # Display-only live FPS counter. This never changes camera modes,
        # recording FPS, presets, or pacing; it only feeds the preview badge.
        self._live_fps_mutex = threading.Lock()
        self._live_fps_window_start = 0.0
        self._live_fps_frames = 0
        self._live_fps_value = 0.0

    def request_property(
        self,
        request_id: int,
        prop: int,
        value: float,
        *,
        brightness=False,
        exposure_mode=False,
        led=False,
        exposure_readback=True,
    ):
        """Queue only the newest value; never block the GUI during a control drag."""

        kind = (
            "led"
            if led
            else (
                "exposure_mode"
                if exposure_mode
                else ("brightness" if brightness else "property")
            )
        )
        locker = QMutexLocker(self._property_mutex)
        try:
            key = (kind, prop)
            replaced = self._pending_properties.get(key)
            self._pending_properties[key] = (
                request_id,
                prop,
                float(value),
                brightness,
                exposure_mode,
                led,
                exposure_readback,
            )
            return replaced[0] if replaced else None
        finally:
            locker.unlock()

    def request_mode(self, width, height, fps):
        with self._mode_mutex:
            self._pending_mode = (int(width), int(height), int(fps))


    def _take_mode(self):
        with self._mode_mutex:
            mode = self._pending_mode
            self._pending_mode = None
            return mode


    def _mode_change_pending(self):
        with self._mode_mutex:
            return self._pending_mode is not None

    def _take_properties(self):
        locker = QMutexLocker(self._property_mutex)
        try:
            pending = list(self._pending_properties.values())
            self._pending_properties.clear()
            return pending
        finally:
            locker.unlock()

    def _ensure_profile_builder(self):
        """Create the persistent identity/profile context once per camera."""

        capabilities = self.camera.capabilities
        device_id = (
            capabilities.device_id
            if capabilities and capabilities.device_id
            else f"opencv-index-{self.camera.device_index}"
        )
        if self._profile_builder is not None and self._profile_builder.device_id == str(device_id):
            return self._profile_builder

        camera_name = capabilities.camera_name if capabilities else "Camera"
        hardware_min = (
            capabilities.exposure_raw_min
            if capabilities and capabilities.exposure_raw_min > 0
            else 1
        )
        hardware_max = (
            capabilities.exposure_raw_max
            if capabilities and capabilities.exposure_raw_max > hardware_min
            else HARDWARE_EXPOSURE_MAX_RAW
        )
        self._profile_builder = CameraProfileBuilder(
            self._profile_store,
            device_id=str(device_id),
            camera_name=camera_name,
            hardware_min=hardware_min,
            hardware_max=hardware_max,
            practical_max=practical_exposure_max(hardware_min, hardware_max),
        )
        self._profile_builder.ensure_mode(self._width, self._height, self._target_fps)
        return self._profile_builder

    def _apply_pending_properties(self):
        for (
            request_id,
            prop,
            value,
            brightness,
            exposure_mode,
            led,
            exposure_readback,
        ) in self._take_properties():
            if led:
                result = self.camera.set_led_enabled(bool(value))
            elif exposure_mode:
                result = self.camera.set_auto_exposure(bool(value))
            elif brightness:
                result = self.camera.set_brightness_percent(value)
            elif cv2 is not None and prop == cv2.CAP_PROP_EXPOSURE:
                result = self.camera.set_exposure(value, readback=exposure_readback)
            else:
                result = self.camera.set_property_verified(prop, value)
            if (
                cv2 is not None
                and prop == cv2.CAP_PROP_EXPOSURE
                and not exposure_mode
                and exposure_readback
            ):
                builder = self._ensure_profile_builder()
                builder.record_exposure_readback(
                    self._width,
                    self._height,
                    self._target_fps,
                    value,
                    result.applied,
                    verified=bool(result.supported and result.applied is not None),
                )
            self.property_result.emit(request_id, result)

    def _apply_pending_mode(self):
        mode = self._take_mode()
        if mode is None:
            return
        width, height, fps = mode
        try:
            # Do not leave an old-resolution frame visible while the capture
            # graph switches mode and the first transition frames are discarded.
            self.camera.clear_latest_frame()
            actual_width, actual_height, actual_fps = self.camera.set_video_mode(
                width, height, fps
            )
            self.camera._manual_exposure = None
            self._width = actual_width
            self._height = actual_height
            self._target_fps = fps
            self._frame_interval = 1.0 / max(1, self._target_fps)
            self._next_frame_deadline = 0.0
            self._reset_live_fps()
            self._discard_frames = 2
            builder = self._ensure_profile_builder()
            builder.ensure_mode(actual_width, actual_height, self._target_fps)
            self.mode_result.emit(
                {
                    "ok": True,
                    "width": actual_width,
                    "height": actual_height,
                    "fps": actual_fps,
                    "target_fps": fps,
                }
            )
        except Exception as exc:
            self.mode_result.emit({"ok": False, "message": str(exc)})


    def _reset_live_fps(self):
        with self._live_fps_mutex:
            self._live_fps_window_start = time.monotonic()
            self._live_fps_frames = 0
            self._live_fps_value = 0.0

    def _count_live_frame(self, now):
        """Update the display-only live FPS value once per ~1 second window."""

        with self._live_fps_mutex:
            if self._live_fps_window_start <= 0.0:
                self._live_fps_window_start = now
            self._live_fps_frames += 1
            elapsed = now - self._live_fps_window_start
            if elapsed >= 1.0:
                self._live_fps_value = self._live_fps_frames / elapsed
                self._live_fps_window_start = now
                self._live_fps_frames = 0

    def live_fps(self):
        with self._live_fps_mutex:
            return float(self._live_fps_value)

    def run(self):
        self.running = True
        self._reset_live_fps()
        while self.running:
            self._apply_pending_mode()
            self._apply_pending_properties()

            # The AM4113T mode is fixed by the UI preset. DirectShow receives
            # the same CAP_PROP_FPS request, while this small pacing gate is a
            # deterministic fallback if the driver ignores that request. It
            # never measures or estimates FPS; it only enforces the selected
            # maximum delivery rate.
            if self._next_frame_deadline:
                delay = self._next_frame_deadline - time.monotonic()
                if delay > 0:
                    time.sleep(min(delay, self._frame_interval))
                    continue

            ok, frame = self.camera.read()
            if not ok or frame is None:
                self.camera_error.emit("The camera stopped returning frames.")
                break
            if self._discard_frames:
                self._discard_frames -= 1
                continue
            now = time.monotonic()
            self._next_frame_deadline = now + self._frame_interval
            self._count_live_frame(now)
            # Publish only frames accepted after mode-change discard. This is
            # the shared runtime frame observed by both Camera and Workspace.
            self.camera.publish_latest_frame(frame)
            with self._frame_mutex:
                self._latest_frame = frame
            with self._recording_mutex:
                recorder = self._recorder
            if recorder is not None:
                recorder.enqueue(frame, timestamp=now)
        self._apply_pending_mode()
        self._apply_pending_properties()

    def take_latest_frame(self):
        with self._frame_mutex:
            frame = self._latest_frame
            self._latest_frame = None
            return frame

    def set_recorder(self, recorder):
        """Attach a recorder only while the capture mode is stable."""

        with self._recording_mutex:
            if (
                not self.running
                or self._mode_change_pending()
                or self._recorder is not None
            ):
                return False
            self._recorder = recorder
            return True

    def clear_recorder(self):
        with self._recording_mutex:
            recorder = self._recorder
            self._recorder = None
            return recorder

    def stop(self):
        self.running = False
        self.wait(1500)


class CameraPage(QWidget):
    def __init__(self):
        super().__init__()
        self.setObjectName("cameraPage")
        self._shutting_down = False
        self.camera = OpenCVCamera()
        self.worker: CameraWorker | None = None
        self._open_worker: CameraOpenWorker | None = None
        self._discovery_worker: CameraDiscoveryWorker | None = None
        self._profile_store = CameraProfileStore()
        self._recorders = set()
        self._active_recorder: RecorderWorker | None = None
        self._photo_workers = set()
        self.devices: list[CameraDevice] = []
        self.last_frame = None
        self.recording = False
        self.recording_path = None
        self._control_request_id = 0
        self._latest_control_request = {}
        self._request_controls = {}
        self._manual_exposure_after_mode = None
        self._pending_exposure_percent = None
        self._active_resolution = None
        self._mode_change_in_progress = False
        self._exposure_timer = QTimer(self)
        self._exposure_timer.setSingleShot(True)
        self._exposure_timer.setInterval(140)
        self._exposure_timer.timeout.connect(self._flush_pending_exposure)
        self._preview_timer = QTimer(self)
        # Start at the default 10-FPS AM4113T preset. The interval follows the
        # selected fixed preset and is never derived from measured frame rate.
        self._preview_timer.setInterval(100)
        self._preview_timer.timeout.connect(self._pull_latest_frame)
        self.capture_folder = CAPTURES_DIR
        self.capture_folder.mkdir(exist_ok=True)
        self._build_ui()
        self._refresh_fps_options_for_resolution(1280, 1024, preserve_fps=10)
        self._sync_preview_timer_to_selected_fps()

    def _build_ui(self):
        root = QVBoxLayout(self)
        root.setContentsMargins(32, 16, 32, 24)
        root.setSpacing(16)

        toolbar_card = QFrame()
        toolbar_card.setObjectName("cameraToolbarCard")
        top = QHBoxLayout(toolbar_card)
        top.setObjectName("cameraToolbarLayout")
        top.setContentsMargins(24, 14, 24, 14)
        # Keep the toolbar compact: this is the visible gap between Search and
        # the Open Capture Folder action. The action-group's own 18 px spacing
        # (Open Folder -> Start Camera) stays unchanged.
        top.setSpacing(9)
        self._camera_toolbar = top

        self.device_combo = CameraComboBox()
        self.device_combo.setObjectName("cameraCombo")
        self.device_combo.setProperty("toolbar", True)
        self.device_combo.setPlaceholderText("Select camera device")
        self.device_combo.setFixedHeight(50)
        self.device_combo.setMinimumWidth(300)
        self.device_combo.setSizePolicy(
            QSizePolicy.Policy.Expanding, QSizePolicy.Policy.Fixed
        )
        self.device_combo.currentIndexChanged.connect(self._device_changed)
        # Do not give this item stretch space. With a fixed combo width, a
        # stretched layout cell can visually push the widget inward. Keeping
        # the cell tight makes its left edge match the Capture button below.
        top.addWidget(self.device_combo)

        self._status_spacer = QWidget()
        self._status_spacer.setFixedWidth(0)
        top.addWidget(self._status_spacer)
        self.status = QPushButton("Search")
        self.status.setObjectName("cameraStatus")
        self.status.setFixedSize(140, 50)
        self.status.setCursor(Qt.CursorShape.PointingHandCursor)
        self.status.clicked.connect(self.refresh_devices)
        top.addWidget(self.status)

        toolbar_actions = QWidget()
        toolbar_actions.setObjectName("cameraToolbarActions")
        toolbar_actions_layout = QHBoxLayout(toolbar_actions)
        toolbar_actions_layout.setContentsMargins(0, 0, 0, 0)
        toolbar_actions_layout.setSpacing(18)
        self._toolbar_actions = toolbar_actions

        self.open_folder_button = QPushButton("Open Capture Folder")
        self.open_folder_button.setObjectName("cameraSecondaryButton")
        self.open_folder_button.setFixedSize(185, 50)
        self.open_folder_button.setIcon(make_folder_icon())
        self.open_folder_button.setIconSize(QSize(18, 18))
        self.open_folder_button.clicked.connect(self.open_capture_folder)
        toolbar_actions_layout.addWidget(self.open_folder_button)
        self.start_button = QPushButton("Start Camera")
        self.start_button.setObjectName("cameraPrimaryButton")
        self.start_button.setFixedSize(180, 50)
        self.start_button.clicked.connect(self.toggle_camera)
        toolbar_actions_layout.addWidget(self.start_button)
        top.addWidget(toolbar_actions)
        root.addWidget(toolbar_card)

        body_grid = QGridLayout()
        body_grid.setContentsMargins(0, 0, 0, 0)
        body_grid.setHorizontalSpacing(16)
        body_grid.setVerticalSpacing(16)
        self._body_grid = body_grid

        preview_card = QGroupBox("Live preview")
        preview_card.setObjectName("cameraCard")
        preview_layout = QVBoxLayout(preview_card)
        preview_layout.setContentsMargins(24, 22, 24, 18)
        preview_layout.setSpacing(16)
        self.preview = QLabel("Camera is stopped")
        self.preview.setObjectName("cameraPreview")
        self.preview.setAlignment(Qt.AlignmentFlag.AlignCenter)
        # The old 560x400 minimum was larger than the available card at the
        # normal 1400x800 window size. Qt then squeezed the action row into the
        # preview. Let the card negotiate its height instead.
        self.preview.setMinimumSize(0, 240)
        self.preview.setSizePolicy(QSizePolicy.Policy.Expanding, QSizePolicy.Policy.Expanding)
        preview_shell = QWidget()
        preview_shell.setObjectName("cameraPreviewShell")
        preview_shell_layout = QGridLayout(preview_shell)
        preview_shell_layout.setContentsMargins(0, 0, 0, 0)
        preview_shell_layout.setSpacing(0)
        preview_shell_layout.addWidget(self.preview, 0, 0)
        self.fps_overlay = QLabel("0 FPS", preview_shell)
        self.fps_overlay.setObjectName("cameraFpsOverlay")
        self.fps_overlay.setAlignment(Qt.AlignmentFlag.AlignCenter)
        self.fps_overlay.setVisible(False)
        preview_shell_layout.addWidget(
            self.fps_overlay,
            0,
            0,
            Qt.AlignmentFlag.AlignTop | Qt.AlignmentFlag.AlignRight,
        )
        preview_layout.addWidget(preview_shell, 1)
        actions = QHBoxLayout()
        actions.setSpacing(14)
        self.capture_button = QPushButton("Capture")
        self.capture_button.setObjectName("cameraActionButton")
        self.capture_button.setIcon(make_camera_icon())
        self.capture_button.setIconSize(QSize(20, 20))
        self.capture_button.setMinimumWidth(140)
        self.capture_button.setFixedHeight(48)
        self.capture_button.setEnabled(False)
        self.capture_button.clicked.connect(self.capture_photo)
        self.record_button = QPushButton("Record")
        self.record_button.setObjectName("cameraActionButton")
        self.record_button.setIcon(make_record_icon())
        self.record_button.setIconSize(QSize(20, 20))
        self.record_button.setMinimumWidth(140)
        self.record_button.setFixedHeight(48)
        self.record_button.setEnabled(False)
        self.record_button.clicked.connect(self.toggle_recording)
        self.stop_button = QPushButton("Stop")
        self.stop_button.setObjectName("cameraActionButton")
        self.stop_button.setIcon(make_stop_icon())
        self.stop_button.setIconSize(QSize(20, 20))
        self.stop_button.setMinimumWidth(140)
        self.stop_button.setFixedHeight(48)
        self.stop_button.setEnabled(False)
        self.stop_button.clicked.connect(self.stop_camera)
        actions.addWidget(self.capture_button, 1)
        actions.addWidget(self.record_button, 1)
        actions.addWidget(self.stop_button, 1)
        preview_layout.addLayout(actions)
        body_grid.addWidget(preview_card, 0, 0)

        controls = QGroupBox("Camera controls")
        controls.setObjectName("cameraCard")
        controls_layout = QVBoxLayout(controls)
        controls_layout.setContentsMargins(24, 22, 24, 18)
        # The LED rows fit into the existing card by using the tighter vertical
        # rhythm from the reference. Card geometry and the preview stay intact.
        controls_layout.setSpacing(8)
        controls.setMinimumWidth(390)
        controls.setMaximumWidth(16777215)
        controls.setSizePolicy(QSizePolicy.Policy.Expanding, QSizePolicy.Policy.Expanding)

        self.resolution_combo = self._combo_row(
            controls_layout, "Resolution", ["1280 × 1024", "640 × 480"]
        )
        self.resolution_combo.setItemData(0, (1280, 1024))
        self.resolution_combo.setItemData(1, (640, 480))
        self.resolution_combo.currentTextChanged.connect(self.apply_resolution)
        controls_layout.addSpacing(4)

        self.fps_combo = self._combo_row(
            controls_layout,
            "FPS",
            ["10 FPS"],
        )
        self.fps_combo.currentTextChanged.connect(self.apply_fps)
        controls_layout.addSpacing(8)

        exposure_row = QHBoxLayout()
        exposure_row.setObjectName("cameraExposureHeader")
        exposure_label = QLabel("Exposure")
        exposure_label.setObjectName("cameraFieldLabel")
        exposure_row.addWidget(exposure_label)
        exposure_row.addStretch()
        self.exposure_device_value = QLabel("50 %")
        self.exposure_device_value.setObjectName("cameraDeviceValue")
        self.exposure_device_value.setMinimumWidth(58)
        self.exposure_device_value.setFixedWidth(58)
        self.exposure_device_value.setAlignment(
            Qt.AlignmentFlag.AlignRight | Qt.AlignmentFlag.AlignVCenter
        )
        self.exposure_device_value.setAttribute(Qt.WidgetAttribute.WA_TransparentForMouseEvents)
        exposure_row.addWidget(self.exposure_device_value)
        auto_label = QLabel("Auto")
        auto_label.setObjectName("cameraFieldLabel")
        self.auto_exposure = CameraSwitch(True)
        self.auto_exposure.setObjectName("cameraToggleSwitch")
        self.auto_exposure.setAccessibleName("Automatic exposure")
        self.auto_exposure.toggled.connect(self.apply_exposure_mode)
        exposure_row.addWidget(auto_label)
        exposure_row.addWidget(self.auto_exposure)
        controls_layout.addLayout(exposure_row)

        self.exposure_slider = CameraSlider(Qt.Orientation.Horizontal)
        self.exposure_slider.setObjectName("parameterSlider")
        self.exposure_slider.setRange(0, 100)
        self.exposure_slider.setValue(50)
        self.exposure_slider.setMinimumHeight(30)
        exposure_slider_row = QHBoxLayout()
        exposure_slider_row.setContentsMargins(0, 0, 0, 0)
        exposure_slider_row.setSpacing(0)
        exposure_slider_row.addWidget(self.exposure_slider, 1)
        controls_layout.addLayout(exposure_slider_row)
        controls_layout.addSpacing(6)
        # The handle remains live during a drag, but the hardware receives a
        # coalesced preview request rather than one request per mouse event.
        self.exposure_slider.setTracking(False)
        self.exposure_slider.sliderMoved.connect(self._preview_exposure)
        self.exposure_slider.valueChanged.connect(self._exposure_value_changed)
        self.exposure_slider.sliderReleased.connect(self._flush_exposure_now)
        self.exposure_slider.setEnabled(False)
        self._set_exposure_value_state(False)
        self.exposure_device_value.setVisible(False)

        self.brightness_slider = self._slider_row(
            controls_layout,
            "Brightness",
            0,
            100,
            50,
            value_above=True,
        )
        self.brightness_slider.valueChanged.connect(self.apply_brightness)
        controls_layout.addSpacing(6)

        # Reference LED row: a simple on/off switch. The DNX64 vendor notes
        # that LED state changes require an established preview, so the switch
        # is enabled only while a supported camera is actually running.
        led_row = QHBoxLayout()
        led_row.setContentsMargins(0, 0, 0, 0)
        led_row.setSpacing(8)
        led_label = QLabel("LED")
        led_label.setObjectName("cameraFieldLabel")
        led_row.addWidget(led_label)
        led_row.addStretch()
        self.led_switch = CameraSwitch(True)
        self.led_switch.setObjectName("cameraLedSwitch")
        self.led_switch.setAccessibleName("Camera LED")
        self.led_switch.setEnabled(False)
        self.led_switch.toggled.connect(self.apply_led_enabled)
        led_row.addWidget(self.led_switch)
        controls_layout.addLayout(led_row)
        controls_layout.addSpacing(6)

        divider = QWidget()
        divider.setObjectName("cameraDivider")
        divider.setFixedHeight(1)
        controls_layout.addWidget(divider)
        focus_row = QHBoxLayout()
        focus_row.setContentsMargins(0, 0, 0, 0)
        focus_row.setSpacing(8)
        focus_icon = QLabel()
        focus_icon.setPixmap(make_info_icon(20).pixmap(20, 20))
        focus_icon.setFixedSize(20, 20)
        focus_hint = QLabel("Focus & magnification: Manual")
        focus_hint.setObjectName("cameraFocusHint")
        focus_row.addWidget(focus_icon)
        focus_row.addWidget(focus_hint)
        focus_row.addStretch()
        controls_layout.addLayout(focus_row)
        controls_layout.addStretch()
        self._controls_card = controls
        controls.setMinimumHeight(560)
        body_grid.addWidget(controls, 0, 1, 2, 1)

        last = QGroupBox("Last capture")
        last.setObjectName("cameraCard")
        last_layout = QHBoxLayout(last)
        last_layout.setContentsMargins(20, 14, 20, 14)
        last_layout.setSpacing(14)
        capture_icon = QLabel()
        capture_icon.setObjectName("cameraCaptureIcon")
        capture_icon.setPixmap(make_folder_icon(20).pixmap(20, 20))
        capture_icon.setAlignment(Qt.AlignmentFlag.AlignCenter)
        capture_icon.setFixedSize(44, 44)
        last_layout.addWidget(capture_icon)
        self.last_capture = QLabel("no image or video captured yet")
        self.last_capture.setObjectName("cameraHint")
        last_layout.addWidget(self.last_capture)
        last.setSizePolicy(QSizePolicy.Policy.Expanding, QSizePolicy.Policy.Fixed)
        body_grid.addWidget(last, 1, 0)
        root.addLayout(body_grid, 1)

        self._update_reference_geometry()

    def resizeEvent(self, event):
        super().resizeEvent(event)
        self._update_reference_geometry()

    def _update_reference_geometry(self):
        """Keep toolbar edges and card proportions aligned at both references.

        The compact toolbar reference is approximately 1086 px wide inside the
        page; the complete-window reference is approximately 1268 px wide.
        Interpolating between those measured states prevents fixed
        widths from leaving unused space or pushing the right buttons inward.
        """
        inner_width = max(0, self.width() - 64)
        scale = max(0.0, min(1.0, (inner_width - 1086.0) / 182.0))

        def lerp(compact, wide):
            return round(compact + (wide - compact) * scale)

        controls_width = max(360, min(450, round(inner_width * 0.35)))
        self._controls_card.setFixedWidth(controls_width)
        start_width = max(124, min(148, round(controls_width * 0.34)))
        self.start_button.setFixedWidth(start_width)
        self.open_folder_button.setFixedWidth(185)
        # Give the action group exactly the room its two fixed-width buttons
        # need.  Previously the container width could fight the child widths.
        self._toolbar_actions.setFixedWidth(185 + 18 + start_width)
        self.device_combo.setFixedWidth(max(260, min(337, round(inner_width * 0.265))))
        # Keep this slot fixed; changing it based on the status text moves the
        # device selector and the toolbar actions when an error appears.
        self.status.setFixedWidth(140)

        # Align Search's right edge exactly with the outer right edge of the
        # Live preview card. Both cards share the same page-left edge; the
        # toolbar has a 24 px inner margin, so only the actual combo width,
        # the two toolbar gaps before Search, and Search itself are subtracted.
        preview_width = max(0, inner_width - 16 - controls_width)
        toolbar_gap = self._camera_toolbar.spacing()
        spacer_width = max(
            0,
            preview_width
            - 24
            - self.device_combo.width()
            - self.status.width()
            - (2 * toolbar_gap),
        )
        self._status_spacer.setFixedWidth(spacer_width)

        # Keep the two main cards aligned while Last capture only sits below
        # the Live preview column, matching the reference layout.
        self._body_grid.setColumnStretch(0, 13)
        self._body_grid.setColumnStretch(1, 10)
        self._body_grid.setRowStretch(0, 1)
        self._body_grid.setRowStretch(1, 0)

    def _combo_row(self, layout, label, values, trailing_text=""):
        row = QHBoxLayout()
        row.setContentsMargins(0, 0, 0, 0)
        row.setSpacing(12)
        field_label = QLabel(label)
        field_label.setObjectName("cameraFieldLabel")
        field_label.setFixedWidth(115)
        row.addWidget(field_label)
        combo = CameraComboBox(trailing_text)
        combo.setObjectName("cameraCombo")
        combo.addItems(values)
        combo.setFixedHeight(44)
        row.addWidget(combo, 1)
        layout.addLayout(row)
        return combo

    def _slider_row(self, layout, label, minimum, maximum, value, value_above=False):
        value_label = QLabel(f"{value} %" if label else "")
        value_label.setObjectName("cameraValue")
        value_label.setFixedWidth(50)
        value_label.setAlignment(Qt.AlignmentFlag.AlignRight | Qt.AlignmentFlag.AlignVCenter)

        if label and value_above:
            header = QHBoxLayout()
            header.setContentsMargins(0, 0, 0, 0)
            header.setSpacing(8)
            field_label = QLabel(label)
            field_label.setObjectName("cameraFieldLabel")
            header.addWidget(field_label)
            header.addStretch()
            header.addWidget(value_label)
            layout.addLayout(header)
        elif label:
            field_label = QLabel(label)
            field_label.setObjectName("cameraFieldLabel")
            layout.addWidget(field_label)

        row = QHBoxLayout()
        row.setContentsMargins(0, 0, 0, 0)
        row.setSpacing(12)
        slider = CameraSlider(Qt.Orientation.Horizontal)
        slider.setObjectName("parameterSlider")
        slider.setRange(minimum, maximum)
        slider.setValue(value)
        row.addWidget(slider, 1)
        if not value_above:
            row.addWidget(value_label)
        slider.valueChanged.connect(lambda number, target=value_label: target.setText(f"{number} %"))
        slider._camera_value_label = value_label
        layout.addLayout(row)
        return slider

    def refresh_devices(self):
        if self._shutting_down:
            return
        if self._discovery_worker is not None and self._discovery_worker.isRunning():
            return
        self._set_status("Searching…")
        worker = CameraDiscoveryWorker(self)
        self._discovery_worker = worker
        worker.devices_ready.connect(self._devices_ready)
        worker.failed.connect(self._device_scan_failed)
        worker.finished.connect(worker.deleteLater)
        worker.finished.connect(self._discovery_finished)
        worker.start()

    def _devices_ready(self, devices):
        selected_device_id = None
        selected_index = self.device_combo.currentData()
        for device in self.devices:
            if device.index == selected_index:
                selected_device_id = device.device_id
                break
        self.devices = list(devices)
        self.device_combo.blockSignals(True)
        self.device_combo.clear()
        for device in self.devices:
            self.device_combo.addItem(device.label, device.index)
        if selected_device_id:
            for combo_index, device in enumerate(self.devices):
                if device.device_id == selected_device_id:
                    self.device_combo.setCurrentIndex(combo_index)
                    break
        if not self.devices:
            self.device_combo.addItem("No cameras detected", -1)
            # Keep actions available as safe no-ops before hardware is connected.
            self.start_button.setEnabled(True)
            self._set_status("Search again")
        else:
            self.start_button.setEnabled(True)
            self._set_status("Ready")
        self.capture_button.setEnabled(True)
        self.record_button.setEnabled(True)
        self.stop_button.setEnabled(True)
        self.device_combo.blockSignals(False)

    def _device_scan_failed(self, message):
        self._set_status("Retry search")
        self.status.setToolTip(message)

    def _discovery_finished(self):
        self._discovery_worker = None

    def _set_status(self, text: str):
        self.status.setText(text)
        self.status.setProperty("state", "ready" if text == "Ready" else "neutral")
        self.status.setFixedSize(140, 50)
        self.status.setEnabled(text != "Searching…")
        self.status.setToolTip(text)
        self.status.style().unpolish(self.status)
        self.status.style().polish(self.status)

    def _set_exposure_value_state(self, enabled):
        self.exposure_device_value.setProperty(
            "state",
            "active" if enabled else "disabled",
        )
        self.exposure_device_value.style().unpolish(self.exposure_device_value)
        self.exposure_device_value.style().polish(self.exposure_device_value)

    def _device_changed(self, _index):
        if self.camera.is_open:
            self.stop_camera()

    def toggle_camera(self):
        self.stop_camera() if self.camera.is_open else self.start_camera()

    def start_camera(self):
        index = self.device_combo.currentData()
        if index is None or int(index) < 0 or self._open_worker is not None:
            return
        device = next((item for item in self.devices if item.index == int(index)), None)
        width, height = self._selected_resolution()
        arguments = {
            "index": int(index),
            "width": width,
            "height": height,
            "fps": self._fps(),
            "sdk_index": device.sdk_index if device else None,
            "device_name": device.label if device else None,
            "device_id": device.device_id if device else None,
            "sdk_config": device.sdk_config if device else None,
        }
        opener = CameraOpenWorker(self.camera, arguments)
        self._open_worker = opener
        opener.opened.connect(self._camera_opened)
        opener.failed.connect(self._camera_open_failed)
        opener.finished.connect(self._camera_open_thread_finished)
        opener.finished.connect(opener.deleteLater)
        opener.start()
        self._set_status("Starting…")
        self.start_button.setEnabled(False)
        self.preview.setText("Opening camera…")

    def _camera_opened(self):
        opener = self._open_worker
        if opener is None or not self.camera.is_open:
            return
        self._apply_detected_camera_options()
        self._sync_camera_controls()

        # Discovery may initially fall back to an OpenCV label such as
        # "Camera 0". If DNX64 attaches successfully once the device is open,
        # replace that generic label with the actual Dino-Lite identity.
        capabilities = self.camera.capabilities
        if capabilities and self.camera._dnx64 is not None:
            current = self.device_combo.currentIndex()
            if current >= 0 and capabilities.camera_name:
                self.device_combo.setItemText(current, capabilities.camera_name)
            dll_path = getattr(self.camera, "dnx64_dll_path", None)
            self.status.setToolTip(
                f"DNX64 connected{f' via {dll_path}' if dll_path else ''}"
            )
        else:
            diagnostic = getattr(self.camera, "dnx64_status", "DNX64 unavailable")
            self.status.setToolTip(
                "Video is running through OpenCV/DirectShow, but Dino-Lite "
                f"controls are not attached: {diagnostic}"
            )

        width, height = self._selected_resolution()
        self.worker = CameraWorker(
            self.camera,
            width,
            height,
            self._fps(),
            self._profile_store,
        )
        self.worker.camera_error.connect(self._camera_error)
        self.worker.property_result.connect(self._camera_property_result)
        self.worker.mode_result.connect(self._camera_mode_result)
        self.worker.start()
        self._preview_timer.start()
        self._mode_change_in_progress = False
        self._active_resolution = self._selected_resolution()
        self._refresh_fps_options_for_resolution(*self._selected_resolution())
        self._sync_preview_timer_to_selected_fps()
        self._queue_camera_property(
            cv2.CAP_PROP_AUTO_EXPOSURE,
            1.0 if self.auto_exposure.isChecked() else 0.0,
            "Automatic exposure",
            exposure_mode=True,
        )
        # DNX64 documents LED writes as valid only once preview is established.
        # At this point the OpenCV preview is open and CameraWorker owns all SDK
        # writes, so no LED call blocks Qt's GUI thread.
        if self.led_switch.isEnabled():
            self._queue_camera_property(
                CAMERA_LED_CONTROL,
                1.0 if self.led_switch.isChecked() else 0.0,
                "LED",
                led=True,
            )
        self._set_status("Ready")
        self.start_button.setText("Stop Camera")
        self.start_button.setEnabled(True)
        self.stop_button.setEnabled(True)
        self.capture_button.setEnabled(True)
        self.record_button.setEnabled(True)

    def _camera_open_failed(self, message):
        self.camera.close()
        self._set_status("Camera error")
        self.preview.setText(message)
        self.start_button.setText("Start Camera")
        self.start_button.setEnabled(True)

    def _camera_open_thread_finished(self):
        finished_worker = self.sender()
        if self._open_worker is finished_worker:
            self._open_worker = None

    def stop_camera(self):
        self.stop_recording()
        self._preview_timer.stop()
        self._exposure_timer.stop()
        if self.worker is not None:
            self.worker.stop()
            self.worker = None
        self.camera.close()
        self.led_switch.setEnabled(False)
        self.start_button.setText("Start Camera")
        # These remain clickable safe no-ops while disconnected so the page
        # stays discoverable and keeps consistent hover affordances.
        self.stop_button.setEnabled(True)
        self.capture_button.setEnabled(True)
        self.record_button.setEnabled(True)
        self.preview.setText("Camera is stopped")
        self._mode_change_in_progress = False
        self._active_resolution = None
        self.last_frame = None
        self.fps_overlay.setVisible(False)

    def on_frame(self, frame):
        # VideoCapture returns a new ndarray for every read, so retaining the
        # reference is safe and avoids one full-resolution copy per UI frame.
        self.last_frame = frame
        pixmap = camera_frame_pixmap(frame, self.preview.size())
        if pixmap is not None:
            self.preview.setPixmap(pixmap)

    def _current_frame(self):
        """Return the shared runtime frame even when this page is hidden."""

        frame = self.camera.latest_frame()
        return frame if frame is not None else self.last_frame

    def capture_photo(self):
        frame = self._current_frame()
        if (
            frame is None
            or cv2 is None
            or not self.camera.is_open
            or self.worker is None
            or not self.worker.isRunning()
        ):
            return
        self.capture_folder.mkdir(exist_ok=True)
        now = datetime.now()
        path = self.capture_folder / f"camera_{now:%Y%m%d_%H%M%S}.png"
        saver = PhotoSaveWorker(path, frame.copy())
        self._photo_workers.add(saver)
        saver.saved.connect(self._photo_saved)
        saver.failed.connect(lambda message: self._set_camera_control_error("Photo", message))
        saver.finished.connect(lambda worker=saver: self._photo_worker_finished(worker))
        saver.start()

    def _photo_saved(self, path_text):
        path = Path(path_text)
        self.last_capture.setText(f"{path.name}   ·   {datetime.now():%H:%M:%S}")

    def _photo_worker_finished(self, worker):
        self._photo_workers.discard(worker)
        worker.deleteLater()

    def toggle_recording(self):
        self.stop_recording() if self.recording else self.start_recording()

    def start_recording(self):
        frame = self._current_frame()
        if (
            frame is None
            or cv2 is None
            or not self.camera.is_open
            or self.worker is None
            or not self.worker.isRunning()
            or self._mode_change_in_progress
        ):
            return
        height, width = frame.shape[:2]
        if self._active_resolution and (width, height) != tuple(self._active_resolution):
            # The visible frame still belongs to the previous mode.
            return
        path = self.capture_folder / f"recording_{datetime.now():%Y%m%d_%H%M%S}.mp4"
        selected_fps = self._fps()
        recorder = RecorderWorker(path, float(selected_fps), (width, height))
        # Worker-side admission is the final race-proof gate: a pending mode
        # change that started after the UI guard above rejects the recorder.
        if not self.worker.set_recorder(recorder):
            return
        self._recorders.add(recorder)
        self._active_recorder = recorder
        recorder.recording_error.connect(self._recording_error)
        recorder.recording_stats.connect(
            lambda dropped, worker=recorder: self._recording_finished(worker, dropped)
        )
        recorder.finished.connect(lambda worker=recorder: self._recorder_thread_finished(worker))
        recorder.start()
        self.recording_path = path
        self.recording = True
        self.record_button.setText("Recording…")

    def stop_recording(self):
        recorder = self.worker.clear_recorder() if self.worker is not None else None
        if recorder is None:
            recorder = self._active_recorder
        if recorder is not None:
            recorder.request_stop()
        self._active_recorder = None
        self.recording = False
        self.record_button.setText("Record")

    def _recording_error(self, message):
        self._set_status("Recording error")
        self.status.setToolTip(message)
        self.recording = False
        self.record_button.setText("Record")

    def _recording_finished(self, recorder, dropped_frames):
        if recorder.path.exists():
            self.last_capture.setText(
                f"{recorder.path.name}   ·   {datetime.now():%H:%M:%S}"
            )
        if dropped_frames:
            self.status.setToolTip(
                f"Recording completed; {dropped_frames} old frames were dropped to keep latency low."
            )
        if self.recording_path == recorder.path:
            self.recording_path = None

    def _recorder_thread_finished(self, recorder):
        self._recorders.discard(recorder)
        recorder.deleteLater()

    def open_capture_folder(self):
        self.capture_folder.mkdir(exist_ok=True)
        QDesktopServices.openUrl(QUrl.fromLocalFile(str(self.capture_folder.resolve())))

    def _fps(self):
        data = self.fps_combo.currentData()
        if isinstance(data, int):
            return int(data)
        text = self.fps_combo.currentText().strip()
        return int(text.split()[0]) if text else 30

    def _sync_preview_timer_to_selected_fps(self):
        """Match preview refresh to the selected fixed AM4113T preset."""

        self._preview_timer.setInterval(max(1, round(1000 / max(1, self._fps()))))

    def _selected_resolution(self):
        data = self.resolution_combo.currentData()
        if isinstance(data, (tuple, list)) and len(data) == 2:
            return int(data[0]), int(data[1])
        text = self.resolution_combo.currentText().replace("×", "x")
        try:
            width, height = text.lower().split("x", 1)
            return int(width.strip()), int(height.strip())
        except (TypeError, ValueError):
            return 1280, 1024


    def _queue_mode_change(self):
        if self.worker is None or not self.worker.isRunning():
            return
        if self.recording:
            self.stop_recording()
        width, height = self._selected_resolution()
        self._mode_change_in_progress = True
        self.last_frame = None
        self.fps_overlay.setVisible(False)
        self._set_status("Applying…")
        self.worker.request_mode(width, height, self._fps())

    def apply_resolution(self):
        width, height = self._selected_resolution()
        self._refresh_fps_options_for_resolution(
            width, height, preserve_fps=self._fps()
        )
        self._sync_preview_timer_to_selected_fps()
        self._queue_mode_change()

    def apply_fps(self):
        self._sync_preview_timer_to_selected_fps()
        self._queue_mode_change()

    def _camera_mode_result(self, result):
        self._mode_change_in_progress = False
        if not result.get("ok"):
            self._set_status("Camera error")
            self.status.setToolTip(result.get("message", "Video mode was rejected"))
            return
        self._set_status("Ready")
        width = int(result.get("width", 0) or 0)
        height = int(result.get("height", 0) or 0)
        if width and height:
            actual_resolution = (width, height)
            resolution_changed = actual_resolution != self._active_resolution
            self._active_resolution = actual_resolution
            wanted = self.resolution_combo.findData(actual_resolution)
            if wanted >= 0 and wanted != self.resolution_combo.currentIndex():
                self.resolution_combo.blockSignals(True)
                self.resolution_combo.setCurrentIndex(wanted)
                self.resolution_combo.blockSignals(False)
            safe_fps = self._fps()
            if safe_fps != int(result.get("target_fps", safe_fps) or safe_fps):
                self._mode_change_in_progress = True
                self.worker.request_mode(width, height, safe_fps)
            if resolution_changed:
                self._refresh_fps_options_for_resolution(
                    width, height, preserve_fps=safe_fps
                )
                self._sync_preview_timer_to_selected_fps()
        # Resolution changes may reset auto/manual mode in the driver. Reapply
        # the visible switch state through the worker before another exposure.
        self._manual_exposure_after_mode = not self.auto_exposure.isChecked()
        self._queue_camera_property(
            cv2.CAP_PROP_AUTO_EXPOSURE,
            1.0 if self.auto_exposure.isChecked() else 0.0,
            "Automatic exposure",
            exposure_mode=True,
        )

    def _refresh_fps_options_for_resolution(
        self, width, height, preserve_fps=None, measured_cap=None
    ):
        """Populate only the known AM4113T(R9) FPS presets for a resolution."""

        del measured_cap  # retained for call compatibility; no FPS is measured
        if preserve_fps is None:
            preserve_fps = self._fps()
        options = am4113t_fps_options(int(width), int(height)) or (10,)

        self.fps_combo.blockSignals(True)
        self.fps_combo.clear()
        for requested in options:
            self.fps_combo.addItem(f"{int(requested)} FPS", int(requested))
        preferred = self.fps_combo.findData(int(preserve_fps))
        if preferred < 0:
            preferred = self.fps_combo.count() - 1
        self.fps_combo.setCurrentIndex(max(0, preferred))
        self.fps_combo.blockSignals(False)

    def apply_exposure_mode(self):
        manual = not self.auto_exposure.isChecked()
        self.exposure_slider.setEnabled(manual)
        self._set_exposure_value_state(manual)
        self.exposure_device_value.setVisible(manual)
        self.exposure_device_value.setText(f"{self.exposure_slider.value()} %")
        if self.camera.is_open and cv2 is not None:
            # DirectShow commonly uses 0.75 for auto and 0.25 for manual, but
            # this is backend-dependent, so the property is always verified.
            # DirectShow property writes can block while the capture thread is
            # reading a frame. Queue the mode change and the manual value in
            # the camera worker so Qt remains responsive during interaction.
            # Mode changes must complete before exposure is written.  On
            # DirectShow the mode change can be asynchronous; sending both
            # commands in the same worker pass lets auto exposure overwrite
            # the manual value immediately.
            self._manual_exposure_after_mode = manual
            self._queue_camera_property(
                cv2.CAP_PROP_AUTO_EXPOSURE,
                0.0 if manual else 1.0,
                "Automatic exposure",
                exposure_mode=True,
            )

    def apply_exposure(self, value):
        self.exposure_device_value.setText(f"{value} %")
        if self.auto_exposure.isChecked():
            return
        self._pending_exposure_percent = int(value)
        # Throttle rather than debounce: a long continuous drag still updates
        # the real camera roughly seven times per second for live visual
        # feedback. Repeated valueChanged events do not restart this timer.
        if self.camera.is_open and not self._exposure_timer.isActive():
            self._exposure_timer.start()

    def _preview_exposure(self, value):
        """Receive pointer movement without making it a committed slider value."""

        self.apply_exposure(value)

    def _exposure_value_changed(self, value):
        """Handle the final commit and programmatic/readback updates."""

        self.exposure_device_value.setText(f"{value} %")
        # During a tracking-disabled drag the value is committed just before
        # sliderReleased().  Do not schedule a second hardware write there.
        if not self.exposure_slider.isSliderDown():
            self.apply_exposure(value)

    def _flush_pending_exposure(self):
        value = self._pending_exposure_percent
        self._pending_exposure_percent = None
        if value is None or self.auto_exposure.isChecked() or not self.camera.is_open:
            return
        self._queue_camera_property(
            cv2.CAP_PROP_EXPOSURE,
            value,
            "Exposure",
            exposure_readback=False,
        )

    def _flush_exposure_now(self):
        self._exposure_timer.stop()
        value = self.exposure_slider.value()
        self._pending_exposure_percent = None
        if self.auto_exposure.isChecked() or not self.camera.is_open:
            return
        self._queue_camera_property(
            cv2.CAP_PROP_EXPOSURE,
            value,
            "Exposure",
            exposure_readback=True,
        )

    def _pull_latest_frame(self):
        if self.worker is None or not self.worker.isRunning():
            return
        frame = self.worker.take_latest_frame()
        if frame is not None:
            # Keep page state current even while another page/Workspace is
            # visible, but avoid doing hidden-page pixmap work every frame.
            self.last_frame = frame
            if self.isVisible():
                pixmap = camera_frame_pixmap(frame, self.preview.size())
                if pixmap is not None:
                    self.preview.setPixmap(pixmap)
        if self.isVisible():
            live_fps = self.worker.live_fps()
            if live_fps > 0.0:
                self.fps_overlay.setText(f"{int(round(live_fps))} FPS")
                self.fps_overlay.setVisible(True)

    def apply_brightness(self, value):
        if self.camera.is_open:
            self._queue_camera_property(
                cv2.CAP_PROP_BRIGHTNESS,
                value,
                "Brightness",
                brightness=True,
            )

    def apply_led_enabled(self, enabled):
        """Apply the reference LED switch through DNX64 on the camera worker."""

        if self.camera.is_open:
            self._queue_camera_property(
                CAMERA_LED_CONTROL,
                1.0 if enabled else 0.0,
                "LED",
                led=True,
            )

    def _queue_camera_property(
        self,
        prop,
        value,
        control,
        *,
        brightness=False,
        exposure_mode=False,
        led=False,
        exposure_readback=True,
    ):
        self._control_request_id += 1
        request_id = self._control_request_id
        kind = (
            "led"
            if led
            else (
                "exposure_mode"
                if exposure_mode
                else ("brightness" if brightness else "property")
            )
        )
        key = (kind, prop)
        self._latest_control_request[key] = request_id
        self._request_controls[request_id] = (
            "Brightness" if brightness else control,
            prop,
            kind,
            exposure_readback,
        )
        if self.worker is not None and self.worker.isRunning():
            replaced_request = self.worker.request_property(
                request_id,
                prop,
                value,
                brightness=brightness,
                exposure_mode=exposure_mode,
                led=led,
                exposure_readback=exposure_readback,
            )
            if replaced_request is not None:
                self._request_controls.pop(replaced_request, None)
        else:
            # Never fall back to a blocking DirectShow/DNX64 write in Qt's GUI
            # thread during startup or shutdown races.
            self._request_controls.pop(request_id, None)
            if self._latest_control_request.get(key) == request_id:
                self._latest_control_request.pop(key, None)

    def _camera_property_result(self, request_id, result):
        # A stale result can arrive after the user has already moved the
        # slider again. Never let an old readback overwrite the current state.
        control, prop, kind, _exposure_readback = self._request_controls.pop(
            request_id, ("Camera control", None, "property", True)
        )
        key = (kind, prop)
        current_request = self._latest_control_request.get(key)
        if request_id != current_request:
            return
        if not result.supported:
            self._set_camera_control_error(control, result.message)
            if control == "LED":
                # SetLEDState has no readback. If the command itself fails,
                # restore the switch to the previous visible state rather than
                # claiming the requested hardware state succeeded.
                self.led_switch.blockSignals(True)
                self.led_switch.setChecked(not bool(result.requested))
                self.led_switch.blockSignals(False)
            return
        if control == "Automatic exposure":
            manual = self._manual_exposure_after_mode
            self._manual_exposure_after_mode = None
            if manual and not self.auto_exposure.isChecked():
                frozen = self.camera.current_exposure_percent
                if frozen is not None:
                    frozen = max(0, min(100, int(frozen)))
                    self.exposure_slider.blockSignals(True)
                    self.exposure_slider.setValue(frozen)
                    self.exposure_slider.blockSignals(False)
                    self.exposure_device_value.setText(f"{frozen} %")
        # A command can finish after the user has already moved the thumb
        # further. Never let an asynchronous readback fight the live drag.
        # Live preview writes deliberately skip readback.  Their synthetic
        # "applied" value must never move the thumb backwards after a fast
        # drag; only the final, verified command may reconcile the UI.
        if (
            result.applied is not None
            and control == "Exposure"
            and _exposure_readback
            and not self.exposure_slider.isSliderDown()
        ):
            self.exposure_slider.blockSignals(True)
            self.exposure_slider.setValue(round(float(result.applied)))
            self.exposure_slider.blockSignals(False)
            self.exposure_device_value.setText(f"{round(float(result.applied))} %")

    def _apply_detected_camera_options(self):
        """Reflect backend capability detection without changing the layout."""
        capabilities = self.camera.capabilities
        if capabilities is None:
            return
        current_resolution = self._selected_resolution()
        current_fps = self._fps()
        self.resolution_combo.blockSignals(True)
        self.resolution_combo.clear()
        for width, height in capabilities.supported_resolutions:
            self.resolution_combo.addItem(f"{width} × {height}", (width, height))
        wanted = f"{current_resolution[0]} × {current_resolution[1]}"
        selected = self.resolution_combo.findText(wanted)
        self.resolution_combo.setCurrentIndex(max(0, selected))
        self.resolution_combo.blockSignals(False)

        self._refresh_fps_options_for_resolution(
            *self._selected_resolution(),
            preserve_fps=current_fps,
        )

    def _sync_camera_controls(self):
        """Synchronize visible controls with the active SDK/UVC backend."""

        if not self.camera.is_open:
            return
        capabilities = self.camera.capabilities
        if capabilities and self.camera._dnx64 is not None:
            exposure_supported = capabilities.exposure_raw_max > capabilities.exposure_raw_min
            brightness_supported = capabilities.brightness_raw_max > capabilities.brightness_raw_min
            brightness_percent = self.camera.initial_brightness_percent
            auto = self.camera.initial_auto_exposure
            if brightness_percent is not None:
                self.brightness_slider.blockSignals(True)
                self.brightness_slider.setValue(max(0, min(100, brightness_percent)))
                self.brightness_slider.blockSignals(False)
            if auto is not None:
                self.auto_exposure.blockSignals(True)
                self.auto_exposure.setChecked(auto)
                self.auto_exposure.blockSignals(False)
            initial_exposure = self.camera.current_exposure_percent
            if initial_exposure is not None:
                initial_exposure = max(0, min(100, int(initial_exposure)))
                self.exposure_slider.blockSignals(True)
                self.exposure_slider.setValue(initial_exposure)
                self.exposure_slider.blockSignals(False)
                self.exposure_device_value.setText(f"{initial_exposure} %")
        else:
            exposure_supported = bool(capabilities and capabilities.auto_exposure_supported)
            brightness_supported = bool(
                capabilities
                and capabilities.brightness_raw_max > capabilities.brightness_raw_min
            )
        self.auto_exposure.setEnabled(exposure_supported)
        self.exposure_slider.setEnabled(
            exposure_supported and not self.auto_exposure.isChecked()
        )
        self.brightness_slider.setEnabled(brightness_supported)
        led_supported = bool(
            capabilities and capabilities.led_supported and self.camera._dnx64 is not None
        )
        self.led_switch.setEnabled(led_supported)
        if exposure_supported and not self.auto_exposure.isChecked():
            self.exposure_device_value.setText(f"{self.exposure_slider.value()} %")

    def _set_camera_control_error(self, control, message):
        # Keep the compact status pill stable; detailed state is available as
        # a tooltip instead of pushing the toolbar around.
        self.status.setToolTip(f"{control}: {message}")

    def _camera_error(self, message):
        self._set_status("Camera error")
        self.preview.setText(message)
        self.stop_camera()

    def session_runtime_idle(self) -> bool:
        """True only when applying UI-only camera preferences cannot hit hardware."""
        opener_busy = self._open_worker is not None and self._open_worker.isRunning()
        return bool(
            not self.camera.is_open
            and self.worker is None
            and not self.recording
            and not self._mode_change_in_progress
            and not opener_busy
        )

    def export_session_state(self):
        width, height = self._selected_resolution()
        return {
            "resolution": [width, height],
            "fps": self._fps(),
            "auto_exposure": bool(self.auto_exposure.isChecked()),
            "exposure_percent": int(self.exposure_slider.value()),
            "brightness_percent": int(self.brightness_slider.value()),
            "led_enabled": bool(self.led_switch.isChecked()),
        }

    def apply_session_state(self, state):
        """Restore stopped-camera UI preferences without issuing camera writes."""
        if not isinstance(state, dict) or not self.session_runtime_idle():
            return False
        resolution = state.get("resolution", [1280, 1024])
        try:
            width, height = int(resolution[0]), int(resolution[1])
        except (TypeError, ValueError, IndexError):
            width, height = 1280, 1024
        target_index = next(
            (index for index in range(self.resolution_combo.count())
             if self.resolution_combo.itemData(index) == (width, height)),
            -1,
        )
        if target_index >= 0:
            self.resolution_combo.blockSignals(True)
            self.resolution_combo.setCurrentIndex(target_index)
            self.resolution_combo.blockSignals(False)
        fps = int(state.get("fps", self._fps()))
        self._refresh_fps_options_for_resolution(width, height, preserve_fps=fps)
        self.auto_exposure.blockSignals(True)
        self.auto_exposure.setChecked(bool(state.get("auto_exposure", True)))
        self.auto_exposure.blockSignals(False)
        self.exposure_slider.blockSignals(True)
        self.exposure_slider.setValue(max(0, min(100, int(state.get("exposure_percent", 50)))))
        self.exposure_slider.blockSignals(False)
        self.brightness_slider.blockSignals(True)
        self.brightness_slider.setValue(max(0, min(100, int(state.get("brightness_percent", 50)))))
        self.brightness_slider.blockSignals(False)
        self.led_switch.blockSignals(True)
        self.led_switch.setChecked(bool(state.get("led_enabled", True)))
        self.led_switch.blockSignals(False)
        # Session restore is UI-only while the camera is stopped. Controls stay
        # disabled until a real DNX64 camera is opened and capabilities are known.
        self.led_switch.setEnabled(False)
        manual = not self.auto_exposure.isChecked()
        self.exposure_slider.setEnabled(manual)
        self._set_exposure_value_state(manual)
        self.exposure_device_value.setVisible(manual)
        self.exposure_device_value.setText(f"{self.exposure_slider.value()} %")
        self._sync_preview_timer_to_selected_fps()
        return True

    def shutdown(self):
        self._shutting_down = True
        self.stop_camera()
        if self._open_worker is not None and self._open_worker.isRunning():
            self._open_worker.wait(5000)
        if self._discovery_worker is not None and self._discovery_worker.isRunning():
            self._discovery_worker.wait(5000)
        for recorder in list(self._recorders):
            recorder.request_stop()
            recorder.wait(5000)
        for saver in list(self._photo_workers):
            saver.wait(5000)
