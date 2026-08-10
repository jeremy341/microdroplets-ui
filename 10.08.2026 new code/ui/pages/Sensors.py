import json
import math
import random
from dataclasses import dataclass
from datetime import datetime
from time import monotonic
from pathlib import Path

from PyQt6.QtCore import QPointF, QRectF, QSize, QTimer, Qt, pyqtSignal
from PyQt6.QtGui import QColor, QFont, QIcon, QPainter, QPainterPath, QPen, QPixmap
from PyQt6.QtWidgets import (
    QComboBox,
    QFileDialog,
    QAbstractScrollArea,
    QFrame,
    QHBoxLayout,
    QLabel,
    QLineEdit,
    QPushButton,
    QScrollArea,
    QSizePolicy,
    QToolButton,
    QVBoxLayout,
    QWidget,
)

from ui.design_tokens import CARD_GAP, CARD_PADDING, PAGE_BOTTOM, PAGE_GUTTER, PAGE_TOP, PRIMARY_CONTROL_HEIGHT, ROW_HEIGHT, SECTION_GAP
from backend.csv_logger import AsyncCsvLogger, LogSample

try:
    import pyqtgraph as pg
except ImportError:  # pragma: no cover - gives a useful startup error below
    pg = None


GREEN = "#13956B"
BLUE = "#0E55FF"
RED = "#FF1F1F"

# The backend normalizes liquid flow to Bartels FluidicStudio's unit.
LIQUID_FLOW_UNIT = "µL/min"

@dataclass(frozen=True)
class MeasurementDefinition:
    """Display metadata for a physical measurement reported by a board."""

    sensor_id: str
    label: str
    unit: str
    quantity: str
    precision: int = 1
    color: str = GREEN
    default: float = 0.0

    @property
    def axis_label(self):
        return f"{self.quantity} ({self.unit})" if self.unit else self.quantity


MEASUREMENTS = {
    "liquid_flow": MeasurementDefinition(
        "liquid_flow", "Liquid Flow Rate", LIQUID_FLOW_UNIT,
        "Liquid flow rate", 2, BLUE, 0.0
    ),
    "pressure": MeasurementDefinition(
        "pressure", "Pressure", "mbar", "Pressure", 1, RED, 101.2
    ),
    "gas_flow": MeasurementDefinition(
        "gas_flow", "Gas Flow Rate", "ml/min", "Gas flow rate", 1, "#00A6A6", 5.0
    ),
    "analog_1": MeasurementDefinition("analog_1", "Analog 1", "V", "Voltage", 2, "#7C3AED", 0.0),
    "analog_2": MeasurementDefinition("analog_2", "Analog 2", "V", "Voltage", 2, "#A855F7", 0.0),
    "analog_3": MeasurementDefinition("analog_3", "Analog 3", "V", "Voltage", 2, "#C026D3", 0.0),
}

# Fallback inventory until the connected board reports its actual sensors.
DEFAULT_SENSOR_SPECS = tuple(
    {
        "id": definition.sensor_id,
        "label": definition.label,
        "unit": definition.unit,
        "precision": definition.precision,
        "active": definition.sensor_id in ("liquid_flow", "pressure"),
        "default": definition.default,
    }
    for definition in MEASUREMENTS.values()
)


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


class SelectionBox(QWidget):
    """Small custom checkbox matching the square controls in the mockup."""

    toggled = pyqtSignal(bool)

    def __init__(self, checked=False, parent=None):
        super().__init__(parent)
        self._checked = checked
        self.setFixedSize(22, 22)
        self.setCursor(Qt.CursorShape.PointingHandCursor)

    def isChecked(self):
        return self._checked

    def setChecked(self, checked):
        checked = bool(checked)
        if self._checked == checked:
            return
        self._checked = checked
        self.update()
        self.toggled.emit(checked)

    def mouseReleaseEvent(self, event):
        if self.isEnabled() and event.button() == Qt.MouseButton.LeftButton:
            self.setChecked(not self._checked)
        super().mouseReleaseEvent(event)

    def paintEvent(self, event):
        painter = QPainter(self)
        painter.setRenderHint(QPainter.RenderHint.Antialiasing)
        box = QRectF(1.5, 1.5, 19, 19)
        comparison = bool(self.property("comparison"))
        if not self.isEnabled() and self._checked and comparison:
            painter.setPen(QPen(QColor("#0F8B64"), 1))
            painter.setBrush(QColor("#20A879"))
        elif not self.isEnabled():
            painter.setPen(QPen(QColor("#D8DEE3"), 1))
            painter.setBrush(QColor("#F2F4F5"))
        elif self._checked:
            painter.setPen(QPen(QColor("#0F8B64"), 1))
            painter.setBrush(QColor("#20A879"))
        else:
            painter.setPen(QPen(QColor("#B9C5CE"), 1))
            painter.setBrush(QColor("#FFFFFF"))
        painter.drawRoundedRect(box, 3, 3)
        if self._checked and (self.isEnabled() or comparison):
            check_pen = QPen(QColor("#FFFFFF"), 2.2)
            check_pen.setCapStyle(Qt.PenCapStyle.RoundCap)
            painter.setPen(check_pen)
            painter.drawLine(QPointF(5.8, 10.8), QPointF(9.3, 14.1))
            painter.drawLine(QPointF(9.3, 14.1), QPointF(16.4, 6.8))


class ToggleSwitch(QWidget):
    toggled = pyqtSignal(bool)

    def __init__(self, checked=False, parent=None):
        super().__init__(parent)
        self._checked = checked
        self.setFixedSize(49, 29)
        self.setCursor(Qt.CursorShape.PointingHandCursor)

    def isChecked(self):
        return self._checked

    def setChecked(self, checked):
        checked = bool(checked)
        if checked == self._checked:
            return
        self._checked = checked
        self.update()
        self.toggled.emit(checked)

    def mouseReleaseEvent(self, event):
        if event.button() == Qt.MouseButton.LeftButton:
            self.setChecked(not self._checked)
        super().mouseReleaseEvent(event)

    def paintEvent(self, event):
        painter = QPainter(self)
        painter.setRenderHint(QPainter.RenderHint.Antialiasing)
        painter.setPen(Qt.PenStyle.NoPen)
        painter.setBrush(QColor("#1EA477" if self._checked else "#CDD1D4"))
        painter.drawRoundedRect(QRectF(0, 0, 49, 29), 14.5, 14.5)
        knob_x = 24 if self._checked else 3
        painter.setBrush(QColor("#FFFFFF"))
        painter.drawEllipse(QRectF(knob_x, 3, 23, 23))


class ChevronComboBox(QComboBox):
    """Native combo box behavior with a stable cross-platform chevron."""

    def paintEvent(self, event):
        super().paintEvent(event)
        painter = QPainter(self)
        painter.setRenderHint(QPainter.RenderHint.Antialiasing)
        color = "#314158" if self.isEnabled() else "#9AA6B2"
        pen = QPen(QColor(color), 1.35)
        pen.setCapStyle(Qt.PenCapStyle.RoundCap)
        pen.setJoinStyle(Qt.PenJoinStyle.RoundJoin)
        painter.setPen(pen)
        center_x = self.width() - 16
        center_y = self.height() / 2
        painter.drawLine(
            QPointF(center_x - 4, center_y - 2),
            QPointF(center_x, center_y + 2),
        )
        painter.drawLine(
            QPointF(center_x, center_y + 2),
            QPointF(center_x + 4, center_y - 2),
        )


class ExpandButton(QToolButton):
    """Accordion button drawn as the line chevron from the mockup."""

    def paintEvent(self, event):
        painter = QPainter(self)
        painter.setRenderHint(QPainter.RenderHint.Antialiasing)
        if self.underMouse():
            painter.setPen(Qt.PenStyle.NoPen)
            painter.setBrush(QColor("#F1F5F7"))
            painter.drawRoundedRect(self.rect(), 5, 5)

        pen = QPen(QColor("#173A55"), 1.35)
        pen.setCapStyle(Qt.PenCapStyle.RoundCap)
        pen.setJoinStyle(Qt.PenJoinStyle.RoundJoin)
        painter.setPen(pen)
        painter.setBrush(Qt.BrushStyle.NoBrush)
        center_x = self.width() / 2
        center_y = self.height() / 2

        if self.isChecked():
            left = QPointF(center_x - 5, center_y + 2)
            center = QPointF(center_x, center_y - 3)
            right = QPointF(center_x + 5, center_y + 2)
        else:
            left = QPointF(center_x - 5, center_y - 2)
            center = QPointF(center_x, center_y + 3)
            right = QPointF(center_x + 5, center_y - 2)

        painter.drawLine(left, center)
        painter.drawLine(center, right)


if pg is not None:
    class _SensorViewBox(pg.ViewBox):
        """ViewBox that tells the chart when a person changes the Y view."""

        user_range_changed = pyqtSignal()

        def wheelEvent(self, event, axis=None):
            self.user_range_changed.emit()
            super().wheelEvent(event, axis=axis)

        def mouseDragEvent(self, event, *args, **kwargs):
            if event.button() in (Qt.MouseButton.LeftButton, Qt.MouseButton.RightButton):
                self.user_range_changed.emit()
            super().mouseDragEvent(event, *args, **kwargs)


    class _SensorTimeAxis(pg.AxisItem):
        """Formats real sample timestamps as HH:MM:SS labels."""

        def __init__(self, chart):
            super().__init__(orientation="bottom")
            self.chart = chart

        def tickStrings(self, values, scale, spacing):
            if not self.chart.uses_datetime_axis:
                return [str(round(value, 1)) for value in values]
            return [datetime.fromtimestamp(value).strftime("%H:%M:%S") for value in values]

class SensorChart(QWidget):
    """One focused, dynamically labelled measurement chart."""

    LIVE_WINDOW_SECONDS = 30.0

    def __init__(self):
        super().__init__()
        self.setObjectName("sensorChartCanvas")
        self.setSizePolicy(QSizePolicy.Policy.Expanding, QSizePolicy.Policy.Expanding)
        self.series = []
        self.timestamps = []
        self.uses_datetime_axis = False
        self._recording = False
        self._manual_scale = False
        self._follow_live = True
        self._current_y_range = None
        self._target_y_range = None
        self._quiet_since = None
        self._programmatic_range_change = False

        if pg is None:
            raise RuntimeError(
                "The Sensors page requires pyqtgraph. Install dependencies with "
                "'python -m pip install -r requirements.txt'."
            )

        self._value_axis = pg.AxisItem("left")
        self._time_axis = _SensorTimeAxis(self)
        self._view_box = _SensorViewBox(enableMenu=False)
        self._view_box.user_range_changed.connect(self._on_user_range_changed)
        self._plot = pg.PlotWidget(
            parent=self,
            viewBox=self._view_box,
            axisItems={
                "left": self._value_axis,
                "bottom": self._time_axis,
            },
            background="#FFFFFF",
        )
        self._plot.setObjectName("sensorPlotWidget")
        self._plot.setSizePolicy(QSizePolicy.Policy.Expanding, QSizePolicy.Policy.Expanding)
        self._plot.setFrameShape(QFrame.Shape.NoFrame)
        self._plot.setMenuEnabled(False)
        self._plot.hideButtons()
        self._plot.showGrid(x=True, y=True, alpha=0.09)
        self._plot.hideAxis("right")
        self._plot.setLabel("bottom", "Time", color="#33445A")
        self._plot.setXRange(0, 72, padding=0)
        self._plot.setLimits(xMin=0, minXRange=2)
        self._plot.setClipToView(True)
        self._plot.setDownsampling(auto=True, mode="peak")

        self._zero_line = pg.InfiniteLine(
            pos=0,
            angle=0,
            pen=pg.mkPen("#98A6B5", width=1, style=Qt.PenStyle.DashLine),
            movable=False,
        )
        self._zero_line.setZValue(-10)
        self._plot.addItem(self._zero_line)

        self._value_axis.setPen(pg.mkPen("#53606D", width=1.15))
        self._value_axis.setTextPen(pg.mkPen("#25364A"))
        self._value_axis.setWidth(48)
        self._value_axis.setStyle(tickFont=QFont("Segoe UI", 8), tickTextOffset=2)
        self._time_axis.setPen(pg.mkPen("#53606D", width=1))
        self._time_axis.setTextPen(pg.mkPen("#25364A"))
        self._time_axis.setHeight(32)
        self._time_axis.setStyle(tickFont=QFont("Segoe UI", 8), tickTextOffset=2)
        self._plot.getPlotItem().getViewBox().setDefaultPadding(0)

        self._scale_timer = QTimer(self)
        self._scale_timer.setInterval(32)
        self._scale_timer.timeout.connect(self._advance_scale_animation)
        self._scale_timer.start()

        self._curves = {}

        layout = QVBoxLayout(self)
        layout.setContentsMargins(0, 0, 0, 0)
        layout.setSpacing(0)
        layout.addWidget(self._plot, 1)

    def set_data(self, series, timestamps, axis_label="Value"):
        """Render arbitrary series without scaling values into another unit."""

        previous_series = self.series
        self.series = [dict(item) for item in series]
        self.timestamps = list(timestamps)
        self.uses_datetime_axis = any(item.get("timestamps") for item in self.series)
        data_changed = len(previous_series) != len(self.series)
        if not data_changed:
            for previous, current in zip(previous_series, self.series):
                if (
                    previous.get("id") != current.get("id")
                    or list(previous.get("values", ()))
                    != list(current.get("values", ()))
                    or list(previous.get("timestamps", ()))
                    != list(current.get("timestamps", ()))
                ):
                    data_changed = True
                    break
        active_ids = {item["id"] for item in self.series}
        for curve_id in tuple(self._curves):
            if curve_id not in active_ids:
                curve = self._curves.pop(curve_id)
                self._plot.removeItem(curve)

        for item in self.series:
            curve = self._curves.get(item["id"])
            if curve is None:
                curve = self._plot.plot()
                self._curves[item["id"]] = curve
            values = list(item.get("values", ()))
            item_timestamps = list(item.get("timestamps", ()))
            if len(item_timestamps) == len(values) and item_timestamps:
                x_values = [timestamp.timestamp() for timestamp in item_timestamps]
            else:
                x_values = list(range(len(values)))
            curve.setData(
                x_values,
                values,
                pen=pg.mkPen(item.get("color", GREEN), width=1.7),
                connect="finite",
            )

        self._plot.setLabel("left", "", color="#33445A")
        if self.series:
            x_values = [
                timestamp.timestamp()
                for item in self.series
                for timestamp in item.get("timestamps", ())
            ]
            if x_values:
                first, latest = min(x_values), max(x_values)
                if self._follow_live:
                    if latest - first < self.LIVE_WINDOW_SECONDS:
                        low = first
                        high = first + self.LIVE_WINDOW_SECONDS
                    else:
                        low = latest - self.LIVE_WINDOW_SECONDS
                        high = latest
                    self._plot.setXRange(low, high, padding=0)
            else:
                max_count = max(len(item.get("values", ())) for item in self.series)
                self._plot.setXRange(0, max(max_count - 1, 2), padding=0)
            # Layout refreshes and accordion changes can call set_data() with
            # the exact same measurements. Do not treat a redraw as new data:
            # recalculating here made the animated Y range drift by a few units
            # even though no measurement had changed.
            if data_changed:
                self._update_stable_y_range()
        self._time_axis.picture = None
        self._time_axis.prepareGeometryChange()
        self._time_axis.update()

    def set_recording(self, recording):
        self._recording = bool(recording)
        if not self._recording and self.series:
            self.fit_data()

    def resume_live(self):
        """Return to the latest samples after a manual pan or zoom."""
        self._follow_live = True
        self._manual_scale = False
        self._quiet_since = None
        if self.series:
            self.set_data(self.series, self.timestamps)
            self._update_stable_y_range(force=True)

    def fit_data(self):
        """Fit the complete buffered history for deliberate session review."""
        self._follow_live = False
        self._manual_scale = False
        self._quiet_since = None
        values = self._finite_values()
        if values:
            self._request_range(self._padded_range(min(values), max(values)), force=True)
        x_values = [
            timestamp.timestamp()
            for item in self.series
            for timestamp in item.get("timestamps", ())
        ]
        if x_values:
            low, high = min(x_values), max(x_values)
            if high <= low:
                high = low + 2.0
            self._plot.setXRange(low, high, padding=0.02)

    def reset_zoom(self):
        self.resume_live()

    def _on_user_range_changed(self):
        if not self._programmatic_range_change:
            self._manual_scale = True

    def _finite_values(self, visible_only=False):
        values = []
        x_low, x_high = self._view_box.viewRange()[0]
        for item in self.series:
            item_values = list(item.get("values", ()))
            item_timestamps = list(item.get("timestamps", ()))
            for index, value in enumerate(item_values):
                if value is None or not math.isfinite(float(value)):
                    continue
                if visible_only and len(item_timestamps) == len(item_values):
                    x_value = item_timestamps[index].timestamp()
                    if x_value < x_low or x_value > x_high:
                        continue
                values.append(float(value))
        return values

    @staticmethod
    def _padded_range(low, high):
        # Preserve both flow directions in the viewport. Only add a modest
        # symmetric margin around the observed signed signal.
        if low >= 0.0:
            span = max(high - low, abs(high) * 0.04, 1.0)
            return 0.0, max(high + span * 0.14, 1.0)
        span = max(high - low, abs(high) * 0.04, abs(low) * 0.04, 1.0)
        padding = span * 0.14
        return low - padding, high + padding

    def _update_stable_y_range(self, force=False):
        if self._manual_scale:
            return
        values = self._finite_values(visible_only=self._follow_live)
        if not values:
            return
        data_low, data_high = min(values), max(values)
        candidate = self._padded_range(data_low, data_high)
        if force or self._current_y_range is None:
            self._request_range(candidate, force=True)
            return

        current_low, current_high = self._current_y_range
        current_span = current_high - current_low
        outer_margin = current_span * 0.08
        needs_expand = (
            data_low < current_low + outer_margin
            or data_high > current_high - outer_margin
        )
        if needs_expand and (candidate[0] < current_low or candidate[1] > current_high):
            self._quiet_since = None
            self._request_range(
                (min(current_low, candidate[0]), max(current_high, candidate[1])),
                force=True,
            )
            return

        inner_margin = current_span * 0.22
        comfortably_inside = data_low > current_low + inner_margin and data_high < current_high - inner_margin
        if comfortably_inside:
            if self._quiet_since is None:
                self._quiet_since = datetime.now()
            elif (datetime.now() - self._quiet_since).total_seconds() >= 2:
                self._request_range(candidate)
        else:
            self._quiet_since = None

    def _request_range(self, target, force=False):
        low, high = target
        if high <= low:
            high = low + 1.0
        if not force and self._current_y_range:
            old_span = self._current_y_range[1] - self._current_y_range[0]
            if high - low >= old_span * 0.82:
                return
        self._target_y_range = (low, high)
        if self._current_y_range is None:
            self._current_y_range = self._target_y_range
            self._apply_y_range(self._current_y_range)

    def _advance_scale_animation(self):
        if not self._target_y_range or not self._current_y_range:
            return
        current_low, current_high = self._current_y_range
        target_low, target_high = self._target_y_range
        delta_low = target_low - current_low
        delta_high = target_high - current_high
        if max(abs(delta_low), abs(delta_high)) < 0.5:
            self._current_y_range = self._target_y_range
            self._target_y_range = None
        else:
            self._current_y_range = (current_low + delta_low * 0.16, current_high + delta_high * 0.16)
        self._apply_y_range(self._current_y_range)

    def _apply_y_range(self, value_range):
        self._programmatic_range_change = True
        try:
            self._view_box.disableAutoRange(axis=pg.ViewBox.YAxis)
            self._view_box.setYRange(*value_range, padding=0, update=True)
        finally:
            self._programmatic_range_change = False


class SensorCard(QFrame):
    measurement_selected = pyqtSignal(str, str)
    expansion_requested = pyqtSignal(str, bool)

    def __init__(self, board_id, board_name, sensors, expanded=True, accent_color=BLUE):
        super().__init__()
        self.board_id = board_id
        self.setObjectName("sensorBoardCard")
        self.setProperty("comparison", False)
        self.setSizePolicy(QSizePolicy.Policy.Expanding, QSizePolicy.Policy.Maximum)

        self.header_widget = QWidget()
        self.header_widget.setObjectName("sensorCardHeader")
        header_layout = QHBoxLayout(self.header_widget)
        header_layout.setContentsMargins(18, 14, 13, 14)
        header_layout.setSpacing(12)

        device_icon = QLabel()
        device_icon.setObjectName("sensorDeviceIcon")
        device_icon.setStyleSheet(
            f"background-color: {accent_color}; border: 1px solid {accent_color};"
        )
        device_icon.setSizePolicy(QSizePolicy.Policy.Fixed, QSizePolicy.Policy.Fixed)

        text_layout = QVBoxLayout()
        text_layout.setContentsMargins(0, 0, 0, 0)
        text_layout.setSpacing(1)
        self.board_label = QLabel(board_name)
        self.board_label.setObjectName("sensorBoardName")
        type_label = QLabel("Device Type: Multiboard")
        type_label.setObjectName("sensorBoardType")
        self.status_label = QLabel("Waiting for sensor data…")
        self.status_label.setObjectName("sensorBoardType")
        text_layout.addWidget(self.board_label)
        text_layout.addWidget(type_label)
        text_layout.addWidget(self.status_label)

        self.comparison_badge = QLabel("COMPARE")
        self.comparison_badge.setObjectName("sensorComparisonBadge")
        self.comparison_badge.setVisible(False)

        self.expand_button = ExpandButton()
        self.expand_button.setObjectName("sensorExpandButton")
        self.expand_button.setCheckable(True)
        self.expand_button.setChecked(expanded)
        self.expand_button.setSizePolicy(
            QSizePolicy.Policy.Fixed, QSizePolicy.Policy.Fixed
        )
        self.expand_button.toggled.connect(self.toggle_body)
        # The arrow remains the only clickable accordion control.
        self.expand_button.clicked.connect(
            lambda checked: self.expansion_requested.emit(self.board_id, checked)
        )

        header_layout.addWidget(device_icon)
        header_layout.addLayout(text_layout, 1)
        header_layout.addWidget(self.comparison_badge)
        header_layout.addWidget(self.expand_button)

        self.body = QWidget()
        self.body.setObjectName("sensorCardBody")
        body_layout = QVBoxLayout(self.body)
        body_layout.setContentsMargins(0, 8, 0, 8)
        body_layout.setSpacing(0)

        self.value_labels = {}
        self.sensor_rows = []
        self.sensor_rows_by_id = {}
        self.sensor_availability = {}
        self.selection_boxes = {}
        for sensor in sensors:
            row_widget = QWidget()
            row_widget.setObjectName("sensorRow")
            row_widget.setSizePolicy(
                QSizePolicy.Policy.Expanding, QSizePolicy.Policy.Fixed
            )
            row_widget.setFixedHeight(ROW_HEIGHT)
            row = QHBoxLayout(row_widget)
            row.setContentsMargins(18, 0, 18, 0)
            row.setSpacing(12)

            checkbox = SelectionBox(sensor.get("visible", False))
            checkbox.toggled.connect(
                lambda checked, sensor_id=sensor["sensor_id"]: self._selection_toggled(
                    sensor_id, checked
                )
            )
            name = QLabel(sensor["label"])
            name.setObjectName("sensorName")
            value = QLabel(sensor.get("value", ""))
            value.setObjectName("sensorValue")
            value.setAlignment(Qt.AlignmentFlag.AlignRight | Qt.AlignmentFlag.AlignVCenter)

            row.addWidget(checkbox)
            row.addWidget(name, 1)
            row.addWidget(value)
            body_layout.addWidget(row_widget)
            available = bool(sensor.get("available", True))
            row_widget.setProperty("available", available)
            row_widget.setEnabled(available)
            checkbox.setEnabled(available)
            self.sensor_rows.append((row_widget, sensor.get("active", True)))
            self.sensor_rows_by_id[sensor["sensor_id"]] = row_widget
            self.sensor_availability[sensor["sensor_id"]] = available
            self.selection_boxes[sensor["sensor_id"]] = checkbox
            self.value_labels[sensor["key"]] = (
                value,
                sensor.get("unit", ""),
                sensor.get("precision", 1),
            )

        layout = QVBoxLayout(self)
        layout.setContentsMargins(0, 0, 0, 0)
        layout.setSpacing(0)
        layout.addWidget(self.header_widget)
        layout.addWidget(self.body)
        self.body.setVisible(expanded)

    def _selection_toggled(self, sensor_id, checked):
        if checked:
            self.set_selected_measurement(sensor_id)
            self.measurement_selected.emit(self.board_id, sensor_id)
            return

        # An empty selection is valid. It means that this board is connected
        # but is not currently included in the chart comparison.
        self.measurement_selected.emit(self.board_id, "")

    def set_selected_measurement(self, sensor_id):
        for key, checkbox in self.selection_boxes.items():
            checkbox.blockSignals(True)
            checkbox.setChecked(key == sensor_id)
            checkbox.blockSignals(False)

    def clear_selected_measurement(self):
        """Leave every measurement unchecked until the user chooses one."""
        for checkbox in self.selection_boxes.values():
            checkbox.blockSignals(True)
            checkbox.setChecked(False)
            checkbox.blockSignals(False)

    def set_comparison_state(self, enabled, sensor_id=None):
        self.setProperty("comparison", bool(enabled))
        self.comparison_badge.setVisible(False)
        if enabled and sensor_id:
            self.set_selected_measurement(sensor_id)
        for current_sensor_id, checkbox in self.selection_boxes.items():
            available = self.sensor_availability.get(current_sensor_id, True)
            allowed = available and (not enabled or current_sensor_id == sensor_id)
            checkbox.setProperty("comparison", bool(enabled and current_sensor_id == sensor_id))
            checkbox.setEnabled(allowed)
            checkbox.update()
            row_widget = self.sensor_rows_by_id.get(current_sensor_id)
            if row_widget is not None:
                row_widget.setEnabled(allowed)
        self.style().unpolish(self)
        self.style().polish(self)
        self.update()

    def toggle_body(self, expanded):
        self.expand_button.update()
        self.body.setVisible(expanded)

    def set_board_name(self, board_name):
        self.board_label.setText(board_name)

    def set_status(self, status):
        self.status_label.setText(str(status or "Waiting for sensor data…"))

    def set_expanded(self, expanded):
        self.expand_button.setChecked(bool(expanded))
        self.toggle_body(bool(expanded))

    def set_hide_inactive(self, hide_inactive):
        for row_widget, active in self.sensor_rows:
            row_widget.setVisible(active or not hide_inactive)

    def update_values(self, values):
        for key, (label, unit, precision) in self.value_labels.items():
            if key in values:
                label.setText(f"Current: {values[key]:,.{precision}f} {unit}")


class SensorsPage(QWidget):
    """Sensor monitoring page matching the approved desktop mockup."""

    def __init__(self):
        super().__init__()
        self.setObjectName("sensorsPage")
        self.active_board = None
        self.connected_boards = []
        self.cards_by_id = {}
        self.expanded_board_id = None
        self.logging = False
        self.csv_logger = None
        self._reported_logger_error = None
        self.hide_inactive = False
        self.current_values = {}
        self.accumulated_volumes_ul = {}
        self.selected_measurements = {}
        self.explicit_measurement_selections = set()
        self.measurement_metadata = {}
        self.histories = {}
        self.history_timestamps = {}
        self.session_rows = []
        self.board_wait_started = {}
        self.board_last_sample_at = {}
        self.board_stream_retry_at = {}
        self.last_demo_update = {}

        root = QVBoxLayout(self)
        root.setContentsMargins(PAGE_GUTTER, PAGE_TOP, PAGE_GUTTER, PAGE_BOTTOM)
        root.setSpacing(SECTION_GAP)

        # The target uses a free-standing row, not one large bordered card.
        # Each control remains an independent widget so Qt can distribute the
        # available width without QSS fighting a fixed parent geometry.
        toolbar = QWidget()
        toolbar.setObjectName("sensorLoggingBar")
        toolbar.setSizePolicy(QSizePolicy.Policy.Expanding, QSizePolicy.Policy.Fixed)
        toolbar_layout = QHBoxLayout(toolbar)
        toolbar_layout.setContentsMargins(0, 0, 0, 0)
        toolbar_layout.setSpacing(SECTION_GAP)

        log_configuration = QWidget()
        log_configuration.setObjectName("sensorLogConfiguration")
        configuration_layout = QHBoxLayout(log_configuration)
        configuration_layout.setContentsMargins(0, 0, 0, 0)
        configuration_layout.setSpacing(0)

        self.browse_button = QPushButton("Open File Explorer")
        self.browse_button.setObjectName("sensorBrowseButton")
        self.browse_button.setIcon(make_folder_icon())
        self.browse_button.setIconSize(QSize(18, 18))
        self.browse_button.setFixedSize(202, PRIMARY_CONTROL_HEIGHT)
        self.browse_button.clicked.connect(self.choose_log_path)

        default_log_path = Path.home() / "Documents" / "log_data.csv"
        self.path_edit = QLineEdit(str(default_log_path).replace("\\", "/"))
        self.path_edit.setObjectName("sensorPathEdit")
        self.path_edit.setFixedHeight(PRIMARY_CONTROL_HEIGHT)
        self.path_edit.setSizePolicy(
            QSizePolicy.Policy.Expanding, QSizePolicy.Policy.Fixed
        )

        rate_label = QLabel("Sample rate")
        rate_label.setObjectName("sensorRateLabel")
        rate_label.setAlignment(Qt.AlignmentFlag.AlignVCenter)
        self.rate_box = ChevronComboBox()
        self.rate_box.setObjectName("sensorRateBox")
        self.rate_box.addItems(
            ["1 sec", "2 sec", "3 sec", "5 sec", "10 sec"]
        )
        self.rate_box.setFixedSize(132, PRIMARY_CONTROL_HEIGHT)

        # Logging buttons
        self.start_button = QPushButton("Start Logging")
        self.start_button.setObjectName("sensorStartButton")
        self.start_button.setFixedSize(150, PRIMARY_CONTROL_HEIGHT)
        self.start_button.clicked.connect(self.start_logging)
        self.stop_button = QPushButton("Stop")
        self.stop_button.setObjectName("sensorStopButton")
        self.stop_button.setFixedSize(96, PRIMARY_CONTROL_HEIGHT)
        self.stop_button.clicked.connect(self.stop_logging)

        configuration_layout.addWidget(self.browse_button)
        configuration_layout.addSpacing(16)
        configuration_layout.addWidget(self.path_edit, 1)
        configuration_layout.addSpacing(24)
        configuration_layout.addWidget(rate_label)
        configuration_layout.addSpacing(12)
        configuration_layout.addWidget(self.rate_box)

        logging_actions = QWidget()
        logging_actions.setObjectName("sensorLoggingActions")
        actions_layout = QHBoxLayout(logging_actions)
        actions_layout.setContentsMargins(0, 0, 0, 0)
        actions_layout.setSpacing(0)
        actions_layout.addStretch(1)
        actions_layout.addWidget(self.start_button)
        actions_layout.addSpacing(27)
        actions_layout.addWidget(self.stop_button)

        toolbar_layout.addWidget(log_configuration, 21)
        toolbar_layout.addWidget(logging_actions, 10)
        root.addWidget(toolbar, 0)

        content = QHBoxLayout()
        content.setContentsMargins(0, 0, 0, 0)
        content.setSpacing(CARD_GAP)

        chart_panel = QFrame()
        chart_panel.setObjectName("sensorChartPanel")
        chart_layout = QVBoxLayout(chart_panel)
        chart_layout.setContentsMargins(CARD_PADDING, CARD_PADDING, CARD_PADDING, CARD_PADDING)
        chart_layout.setSpacing(SECTION_GAP)

        chart_header = QHBoxLayout()
        chart_header.setContentsMargins(0, 0, 0, 0)
        self.chart_title = QLabel("Live sensor data")
        self.chart_title.setObjectName("sensorSectionTitle")
        chart_header.addWidget(self.chart_title)
        chart_header.addStretch()
        self.legend_widget = QWidget()
        self.legend_widget.setObjectName("sensorDynamicLegend")
        self.legend_layout = QVBoxLayout(self.legend_widget)
        self.legend_layout.setContentsMargins(0, 0, 0, 0)
        self.legend_layout.setSpacing(8)
        # Reserve the two-board legend height so adding MB2 never moves the plot.
        self.legend_widget.setFixedHeight(32)
        chart_header.addWidget(self.legend_widget)

        self.fit_button = QPushButton("Fit data")
        self.fit_button.setObjectName("sensorFitButton")
        self.fit_button.setToolTip("Show the complete buffered session")
        self.fit_button.clicked.connect(self.chart_fit_data)
        chart_header.addSpacing(14)
        chart_header.addWidget(self.fit_button)

        self.auto_scale_button = QPushButton("Live view")
        self.auto_scale_button.setObjectName("sensorAutoScaleButton")
        self.auto_scale_button.setToolTip("Follow the newest samples and resume automatic scaling")
        self.auto_scale_button.clicked.connect(self.chart_auto_scale)
        chart_header.addWidget(self.auto_scale_button)

        self.axis_title = QLabel(f"Liquid Flow ({LIQUID_FLOW_UNIT})")
        self.axis_title.setObjectName("sensorAxisTitle")
        self.axis_title.setVisible(False)

        self.chart = SensorChart()
        self.chart.set_data([], [])
        chart_layout.addLayout(chart_header)
        chart_layout.addSpacing(12)
        chart_layout.addWidget(self.axis_title)
        chart_layout.addSpacing(23)
        chart_layout.addWidget(self.chart, 1)
        content.addWidget(chart_panel, 21)

        side = QWidget()
        side.setObjectName("sensorSidePanel")
        side_layout = QVBoxLayout(side)
        side_layout.setContentsMargins(0, 0, 0, 0)
        side_layout.setSpacing(12)

        self.cards_container = QWidget()
        self.cards_layout = QVBoxLayout(self.cards_container)
        self.cards_layout.setContentsMargins(0, 0, 0, 0)
        self.cards_layout.setSpacing(CARD_GAP)

        # Board cards are inserted here only while those boards are connected.
        self.cards_layout.addStretch()

        scroll = QScrollArea()
        scroll.setObjectName("sensorScrollArea")
        scroll.setWidgetResizable(True)
        scroll.setSizeAdjustPolicy(QAbstractScrollArea.SizeAdjustPolicy.AdjustIgnored)
        scroll.setMinimumHeight(0)
        scroll.setFrameShape(QFrame.Shape.NoFrame)
        scroll.setHorizontalScrollBarPolicy(Qt.ScrollBarPolicy.ScrollBarAlwaysOff)
        scroll.setWidget(self.cards_container)
        side_layout.addWidget(scroll, 1)

        filter_row = QHBoxLayout()
        filter_row.setContentsMargins(0, 0, 8, 0)
        filter_row.addStretch()
        self.hide_switch = ToggleSwitch(False)
        self.hide_switch.toggled.connect(self.set_hide_inactive)
        filter_label = QLabel("Hide Inactive Sensors")
        filter_label.setObjectName("hideInactiveLabel")
        filter_row.addWidget(self.hide_switch)
        filter_row.addSpacing(6)
        filter_row.addWidget(filter_label)
        side_layout.addLayout(filter_row)
        content.addWidget(side, 10)
        root.addLayout(content, 1)

        self.display_timer = QTimer(self)
        self.display_timer.timeout.connect(self.update_sensor_values)
        self.display_timer.start(100)

    @staticmethod
    def _legend_item(color, text):
        item = QWidget()
        layout = QHBoxLayout(item)
        layout.setContentsMargins(0, 0, 0, 0)
        layout.setSpacing(8)
        line = QFrame()
        line.setObjectName("sensorLegendLine")
        line.setSizePolicy(QSizePolicy.Policy.Fixed, QSizePolicy.Policy.Fixed)
        line.setStyleSheet(f"background-color: {color}; border: none;")
        label = QLabel(text)
        label.setObjectName("sensorLegendText")
        layout.addWidget(line)
        layout.addWidget(label)
        return item

    def chart_fit_data(self):
        self.chart.fit_data()

    def chart_auto_scale(self):
        self.chart.reset_zoom()

    @staticmethod
    def _board_id(board):
        if not board:
            return None
        value = board.get("port") or board.get("id") or board.get("name")
        return str(value) if value is not None else None

    @staticmethod
    def _sensor_id(sensor, index):
        value = (
            sensor.get("id")
            or sensor.get("sensor_id")
            or sensor.get("key")
            or sensor.get("name")
            or sensor.get("label")
            or f"sensor_{index + 1}"
        )
        value = "_".join(str(value).rsplit(":", 1)[-1].lower().replace("-", " ").split())
        return {
            "liquid_flow_rate": "liquid_flow",
            "flow_rate": "liquid_flow",
            "gas_flow_rate": "gas_flow",
        }.get(value, value)

    @classmethod
    def _sensor_catalog(cls, boards):
        """Build the visible union of sensors reported by all boards."""

        catalog = {spec["id"]: dict(spec) for spec in DEFAULT_SENSOR_SPECS}
        for board in boards:
            for index, source in enumerate(board.get("sensors") or ()):
                sensor = dict(source)
                sensor_id = cls._sensor_id(sensor, index)
                catalog.setdefault(sensor_id, sensor)
        return tuple(catalog.values())

    def _sensors_for_board(self, board, board_index, sensor_catalog=None):
        board_id = self._board_id(board)
        reported_sensors = board.get("sensors") or ()
        if sensor_catalog is None or not reported_sensors:
            sensors = board.get("sensors") or DEFAULT_SENSOR_SPECS
        else:
            reported = {
                self._sensor_id(source, index): dict(source)
                for index, source in enumerate(reported_sensors)
            }
            sensors = []
            for index, source in enumerate(sensor_catalog):
                sensor_id = self._sensor_id(source, index)
                if sensor_id in reported:
                    sensors.append(reported[sensor_id])
                else:
                    unavailable = dict(source)
                    unavailable["active"] = False
                    unavailable["available"] = False
                    sensors.append(unavailable)

        rows = []
        active_ids = []
        for index, source in enumerate(sensors):
            sensor = dict(source)
            sensor_id = self._sensor_id(sensor, index)
            key = f"{board_id}:{sensor_id}"
            known = MEASUREMENTS.get(sensor_id)
            label = sensor.get("label") or sensor.get("name") or (
                known.label if known else sensor_id.replace("_", " ").title()
            )
            # Always use Bartels' liquid-flow unit, even if an older metadata
            # payload still reports the firmware's raw unit.
            unit = (
                LIQUID_FLOW_UNIT
                if sensor_id == "liquid_flow"
                else sensor.get("unit", known.unit if known else "")
            )
            quantity = sensor.get("quantity") or (known.quantity if known else label)
            precision = int(sensor.get("precision", known.precision if known else 1))
            active = bool(sensor.get("active", sensor.get("available", True)))
            available = bool(sensor.get("available", True))
            default = float(sensor.get("value", sensor.get("default", known.default if known else 0.0)))

            self.measurement_metadata[key] = {
                "sensor_id": sensor_id,
                "label": label,
                "unit": unit,
                "axis_label": sensor.get("axis_label") or (
                    f"{quantity} ({unit})" if unit else quantity
                ),
                "precision": precision,
                "color": sensor.get("color", known.color if known else GREEN),
                "default": default,
                "active": active,
                "available": available,
            }
            if active:
                active_ids.append(sensor_id)

            if key not in self.histories:
                self.histories[key] = []
            if key not in self.history_timestamps:
                self.history_timestamps[key] = []
            if self.histories[key]:
                self.current_values[key] = self.histories[key][-1]

            rows.append(
                {
                    "key": key,
                    "sensor_id": sensor_id,
                    "label": label,
                    "unit": unit,
                    "precision": precision,
                    "visible": False,
                    "active": active,
                    "available": available,
                }
            )

        if board_id not in self.selected_measurements:
            preferred = "liquid_flow" if "liquid_flow" in active_ids else (
                active_ids[0] if active_ids else (rows[0]["sensor_id"] if rows else None)
            )
            self.selected_measurements[board_id] = preferred
        selected = self.selected_measurements.get(board_id)
        for row in rows:
            row["visible"] = row["sensor_id"] == selected
        return rows

    @staticmethod
    def _simulated_value(default, sensor_id, sample_index, board_index):
        """Temporary source until SensorService supplies real samples."""

        phase = sample_index + board_index * 2.4
        amplitude = max(abs(default) * 0.012, 0.05)
        if sensor_id.startswith("analog_"):
            amplitude = 0.08
        noise = random.uniform(-amplitude * 0.045, amplitude * 0.045)
        offset = -board_index * max(abs(default) * 0.065, amplitude)
        trend = amplitude * 0.58 * math.sin(phase / 12.0)
        ripple = amplitude * 0.27 * math.sin(phase / 2.35)
        fine_motion = amplitude * 0.12 * math.sin(phase / 1.18)
        return default + offset + trend + ripple + fine_motion + noise

    def set_connected_boards(self, boards):
        """Create exactly one card per currently connected board."""

        connected = []
        seen_ids = set()
        for board in boards or []:
            board_id = self._board_id(board)
            if board_id is not None and board_id not in seen_ids:
                seen_ids.add(board_id)
                connected.append(board)

        old_ids = set(self.cards_by_id)
        new_ids = [self._board_id(board) for board in connected]
        newly_added_ids = set(new_ids) - old_ids
        now = monotonic()
        for board_id in newly_added_ids:
            self.board_wait_started[board_id] = now
            self.board_last_sample_at.pop(board_id, None)
            self.board_stream_retry_at.pop(board_id, None)
        for board_id in old_ids - set(new_ids):
            self.board_wait_started.pop(board_id, None)
            self.board_last_sample_at.pop(board_id, None)
            self.board_stream_retry_at.pop(board_id, None)
        self.explicit_measurement_selections.intersection_update(new_ids)
        sensor_catalog = self._sensor_catalog(connected)

        for board_id in old_ids - set(new_ids):
            card = self.cards_by_id.pop(board_id)
            self.cards_layout.removeWidget(card)
            card.deleteLater()

        if self.expanded_board_id not in new_ids:
            if new_ids and (not old_ids or self.expanded_board_id is not None):
                self.expanded_board_id = new_ids[0]
            else:
                self.expanded_board_id = None

        self.connected_boards = connected
        for index, board in enumerate(connected):
            board_id = self._board_id(board)
            card = self.cards_by_id.get(board_id)
            if card is None:
                card = SensorCard(
                    board_id,
                    board.get("name", board_id),
                    self._sensors_for_board(board, index, sensor_catalog),
                    expanded=board_id == self.expanded_board_id,
                    accent_color=BLUE if index == 0 else RED,
                )
                card.measurement_selected.connect(self.set_measurement)
                card.expansion_requested.connect(self.set_card_expanded)
                self.cards_by_id[board_id] = card
                self.cards_layout.insertWidget(index, card)
                # Adding a second board must not silently start a comparison.
                # Its compatible measurement stays available, but the user
                # must explicitly select it before MB2 is plotted.
                if index > 0 and board_id in newly_added_ids:
                    self.selected_measurements.pop(board_id, None)
                    self.explicit_measurement_selections.discard(board_id)
                    card.clear_selected_measurement()
            else:
                card.set_board_name(board.get("name", board_id))

            card.set_status(board.get("sensor_status", "Waiting for sensor data…"))

            card.set_hide_inactive(self.hide_inactive)
            card.set_expanded(board_id == self.expanded_board_id)
            selected_sensor = self.selected_measurements.get(board_id)
            if selected_sensor:
                card.set_selected_measurement(selected_sensor)
            elif board_id in newly_added_ids and index > 0:
                card.clear_selected_measurement()
            card.update_values(self.current_values)

        self._reserve_card_list_height()
        self._apply_comparison_state()
        self.refresh_chart()

    def _reserve_card_list_height(self):
        """Keep accordion expansion inside the scroll area, not the chart row."""
        cards = list(self.cards_by_id.values())
        if not cards:
            self.cards_container.setMinimumHeight(0)
            return

        headers = [card.header_widget.sizeHint().height() for card in cards]
        bodies = [card.body.layout().sizeHint().height() for card in cards]
        gap_height = max(0, len(cards) - 1) * self.cards_layout.spacing()
        reserved_height = sum(headers) + max(bodies) + gap_height
        self.cards_container.setMinimumHeight(reserved_height)

    def set_card_expanded(self, board_id, expanded):
        """Toggle card visibility without changing measurement selections."""

        if board_id not in self.cards_by_id:
            return
        if expanded:
            self.expanded_board_id = board_id
        elif self.expanded_board_id == board_id:
            self.expanded_board_id = None

        for card_id, card in self.cards_by_id.items():
            card.set_expanded(card_id == self.expanded_board_id)
        self._reserve_card_list_height()
        self._apply_comparison_state()
        self.refresh_chart()

    def set_active_board(self, board):
        self.active_board = board
        board_id = self._board_id(board)
        if board_id in self.cards_by_id and board_id != self.expanded_board_id:
            self.expanded_board_id = board_id
            for card_id, card in self.cards_by_id.items():
                card.set_expanded(card_id == board_id)
            self._apply_comparison_state()
        self.refresh_chart()

    def set_measurement(self, board_id, sensor_id):
        if board_id not in self.cards_by_id:
            return

        if not sensor_id:
            self.selected_measurements.pop(board_id, None)
            self.explicit_measurement_selections.discard(board_id)
            self.cards_by_id[board_id].clear_selected_measurement()
            self._apply_comparison_state()
            self.refresh_chart()
            return

        self.selected_measurements[board_id] = sensor_id
        self.explicit_measurement_selections.add(board_id)
        self.cards_by_id[board_id].set_selected_measurement(sensor_id)
        self._apply_comparison_state()
        self.refresh_chart()

    def set_hide_inactive(self, checked):
        self.hide_inactive = checked
        for card in self.cards_by_id.values():
            card.set_hide_inactive(checked)

    def _display_board_id(self):
        active_id = self._board_id(self.active_board)
        if active_id in self.cards_by_id:
            return active_id
        return self._board_id(self.connected_boards[0]) if self.connected_boards else None

    def _comparison_board_ids(self):
        """Return boards only when both have selected the same valid sensor."""

        if len(self.connected_boards) < 2:
            return None
        primary_id = self._board_id(self.connected_boards[0])
        secondary_id = self._board_id(self.connected_boards[1])
        primary_sensor = self.selected_measurements.get(primary_id)
        secondary_sensor = self.selected_measurements.get(secondary_id)
        # MB1 may use its default selected measurement while it is the only
        # board being tracked.  Selecting the matching measurement on MB2 is
        # the explicit action that enables comparison, so only MB2 must be
        # explicitly selected here.  Requiring MB1 to be explicit caused the
        # chart to fall back to the active board and replace MB1's curve with
        # a blue MB2 curve.
        if (
            secondary_id not in self.explicit_measurement_selections
            or not primary_sensor
            or primary_sensor != secondary_sensor
        ):
            return None
        primary_metadata = self.measurement_metadata.get(f"{primary_id}:{primary_sensor}")
        secondary_metadata = self.measurement_metadata.get(f"{secondary_id}:{secondary_sensor}")
        if primary_metadata and secondary_metadata and primary_metadata["active"] and secondary_metadata["active"]:
            return primary_id, secondary_id
        return None

    def _apply_comparison_state(self):
        comparison_ids = self._comparison_board_ids()
        comparison_id = comparison_ids[1] if comparison_ids else None
        primary_sensor = (
            self.selected_measurements.get(comparison_ids[0]) if comparison_ids else None
        )
        if comparison_id and primary_sensor:
            self.selected_measurements[comparison_id] = primary_sensor

        for board_id, card in self.cards_by_id.items():
            card.set_comparison_state(
                board_id == comparison_id,
                primary_sensor if board_id == comparison_id else None,
            )

    def update_sensor_values(self):
        """Drain real samples; generate values only for the explicit demo board."""

        self._update_logging_status()
        changed = False
        for board_index, board in enumerate(self.connected_boards):
            board_id = self._board_id(board)
            connection = board.get("connection")

            if board.get("mode") == "demo":
                now = datetime.now()
                last_update = self.last_demo_update.get(board_id)
                if last_update and (now - last_update).total_seconds() < 0.8:
                    continue
                if f"{board_id}:liquid_flow" in self.measurement_metadata:
                    value = self._simulated_value(
                        1.0,
                        "liquid_flow",
                        len(self.histories.get(f"{board_id}:liquid_flow", ())),
                        board_index,
                    )
                    self._append_sample(
                        board_id,
                        "liquid_flow",
                        value * 1000.0,
                        now,
                        None,
                    )
                    self.last_demo_update[board_id] = now
                    changed = True
                continue

            if connection is None:
                continue
            now_monotonic = monotonic()
            for event in connection.drain_events():
                if event.kind == "state" and event.message:
                    # Expose the same connection progression as Bartels:
                    # identify/configure first, then wait for real samples.
                    board["connection_state"] = getattr(connection, "state", "unknown")
                    if board.get("sensor_status", "").startswith("Sensor error:"):
                        continue
                    board["sensor_status"] = event.message
                elif event.kind == "command" and event.message:
                    board["last_command"] = event.message
                elif event.kind == "raw":
                    # Keep the last wire response inspectable without putting
                    # raw protocol text into the graph or CSV sample stream.
                    board["last_serial_line"] = event.message
                elif event.kind == "firmware" and event.message:
                    board["firmware"] = event.message.replace("Multiboard ", "")
                    board["connection_state"] = "ready"
                elif event.kind == "error":
                    board["sensor_status"] = f"Sensor error: {event.message or 'serial read failed'}"
                sample = event.sample
                if sample is None:
                    continue
                board["sensor_status"] = "Liquid-flow stream active"
                self.board_last_sample_at[board_id] = now_monotonic
                self._append_sample(
                    board_id,
                    sample.sensor_id,
                    sample.value,
                    sample.timestamp.astimezone().replace(tzinfo=None),
                    sample.accumulated_volume_ul,
                    sample.raw_line,
                )
                changed = True

            status = str(board.get("sensor_status", ""))
            if not status.startswith("Sensor error:"):
                last_sample_at = self.board_last_sample_at.get(board_id)
                if last_sample_at is None:
                    waited = now_monotonic - self.board_wait_started.get(
                        board_id, now_monotonic
                    )
                    if waited >= 12.0 and board_id not in self.board_stream_retry_at:
                        try:
                            retried = connection.retry_active_stream()
                        except Exception as exc:
                            board["sensor_status"] = f"Sensor error: stream retry failed: {exc}"
                        else:
                            if retried:
                                self.board_stream_retry_at[board_id] = now_monotonic
                                board["sensor_status"] = (
                                    "Still waiting — flow stream requested again automatically"
                                )
                            else:
                                board["sensor_status"] = (
                                    "Waiting for sensor data — connection remains open"
                                )
                    elif board_id not in self.board_stream_retry_at:
                        board["sensor_status"] = "Waiting for sensor data…"
                elif now_monotonic - last_sample_at >= 5.0:
                    board["sensor_status"] = (
                        "Signal paused — waiting; connection remains open"
                    )

            card = self.cards_by_id.get(board_id)
            if card is not None:
                card.set_status(board.get("sensor_status"))

        if changed:
            for board_id, card in self.cards_by_id.items():
                card.update_values(self.current_values)
            self.refresh_chart()

    def _append_sample(
        self,
        board_id,
        sensor_id,
        value,
        timestamp,
        accumulated_volume_ul,
        raw_line="",
    ):
        key = f"{board_id}:{sensor_id}"
        metadata = self.measurement_metadata.get(key)
        if metadata is None:
            return
        self.histories.setdefault(key, []).append(float(value))
        self.histories[key] = self.histories[key][-300:]
        self.history_timestamps.setdefault(key, []).append(timestamp)
        self.history_timestamps[key] = self.history_timestamps[key][-300:]
        self.current_values[key] = float(value)
        if accumulated_volume_ul is not None:
            self.accumulated_volumes_ul[key] = float(accumulated_volume_ul)
        self.session_rows.append(
            {
                "timestamp": timestamp.isoformat(timespec="milliseconds"),
                "board": board_id,
                "sensor_id": sensor_id,
                "value": float(value),
                "unit": metadata.get("unit", ""),
                "accumulated_volume_ul": accumulated_volume_ul,
                "raw_line": raw_line,
            }
        )
        self._queue_log_sample(
            board_id,
            sensor_id,
            float(value),
            timestamp,
            accumulated_volume_ul,
            raw_line,
            metadata,
        )

    def refresh_chart(self):
        comparison_ids = self._comparison_board_ids()
        board_ids = list(comparison_ids) if comparison_ids else [self._display_board_id()]
        board_ids = [board_id for board_id in board_ids if board_id is not None]
        if not board_ids:
            self.chart.set_data([], [])
            self._set_legend([])
            return

        primary_id = board_ids[0]
        sensor_id = self.selected_measurements.get(primary_id)
        key = f"{primary_id}:{sensor_id}"
        metadata = self.measurement_metadata.get(key)
        if metadata is None:
            self.chart.set_data([], [])
            self._set_legend([])
            return

        series = []
        legend = []
        comparison_colors = (BLUE, RED)
        for index, board_id in enumerate(board_ids):
            series_key = f"{board_id}:{sensor_id}"
            if series_key not in self.histories:
                continue
            board = next(
                (item for item in self.connected_boards if self._board_id(item) == board_id),
                {},
            )
            name = board.get("name", board_id)
            color = comparison_colors[index] if comparison_ids else metadata["color"]
            series.append({
                "id": series_key,
                "values": self.histories[series_key],
                "timestamps": self.history_timestamps.get(series_key, []),
                "color": color,
            })
            legend_text = f"{name} — {metadata['label']}"
            legend.append((color, legend_text))

        title = metadata["label"]
        self.chart_title.setText("Live sensor data")
        self.axis_title.setText(metadata["axis_label"])
        self.axis_title.setVisible(bool(series))
        self._set_legend(legend)
        self.chart.set_data(series, [], metadata["axis_label"])

    def _set_legend(self, entries):
        while self.legend_layout.count():
            item = self.legend_layout.takeAt(0)
            if item.widget():
                item.widget().deleteLater()
        for color, text in entries:
            self.legend_layout.addWidget(self._legend_item(color, text))

    def choose_log_path(self):
        path, _ = QFileDialog.getSaveFileName(
            self, "Choose log file", self.path_edit.text(), "CSV files (*.csv)"
        )
        if path:
            self.path_edit.setText(path)

    def _sample_interval_seconds(self):
        text = self.rate_box.currentText()
        return float(text.split()[0])

    def _logger_series_labels(self):
        """Return Bartels-style column labels for currently connected boards."""
        labels = []
        sensor_id = "liquid_flow"
        for board in self.connected_boards:
            board_id = self._board_id(board)
            if not board_id:
                continue
            metadata = self.measurement_metadata.get(f"{board_id}:{sensor_id}")
            if metadata is None:
                continue
            labels.append(f"{board_id} - {metadata['label'].replace(' ', '')}")
        return tuple(labels)

    def start_logging(self):
        if self.logging:
            return
        path_text = self.path_edit.text().strip()
        if not path_text:
            self.start_button.setText("Choose a CSV file")
            self.start_button.setToolTip("Select a valid CSV path before logging")
            return
        path = Path(path_text)
        if path.suffix.lower() != ".csv":
            path = path.with_suffix(".csv")
            self.path_edit.setText(str(path).replace("\\", "/"))

        logger = AsyncCsvLogger(
            path,
            self._sample_interval_seconds(),
            self._logger_series_labels(),
        )
        self.csv_logger = logger
        self._reported_logger_error = None
        self.logging = True
        self.chart.set_recording(True)
        self.start_button.setText("Starting…")
        self.start_button.setToolTip(
            "CSV logging runs independently from the sensor stream and live graph"
        )
        self.start_button.setEnabled(False)
        self.path_edit.setEnabled(False)
        self.rate_box.setEnabled(False)
        logger.start()

    def stop_logging(self):
        logger = self.csv_logger
        self.logging = False
        self.chart.set_recording(False)
        if logger is not None:
            logger.stop()
        self.csv_logger = None
        self.start_button.setText("Start Logging")
        self.start_button.setToolTip("")
        self.start_button.setEnabled(True)
        self.path_edit.setEnabled(True)
        self.rate_box.setEnabled(True)

    def _queue_log_sample(
        self,
        board_id,
        sensor_id,
        value,
        timestamp,
        accumulated_volume_ul,
        raw_line,
        metadata,
    ):
        logger = self.csv_logger
        if not self.logging or logger is None:
            return
        logger.submit(
            LogSample(
                timestamp=timestamp,
                board_id=board_id,
                sensor_id=sensor_id,
                sensor_type=metadata["label"],
                value=value,
                precision=metadata["precision"],
                unit=metadata["unit"],
                accumulated_volume_ul=accumulated_volume_ul,
                raw_line=raw_line,
            )
        )

    def _update_logging_status(self):
        logger = self.csv_logger
        if not self.logging or logger is None:
            return
        if logger.status == "running":
            self.start_button.setText("Logging…")
            return
        if logger.status != "failed":
            return

        error = logger.error or "Could not write CSV file"
        if error == self._reported_logger_error:
            return
        self._reported_logger_error = error
        self.logging = False
        self.chart.set_recording(False)
        self.start_button.setText("Logging failed")
        self.start_button.setToolTip(error)
        self.start_button.setEnabled(True)
        self.path_edit.setEnabled(True)
        self.rate_box.setEnabled(True)

    def shutdown_logging(self):
        """Flush queued CSV rows before the application exits."""
        self.stop_logging()

    def has_session_data(self):
        return bool(self.session_rows)

    def save_session(self):
        path, _ = QFileDialog.getSaveFileName(
            self, "Save sensor session", "sensor_session.json", "JSON files (*.json)"
        )
        if not path:
            return
        payload = {
            "log_path": self.path_edit.text(),
            "sample_rate": self.rate_box.currentText(),
            "hide_inactive": self.hide_switch.isChecked(),
            "selected_measurements": self.selected_measurements,
        }
        Path(path).write_text(json.dumps(payload, indent=2), encoding="utf-8")

    def load_session(self):
        path, _ = QFileDialog.getOpenFileName(
            self, "Load sensor session", "", "JSON files (*.json)"
        )
        if not path:
            return
        payload = json.loads(Path(path).read_text(encoding="utf-8"))
        self.path_edit.setText(payload.get("log_path", self.path_edit.text()))
        rate = payload.get("sample_rate", self.rate_box.currentText())
        index = self.rate_box.findText(rate)
        if index >= 0:
            self.rate_box.setCurrentIndex(index)
        self.hide_switch.setChecked(payload.get("hide_inactive", False))
        selections = payload.get("selected_measurements", {})
        for board_id, sensor_id in selections.items():
            if board_id in self.cards_by_id:
                self.selected_measurements[board_id] = sensor_id
                self.cards_by_id[board_id].set_selected_measurement(sensor_id)
        self._apply_comparison_state()
        self.refresh_chart()
