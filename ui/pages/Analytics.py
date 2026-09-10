"""Functional offline droplet Analytics workspace.

The page intentionally stays visually compact.  Detailed measurements are
exported to CSV; the main UI focuses on loading/reviewing a recording,
calibration/setup, running the analysis, inspecting detections, and viewing the
four primary summary values plus a time-series graph.
"""

from __future__ import annotations

from dataclasses import replace
from pathlib import Path
import math
import threading

import cv2
import numpy as np
import pyqtgraph as pg
from PyQt6.QtCore import QPointF, QRectF, QSize, Qt, QThread, QTimer, pyqtSignal
from PyQt6.QtGui import QColor, QImage, QPainter, QPen, QPixmap
from PyQt6.QtWidgets import (
    QButtonGroup,
    QComboBox,
    QDialog,
    QDialogButtonBox,
    QDoubleSpinBox,
    QFileDialog,
    QFormLayout,
    QFrame,
    QGridLayout,
    QHBoxLayout,
    QLabel,
    QMessageBox,
    QPushButton,
    QRadioButton,
    QScrollArea,
    QSizePolicy,
    QSlider,
    QVBoxLayout,
    QWidget,
)

from backend.analytics.analyzer import DropletAnalyzer, compute_cache_key
from backend.analytics.calibration import manual_scale, scale_from_reference
from backend.analytics.csv_export import export_result_csv, unique_path
from backend.analytics.models import AnalysisConfig, AnalysisResult, Calibration, OverlayDetection
from backend.analytics.render import render_analysis_frame
from backend.analytics.result_store import ResultStore
from backend.analytics.video_source import SUPPORTED_EXTENSIONS, VideoOpenError, VideoSource, discover_videos
from backend.application_paths import (
    ANALYTICS_DIR,
    ANALYTICS_EXPORT_DIR,
    ANALYTICS_SETTINGS_PATH,
    CAPTURES_DIR,
)
from ui.design_tokens import PAGE_GUTTER


class FrameView(QLabel):
    """Aspect-preserving image label for source and processed frames."""

    def __init__(self, parent: QWidget | None = None) -> None:
        super().__init__(parent)
        self._image: QImage | None = None
        self.setAlignment(Qt.AlignmentFlag.AlignCenter)
        self.setMinimumHeight(262)
        self.setSizePolicy(QSizePolicy.Policy.Expanding, QSizePolicy.Policy.Expanding)
        self.setText("Load a video to begin")

    def set_bgr_frame(self, frame: np.ndarray | None) -> None:
        if frame is None:
            self._image = None
            self.clear()
            self.setText("Load a video to begin")
            return
        rgb = cv2.cvtColor(frame, cv2.COLOR_BGR2RGB)
        height, width, channels = rgb.shape
        image = QImage(rgb.data, width, height, channels * width, QImage.Format.Format_RGB888)
        self._image = image.copy()
        self._refresh_pixmap()

    def resizeEvent(self, event) -> None:  # noqa: N802
        super().resizeEvent(event)
        self._refresh_pixmap()

    def _refresh_pixmap(self) -> None:
        if self._image is None or self.width() <= 2 or self.height() <= 2:
            return
        pixmap = QPixmap.fromImage(self._image).scaled(
            self.size(),
            Qt.AspectRatioMode.KeepAspectRatio,
            Qt.TransformationMode.SmoothTransformation,
        )
        self.setPixmap(pixmap)


class SetupCanvas(QWidget):
    """Small reusable frame canvas for ROI/flow/calibration mouse gestures."""

    selection_changed = pyqtSignal()

    def __init__(self, frame: np.ndarray, parent: QWidget | None = None) -> None:
        super().__init__(parent)
        self.frame = frame.copy()
        self.frame_height, self.frame_width = self.frame.shape[:2]
        rgb = cv2.cvtColor(self.frame, cv2.COLOR_BGR2RGB)
        self._image = QImage(
            rgb.data,
            self.frame_width,
            self.frame_height,
            3 * self.frame_width,
            QImage.Format.Format_RGB888,
        ).copy()
        self.setMinimumSize(620, 330)
        self.setSizePolicy(QSizePolicy.Policy.Expanding, QSizePolicy.Policy.Expanding)
        self.mode: str | None = None
        self.roi = (0.0, 0.0, 1.0, 1.0)
        self.flow_direction: tuple[float, float] | None = None
        self.reference_line_px: tuple[tuple[float, float], tuple[float, float]] | None = None
        self._drag_start: tuple[float, float] | None = None
        self._drag_current: tuple[float, float] | None = None
        self._image_rect = QRectF()

    def set_roi(self, roi: tuple[float, float, float, float]) -> None:
        self.roi = tuple(float(v) for v in roi)
        self.update()

    def set_flow_direction(self, direction: tuple[float, float] | None) -> None:
        self.flow_direction = direction
        self.update()

    @property
    def reference_length_px(self) -> float | None:
        if self.reference_line_px is None:
            return None
        a, b = self.reference_line_px
        return math.hypot(b[0] - a[0], b[1] - a[1])

    def paintEvent(self, event) -> None:  # noqa: N802
        del event
        painter = QPainter(self)
        painter.setRenderHint(QPainter.RenderHint.Antialiasing, True)
        painter.fillRect(self.rect(), QColor("#F2F5F3"))
        pixmap = QPixmap.fromImage(self._image)
        scaled = pixmap.scaled(
            self.size(), Qt.AspectRatioMode.KeepAspectRatio, Qt.TransformationMode.SmoothTransformation
        )
        x = (self.width() - scaled.width()) / 2.0
        y = (self.height() - scaled.height()) / 2.0
        self._image_rect = QRectF(x, y, scaled.width(), scaled.height())
        painter.drawPixmap(int(x), int(y), scaled)

        painter.setPen(QPen(QColor("#7BC5A8"), 2))
        rx, ry, rw, rh = self.roi
        roi_rect = QRectF(
            x + rx * scaled.width(),
            y + ry * scaled.height(),
            rw * scaled.width(),
            rh * scaled.height(),
        )
        painter.drawRect(roi_rect)

        if self.flow_direction is not None:
            ux, uy = self.flow_direction
            norm = math.hypot(ux, uy) or 1.0
            ux, uy = ux / norm, uy / norm
            center = roi_rect.center()
            length = min(roi_rect.width(), roi_rect.height()) * 0.35
            start = QPointF(center.x() - ux * length / 2, center.y() - uy * length / 2)
            end = QPointF(center.x() + ux * length / 2, center.y() + uy * length / 2)
            painter.setPen(QPen(QColor("#168F69"), 3))
            painter.drawLine(start, end)
            arrow = 9.0
            angle = math.atan2(end.y() - start.y(), end.x() - start.x())
            p1 = QPointF(end.x() - arrow * math.cos(angle - 0.55), end.y() - arrow * math.sin(angle - 0.55))
            p2 = QPointF(end.x() - arrow * math.cos(angle + 0.55), end.y() - arrow * math.sin(angle + 0.55))
            painter.drawLine(end, p1)
            painter.drawLine(end, p2)

        if self.reference_line_px is not None:
            a, b = self.reference_line_px
            pa = self._frame_to_widget(a)
            pb = self._frame_to_widget(b)
            painter.setPen(QPen(QColor("#E0B443"), 3))
            painter.drawLine(pa, pb)

        if self._drag_start is not None and self._drag_current is not None:
            if self.mode == "roi":
                a = self._frame_to_widget(self._drag_start)
                b = self._frame_to_widget(self._drag_current)
                painter.setPen(QPen(QColor("#168F69"), 2, Qt.PenStyle.DashLine))
                painter.drawRect(QRectF(a, b).normalized())
            elif self.mode in {"flow", "reference"}:
                a = self._frame_to_widget(self._drag_start)
                b = self._frame_to_widget(self._drag_current)
                painter.setPen(QPen(QColor("#168F69" if self.mode == "flow" else "#E0B443"), 2))
                painter.drawLine(a, b)

    def mousePressEvent(self, event) -> None:  # noqa: N802
        if self.mode is None or event.button() != Qt.MouseButton.LeftButton:
            return
        point = self._widget_to_frame(event.position())
        if point is None:
            return
        self._drag_start = point
        self._drag_current = point
        self.update()

    def mouseMoveEvent(self, event) -> None:  # noqa: N802
        if self._drag_start is None:
            return
        point = self._widget_to_frame(event.position())
        if point is None:
            return
        self._drag_current = point
        self.update()

    def mouseReleaseEvent(self, event) -> None:  # noqa: N802
        if self._drag_start is None or self._drag_current is None:
            return
        start, end = self._drag_start, self._drag_current
        if self.mode == "roi":
            x0, x1 = sorted((start[0], end[0]))
            y0, y1 = sorted((start[1], end[1]))
            if x1 - x0 >= 8 and y1 - y0 >= 8:
                self.roi = (
                    x0 / self.frame_width,
                    y0 / self.frame_height,
                    (x1 - x0) / self.frame_width,
                    (y1 - y0) / self.frame_height,
                )
        elif self.mode == "flow":
            dx, dy = end[0] - start[0], end[1] - start[1]
            norm = math.hypot(dx, dy)
            if norm >= 5:
                self.flow_direction = (dx / norm, dy / norm)
        elif self.mode == "reference":
            if math.hypot(end[0] - start[0], end[1] - start[1]) >= 5:
                self.reference_line_px = (start, end)
        self._drag_start = self._drag_current = None
        self.selection_changed.emit()
        self.update()

    def _widget_to_frame(self, point: QPointF) -> tuple[float, float] | None:
        if not self._image_rect.contains(point):
            return None
        x = (point.x() - self._image_rect.left()) / max(1.0, self._image_rect.width()) * self.frame_width
        y = (point.y() - self._image_rect.top()) / max(1.0, self._image_rect.height()) * self.frame_height
        return max(0.0, min(self.frame_width - 1.0, x)), max(0.0, min(self.frame_height - 1.0, y))

    def _frame_to_widget(self, point: tuple[float, float]) -> QPointF:
        return QPointF(
            self._image_rect.left() + point[0] / self.frame_width * self._image_rect.width(),
            self._image_rect.top() + point[1] / self.frame_height * self._image_rect.height(),
        )


class AnalysisSetupDialog(QDialog):
    def __init__(self, frame: np.ndarray, config: AnalysisConfig, parent: QWidget | None = None) -> None:
        super().__init__(parent)
        self.setWindowTitle("Analysis Setup")
        self.resize(760, 540)
        layout = QVBoxLayout(self)
        description = QLabel(
            "Optional: restrict the analysis region and define the flow direction. "
            "Detection remains automatic."
        )
        description.setWordWrap(True)
        layout.addWidget(description)
        self.canvas = SetupCanvas(frame)
        self.canvas.set_roi(config.roi)
        self.canvas.set_flow_direction(config.flow_direction)
        layout.addWidget(self.canvas, 1)

        controls = QHBoxLayout()
        roi_button = QPushButton("Select ROI")
        roi_button.clicked.connect(lambda: self._set_mode("roi"))
        full_button = QPushButton("Use Full Frame")
        full_button.clicked.connect(self._full_frame)
        flow_button = QPushButton("Set Flow Direction")
        flow_button.clicked.connect(lambda: self._set_mode("flow"))
        auto_flow_button = QPushButton("Auto Flow")
        auto_flow_button.clicked.connect(self._auto_flow)
        for button in (roi_button, full_button, flow_button, auto_flow_button):
            controls.addWidget(button)
        controls.addStretch(1)
        layout.addLayout(controls)
        note = QLabel("Tip: drag from upstream to downstream when setting flow direction.")
        note.setProperty("analyticsRole", "mediaMeta")
        layout.addWidget(note)
        buttons = QDialogButtonBox(QDialogButtonBox.StandardButton.Cancel | QDialogButtonBox.StandardButton.Ok)
        buttons.accepted.connect(self.accept)
        buttons.rejected.connect(self.reject)
        layout.addWidget(buttons)

    def _set_mode(self, mode: str) -> None:
        self.canvas.mode = mode

    def _full_frame(self) -> None:
        self.canvas.set_roi((0.0, 0.0, 1.0, 1.0))
        self.canvas.mode = None

    def _auto_flow(self) -> None:
        self.canvas.set_flow_direction(None)
        self.canvas.mode = None

    def updated_config(self, original: AnalysisConfig) -> AnalysisConfig:
        config = AnalysisConfig.from_dict(original.to_dict())
        config.roi = self.canvas.roi
        config.flow_direction = self.canvas.flow_direction
        return config


class CalibrationDialog(QDialog):
    def __init__(self, frame: np.ndarray, config: AnalysisConfig, parent: QWidget | None = None) -> None:
        super().__init__(parent)
        self.setWindowTitle("Calibration")
        self.resize(760, 600)
        self.frame = frame
        height, width = frame.shape[:2]
        self.resolution = (width, height)
        layout = QVBoxLayout(self)
        self.canvas = SetupCanvas(frame)
        self.canvas.mode = "reference"
        layout.addWidget(self.canvas, 1)

        self.pixels_radio = QRadioButton("Pixels only")
        self.manual_radio = QRadioButton("Enter scale manually")
        self.reference_radio = QRadioButton("Calibrate from a known reference")
        group = QButtonGroup(self)
        for radio in (self.pixels_radio, self.manual_radio, self.reference_radio):
            group.addButton(radio)

        current = config.calibration
        if not current.calibrated:
            self.pixels_radio.setChecked(True)
        elif current.source == "reference":
            self.reference_radio.setChecked(True)
        else:
            self.manual_radio.setChecked(True)

        options = QVBoxLayout()
        options.addWidget(self.pixels_radio)
        manual_row = QHBoxLayout()
        manual_row.addWidget(self.manual_radio)
        self.manual_scale = QDoubleSpinBox()
        self.manual_scale.setDecimals(6)
        self.manual_scale.setRange(0.000001, 100000.0)
        self.manual_scale.setValue(current.um_per_px or 1.0)
        self.manual_scale.setSuffix(" µm/px")
        manual_row.addWidget(self.manual_scale)
        manual_row.addStretch(1)
        options.addLayout(manual_row)

        reference_row = QHBoxLayout()
        reference_row.addWidget(self.reference_radio)
        self.known_length = QDoubleSpinBox()
        self.known_length.setDecimals(3)
        self.known_length.setRange(0.001, 1_000_000.0)
        self.known_length.setValue(100.0)
        self.known_length.setSuffix(" µm")
        reference_row.addWidget(self.known_length)
        self.draw_reference_button = QPushButton("Draw reference line")
        self.draw_reference_button.clicked.connect(lambda: setattr(self.canvas, "mode", "reference"))
        reference_row.addWidget(self.draw_reference_button)
        reference_row.addStretch(1)
        options.addLayout(reference_row)
        self.reference_status = QLabel("Draw over a known distance in the frame.")
        self.canvas.selection_changed.connect(self._update_reference_status)
        options.addWidget(self.reference_status)
        layout.addLayout(options)

        buttons = QDialogButtonBox(QDialogButtonBox.StandardButton.Cancel | QDialogButtonBox.StandardButton.Ok)
        buttons.accepted.connect(self._accept_if_valid)
        buttons.rejected.connect(self.reject)
        layout.addWidget(buttons)
        self._calibration: Calibration | None = None

    def _update_reference_status(self) -> None:
        length = self.canvas.reference_length_px
        if length:
            self.reference_status.setText(f"Reference line: {length:.1f} px")

    def _accept_if_valid(self) -> None:
        try:
            if self.pixels_radio.isChecked():
                self._calibration = Calibration(None, self.resolution, "pixels")
            elif self.manual_radio.isChecked():
                self._calibration = manual_scale(self.manual_scale.value(), self.resolution)
            else:
                length_px = self.canvas.reference_length_px
                if not length_px:
                    raise ValueError("Draw a reference line first.")
                self._calibration = scale_from_reference(length_px, self.known_length.value(), self.resolution)
        except ValueError as exc:
            QMessageBox.warning(self, "Calibration", str(exc))
            return
        self.accept()

    @property
    def calibration(self) -> Calibration | None:
        return self._calibration


class AnalyticsWorker(QThread):
    progress = pyqtSignal(int, int)
    preview = pyqtSignal(int, object, object)
    result_ready = pyqtSignal(object)
    failed = pyqtSignal(str)

    def __init__(self, video_path: Path, config: AnalysisConfig, parent: QWidget | None = None) -> None:
        super().__init__(parent)
        self.video_path = Path(video_path)
        self.config = AnalysisConfig.from_dict(config.to_dict())
        self._cancel = threading.Event()

    def request_cancel(self) -> None:
        self._cancel.set()

    def run(self) -> None:
        try:
            analyzer = DropletAnalyzer(self.config)
            result = analyzer.analyze(
                self.video_path,
                progress_callback=lambda done, total: self.progress.emit(done, total),
                preview_callback=lambda index, frame, overlays: self.preview.emit(index, frame, overlays),
                cancel_check=self._cancel.is_set,
            )
            self.result_ready.emit(result)
        except Exception as exc:  # worker must report failures without killing Qt
            self.failed.emit(str(exc))


class AnalyticsPage(QWidget):
    NARROW_BREAKPOINT = 860

    def __init__(self) -> None:
        super().__init__()
        self.setObjectName("analyticsPage")
        self._layout_mode: str | None = None
        self._apply_page_styles()
        self._store = ResultStore(ANALYTICS_DIR, ANALYTICS_SETTINGS_PATH)
        self._config = self._store.load_settings()
        self._video_source: VideoSource | None = None
        self._current_video_path: Path | None = None
        self._current_frame_index = 0
        self._current_frame: np.ndarray | None = None
        self._result: AnalysisResult | None = None
        self._worker: AnalyticsWorker | None = None
        self._changing_video_box = False

        self._play_timer = QTimer(self)
        self._play_timer.timeout.connect(self._playback_tick)

        page_layout = QVBoxLayout(self)
        page_layout.setContentsMargins(0, 0, 0, 0)
        page_layout.setSpacing(0)
        self._scroll = QScrollArea()
        self._scroll.setObjectName("analyticsScroll")
        self._scroll.setFrameShape(QFrame.Shape.NoFrame)
        self._scroll.setWidgetResizable(True)
        self._scroll.setHorizontalScrollBarPolicy(Qt.ScrollBarPolicy.ScrollBarAlwaysOff)
        self._content = QWidget()
        self._content.setObjectName("analyticsContent")
        self._scroll.setWidget(self._content)
        page_layout.addWidget(self._scroll)

        self._root = QVBoxLayout(self._content)
        self._root.setContentsMargins(PAGE_GUTTER, 8, PAGE_GUTTER, 8)
        self._root.setSpacing(8)
        self._toolbar = self._build_toolbar()
        self._preview_card = self._build_preview_card()
        self._summary_card = self._build_summary_card()
        self._graph_card = self._build_graph_card()
        self._main_holder = QWidget()
        self._main_holder.setObjectName("analyticsResponsiveHolder")
        self._main_grid = QGridLayout(self._main_holder)
        self._main_grid.setContentsMargins(0, 0, 0, 0)
        self._main_grid.setHorizontalSpacing(8)
        self._main_grid.setVerticalSpacing(8)
        self._root.addWidget(self._toolbar)
        self._root.addWidget(self._main_holder)
        self._root.addWidget(self._graph_card)
        self._root.addStretch(1)
        self._apply_responsive_layout(1085)

        self._refresh_video_list()
        self._update_scale_chip()
        self._set_result(None)

    # ------------------------------ responsive ------------------------------
    def resizeEvent(self, event) -> None:  # noqa: N802
        super().resizeEvent(event)
        self._apply_responsive_layout(event.size().width())

    @staticmethod
    def _clear_layout(layout) -> None:
        while layout.count():
            layout.takeAt(0)

    def _apply_responsive_layout(self, available_width: int) -> None:
        mode = "narrow" if available_width < self.NARROW_BREAKPOINT else "normal"
        if mode == self._layout_mode:
            return
        self._layout_mode = mode
        self._clear_layout(self._main_grid)
        if mode == "normal":
            self._main_grid.addWidget(self._preview_card, 0, 0)
            self._main_grid.addWidget(self._summary_card, 0, 1)
            self._main_grid.setColumnStretch(0, 66)
            self._main_grid.setColumnStretch(1, 34)
            self._toolbar.setFixedHeight(62)
            self._preview_card.setFixedHeight(380)
            self._summary_card.setFixedHeight(380)
            self._graph_card.setFixedHeight(230)
            self._content.setMinimumHeight(704)
        else:
            self._main_grid.addWidget(self._preview_card, 0, 0)
            self._main_grid.addWidget(self._summary_card, 1, 0)
            self._main_grid.setColumnStretch(0, 1)
            self._toolbar.setFixedHeight(104)
            self._preview_card.setFixedHeight(370)
            self._summary_card.setFixedHeight(236)
            self._graph_card.setFixedHeight(220)
            self._content.setMinimumHeight(962)
        self._layout_toolbar(mode)
        self._layout_summary()

    # ------------------------------- toolbar --------------------------------
    def _build_toolbar(self) -> QFrame:
        card = QFrame()
        card.setObjectName("analyticsToolbar")
        outer = QVBoxLayout(card)
        outer.setContentsMargins(14, 8, 14, 8)
        outer.setSpacing(6)
        self._toolbar_row_1_widget = QWidget()
        self._toolbar_row_1_widget.setObjectName("analyticsToolbarRow")
        self._toolbar_row_1 = QHBoxLayout(self._toolbar_row_1_widget)
        self._toolbar_row_1.setContentsMargins(0, 0, 0, 0)
        self._toolbar_row_1.setSpacing(8)
        outer.addWidget(self._toolbar_row_1_widget)
        self._toolbar_row_2_widget = QWidget()
        self._toolbar_row_2_widget.setObjectName("analyticsToolbarRow")
        self._toolbar_row_2 = QHBoxLayout(self._toolbar_row_2_widget)
        self._toolbar_row_2.setContentsMargins(0, 0, 0, 0)
        self._toolbar_row_2.setSpacing(8)
        outer.addWidget(self._toolbar_row_2_widget)

        self._video_label = QLabel("Video")
        self._video_label.setProperty("analyticsRole", "fieldLabel")
        self._video_box = QComboBox()
        self._video_box.setObjectName("analyticsVideoCombo")
        self._video_box.setMinimumWidth(190)
        self._video_box.setMaximumWidth(245)
        self._video_box.setSizePolicy(QSizePolicy.Policy.Expanding, QSizePolicy.Policy.Fixed)
        self._video_box.currentIndexChanged.connect(self._video_selection_changed)

        self._scale_chip = QPushButton()
        self._scale_chip.setObjectName("analyticsInfoChip")
        self._scale_chip.setMinimumWidth(132)
        self._scale_chip.setMaximumWidth(156)
        self._scale_chip.clicked.connect(self._open_calibration)
        self._scale_chip.setEnabled(False)
        self._mode_chip = QPushButton("Mode: Auto")
        self._mode_chip.setObjectName("analyticsModeChip")
        self._mode_chip.setFixedWidth(96)
        self._mode_chip.clicked.connect(self._open_analysis_setup)
        self._mode_chip.setEnabled(False)

        self._load_button = QPushButton("Load Video")
        self._load_button.setObjectName("analyticsActionButton")
        self._load_button.setMinimumWidth(100)
        self._load_button.clicked.connect(self._load_external_video)
        self._analyze_button = QPushButton("Analyze")
        self._analyze_button.setObjectName("analyticsPrimaryButton")
        self._analyze_button.setMinimumWidth(104)
        self._analyze_button.setEnabled(False)
        self._analyze_button.clicked.connect(self._analyze_or_stop)
        self._export_csv_button = QPushButton("Export CSV")
        self._export_csv_button.setObjectName("analyticsActionButton")
        self._export_csv_button.setMinimumWidth(108)
        self._export_csv_button.setEnabled(False)
        self._export_csv_button.clicked.connect(self._export_csv)
        self._toolbar_actions = (self._load_button, self._analyze_button, self._export_csv_button)
        return card

    def _layout_toolbar(self, mode: str) -> None:
        self._clear_layout(self._toolbar_row_1)
        self._clear_layout(self._toolbar_row_2)
        self._toolbar_row_1.addWidget(self._video_label)
        self._toolbar_row_1.addWidget(self._video_box, 1)
        self._toolbar_row_1.addWidget(self._scale_chip)
        self._toolbar_row_1.addWidget(self._mode_chip)
        if mode == "normal":
            self._toolbar_row_2_widget.hide()
            self._toolbar_row_1.addStretch(1)
            for button in self._toolbar_actions:
                self._toolbar_row_1.addWidget(button)
        else:
            self._toolbar_row_2_widget.show()
            self._toolbar_row_1.addStretch(1)
            self._toolbar_row_2.addStretch(1)
            for button in self._toolbar_actions:
                self._toolbar_row_2.addWidget(button)

    # ---------------------------- processed frame ----------------------------
    def _build_preview_card(self) -> QFrame:
        card = self._card()
        layout = QVBoxLayout(card)
        layout.setContentsMargins(12, 10, 12, 9)
        layout.setSpacing(7)
        title = QLabel("Processed frame")
        title.setProperty("analyticsRole", "sectionTitle")
        layout.addWidget(title)
        self._frame_view = FrameView()
        self._frame_view.setObjectName("analyticsProcessedFrame")
        layout.addWidget(self._frame_view, 1)
        controls = QHBoxLayout()
        controls.setContentsMargins(0, 0, 0, 0)
        controls.setSpacing(7)
        self._previous_button = QPushButton("|◀")
        self._play_button = QPushButton("▶")
        self._next_button = QPushButton("▶|")
        for button in (self._previous_button, self._play_button, self._next_button):
            button.setObjectName("analyticsMediaButton")
            button.setFixedSize(38, 30)
            button.setEnabled(False)
            controls.addWidget(button)
        self._previous_button.clicked.connect(lambda: self._step_frame(-1))
        self._play_button.clicked.connect(self._toggle_playback)
        self._next_button.clicked.connect(lambda: self._step_frame(1))
        self._seek = QSlider(Qt.Orientation.Horizontal)
        self._seek.setObjectName("analyticsSeekSlider")
        self._seek.setRange(0, 0)
        self._seek.setMinimumWidth(120)
        self._seek.setEnabled(False)
        self._seek.sliderMoved.connect(self._seek_to_frame)
        controls.addWidget(self._seek, 1)
        self._timestamp = QLabel("00:00 / 00:00   •   Frame 0 / 0")
        self._timestamp.setProperty("analyticsRole", "mediaMeta")
        self._timestamp.setAlignment(Qt.AlignmentFlag.AlignRight | Qt.AlignmentFlag.AlignVCenter)
        controls.addWidget(self._timestamp)
        layout.addLayout(controls)
        return card

    # ------------------------------- summary --------------------------------
    def _build_summary_card(self) -> QFrame:
        card = self._card()
        layout = QVBoxLayout(card)
        layout.setContentsMargins(14, 10, 14, 12)
        layout.setSpacing(9)
        title = QLabel("Analysis summary")
        title.setProperty("analyticsRole", "sectionTitle")
        layout.addWidget(title)
        self._summary_grid = QGridLayout()
        self._summary_grid.setContentsMargins(0, 0, 0, 0)
        self._summary_grid.setHorizontalSpacing(9)
        self._summary_grid.setVerticalSpacing(9)
        self._metric_values: dict[str, QLabel] = {}
        self._metric_cards = [
            self._metric_card("Droplets", "droplets"),
            self._metric_card("Mean length", "mean_length"),
            self._metric_card("Generation rate", "generation_rate"),
            self._metric_card("Valid", "valid"),
        ]
        layout.addLayout(self._summary_grid, 1)
        return card

    def _layout_summary(self) -> None:
        self._clear_layout(self._summary_grid)
        for idx, metric in enumerate(self._metric_cards):
            self._summary_grid.addWidget(metric, idx // 2, idx % 2)
        self._summary_grid.setColumnStretch(0, 1)
        self._summary_grid.setColumnStretch(1, 1)

    def _metric_card(self, label: str, key: str) -> QFrame:
        frame = QFrame()
        frame.setObjectName("analyticsMetricCard")
        layout = QVBoxLayout(frame)
        layout.setContentsMargins(8, 8, 8, 8)
        layout.setSpacing(5)
        layout.setAlignment(Qt.AlignmentFlag.AlignCenter)
        label_widget = QLabel(label)
        label_widget.setProperty("analyticsRole", "metricLabel")
        label_widget.setAlignment(Qt.AlignmentFlag.AlignCenter)
        value_widget = QLabel("—")
        value_widget.setProperty("analyticsRole", "metricValue")
        value_widget.setAlignment(Qt.AlignmentFlag.AlignCenter)
        self._metric_values[key] = value_widget
        layout.addWidget(label_widget)
        layout.addWidget(value_widget)
        return frame

    # -------------------------------- graph ----------------------------------
    def _build_graph_card(self) -> QFrame:
        card = self._card()
        layout = QVBoxLayout(card)
        layout.setContentsMargins(12, 9, 12, 7)
        layout.setSpacing(3)
        header = QHBoxLayout()
        title = QLabel("Droplet length over time")
        title.setProperty("analyticsRole", "sectionTitle")
        header.addWidget(title)
        header.addStretch(1)
        self._metric_combo = QComboBox()
        self._metric_combo.setObjectName("analyticsSmallCombo")
        self._metric_combo.addItems(["Length", "Velocity", "Spacing"])
        self._metric_combo.setFixedWidth(124)
        self._metric_combo.currentIndexChanged.connect(self._update_graph)
        header.addWidget(self._metric_combo)
        self._filter_combo = QComboBox()
        self._filter_combo.setObjectName("analyticsSmallCombo")
        self._filter_combo.addItems(["All droplets", "Valid droplets", "Rejected only"])
        self._filter_combo.setFixedWidth(142)
        self._filter_combo.currentIndexChanged.connect(self._update_graph)
        header.addWidget(self._filter_combo)
        layout.addLayout(header)
        self._plot = pg.PlotWidget(background="#FFFFFF")
        self._plot.setMenuEnabled(False)
        self._plot.showGrid(x=True, y=True, alpha=0.18)
        self._plot.getAxis("left").setTextPen("#69766F")
        self._plot.getAxis("bottom").setTextPen("#69766F")
        self._plot.getAxis("left").setPen("#C1CBC6")
        self._plot.getAxis("bottom").setPen("#C1CBC6")
        self._plot.setLabel("bottom", "Time", units="s")
        self._plot.setLabel("left", "Length", units="px")
        self._curve = self._plot.plot(
            [], [],
            pen=pg.mkPen("#168F69", width=1.5),
            symbol="o", symbolSize=5,
            symbolBrush="#FFFFFF", symbolPen=pg.mkPen("#168F69", width=1.1),
        )
        layout.addWidget(self._plot, 1)
        return card

    # ------------------------------ video state ------------------------------
    def _refresh_video_list(self) -> None:
        videos = discover_videos(CAPTURES_DIR)
        self._changing_video_box = True
        self._video_box.clear()
        if not videos:
            self._video_box.addItem("No recordings found", None)
        else:
            for path in videos:
                self._video_box.addItem(path.name, str(path))
        self._changing_video_box = False
        if videos:
            self._load_video(videos[0], add_to_combo=False)

    def _video_selection_changed(self, index: int) -> None:
        if self._changing_video_box:
            return
        value = self._video_box.itemData(index)
        if value:
            self._load_video(Path(value), add_to_combo=False)

    def _load_external_video(self) -> None:
        filters = "Video files (*.mp4 *.avi *.mov *.mkv *.m4v);;All files (*)"
        filename, _ = QFileDialog.getOpenFileName(self, "Load Video", str(CAPTURES_DIR), filters)
        if filename:
            self._load_video(Path(filename), add_to_combo=True)

    def _load_video(self, path: Path, *, add_to_combo: bool) -> None:
        self._stop_playback()
        self._cancel_worker_if_running()
        try:
            source = VideoSource(path)
        except VideoOpenError as exc:
            QMessageBox.warning(self, "Analytics", str(exc))
            return
        if self._video_source is not None:
            self._video_source.release()
        source.open()
        self._video_source = source
        self._current_video_path = source.metadata.path
        self._current_frame_index = 0
        if self._config.calibration.calibrated and not self._config.calibration.compatible_with(
            source.metadata.width, source.metadata.height
        ):
            self._config.calibration = Calibration(None, (source.metadata.width, source.metadata.height), "pixels")
            self._store.save_settings(self._config)
        if add_to_combo:
            existing = [self._video_box.itemData(i) for i in range(self._video_box.count())]
            value = str(source.metadata.path)
            self._changing_video_box = True
            if value not in existing:
                self._video_box.insertItem(0, source.metadata.path.name, value)
            self._video_box.setCurrentIndex(existing.index(value) if value in existing else 0)
            self._changing_video_box = False
        self._seek.setRange(0, source.metadata.frame_count - 1)
        self._seek.setValue(0)
        for widget in (self._seek, self._previous_button, self._play_button, self._next_button):
            widget.setEnabled(True)
        self._scale_chip.setEnabled(True)
        self._mode_chip.setEnabled(True)
        self._analyze_button.setEnabled(True)
        self._result = None
        self._set_result(None)
        self._update_scale_chip()
        self._show_frame(0)
        # Restore a matching cached result instantly when possible.
        expected = compute_cache_key(source.metadata.path, self._config)
        cached = self._store.load_cached(source.metadata.path, expected)
        if cached is not None:
            self._apply_result(cached)

    def _show_frame(self, frame_index: int, frame: np.ndarray | None = None, overlays=None) -> None:
        if self._video_source is None:
            return
        index = max(0, min(self._video_source.metadata.frame_count - 1, int(frame_index)))
        try:
            raw = frame if frame is not None else self._video_source.read_frame(index)
        except VideoOpenError as exc:
            self._stop_playback()
            QMessageBox.warning(self, "Analytics", str(exc))
            return
        self._current_frame_index = index
        self._current_frame = raw.copy()
        if self._result is not None:
            annotated = render_analysis_frame(
                raw,
                roi_px=self._result.roi_px,
                flow_direction=self._result.flow_direction,
                line_a_s=self._result.line_a_s,
                line_b_s=self._result.line_b_s,
                overlays=self._result.overlays.get(index, []),
                calibration_um_per_px=self._result.config.calibration.um_per_px,
            )
        elif overlays is not None:
            annotated = render_analysis_frame(raw, overlays=overlays)
        else:
            roi = self._roi_px_for_current_video()
            annotated = render_analysis_frame(
                raw,
                roi_px=roi,
                flow_direction=self._config.flow_direction,
                calibration_um_per_px=self._config.calibration.um_per_px,
            )
        self._frame_view.set_bgr_frame(annotated)
        self._seek.blockSignals(True)
        self._seek.setValue(index)
        self._seek.blockSignals(False)
        self._update_timestamp()

    def _roi_px_for_current_video(self):
        if self._video_source is None:
            return None
        x, y, w, h = self._config.normalized_roi()
        width, height = self._video_source.metadata.width, self._video_source.metadata.height
        x0 = int(round(x * width)); y0 = int(round(y * height))
        return x0, y0, max(1, int(round(w * width))), max(1, int(round(h * height)))

    def _update_timestamp(self) -> None:
        if self._video_source is None:
            self._timestamp.setText("00:00 / 00:00   •   Frame 0 / 0")
            return
        meta = self._video_source.metadata
        current_s = self._current_frame_index / meta.fps
        self._timestamp.setText(
            f"{self._format_time(current_s)} / {self._format_time(meta.duration_s)}   •   "
            f"Frame {self._current_frame_index + 1} / {meta.frame_count}"
        )

    @staticmethod
    def _format_time(seconds: float) -> str:
        seconds = max(0.0, float(seconds))
        minutes = int(seconds // 60)
        secs = int(seconds % 60)
        return f"{minutes:02d}:{secs:02d}"

    def _seek_to_frame(self, frame_index: int) -> None:
        self._show_frame(frame_index)

    def _step_frame(self, delta: int) -> None:
        self._stop_playback()
        self._show_frame(self._current_frame_index + delta)

    def _toggle_playback(self) -> None:
        if self._video_source is None:
            return
        if self._play_timer.isActive():
            self._stop_playback()
            return
        interval = max(1, int(round(1000.0 / self._video_source.metadata.fps)))
        self._play_timer.start(interval)
        self._play_button.setText("Ⅱ")

    def _stop_playback(self) -> None:
        self._play_timer.stop()
        if hasattr(self, "_play_button"):
            self._play_button.setText("▶")

    def _playback_tick(self) -> None:
        if self._video_source is None:
            self._stop_playback()
            return
        next_index = self._current_frame_index + 1
        if next_index >= self._video_source.metadata.frame_count:
            self._stop_playback()
            return
        self._show_frame(next_index)

    # ------------------------- calibration / setup ---------------------------
    def _update_scale_chip(self) -> None:
        calibration = self._config.calibration
        if calibration.calibrated:
            self._scale_chip.setText(f"Scale: {calibration.um_per_px:.4g} µm/px")
        else:
            self._scale_chip.setText("Scale: Pixels only")

    def _open_calibration(self) -> None:
        if self._current_frame is None:
            return
        dialog = CalibrationDialog(self._current_frame, self._config, self)
        if dialog.exec() != QDialog.DialogCode.Accepted or dialog.calibration is None:
            return
        self._config.calibration = dialog.calibration
        self._store.save_settings(self._config)
        self._update_scale_chip()
        self._invalidate_analysis()
        self._show_frame(self._current_frame_index)

    def _open_analysis_setup(self) -> None:
        if self._current_frame is None:
            return
        dialog = AnalysisSetupDialog(self._current_frame, self._config, self)
        if dialog.exec() != QDialog.DialogCode.Accepted:
            return
        self._config = dialog.updated_config(self._config)
        self._store.save_settings(self._config)
        self._invalidate_analysis()
        self._show_frame(self._current_frame_index)

    def _invalidate_analysis(self) -> None:
        self._result = None
        self._set_result(None)

    # ------------------------------- analysis --------------------------------
    def _analyze_or_stop(self) -> None:
        if self._worker is not None and self._worker.isRunning():
            self._worker.request_cancel()
            self._analyze_button.setText("Stopping…")
            self._analyze_button.setEnabled(False)
            return
        self._start_analysis()

    def _start_analysis(self) -> None:
        if self._current_video_path is None:
            return
        self._stop_playback()
        # Analysis always means the complete recording.  Reset review playback
        # immediately so the current slider position can never imply a partial
        # analysis range.
        self._show_frame(0)
        self._worker = AnalyticsWorker(self._current_video_path, self._config, self)
        self._worker.progress.connect(self._analysis_progress)
        self._worker.preview.connect(self._analysis_preview)
        self._worker.result_ready.connect(self._analysis_finished)
        self._worker.failed.connect(self._analysis_failed)
        self._worker.finished.connect(self._worker_done)
        self._analyze_button.setText("Analyzing 0%")
        self._export_csv_button.setEnabled(False)
        self._video_box.setEnabled(False)
        self._load_button.setEnabled(False)
        self._scale_chip.setEnabled(False)
        self._mode_chip.setEnabled(False)
        self._worker.start()

    def _analysis_progress(self, done: int, total: int) -> None:
        percent = int(round(done / max(1, total) * 100.0))
        self._analyze_button.setText(f"Stop · {percent}%")
        self._analyze_button.setEnabled(True)

    def _analysis_preview(self, index: int, frame: object, overlays: object) -> None:
        if isinstance(frame, np.ndarray):
            self._show_frame(index, frame, overlays)

    def _analysis_finished(self, result: object) -> None:
        if not isinstance(result, AnalysisResult):
            return
        self._store.save_result(result)
        self._apply_result(result)
        if result.complete:
            self._analyze_button.setText("Analysis complete")
            QTimer.singleShot(1600, lambda: self._analyze_button.setText("Analyze"))
        else:
            self._analyze_button.setText("Analysis stopped")
            QTimer.singleShot(1600, lambda: self._analyze_button.setText("Analyze"))

    def _analysis_failed(self, message: str) -> None:
        QMessageBox.warning(self, "Analytics", message)
        self._analyze_button.setText("Analyze")

    def _worker_done(self) -> None:
        self._worker = None
        self._analyze_button.setEnabled(self._current_video_path is not None)
        self._video_box.setEnabled(True)
        self._load_button.setEnabled(True)
        self._scale_chip.setEnabled(self._current_video_path is not None)
        self._mode_chip.setEnabled(self._current_video_path is not None)
        self._export_csv_button.setEnabled(self._result is not None and bool(self._result.measurements))

    def _cancel_worker_if_running(self) -> None:
        if self._worker is not None and self._worker.isRunning():
            self._worker.request_cancel()
            self._worker.wait(3000)
            self._worker = None

    def _apply_result(self, result: AnalysisResult) -> None:
        self._result = result
        self._config = AnalysisConfig.from_dict(result.config.to_dict())
        self._store.save_settings(self._config)
        self._update_scale_chip()
        self._set_result(result)
        # After analysis, review starts from the beginning instead of leaving
        # the slider on the final preview frame.
        self._show_frame(0)

    def _set_result(self, result: AnalysisResult | None) -> None:
        if result is None:
            for label in self._metric_values.values():
                label.setText("—")
            self._export_csv_button.setEnabled(False)
            self._curve.setData([], [])
            return
        summary = result.summary
        self._metric_values["droplets"].setText(str(summary.total_droplets))
        if result.config.calibration.calibrated and summary.mean_length_um is not None:
            self._metric_values["mean_length"].setText(f"{summary.mean_length_um:.1f} µm")
        elif summary.mean_length_px is not None:
            self._metric_values["mean_length"].setText(f"{summary.mean_length_px:.1f} px")
        else:
            self._metric_values["mean_length"].setText("—")
        self._metric_values["generation_rate"].setText(
            "—" if summary.generation_rate_s is None else f"{summary.generation_rate_s:.1f} /s"
        )
        self._metric_values["valid"].setText(f"{summary.valid_droplets} / {summary.total_droplets}")
        self._export_csv_button.setEnabled(bool(result.measurements))
        self._update_graph()

    # -------------------------------- graph ----------------------------------
    def _update_graph(self) -> None:
        result = self._result
        if result is None:
            self._curve.setData([], [])
            return
        filter_name = self._filter_combo.currentText()
        measurements = result.measurements
        if filter_name == "Valid droplets":
            measurements = [item for item in measurements if item.valid]
        elif filter_name == "Rejected only":
            measurements = [item for item in measurements if not item.valid]
        metric = self._metric_combo.currentText()
        x_values, y_values = [], []
        calibrated = result.config.calibration.calibrated
        for item in measurements:
            if metric == "Length":
                value = item.length_um if calibrated else item.length_px
                label, unit = "Length", "µm" if calibrated else "px"
            elif metric == "Velocity":
                value = item.velocity_mm_s if calibrated else item.velocity_px_s
                label, unit = "Velocity", "mm/s" if calibrated else "px/s"
            else:
                value = item.spacing_um if calibrated else item.spacing_px
                label, unit = "Spacing", "µm" if calibrated else "px"
            if value is None:
                continue
            x_values.append(item.timestamp_s)
            y_values.append(value)
        self._plot.setLabel("left", label, units=unit)
        self._curve.setData(x_values, y_values)

    # -------------------------------- export ---------------------------------
    def _export_csv(self) -> None:
        if self._result is None or self._current_video_path is None:
            return
        default = unique_path(
            ANALYTICS_EXPORT_DIR,
            f"{self._current_video_path.stem}_analytics",
        )
        filename, _ = QFileDialog.getSaveFileName(
            self,
            "Export Analytics CSV",
            str(default),
            "CSV files (*.csv)",
        )
        if not filename:
            return
        try:
            target = export_result_csv(self._result, filename)
        except OSError as exc:
            QMessageBox.warning(self, "Export CSV", str(exc))
            return
        self._export_csv_button.setText("Exported")
        self._export_csv_button.setToolTip(str(target))
        QTimer.singleShot(1400, lambda: self._export_csv_button.setText("Export CSV"))

    # ------------------------------- cleanup ---------------------------------
    def shutdown(self) -> None:
        self._stop_playback()
        self._cancel_worker_if_running()
        if self._video_source is not None:
            self._video_source.release()

    @staticmethod
    def _card() -> QFrame:
        card = QFrame()
        card.setObjectName("analyticsCard")
        card.setSizePolicy(QSizePolicy.Policy.Expanding, QSizePolicy.Policy.Fixed)
        return card

    def _apply_page_styles(self) -> None:
        self.setStyleSheet(
            """
QWidget#analyticsPage, QWidget#analyticsContent, QWidget#analyticsResponsiveHolder,
QScrollArea#analyticsScroll, QScrollArea#analyticsScroll > QWidget > QWidget {
    background: #F8FAF9; border: none;
}
QWidget#analyticsToolbarRow { background: transparent; border: none; }
QFrame#analyticsToolbar, QFrame#analyticsCard {
    background: #FFFFFF; border: 1px solid #D8E1DC; border-radius: 6px;
}
QLabel[analyticsRole="fieldLabel"] { color: #26332E; font-size: 13px; font-weight: 400; }
QLabel[analyticsRole="sectionTitle"] { color: #17241F; font-size: 15px; font-weight: 700; }
QComboBox#analyticsVideoCombo, QComboBox#analyticsSmallCombo {
    background: #FFFFFF; color: #26332E; border: 1px solid #CBD7D2; border-radius: 6px;
    padding: 0 26px 0 10px; font-size: 12px;
}
QComboBox#analyticsVideoCombo { min-height: 36px; max-height: 36px; }
QComboBox#analyticsSmallCombo { min-height: 30px; max-height: 30px; padding-left: 9px; }
QComboBox#analyticsVideoCombo::drop-down, QComboBox#analyticsSmallCombo::drop-down { width: 24px; border: 0; }
QComboBox#analyticsVideoCombo QAbstractItemView, QComboBox#analyticsSmallCombo QAbstractItemView {
    background: #FFFFFF; color: #26332E; border: 1px solid #CBD7D2;
    selection-background-color: #E6F3ED; selection-color: #26332E; outline: none;
}
QPushButton#analyticsInfoChip, QPushButton#analyticsModeChip {
    min-height: 34px; max-height: 34px; border-radius: 6px; font-size: 12px; padding: 0 8px;
}
QPushButton#analyticsInfoChip {
    background: #F4F7F5; color: #33403A; border: 1px solid #DDE5E1;
}
QPushButton#analyticsModeChip {
    background: #EAF5F0; color: #167F60; border: 1px solid #D5EAE1; font-weight: 500;
}
QPushButton#analyticsInfoChip:hover, QPushButton#analyticsModeChip:hover { border-color: #81C8AD; }
QPushButton#analyticsActionButton, QPushButton#analyticsPrimaryButton {
    min-height: 36px; max-height: 36px; border-radius: 6px; padding: 0 12px;
    font-size: 12px; font-weight: 500;
}
QPushButton#analyticsActionButton { background: #FFFFFF; color: #168A68; border: 1px solid #81C8AD; }
QPushButton#analyticsPrimaryButton { background: #168E69; color: #FFFFFF; border: 1px solid #168E69; font-weight: 600; }
QPushButton#analyticsActionButton:hover { background: #F4FAF7; border-color: #39A87F; }
QPushButton#analyticsPrimaryButton:hover { background: #137C5C; border-color: #137C5C; }
QPushButton:disabled { color: #97A29D; background: #F1F4F2; border-color: #DEE4E1; }
QLabel#analyticsProcessedFrame { background: #D7DBD9; border: 1px solid #D1D8D5; border-radius: 4px; }
QPushButton#analyticsMediaButton {
    background: #F7F9F8; color: #44534D; border: 1px solid #D5DEDA; border-radius: 5px;
    font-size: 11px; padding: 0;
}
QPushButton#analyticsMediaButton:hover { background: #EFF6F2; color: #168A68; }
QSlider#analyticsSeekSlider::groove:horizontal { height: 4px; background: #DCE4E0; border-radius: 2px; }
QSlider#analyticsSeekSlider::sub-page:horizontal { background: #168F69; border-radius: 2px; }
QSlider#analyticsSeekSlider::handle:horizontal {
    width: 12px; height: 12px; margin: -5px 0; background: #FFFFFF;
    border: 1px solid #79BDA4; border-radius: 6px;
}
QLabel[analyticsRole="mediaMeta"] { color: #394842; font-size: 11px; }
QFrame#analyticsMetricCard { background: #FFFFFF; border: 1px solid #D8E1DC; border-radius: 6px; }
QLabel[analyticsRole="metricLabel"] { color: #26332E; font-size: 12px; font-weight: 400; }
QLabel[analyticsRole="metricValue"] { color: #108963; font-size: 19px; font-weight: 700; }
"""
        )
