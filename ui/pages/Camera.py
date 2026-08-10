from __future__ import annotations

from datetime import datetime
from pathlib import Path

from PyQt6.QtCore import QThread, QUrl, Qt, QSize, QRectF, QPointF, pyqtSignal
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
    QAbstractButton, QComboBox, QGridLayout, QGroupBox, QHBoxLayout, QLabel, QPushButton, QSlider,
    QSizePolicy, QVBoxLayout, QWidget,
)

try:
    import cv2
except ImportError:
    cv2 = None

from backend.camera_service import CameraDevice, DinoLiteSdkBridge, OpenCVCamera, enumerate_cameras
from ui.design_tokens import CARD_GAP, CARD_PADDING, PAGE_BOTTOM, PAGE_GUTTER, PAGE_TOP, SECTION_GAP


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
            (self.value() - self.minimum()) / value_range
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


class CameraWorker(QThread):
    frame_ready = pyqtSignal(object)
    camera_error = pyqtSignal(str)

    def __init__(self, camera: OpenCVCamera):
        super().__init__()
        self.camera = camera
        self.running = False

    def run(self):
        self.running = True
        while self.running:
            ok, frame = self.camera.read()
            if not ok or frame is None:
                self.camera_error.emit("The camera stopped returning frames.")
                break
            self.frame_ready.emit(frame)
            self.msleep(15)

    def stop(self):
        self.running = False
        self.wait(1500)


class CameraPage(QWidget):
    def __init__(self):
        super().__init__()
        self.setObjectName("cameraPage")
        self.camera = OpenCVCamera()
        self.sdk = DinoLiteSdkBridge()
        self.worker: CameraWorker | None = None
        self.devices: list[CameraDevice] = []
        self.last_frame = None
        self.recording = False
        self.video_writer = None
        self.capture_folder = Path(__file__).resolve().parents[2] / "captures"
        self.capture_folder.mkdir(exist_ok=True)
        self._build_ui()
        self.refresh_devices()

    def _build_ui(self):
        root = QVBoxLayout(self)
        root.setContentsMargins(32, 32, 32, 24)
        root.setSpacing(18)

        title = QLabel("Camera")
        title.setObjectName("pageTitle")
        root.addWidget(title)

        top = QHBoxLayout()
        top.setObjectName("cameraToolbarLayout")
        top.setSpacing(14)
        self._camera_toolbar = top
        device_label = QLabel("Camera device")
        device_label.setObjectName("cameraFieldLabel")
        device_label.setFixedWidth(115)
        top.addWidget(device_label)
        self.device_combo = CameraComboBox()
        self.device_combo.setObjectName("cameraCombo")
        self.device_combo.setProperty("toolbar", True)
        self.device_combo.setFixedHeight(50)
        self.device_combo.currentIndexChanged.connect(self._device_changed)
        top.addWidget(self.device_combo)
        self._status_spacer = QWidget()
        self._status_spacer.setFixedWidth(0)
        top.addWidget(self._status_spacer)
        self.status = QLabel("No camera detected")
        self.status.setObjectName("cameraStatus")
        self.status.setFixedSize(160, 50)
        self.status.setAlignment(Qt.AlignmentFlag.AlignCenter)
        top.addWidget(self.status)
        top.addStretch()

        # This container intentionally spans the same width as the controls
        # card below. That makes the folder button start at the card's left
        # edge and the Start button finish at its right edge.
        toolbar_actions = QWidget()
        toolbar_actions.setObjectName("cameraToolbarActions")
        toolbar_actions_layout = QHBoxLayout(toolbar_actions)
        toolbar_actions_layout.setContentsMargins(0, 0, 0, 0)
        toolbar_actions_layout.setSpacing(16)
        self._toolbar_actions = toolbar_actions

        self.open_folder_button = QPushButton("Open Capture Folder")
        self.open_folder_button.setObjectName("cameraSecondaryButton")
        self.open_folder_button.setFixedHeight(48)
        self.open_folder_button.setIcon(make_folder_icon())
        self.open_folder_button.setIconSize(QSize(18, 18))
        self.open_folder_button.clicked.connect(self.open_capture_folder)
        toolbar_actions_layout.addWidget(self.open_folder_button, 1)
        self.start_button = QPushButton("Start Camera")
        self.start_button.setObjectName("cameraPrimaryButton")
        self.start_button.setFixedHeight(48)
        self.start_button.clicked.connect(self.toggle_camera)
        toolbar_actions_layout.addWidget(self.start_button)
        top.addWidget(toolbar_actions)
        root.addLayout(top)

        work = QHBoxLayout()
        work.setSpacing(16)
        self._work_layout = work

        preview_card = QGroupBox("Live preview")
        preview_card.setObjectName("cameraCard")
        preview_layout = QVBoxLayout(preview_card)
        preview_layout.setContentsMargins(20, 20, 20, 18)
        preview_layout.setSpacing(14)
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
        self.live_badge = QLabel("●  LIVE")
        self.live_badge.setObjectName("cameraLiveBadge")
        self.live_badge.setVisible(False)
        preview_shell_layout.addWidget(
            self.live_badge,
            0,
            0,
            Qt.AlignmentFlag.AlignLeft | Qt.AlignmentFlag.AlignTop,
        )
        preview_layout.addWidget(preview_shell, 1)
        actions = QHBoxLayout()
        actions.setAlignment(Qt.AlignmentFlag.AlignCenter)
        actions.setSpacing(14)
        self.capture_button = QPushButton("Capture")
        self.capture_button.setObjectName("cameraActionButton")
        self.capture_button.setIcon(make_camera_icon())
        self.capture_button.setIconSize(QSize(20, 20))
        self.capture_button.setFixedSize(140, 48)
        self.capture_button.setEnabled(False)
        self.capture_button.clicked.connect(self.capture_photo)
        self.record_button = QPushButton("Record")
        self.record_button.setObjectName("cameraActionButton")
        self.record_button.setIcon(make_record_icon())
        self.record_button.setIconSize(QSize(20, 20))
        self.record_button.setFixedSize(140, 48)
        self.record_button.setEnabled(False)
        self.record_button.clicked.connect(self.toggle_recording)
        self.stop_button = QPushButton("Stop")
        self.stop_button.setObjectName("cameraActionButton")
        self.stop_button.setIcon(make_stop_icon())
        self.stop_button.setIconSize(QSize(20, 20))
        self.stop_button.setFixedSize(140, 48)
        self.stop_button.setEnabled(False)
        self.stop_button.clicked.connect(self.stop_camera)
        actions.addWidget(self.capture_button)
        actions.addWidget(self.record_button)
        actions.addWidget(self.stop_button)
        preview_layout.addLayout(actions)
        work.addWidget(preview_card, 3)

        controls = QGroupBox("Camera controls")
        controls.setObjectName("cameraCard")
        controls_layout = QVBoxLayout(controls)
        controls_layout.setContentsMargins(20, 20, 20, 18)
        controls_layout.setSpacing(16)
        controls.setMinimumWidth(360)
        controls.setMaximumWidth(420)
        controls.setSizePolicy(QSizePolicy.Policy.Fixed, QSizePolicy.Policy.Expanding)
        self.resolution_combo = self._combo_row(controls_layout, "Resolution", ["1280 × 1024", "640 × 480"])
        self.resolution_combo.currentTextChanged.connect(self.apply_resolution)
        self.fps_combo = self._combo_row(
            controls_layout,
            "FPS",
            ["30 FPS", "15 FPS", "60 FPS"],
        )
        self.fps_combo.currentTextChanged.connect(self.apply_fps)

        exposure_row = QHBoxLayout()
        exposure_row.setObjectName("cameraExposureHeader")
        exposure_label = QLabel("Exposure")
        exposure_label.setObjectName("cameraFieldLabel")
        exposure_row.addWidget(exposure_label)
        exposure_row.addStretch()
        self.exposure_device_value = QLabel("-6")
        self.exposure_device_value.setObjectName("cameraDeviceValue")
        self.exposure_device_value.setFixedWidth(28)
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
        self.exposure_slider.setRange(-13, 0)
        self.exposure_slider.setValue(-6)
        self.exposure_slider.setMinimumHeight(30)
        exposure_slider_row = QHBoxLayout()
        exposure_slider_row.setContentsMargins(0, 0, 0, 0)
        exposure_slider_row.setSpacing(0)
        exposure_slider_row.addWidget(self.exposure_slider, 1)
        controls_layout.addLayout(exposure_slider_row)
        controls_layout.addSpacing(8)
        self.exposure_slider.valueChanged.connect(self.apply_exposure)
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
        controls_layout.addSpacing(10)
        controls_layout.addSpacing(8)

        led_row = QHBoxLayout()
        led_label = QLabel("LED power")
        led_label.setObjectName("cameraFieldLabel")
        led_row.addWidget(led_label)
        led_note = QLabel("8 LEDs")
        led_note.setObjectName("cameraHint")
        led_row.addWidget(led_note)
        led_row.addStretch()
        self.led_button = CameraSwitch(True)
        self.led_button.setObjectName("cameraToggleSwitch")
        self.led_button.setAccessibleName("Camera LED power")
        self.led_button.setToolTip("Toggle the camera illumination when supported by the connected device.")
        self.led_button.toggled.connect(self.apply_led)
        led_row.addWidget(self.led_button)
        controls_layout.addLayout(led_row)
        controls_layout.addSpacing(10)
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
        work.addWidget(controls, 0)
        root.addLayout(work, 1)

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
        self.last_capture = QLabel("No image captured yet")
        self.last_capture.setObjectName("cameraHint")
        last_layout.addWidget(self.last_capture)
        root.addWidget(last)

        self._update_reference_geometry()

    def resizeEvent(self, event):
        super().resizeEvent(event)
        self._update_reference_geometry()

    def _update_reference_geometry(self):
        """Keep toolbar edges and card proportions aligned at both references.

        The compact toolbar reference is approximately 1086 px wide inside the
        page; the complete-window reference is approximately 1268 px wide.
        Interpolating between those two measured states prevents fixed V23
        widths from leaving unused space or pushing the right buttons inward.
        """
        inner_width = max(0, self.width() - 64)
        scale = max(0.0, min(1.0, (inner_width - 1086.0) / 182.0))

        def lerp(compact, wide):
            return round(compact + (wide - compact) * scale)

        controls_width = max(360, min(450, round(inner_width * 0.35)))
        self._controls_card.setFixedWidth(controls_width)
        self._toolbar_actions.setFixedWidth(controls_width)
        self.start_button.setFixedWidth(max(124, min(148, round(controls_width * 0.34))))
        self.device_combo.setFixedWidth(max(260, min(337, round(inner_width * 0.265))))
        self.status.setFixedWidth(
            max(96, min(115, round(inner_width * 0.091)))
            if self.status.text().endswith("Ready")
            else 160
        )

        # Align the Ready pill's right edge with the Live preview card's
        # right edge, while leaving the action group aligned to the controls.
        preview_width = max(0, inner_width - 16 - controls_width)
        leading_width = 115 + 14 + self.device_combo.width()
        spacer_width = max(
            0,
            preview_width - self.status.width() - leading_width - 28,
        )
        self._status_spacer.setFixedWidth(spacer_width)

        # The smaller reference is close to 3:2; the complete-window reference
        # is about 16:9. Keep both cards flush with the same right edge.
        if inner_width >= 1170:
            self._work_layout.setStretch(0, 1)
            self._work_layout.setStretch(1, 0)
        else:
            self._work_layout.setStretch(0, 1)
            self._work_layout.setStretch(1, 0)

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
        layout.addLayout(row)
        return slider

    def refresh_devices(self):
        self.devices = enumerate_cameras()
        self.device_combo.blockSignals(True)
        self.device_combo.clear()
        for device in self.devices:
            self.device_combo.addItem(device.label, device.index)
        if not self.devices:
            self.device_combo.addItem("No cameras detected", -1)
            # Keep actions available as safe no-ops before hardware is connected.
            self.start_button.setEnabled(True)
            self._set_status("No camera detected")
        else:
            self.start_button.setEnabled(True)
            self._set_status("Ready")
        self.capture_button.setEnabled(True)
        self.record_button.setEnabled(True)
        self.stop_button.setEnabled(True)
        self.device_combo.blockSignals(False)

    def _set_status(self, text: str):
        self.status.setText(text)
        # Ready is intentionally compact like the reference. Longer states
        # receive only the width they need, without changing the page layout.
        self.status.setProperty("state", "ready" if text == "Ready" else "neutral")
        self.status.setFixedSize(100 if text == "Ready" else 160, 50)
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
        if index is None or int(index) < 0:
            return
        try:
            width, height = (1280, 1024) if self.resolution_combo.currentIndex() == 0 else (640, 480)
            self.sdk.set_device_index(int(index))
            self.camera.open(int(index), width, height, self._fps())
            self.worker = CameraWorker(self.camera)
            self.worker.frame_ready.connect(self.on_frame)
            self.worker.camera_error.connect(self._camera_error)
            self.worker.start()
            self._set_status("Ready")
            self.start_button.setText("Stop Camera")
            self.live_badge.setVisible(True)
            self.stop_button.setEnabled(True)
            self.capture_button.setEnabled(True)
            self.record_button.setEnabled(True)
        except Exception as exc:
            self._set_status("Camera error")
            self.preview.setText(str(exc))

    def stop_camera(self):
        self.stop_recording()
        if self.worker is not None:
            self.worker.stop()
            self.worker = None
        self.camera.close()
        self.start_button.setText("Start Camera")
        self.live_badge.setVisible(False)
        # These remain clickable safe no-ops while disconnected so the page
        # stays discoverable and keeps consistent hover affordances.
        self.stop_button.setEnabled(True)
        self.capture_button.setEnabled(True)
        self.record_button.setEnabled(True)
        self.preview.setText("Camera is stopped")

    def on_frame(self, frame):
        self.last_frame = frame.copy()
        if self.recording and self.video_writer is not None:
            self.video_writer.write(frame)
        if cv2 is None:
            return
        rgb = cv2.cvtColor(frame, cv2.COLOR_BGR2RGB)
        image = QImage(rgb.data, rgb.shape[1], rgb.shape[0], rgb.strides[0], QImage.Format.Format_RGB888).copy()
        source = QPixmap.fromImage(image)
        scaled = source.scaled(
            self.preview.size(),
            Qt.AspectRatioMode.KeepAspectRatioByExpanding,
            Qt.TransformationMode.SmoothTransformation,
        )
        x = max(0, (scaled.width() - self.preview.width()) // 2)
        y = max(0, (scaled.height() - self.preview.height()) // 2)
        self.preview.setPixmap(scaled.copy(x, y, self.preview.width(), self.preview.height()))

    def capture_photo(self):
        if self.last_frame is None or cv2 is None:
            return
        if self.sdk.available:
            self.sdk.trigger_microtouch()
        self.capture_folder.mkdir(exist_ok=True)
        now = datetime.now()
        path = self.capture_folder / f"camera_{now:%Y%m%d_%H%M%S}.png"
        cv2.imwrite(str(path), self.last_frame)
        self.last_capture.setText(f"{path.name}   ·   {now:%H:%M:%S}")

    def toggle_recording(self):
        self.stop_recording() if self.recording else self.start_recording()

    def start_recording(self):
        if self.last_frame is None or cv2 is None:
            return
        height, width = self.last_frame.shape[:2]
        path = self.capture_folder / f"recording_{datetime.now():%Y%m%d_%H%M%S}.mp4"
        self.video_writer = cv2.VideoWriter(str(path), cv2.VideoWriter_fourcc(*"mp4v"), self._fps(), (width, height))
        if not self.video_writer.isOpened():
            self.video_writer.release()
            self.video_writer = None
            self._set_status("Recording error")
            return
        self.recording = True
        self.record_button.setText("Recording…")

    def stop_recording(self):
        if self.video_writer is not None:
            self.video_writer.release()
        self.video_writer = None
        self.recording = False
        self.record_button.setText("Record")

    def open_capture_folder(self):
        self.capture_folder.mkdir(exist_ok=True)
        QDesktopServices.openUrl(QUrl.fromLocalFile(str(self.capture_folder.resolve())))

    def _fps(self):
        return int(self.fps_combo.currentText().split()[0])

    def apply_resolution(self):
        if self.camera.is_open:
            self.camera.set_resolution(*(1280, 1024) if self.resolution_combo.currentIndex() == 0 else (640, 480))

    def apply_fps(self):
        if self.camera.is_open:
            self.camera.set_fps(self._fps())

    def apply_exposure_mode(self):
        manual = not self.auto_exposure.isChecked()
        self.exposure_slider.setEnabled(manual)
        self._set_exposure_value_state(manual)
        self.exposure_device_value.setVisible(manual)
        self.exposure_device_value.setText(
            f"{self.exposure_slider.value()}"
        )
        if self.sdk.available:
            self.sdk.set_auto_exposure(not manual)
        elif self.camera.is_open and cv2 is not None:
            self.camera.set_property(cv2.CAP_PROP_AUTO_EXPOSURE, 0.25 if not manual else 0.75)

    def apply_exposure(self, value):
        if not self.auto_exposure.isChecked():
            self.exposure_device_value.setText(f"{value}")
        if self.sdk.available:
            self.sdk.set_exposure(value)
        elif self.camera.is_open and cv2 is not None:
            self.camera.set_property(cv2.CAP_PROP_EXPOSURE, value)

    def apply_brightness(self, value):
        if self.camera.is_open and cv2 is not None:
            self.camera.set_property(cv2.CAP_PROP_BRIGHTNESS, value / 100.0)

    def apply_led(self, enabled):
        # Keep the visual control usable even without the optional vendor SDK.
        # OpenCV cameras may expose their own illumination control separately.
        self.sdk.set_led_enabled(enabled)

    def _camera_error(self, message):
        self._set_status("Camera error")
        self.preview.setText(message)
        self.stop_camera()

    def shutdown(self):
        self.stop_camera()
