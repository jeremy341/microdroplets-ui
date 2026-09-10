"""Sensor selection, live plotting, comparison and CSV logging page.

The page consumes parsed ``BackendEvent`` objects exposed by ``app.py``/the
connected board model; serial parsing itself remains in the backend.  See
``docs/DEVELOPER_GUIDE.md`` and ``docs/DEVELOPER_GUIDE.md``.
"""

import json
import math
from dataclasses import dataclass
from datetime import datetime
from time import monotonic
from pathlib import Path

from PyQt6.QtCore import QPointF, QRectF, QSize, QTimer, QUrl, Qt, pyqtSignal
from PyQt6.QtGui import QColor, QDesktopServices, QFont, QIcon, QPainter, QPainterPath, QPen, QPixmap
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
from backend.application_paths import DEFAULT_SENSOR_LOG_PATH, SENSOR_LOGS_DIR
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


def format_sensor_value(value, precision):
    """Keep useful decimals without displaying a redundant trailing .00."""

    text = f"{float(value):,.{max(0, int(precision))}f}"
    if "." in text:
        text = text.rstrip("0").rstrip(".")
    return text

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

    # Live view shows only the newest 30 seconds, while the Sensors page keeps
    # a longer in-memory history that the user can inspect by pausing/panning.
    LIVE_WINDOW_SECONDS = 30.0

    def __init__(self):
        super().__init__()
        self.setObjectName("sensorChartCanvas")
        self.setSizePolicy(QSizePolicy.Policy.Expanding, QSizePolicy.Policy.Expanding)
        self.series = []
        self.timestamps = []
        self.uses_datetime_axis = False
        self._recording = False
        self._axis_labels = ("Value", "")
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
        self._plot_item = self._plot.getPlotItem()
        self._right_view = pg.ViewBox(enableMenu=False)
        self._right_view.setMouseEnabled(x=False, y=False)
        self._plot_item.showAxis("right")
        self._plot_item.scene().addItem(self._right_view)
        self._plot_item.getAxis("right").linkToView(self._right_view)
        self._right_view.setXLink(self._view_box)
        self._view_box.sigResized.connect(self._sync_right_view)
        self._plot_item.getAxis("right").setPen(pg.mkPen("#53606D", width=1.15))
        self._plot_item.getAxis("right").setTextPen(pg.mkPen("#25364A"))
        self._plot_item.getAxis("right").setWidth(48)
        self._plot_item.getAxis("right").setStyle(
            tickFont=QFont("Segoe UI", 8), tickTextOffset=2
        )
        self._plot_item.getAxis("right").setVisible(False)
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
        self._curve_axes = {}
        self._right_y_range = None

        layout = QVBoxLayout(self)
        layout.setContentsMargins(0, 0, 0, 0)
        layout.setSpacing(0)
        layout.addWidget(self._plot, 1)

    def _sync_right_view(self):
        self._right_view.setGeometry(self._view_box.sceneBoundingRect())
        self._right_view.linkedViewChanged(self._view_box, self._right_view.XAxis)

    def set_data(self, series, timestamps, axis_label=None):
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
                    or previous.get("color") != current.get("color")
                    or previous.get("axis", "left") != current.get("axis", "left")
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
                axis = self._curve_axes.pop(curve_id, "left")
                (self._right_view if axis == "right" else self._view_box).removeItem(curve)

        if data_changed:
            for item in self.series:
                axis = item.get("axis", "left")
                curve = self._curves.get(item["id"])
                if curve is not None and self._curve_axes.get(item["id"]) != axis:
                    previous_axis = self._curve_axes.get(item["id"], "left")
                    (self._right_view if previous_axis == "right" else self._view_box).removeItem(curve)
                    curve = None
                if curve is None:
                    curve = pg.PlotDataItem()
                    (self._right_view if axis == "right" else self._view_box).addItem(curve)
                    self._curves[item["id"]] = curve
                    self._curve_axes[item["id"]] = axis
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

        if axis_label is None:
            axis_label = self._axis_labels
        if isinstance(axis_label, (tuple, list)):
            left_label = axis_label[0] if axis_label else "Value"
            right_label = axis_label[1] if len(axis_label) > 1 else ""
        else:
            left_label, right_label = axis_label, ""
        self._axis_labels = (left_label, right_label)
        self._plot.setLabel("left", left_label or "", color="#33445A")
        self._plot.setLabel("right", right_label or "", color="#33445A")
        self._plot_item.getAxis("right").setVisible(bool(right_label))
        self._sync_right_view()
        if self.series:
            x_values = [
                timestamp.timestamp()
                for item in self.series
                for timestamp in item.get("timestamps", ())
            ]
            if data_changed and x_values:
                first, latest = min(x_values), max(x_values)
                if self._follow_live:
                    if latest - first < self.LIVE_WINDOW_SECONDS:
                        low = first
                        high = first + self.LIVE_WINDOW_SECONDS
                    else:
                        low = latest - self.LIVE_WINDOW_SECONDS
                        high = latest
                    self._plot.setXRange(low, high, padding=0)
            elif data_changed:
                max_count = max(len(item.get("values", ())) for item in self.series)
                self._plot.setXRange(0, max(max_count - 1, 2), padding=0)
            # Layout refreshes and accordion changes can call set_data() with
            # the exact same measurements. Do not treat a redraw as new data:
            # recalculating here made the animated Y range drift by a few units
            # even though no measurement had changed.
            if data_changed:
                self._update_stable_y_range()
                if not self._manual_scale:
                    right_values = self._finite_values(
                        visible_only=self._follow_live,
                        axis="right",
                    )
                    if right_values:
                        self._right_y_range = self._padded_range(
                            min(right_values), max(right_values)
                        )
                        self._right_view.setYRange(
                            *self._right_y_range, padding=0, update=True
                        )
        if data_changed:
            self._time_axis.picture = None
            self._time_axis.prepareGeometryChange()
            self._time_axis.update()

    def set_recording(self, recording):
        # Logging is a data-output state, not a chart-navigation action. It
        # must not disable live follow or change the visible time window.
        self._recording = bool(recording)

    @property
    def is_live_view(self):
        """Return True only while the chart is actively following new data."""
        return self._follow_live

    def pause_view(self):
        """Freeze the current viewport while sensor data keeps updating.

        Pause is intentionally a view-only action. It does not touch the
        serial stream, logging, histories, or curve data. By switching the
        chart into manual scaling without setting a new range, the exact
        current X/Y viewport is preserved. The user can still pan and zoom;
        Live view explicitly returns to automatic following afterwards.
        """
        self._follow_live = False
        self._manual_scale = True
        self._quiet_since = None
        self._target_y_range = None

    def resume_live(self):
        """Return to the latest samples after a manual pan or zoom."""
        self._follow_live = True
        self._manual_scale = False
        self._quiet_since = None
        if self.series:
            self.set_data(self.series, self.timestamps, self._axis_labels)
            x_values = [
                timestamp.timestamp()
                for item in self.series
                for timestamp in item.get("timestamps", ())
            ]
            if x_values:
                first, latest = min(x_values), max(x_values)
                low = (
                    first
                    if latest - first < self.LIVE_WINDOW_SECONDS
                    else latest - self.LIVE_WINDOW_SECONDS
                )
                high = max(latest, low + self.LIVE_WINDOW_SECONDS)
                self._plot.setXRange(low, high, padding=0)
            self._update_stable_y_range(force=True)
            right_values = self._finite_values(visible_only=True, axis="right")
            if right_values:
                self._right_y_range = self._padded_range(
                    min(right_values), max(right_values)
                )
                self._right_view.setYRange(
                    *self._right_y_range, padding=0, update=True
                )

    def fit_data(self):
        """Fit the complete buffered history for deliberate session review."""
        self._follow_live = False
        self._manual_scale = False
        self._quiet_since = None
        values = self._finite_values()
        if values:
            self._request_range(self._padded_range(min(values), max(values)), force=True)
        right_values = self._finite_values(axis="right")
        if right_values:
            self._right_y_range = self._padded_range(
                min(right_values), max(right_values)
            )
            self._right_view.setYRange(*self._right_y_range, padding=0, update=True)
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

    def _finite_values(self, visible_only=False, axis=None):
        values = []
        x_low, x_high = self._view_box.viewRange()[0]
        for item in self.series:
            if axis is not None and item.get("axis", "left") != axis:
                continue
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
        values = self._finite_values(
            visible_only=self._follow_live,
            axis="left",
        )
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

    def showEvent(self, event):
        super().showEvent(event)
        if not self._scale_timer.isActive():
            self._scale_timer.start()

    def hideEvent(self, event):
        # A hidden pyqtgraph widget does not need a 31 Hz scale animation.
        self._scale_timer.stop()
        super().hideEvent(event)

    def _apply_y_range(self, value_range):
        self._programmatic_range_change = True
        try:
            self._view_box.disableAutoRange(axis=pg.ViewBox.YAxis)
            self._view_box.setYRange(*value_range, padding=0, update=True)
        finally:
            self._programmatic_range_change = False


class SensorCard(QFrame):
    measurement_selected = pyqtSignal(str, str, bool)
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
        self._selected_sensor_ids = []
        self._comparison_sensor_id = None
        self._max_selected = 2
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
            # Hardware detection and chart selection are deliberately
            # independent. Every measurement remains selectable even before
            # its sensor has produced data; only the two-selection UI limit
            # may temporarily disable an unselected row.
            row_widget.setProperty("available", available)
            row_widget.setProperty("selectionLimited", False)
            row_widget.setEnabled(True)
            checkbox.setEnabled(True)
            self.sensor_rows.append((row_widget, sensor.get("active", True)))
            self.sensor_rows_by_id[sensor["sensor_id"]] = row_widget
            self.sensor_availability[sensor["sensor_id"]] = available
            self.selection_boxes[sensor["sensor_id"]] = checkbox
            if sensor.get("visible", False):
                self._selected_sensor_ids.append(sensor["sensor_id"])
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
        self.measurement_selected.emit(self.board_id, sensor_id, bool(checked))

    def set_selected_measurement(self, sensor_id):
        self.set_selected_measurements([sensor_id] if sensor_id else [])

    def set_selected_measurements(self, sensor_ids):
        self._selected_sensor_ids = list(dict.fromkeys(sensor_ids or ()))[
            : self._max_selected
        ]
        for key, checkbox in self.selection_boxes.items():
            checkbox.blockSignals(True)
            checkbox.setChecked(key in self._selected_sensor_ids)
            checkbox.blockSignals(False)
        self._refresh_row_states()

    def clear_selected_measurement(self):
        """Leave every measurement unchecked until the user chooses one."""
        self.set_selected_measurements([])

    def _refresh_row_states(self):
        at_limit = len(self._selected_sensor_ids) >= self._max_selected
        for sensor_id, checkbox in self.selection_boxes.items():
            selected = sensor_id in self._selected_sensor_ids
            if self._comparison_sensor_id is not None:
                allowed = sensor_id == self._comparison_sensor_id
            else:
                allowed = selected or not at_limit
            checkbox.setProperty(
                "comparison",
                bool(self._comparison_sensor_id is not None and selected),
            )
            checkbox.setEnabled(allowed)
            checkbox.update()
            row_widget = self.sensor_rows_by_id.get(sensor_id)
            if row_widget is not None:
                row_widget.setProperty("selectable", allowed)
                # Keep the row and its labels enabled. Disabling the parent
                # propagates Qt's disabled palette to the labels and can mute
                # selected text. Only the checkbox is disabled; this property
                # controls the grey label styling when the selection limit is
                # reached.
                row_widget.setProperty("selectionLimited", not allowed)
                row_widget.setEnabled(True)
                row_widget.style().unpolish(row_widget)
                row_widget.style().polish(row_widget)

    def set_max_selected(self, maximum):
        self._max_selected = max(1, int(maximum))
        self.set_selected_measurements(self._selected_sensor_ids)

    def set_comparison_state(self, enabled, sensor_id=None):
        self.setProperty("comparison", bool(enabled))
        self.comparison_badge.setVisible(False)
        if enabled and sensor_id:
            self._selected_sensor_ids = [sensor_id]
        self._comparison_sensor_id = sensor_id if enabled else None
        self.set_selected_measurements(self._selected_sensor_ids)
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

    def set_sensor_available(self, sensor_id, available):
        """Record detection without changing the user's chart selection."""
        available = bool(available)
        self.sensor_availability[sensor_id] = available
        row_widget = self.sensor_rows_by_id.get(sensor_id)
        if row_widget is not None:
            row_widget.setProperty("available", available)
            row_widget.style().unpolish(row_widget)
            row_widget.style().polish(row_widget)
        self._refresh_row_states()

    def update_values(self, values):
        for key, (label, unit, precision) in self.value_labels.items():
            if key in values:
                label.setText(
                    f"Current: {format_sensor_value(values[key], precision)} {unit}"
                )


class SensorsPage(QWidget):
    """Sensor monitoring page matching the approved desktop mockup."""

    # Keep ten minutes of graph history during normal live operation.
    # Pause/Fit Data suspend trimming so the data under a manual viewport
    # cannot disappear while the user is inspecting it.
    LIVE_HISTORY_SECONDS = 10 * 60

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
        self.selected_measurement_sets = {}
        self.explicit_measurement_selections = set()
        self.measurement_metadata = {}
        self.histories = {}
        self.history_timestamps = {}
        self.session_rows = []
        self.board_wait_started = {}
        self.board_last_sample_at = {}
        self.board_valid_sample_count = {}
        self._chart_dirty = True

        root = QVBoxLayout(self)
        root.setContentsMargins(PAGE_GUTTER, PAGE_TOP, PAGE_GUTTER, PAGE_BOTTOM)
        root.setSpacing(18)

        # The target uses a free-standing row, not one large bordered card.
        # Each control remains an independent widget so Qt can distribute the
        # available width without QSS fighting a fixed parent geometry.
        toolbar = QFrame()
        toolbar.setObjectName("sensorLoggingBar")
        toolbar.setSizePolicy(QSizePolicy.Policy.Expanding, QSizePolicy.Policy.Fixed)
        toolbar_layout = QHBoxLayout(toolbar)
        toolbar_layout.setContentsMargins(20, 14, 20, 14)
        toolbar_layout.setSpacing(24)

        log_configuration = QWidget()
        log_configuration.setObjectName("sensorLogConfiguration")
        configuration_layout = QHBoxLayout(log_configuration)
        configuration_layout.setContentsMargins(0, 0, 0, 0)
        configuration_layout.setSpacing(0)

        self.browse_button = QPushButton("Open File Explorer")
        self.browse_button.setObjectName("sensorBrowseButton")
        self.browse_button.setIcon(make_folder_icon())
        self.browse_button.setIconSize(QSize(18, 18))
        self.browse_button.setFixedSize(204, PRIMARY_CONTROL_HEIGHT)
        self.browse_button.clicked.connect(self.choose_log_path)

        default_log_path = DEFAULT_SENSOR_LOG_PATH
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
        self.start_button.setFixedSize(158, PRIMARY_CONTROL_HEIGHT)
        self.start_button.clicked.connect(self.start_logging)
        self.stop_button = QPushButton("Stop")
        self.stop_button.setObjectName("sensorStopButton")
        self.stop_button.setFixedSize(122, PRIMARY_CONTROL_HEIGHT)
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
        actions_layout.addSpacing(18)
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
        chart_layout.setContentsMargins(24, 18, 18, 18)
        chart_layout.setSpacing(14)

        chart_header = QHBoxLayout()
        chart_header.setContentsMargins(0, 0, 0, 0)
        self.chart_title = QLabel("Live sensor data")
        self.chart_title.setObjectName("sensorSectionTitle")
        chart_header.addWidget(self.chart_title)
        chart_header.addStretch()
        self.pause_button = QPushButton("Pause")
        self.pause_button.setObjectName("sensorPauseButton")
        self.pause_button.setToolTip(
            "Freeze the current chart view while sensor data continues in the background"
        )
        self.pause_button.clicked.connect(self.chart_pause)
        chart_header.addSpacing(12)
        chart_header.addWidget(self.pause_button)

        self.fit_button = QPushButton("Fit data")
        self.fit_button.setObjectName("sensorFitButton")
        self.fit_button.setToolTip("Show the complete buffered session")
        self.fit_button.clicked.connect(self.chart_fit_data)
        chart_header.addWidget(self.fit_button)

        self.auto_scale_button = QPushButton("Live view")
        self.auto_scale_button.setObjectName("sensorAutoScaleButton")
        self.auto_scale_button.setToolTip("Follow the newest samples and resume automatic scaling")
        self.auto_scale_button.clicked.connect(self.chart_auto_scale)
        chart_header.addWidget(self.auto_scale_button)

        # Selected measurements belong directly beneath the section title.
        # The row is hidden completely until the user selects a measurement.
        self.legend_widget = QWidget()
        self.legend_widget.setObjectName("sensorDynamicLegend")
        self.legend_layout = QHBoxLayout(self.legend_widget)
        self.legend_layout.setContentsMargins(0, 0, 0, 0)
        self.legend_layout.setSpacing(28)
        self.legend_widget.setVisible(False)

        self.chart = SensorChart()
        self.chart.set_data([], [])
        chart_layout.addLayout(chart_header)
        chart_layout.addWidget(self.legend_widget)
        chart_layout.addWidget(self.chart, 1)
        content.addWidget(chart_panel, 21)

        side = QWidget()
        side.setObjectName("sensorSidePanel")
        side_layout = QVBoxLayout(side)
        side_layout.setContentsMargins(0, 0, 0, 0)
        side_layout.setSpacing(16)

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
        filter_row.setContentsMargins(0, 0, 20, 0)
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
        item.setSizePolicy(QSizePolicy.Policy.Maximum, QSizePolicy.Policy.Fixed)
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

    def chart_pause(self):
        self.chart.pause_view()

    def chart_fit_data(self):
        self.chart.fit_data()

    def chart_auto_scale(self):
        # Returning to Live view also returns graph storage to the normal
        # rolling ten-minute window. Trim first, rebuild the curves, then let
        # the chart jump to the newest live window and resume auto-scaling.
        self._trim_all_histories_to_live_window()
        self.refresh_chart()
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

        # Connecting a board must never choose a graph measurement for the
        # user. The serial sensor loop continues independently in the backend.
        if board_id not in self.selected_measurement_sets:
            self.selected_measurement_sets[board_id] = []
            self.selected_measurements.pop(board_id, None)
        selected = set(self.selected_measurement_sets.get(board_id, ()))
        for row in rows:
            row["visible"] = row["sensor_id"] in selected
        return rows

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
            self.board_valid_sample_count[board_id] = 0
        for board_id in old_ids - set(new_ids):
            self.board_wait_started.pop(board_id, None)
            self.board_last_sample_at.pop(board_id, None)
            self.board_valid_sample_count.pop(board_id, None)
        self.explicit_measurement_selections.intersection_update(new_ids)
        self.selected_measurement_sets = {
            board_id: selections
            for board_id, selections in self.selected_measurement_sets.items()
            if board_id in new_ids
        }
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
        maximum_selections = 2 if len(connected) == 1 else 1
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
                # No board starts with a chart measurement selected. Sensor
                # acquisition continues regardless of this presentation state.
                if board_id in newly_added_ids:
                    self.selected_measurements.pop(board_id, None)
                    self.selected_measurement_sets[board_id] = []
                    self.explicit_measurement_selections.discard(board_id)
                    card.clear_selected_measurement()
            else:
                card.set_board_name(board.get("name", board_id))

            card.set_max_selected(maximum_selections)
            current_selections = self.selected_measurement_sets.get(board_id, [])
            if len(current_selections) > maximum_selections:
                current_selections = current_selections[:maximum_selections]
                self.selected_measurement_sets[board_id] = current_selections
                if current_selections:
                    self.selected_measurements[board_id] = current_selections[0]

            card.set_status(board.get("sensor_status", "Waiting for sensor data…"))

            card.set_hide_inactive(self.hide_inactive)
            card.set_expanded(board_id == self.expanded_board_id)
            selected_sensor = self.selected_measurements.get(board_id)
            selected_sensors = self.selected_measurement_sets.get(
                board_id,
                [selected_sensor] if selected_sensor else [],
            )
            if selected_sensors:
                card.set_selected_measurements(selected_sensors)
            elif board_id in newly_added_ids:
                card.clear_selected_measurement()
            card.update_values(self.current_values)

        self._reserve_card_list_height()
        self._apply_comparison_state()
        self._chart_dirty = True
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
        self._chart_dirty = True
        self.refresh_chart()

    def set_active_board(self, board):
        self.active_board = board
        board_id = self._board_id(board)
        if board_id in self.cards_by_id and board_id != self.expanded_board_id:
            self.expanded_board_id = board_id
            for card_id, card in self.cards_by_id.items():
                card.set_expanded(card_id == board_id)
            self._apply_comparison_state()
        self._chart_dirty = True
        self.refresh_chart()

    def set_primary_measurement(self, board_id, sensor_id):
        """Make *sensor_id* the primary selected measurement for one board.

        Workspace is a compact second view of the Sensors page and can display
        one signal at a time.  Keep that compact selection in the same shared
        SensorsPage selection model instead of maintaining a Workspace-only
        copy.  When the full page already has a second comparison signal, keep
        it where possible; only the primary slot is replaced.
        """
        if board_id not in self.cards_by_id:
            return
        if sensor_id not in MEASUREMENTS:
            return

        maximum_selections = 2 if len(self.connected_boards) == 1 else 1
        existing = [
            item
            for item in self.selected_measurement_sets.get(board_id, ())
            if item != sensor_id
        ]
        selections = [sensor_id] + existing[: max(0, maximum_selections - 1)]

        self.selected_measurement_sets[board_id] = selections
        self.selected_measurements[board_id] = sensor_id
        self.explicit_measurement_selections.add(board_id)
        self.cards_by_id[board_id].set_selected_measurements(selections)
        self._apply_comparison_state()
        self._chart_dirty = True
        self.refresh_chart()

    def set_measurement(self, board_id, sensor_id, checked=True):
        if board_id not in self.cards_by_id:
            return

        selections = list(self.selected_measurement_sets.get(board_id, ()))
        maximum_selections = 2 if len(self.connected_boards) == 1 else 1
        if checked:
            if sensor_id not in selections and len(selections) < maximum_selections:
                selections.append(sensor_id)
        else:
            selections = [item for item in selections if item != sensor_id]

        self.selected_measurement_sets[board_id] = selections
        if not selections:
            self.selected_measurements.pop(board_id, None)
            self.explicit_measurement_selections.discard(board_id)
        else:
            self.selected_measurements[board_id] = selections[0]
            self.explicit_measurement_selections.add(board_id)
        self.cards_by_id[board_id].set_selected_measurements(selections)
        self._apply_comparison_state()
        self._chart_dirty = True
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

    def _set_sensor_detected(self, board, board_id, sensor_id, detected):
        detected = bool(detected)
        for index, sensor in enumerate(board.get("sensors") or ()):
            if self._sensor_id(sensor, index) == sensor_id:
                sensor["active"] = detected
                sensor["available"] = detected
        metadata = self.measurement_metadata.get(f"{board_id}:{sensor_id}")
        if metadata is not None:
            metadata["active"] = detected
            metadata["available"] = detected
        card = self.cards_by_id.get(board_id)
        if card is not None:
            card.set_sensor_available(sensor_id, detected)

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
            self.selected_measurement_sets[comparison_ids[0]] = [primary_sensor]
            primary_card = self.cards_by_id.get(comparison_ids[0])
            if primary_card is not None:
                primary_card.set_selected_measurements([primary_sensor])
            self.selected_measurements[comparison_id] = primary_sensor
            self.selected_measurement_sets[comparison_id] = [primary_sensor]

        for board_id, card in self.cards_by_id.items():
            card.set_comparison_state(
                board_id == comparison_id,
                primary_sensor if board_id == comparison_id else None,
            )

    def update_sensor_values(self):
        """Drain sensor samples from the connected physical Multiboards."""

        self._update_logging_status()
        changed = False
        for board in self.connected_boards:
            board_id = self._board_id(board)
            connection = board.get("connection")

            if connection is None:
                continue
            now_monotonic = monotonic()
            for event in connection.drain_events():
                if event.kind == "state" and event.message:
                    # Expose the same connection progression as Bartels:
                    # identify/configure first, then wait for real samples.
                    board["connection_state"] = getattr(connection, "state", "unknown")
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
                elif event.kind == "sensor_retry":
                    board["sensor_status"] = "Sensor not detected — retrying…"
                    self.board_valid_sample_count[board_id] = 0
                    self._set_sensor_detected(board, board_id, "liquid_flow", False)
                elif event.kind == "diagnostic" and event.message:
                    board["sensor_status"] = "Sensor bus error — retrying…"
                    self.board_valid_sample_count[board_id] = 0
                    self._set_sensor_detected(board, board_id, "liquid_flow", False)
                sample = event.sample
                if sample is None:
                    continue
                valid_count = self.board_valid_sample_count.get(board_id, 0) + 1
                self.board_valid_sample_count[board_id] = valid_count
                detected = valid_count >= 2
                board["sensor_status"] = (
                    "Liquid-flow sensor connected"
                    if detected
                    else "Checking sensor — validating data…"
                )
                if detected:
                    self._set_sensor_detected(
                        board, board_id, sample.sensor_id, True
                    )
                self.board_last_sample_at[board_id] = now_monotonic
                self._append_sample(
                    board_id,
                    sample.sensor_id,
                    sample.value,
                    sample.timestamp.astimezone().replace(tzinfo=None),
                    sample.accumulated_volume_ul,
                    sample.raw_line,
                    sample.raw_value_ml_min,
                )
                changed = True

            last_sample_at = self.board_last_sample_at.get(board_id)
            if last_sample_at is not None and now_monotonic - last_sample_at >= 5.0:
                board["sensor_status"] = "Signal paused — reconnecting sensor stream…"

            card = self.cards_by_id.get(board_id)
            if card is not None:
                card.set_status(board.get("sensor_status"))

        page_visible = self.isVisible()
        if changed and page_visible:
            for board_id, card in self.cards_by_id.items():
                card.update_values(self.current_values)

        # Continue processing the backend on every tick, but keep the plot
        # static when no new measurement or chart-selection state exists.
        # The last received value therefore remains visible as a genuine
        # flatline, including negative values such as -2.
        if page_visible and (changed or self._chart_dirty):
            self._chart_dirty = False
            self.refresh_chart()

    def showEvent(self, event):
        super().showEvent(event)
        self.display_timer.setInterval(100)
        if self._chart_dirty:
            self._chart_dirty = False
            self.refresh_chart()

    def hideEvent(self, event):
        # Keep draining serial events for logging, but at a lower rate and
        # without rebuilding hidden cards/plots.
        self.display_timer.setInterval(250)
        super().hideEvent(event)

    def _append_sample(
        self,
        board_id,
        sensor_id,
        value,
        timestamp,
        accumulated_volume_ul,
        raw_line="",
        raw_value_ml_min=None,
    ):
        key = f"{board_id}:{sensor_id}"
        metadata = self.measurement_metadata.get(key)
        if metadata is None:
            return
        self.histories.setdefault(key, []).append(float(value))
        self.history_timestamps.setdefault(key, []).append(timestamp)
        if self.chart.is_live_view:
            self._trim_history_to_live_window(key)
        self.current_values[key] = float(value)
        self._chart_dirty = True
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
                "raw_value_ml_min": raw_value_ml_min,
            }
        )
        self._queue_log_sample(
            board_id,
            sensor_id,
            float(value),
            timestamp,
            accumulated_volume_ul,
            raw_line,
            raw_value_ml_min,
            metadata,
        )

    def _trim_history_to_live_window(self, key):
        """Keep only the newest LIVE_HISTORY_SECONDS for one plotted series.

        This is intentionally disabled while Pause or Fit Data is active.
        CSV/session logging is independent and is never trimmed here.
        """
        timestamps = self.history_timestamps.get(key, [])
        values = self.histories.get(key, [])
        if not timestamps or not values:
            return

        # Histories and timestamps are appended together. If a legacy or
        # malformed state ever makes their lengths differ, preserve matching
        # pairs from the newest end before applying the time window.
        if len(values) != len(timestamps):
            pair_count = min(len(values), len(timestamps))
            values[:] = values[-pair_count:]
            timestamps[:] = timestamps[-pair_count:]
            if not timestamps:
                return

        latest_seconds = timestamps[-1].timestamp()
        cutoff_seconds = latest_seconds - self.LIVE_HISTORY_SECONDS
        first_keep = 0
        for first_keep, sample_time in enumerate(timestamps):
            if sample_time.timestamp() >= cutoff_seconds:
                break
        else:
            first_keep = len(timestamps) - 1

        if first_keep > 0:
            del timestamps[:first_keep]
            del values[:first_keep]

    def _trim_all_histories_to_live_window(self):
        """Restore the normal rolling window when the user resumes Live view."""
        for key in tuple(self.histories):
            self._trim_history_to_live_window(key)

    def refresh_chart(self):
        comparison_ids = self._comparison_board_ids()
        display_board_id = self._display_board_id()
        if comparison_ids:
            board_ids = list(comparison_ids)
            sensor_ids = [self.selected_measurements.get(board_ids[0])]
        else:
            board_ids = [display_board_id] if display_board_id is not None else []
            sensor_ids = list(
                self.selected_measurement_sets.get(
                    display_board_id,
                    [self.selected_measurements.get(display_board_id)],
                )
            )
        sensor_ids = [sensor_id for sensor_id in sensor_ids if sensor_id]
        if not board_ids or not sensor_ids:
            self.chart.set_data([], [])
            self._set_legend([])
            return

        primary_id = board_ids[0]
        primary_metadata = self.measurement_metadata.get(
            f"{primary_id}:{sensor_ids[0]}"
        )
        if primary_metadata is None:
            self.chart.set_data([], [])
            self._set_legend([])
            return

        series = []
        legend = []
        comparison_colors = (BLUE, RED)
        if comparison_ids:
            sensor_id = sensor_ids[0]
            for index, board_id in enumerate(board_ids):
                series_key = f"{board_id}:{sensor_id}"
                metadata = self.measurement_metadata.get(series_key)
                if metadata is None or series_key not in self.histories:
                    continue
                board = next(
                    (
                        item
                        for item in self.connected_boards
                        if self._board_id(item) == board_id
                    ),
                    {},
                )
                name = board.get("name", board_id)
                color = comparison_colors[index]
                series.append(
                    self._chart_series(series_key, color, axis="left")
                )
                legend.append((color, f"{name} — {metadata['label']}"))
            axis_labels = (primary_metadata["axis_label"], "")
        else:
            board = next(
                (
                    item
                    for item in self.connected_boards
                    if self._board_id(item) == primary_id
                ),
                {},
            )
            name = board.get("name", primary_id)
            axis_labels_list = []
            for sensor_id in sensor_ids[:2]:
                series_key = f"{primary_id}:{sensor_id}"
                metadata = self.measurement_metadata.get(series_key)
                if metadata is None or series_key not in self.histories:
                    continue
                axis = "left" if not series else "right"
                color = metadata["color"]
                series.append(self._chart_series(series_key, color, axis=axis))
                legend.append((color, f"{name} — {metadata['label']}"))
                axis_labels_list.append(metadata["axis_label"])
            axis_labels = tuple(axis_labels_list)

        self.chart_title.setText("Live sensor data")
        self._set_legend(legend)
        self.chart.set_data(series, [], axis_labels)

    def _chart_series(self, series_key, color, *, axis):
        return {
            "id": series_key,
            # Pass snapshots to pyqtgraph. The history lists continue to grow
            # on every serial sample; sharing them would compare a list with itself.
            "values": list(self.histories[series_key]),
            "timestamps": list(self.history_timestamps.get(series_key, [])),
            "color": color,
            "axis": axis,
        }

    def _set_legend(self, entries):
        while self.legend_layout.count():
            item = self.legend_layout.takeAt(0)
            if item.widget():
                item.widget().deleteLater()
        for color, text in entries:
            self.legend_layout.addWidget(self._legend_item(color, text))
        self.legend_layout.addStretch(1)
        self.legend_widget.setVisible(bool(entries))

    def choose_log_path(self):
        # This button promises Explorer, so open the normal system folder
        # instead of showing Qt's Save As dialog. The filename remains directly
        # editable in the adjacent path field.
        candidate = Path(self.path_edit.text().strip()).expanduser()
        folder = candidate if candidate.is_dir() else candidate.parent
        while not folder.exists() and folder != folder.parent:
            folder = folder.parent
        if not folder.exists():
            folder = SENSOR_LOGS_DIR
        QDesktopServices.openUrl(QUrl.fromLocalFile(str(folder.resolve())))

    def _sample_interval_seconds(self):
        text = self.rate_box.currentText()
        return float(text.split()[0])

    def _logger_series_labels(self):
        """Return Bartels-style column labels for currently connected boards."""
        labels = []
        for board in self.connected_boards:
            board_id = self._board_id(board)
            if not board_id:
                continue
            for key, metadata in self.measurement_metadata.items():
                if not key.startswith(f"{board_id}:") or not metadata.get(
                    "available", False
                ):
                    continue
                labels.append(
                    f"{board_id} - {metadata['label'].replace(' ', '')}"
                )
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
            "CSV logging runs independently; every raw sample is also saved to *_raw.csv"
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
        raw_value_ml_min,
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
                raw_value_ml_min=raw_value_ml_min,
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

    def session_runtime_idle(self) -> bool:
        return not bool(self.logging)

    def export_session_state(self):
        return {
            "log_path": self.path_edit.text(),
            "sample_rate": self.rate_box.currentText(),
            "hide_inactive": bool(self.hide_switch.isChecked()),
            "selected_measurement_sets": {
                str(board_id): list(sensor_ids)
                for board_id, sensor_ids in self.selected_measurement_sets.items()
            },
        }

    def apply_session_state(self, state):
        """Restore presentation/logging configuration without starting logging."""
        if not isinstance(state, dict) or self.logging:
            return False
        self.path_edit.setText(str(state.get("log_path", self.path_edit.text())))
        rate = str(state.get("sample_rate", self.rate_box.currentText()))
        index = self.rate_box.findText(rate)
        if index >= 0:
            self.rate_box.setCurrentIndex(index)
        self.hide_switch.setChecked(bool(state.get("hide_inactive", False)))
        selections = state.get("selected_measurement_sets", {})
        if isinstance(selections, dict):
            self.selected_measurement_sets = {
                str(board_id): list(dict.fromkeys(sensor_ids or ()))[:2]
                for board_id, sensor_ids in selections.items()
                if isinstance(sensor_ids, (list, tuple))
            }
            self.selected_measurements = {
                board_id: sensor_ids[0]
                for board_id, sensor_ids in self.selected_measurement_sets.items()
                if sensor_ids
            }
            for board_id, card in self.cards_by_id.items():
                card.set_selected_measurements(
                    self.selected_measurement_sets.get(board_id, [])
                )
        self._apply_comparison_state()
        self._chart_dirty = True
        self.refresh_chart()
        return True

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
            "selected_measurement_sets": self.selected_measurement_sets,
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
        selections = payload.get("selected_measurement_sets")
        if not isinstance(selections, dict):
            selections = {
                board_id: [sensor_id]
                for board_id, sensor_id in payload.get(
                    "selected_measurements", {}
                ).items()
            }
        for board_id, sensor_ids in selections.items():
            if board_id in self.cards_by_id:
                selected = list(dict.fromkeys(sensor_ids or ()))[:2]
                self.selected_measurement_sets[board_id] = selected
                if selected:
                    self.selected_measurements[board_id] = selected[0]
                else:
                    self.selected_measurements.pop(board_id, None)
                self.cards_by_id[board_id].set_selected_measurements(selected)
        self._apply_comparison_state()
        self.refresh_chart()
