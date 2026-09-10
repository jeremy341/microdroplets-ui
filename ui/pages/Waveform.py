"""Saved-waveform editor, preview, compatibility UI and test execution page.

Wave definitions remain channel-independent. ``Test on`` selects only a runtime
target; hardware compatibility and generated effective steps are delegated to
``backend.waveform_engine``. Actual execution still uses the tested
``WaveformRunner`` through the board-level ``WaveExecutionService`` so Pumps and
Wave never grow separate automatic-wave implementations. See ``docs/DEVELOPER_GUIDE.md``.
"""

from __future__ import annotations

from dataclasses import replace
from threading import Thread

from PyQt6.QtCore import QAbstractTableModel, QModelIndex, QTimer, Qt
from PyQt6.QtGui import QColor, QStandardItem, QStandardItemModel
from PyQt6.QtWidgets import (
    QAbstractItemView,
    QComboBox,
    QDoubleSpinBox,
    QFrame,
    QGridLayout,
    QHeaderView,
    QHBoxLayout,
    QLabel,
    QLineEdit,
    QMessageBox,
    QPushButton,
    QSizePolicy,
    QSpinBox,
    QStackedWidget,
    QTableView,
    QVBoxLayout,
    QWidget,
)

from backend.application_paths import WAVEFORM_LIBRARY_PATH
from backend.waveform_engine import (
    DRIVER_FREQUENCY_HZ,
    MAX_STEP_DURATION_MS,
    MIN_STEP_DURATION_MS,
    WAVEFORM_SUPPORTED_CHANNELS,
    SUPPORTED_TEMPLATES,
    WaveStep,
    WaveformDefinition,
    check_compatibility,
    driver_for_channel,
    generate_steps,
    validate_definition,
    timing_samples_per_cycle,
    waveform_stats,
)
from backend.waveform_library import WaveformLibrary
from backend.protocol import DRIVER_FREQUENCY_LIMITS, PUMP_DRIVER_CHANNELS
from ui.design_tokens import (
    CARD_GAP,
    CARD_PADDING,
    CONTROL_HEIGHT,
    PAGE_BOTTOM,
    PAGE_GUTTER,
    PAGE_TOP,
    SECTION_GAP,
)

try:
    import pyqtgraph as pg
except ImportError:  # pragma: no cover - startup dependency guard
    pg = None


GREEN = "#13956B"


class WaveStepTableModel(QAbstractTableModel):
    HEADERS = ("Step", "Amplitude (Vpp)", "Duration (ms)")

    def __init__(self, parent=None):
        super().__init__(parent)
        self._steps: list[WaveStep] = []

    def set_steps(self, steps: list[WaveStep]) -> None:
        self.beginResetModel()
        self._steps = list(steps)
        self.endResetModel()

    def rowCount(self, parent=QModelIndex()):
        return 0 if parent.isValid() else len(self._steps)

    def columnCount(self, parent=QModelIndex()):
        return 0 if parent.isValid() else 3

    def data(self, index, role=Qt.ItemDataRole.DisplayRole):
        if not index.isValid() or not 0 <= index.row() < len(self._steps):
            return None
        step = self._steps[index.row()]
        if role == Qt.ItemDataRole.DisplayRole:
            if index.column() == 0:
                return str(index.row() + 1)
            if index.column() == 1:
                return str(step.amplitude_vpp)
            if index.column() == 2:
                return str(step.duration_ms)
        if role == Qt.ItemDataRole.TextAlignmentRole:
            return int(Qt.AlignmentFlag.AlignCenter)
        return None

    def headerData(self, section, orientation, role=Qt.ItemDataRole.DisplayRole):
        if role != Qt.ItemDataRole.DisplayRole:
            return None
        if orientation == Qt.Orientation.Horizontal and 0 <= section < len(self.HEADERS):
            return self.HEADERS[section]
        return None


class WaveformPreview(QWidget):
    """PyQtGraph preview that always displays the generated hardware steps."""

    def __init__(self, parent=None):
        super().__init__(parent)
        if pg is None:
            raise RuntimeError("pyqtgraph is required for the Wave page")

        layout = QVBoxLayout(self)
        layout.setContentsMargins(0, 0, 0, 0)
        layout.setSpacing(0)

        self.plot = pg.PlotWidget(background="#FFFFFF")
        self.plot.setObjectName("wavePreviewPlot")
        self.plot.setMenuEnabled(False)
        self.plot.hideButtons()
        self.plot.setMouseEnabled(x=False, y=False)
        self.plot.showGrid(x=True, y=True, alpha=0.16)
        self.plot.setLabel("left", "Amplitude (Vpp)")
        self.plot.setLabel("bottom", "Time (ms)")
        self.plot.getAxis("left").setTextPen(QColor("#4B5752"))
        self.plot.getAxis("bottom").setTextPen(QColor("#4B5752"))
        self.plot.getAxis("left").setPen(QColor("#9BA7A1"))
        self.plot.getAxis("bottom").setPen(QColor("#9BA7A1"))
        self.plot.getViewBox().setDefaultPadding(0.0)

        self.curve = self.plot.plot([], [], pen=pg.mkPen(GREEN, width=1.7), stepMode=True)
        layout.addWidget(self.plot)

    def clear_preview(self) -> None:
        self.curve.setData([], [])
        self.plot.setRange(xRange=(0, 1000), yRange=(0, 250), padding=0)

    def set_steps(self, steps: list[WaveStep], min_vpp: int, max_vpp: int) -> None:
        if not steps:
            self.clear_preview()
            return

        x_edges = [0.0]
        elapsed = 0.0
        y_values = []
        for step in steps:
            y_values.append(float(step.amplitude_vpp))
            elapsed += float(step.duration_ms)
            x_edges.append(elapsed)

        self.curve.setData(x_edges, y_values)

        span = max(1.0, float(max_vpp - min_vpp))
        y_padding = max(5.0, span * 0.08)
        y_low = max(0.0, float(min_vpp) - y_padding)
        y_high = min(250.0, float(max_vpp) + y_padding)
        if y_high - y_low < 10.0:
            center = (y_high + y_low) / 2.0
            y_low = max(0.0, center - 5.0)
            y_high = min(250.0, center + 5.0)

        x_high = max(1.0, elapsed)
        self.plot.setRange(
            xRange=(0.0, x_high),
            yRange=(y_low, y_high),
            padding=0.0,
            disableAutoRange=True,
        )


class WavePage(QWidget):
    # Pumps listens to this signal so newly saved/deleted waves appear there
    # immediately without either page owning a second library implementation.

    def __init__(self):
        super().__init__()

        self.setObjectName("wavePage")
        self.active_board = None
        self.library = WaveformLibrary(WAVEFORM_LIBRARY_PATH)
        self._current_saved_id: str | None = None
        self._loading = False
        self._dirty = False
        self._last_valid_steps: list[WaveStep] = []
        self._runtime_signature = None

        self._build_ui()
        self._connect_signals()
        self._refresh_library_combo()
        if self.library.items:
            self._load_definition(self.library.items[0])
        else:
            self._new_draft(initial=True)

        # Runtime ownership can change from the Pumps page or a worker thread.
        # Polling only this tiny backend snapshot keeps all Qt mutations on the
        # GUI thread and avoids cross-thread widget calls.
        self._runtime_timer = QTimer(self)
        self._runtime_timer.setInterval(100)
        self._runtime_timer.timeout.connect(self._sync_runtime_state)
        self._runtime_timer.start()

    # ------------------------------------------------------------------ UI
    def _build_ui(self):
        root = QVBoxLayout(self)
        root.setContentsMargins(PAGE_GUTTER, PAGE_TOP, PAGE_GUTTER, PAGE_BOTTOM)
        root.setSpacing(SECTION_GAP)

        # Saved-waveform library row -------------------------------------
        self.library_card = QFrame()
        self.library_card.setObjectName("waveLibraryCard")
        library_layout = QHBoxLayout(self.library_card)
        library_layout.setContentsMargins(CARD_PADDING, 12, CARD_PADDING, 12)
        library_layout.setSpacing(10)

        saved_label = QLabel("Saved waveform")
        saved_label.setObjectName("waveInlineLabel")
        self.saved_combo = QComboBox()
        self.saved_combo.setObjectName("waveSavedCombo")
        self.saved_combo.setFixedHeight(CONTROL_HEIGHT)
        self.saved_combo.setMinimumWidth(210)

        self.new_button = QPushButton("New")
        self.new_button.setObjectName("waveLibraryButton")
        self.new_button.setFixedHeight(CONTROL_HEIGHT)
        self.new_button.setMinimumWidth(82)

        self.save_button = QPushButton("Save Changes")
        self.save_button.setObjectName("waveLibraryButton")
        self.save_button.setFixedHeight(CONTROL_HEIGHT)
        self.save_button.setMinimumWidth(126)

        self.delete_button = QPushButton("Delete")
        self.delete_button.setObjectName("waveDangerButton")
        self.delete_button.setFixedHeight(CONTROL_HEIGHT)
        self.delete_button.setMinimumWidth(86)

        library_layout.addWidget(saved_label)
        library_layout.addWidget(self.saved_combo)
        library_layout.addSpacing(8)
        self.compatibility_label = QLabel()
        self.compatibility_label.setObjectName("waveCompatibilityBadge")
        self.compatibility_label.setWordWrap(True)
        self.compatibility_label.setMinimumHeight(48)
        self.compatibility_label.setMinimumWidth(430)

        library_layout.addWidget(self.new_button)
        library_layout.addWidget(self.save_button)
        library_layout.addWidget(self.delete_button)
        library_layout.addSpacing(14)
        library_layout.addWidget(self.compatibility_label, 1)
        root.addWidget(self.library_card)

        # Main split ------------------------------------------------------
        main_row = QHBoxLayout()
        main_row.setContentsMargins(0, 0, 0, 0)
        main_row.setSpacing(CARD_GAP)

        self.editor_card = QFrame()
        self.editor_card.setObjectName("waveCard")
        self.editor_card.setMinimumWidth(410)
        self.editor_card.setMaximumWidth(560)
        self.editor_card.setSizePolicy(QSizePolicy.Policy.Preferred, QSizePolicy.Policy.Expanding)
        editor_layout = QVBoxLayout(self.editor_card)
        editor_layout.setContentsMargins(CARD_PADDING, CARD_PADDING, CARD_PADDING, CARD_PADDING)
        editor_layout.setSpacing(12)

        editor_title = QLabel("Wave Editor")
        editor_title.setObjectName("waveSectionTitle")
        editor_layout.addWidget(editor_title)

        form = QGridLayout()
        form.setContentsMargins(0, 4, 0, 0)
        form.setHorizontalSpacing(14)
        form.setVerticalSpacing(10)
        form.setColumnStretch(0, 0)
        form.setColumnStretch(1, 1)
        form.setColumnStretch(2, 0)
        form.setColumnStretch(3, 1)

        self.name_edit = QLineEdit()
        self.name_edit.setObjectName("waveInput")
        self.name_edit.setMaxLength(50)
        self.name_edit.setFixedHeight(CONTROL_HEIGHT)
        self._add_full_field(form, 0, "Waveform name", self.name_edit)

        self.template_combo = QComboBox()
        self.template_combo.setObjectName("waveCombo")
        self.template_combo.addItems(SUPPORTED_TEMPLATES)
        self.template_combo.setFixedHeight(CONTROL_HEIGHT)

        self.channel_combo = QComboBox()
        self.channel_combo.setObjectName("waveCombo")
        channel_model = QStandardItemModel(self.channel_combo)
        for channel in range(1, 7):
            item = QStandardItem(f"CH{channel}")
            item.setData(channel, Qt.ItemDataRole.UserRole)
            if channel not in WAVEFORM_SUPPORTED_CHANNELS:
                item.setEnabled(False)
            channel_model.appendRow(item)
        self.channel_combo.setModel(channel_model)
        for index in range(self.channel_combo.count()):
            if self.channel_combo.itemData(index) == 2:
                self.channel_combo.setCurrentIndex(index)
                break
        self.channel_combo.setFixedHeight(CONTROL_HEIGHT)

        form.addWidget(self._field_label("Create from"), 1, 0)
        form.addWidget(self.template_combo, 1, 1)
        form.addWidget(self._field_label("Test on"), 1, 2)
        form.addWidget(self.channel_combo, 1, 3)

        self.min_spin = self._spin(0, 250, 80, "Vpp")
        self.max_spin = self._spin(0, 250, 180, "Vpp")
        self._add_full_field(form, 2, "Min amplitude", self.min_spin)
        self._add_full_field(form, 3, "Max amplitude", self.max_spin)

        self.dynamic_stack = QStackedWidget()
        self.dynamic_stack.setObjectName("waveDynamicStack")
        self.dynamic_stack.setFixedHeight(CONTROL_HEIGHT)

        self.increment_spin = self._spin(1, 250, 10, "Vpp")
        self.increment_spin.setToolTip(
            "CH1–CH4 are quantized to the real mp-Highdriver4 5-bit amplitude levels. "
            "Generated Steps and the preview show the effective commands."
        )
        self.samples_spin = self._spin(1, 30000, 20, "")
        self.samples_spin.setReadOnly(True)
        self.samples_spin.setButtonSymbols(QSpinBox.ButtonSymbols.NoButtons)
        self.samples_spin.setToolTip(
            "Calculated automatically from Cycle duration and Step duration."
        )
        self.square_placeholder = QLineEdit("Not required")
        self.square_placeholder.setObjectName("waveReadOnlyInput")
        self.square_placeholder.setReadOnly(True)
        self.square_placeholder.setFixedHeight(CONTROL_HEIGHT)
        self.dynamic_stack.addWidget(self.increment_spin)
        self.dynamic_stack.addWidget(self.samples_spin)
        self.dynamic_stack.addWidget(self.square_placeholder)
        self.dynamic_label = self._field_label("Increment")
        form.addWidget(self.dynamic_label, 4, 0)
        form.addWidget(self.dynamic_stack, 4, 1, 1, 3)

        self.duration_spin = self._spin(
            MIN_STEP_DURATION_MS, MAX_STEP_DURATION_MS, 100, "ms"
        )
        self.duration_spin.setSingleStep(10)
        self.duration_spin.setToolTip(
            f"Wave MVP safety range: {MIN_STEP_DURATION_MS}–{MAX_STEP_DURATION_MS} ms."
        )
        self.cycles_spin = self._spin(1, 1000, 5, "")
        self._add_full_field(form, 5, "Step duration", self.duration_spin)
        self._add_full_field(form, 6, "Cycles", self.cycles_spin)

        self.frequency_edit = QSpinBox()
        self.frequency_edit.setObjectName("waveSpin")
        self.frequency_edit.setRange(8, 800)
        self.frequency_edit.setValue(DRIVER_FREQUENCY_HZ)
        self.frequency_edit.setSuffix("  Hz")
        self.frequency_edit.setFixedHeight(CONTROL_HEIGHT)
        self.frequency_edit.setToolTip(
            "Initial driver frequency. While this channel's Wave is running, changes are applied live."
        )
        self._add_full_field(form, 7, "Driver frequency", self.frequency_edit)

        self.cycle_duration_spin = QDoubleSpinBox()
        self.cycle_duration_spin.setObjectName("waveSpin")
        self.cycle_duration_spin.setRange(0.1, 600.0)
        self.cycle_duration_spin.setDecimals(1)
        self.cycle_duration_spin.setSingleStep(0.1)
        self.cycle_duration_spin.setFixedHeight(CONTROL_HEIGHT)
        self.cycle_duration_spin.setSuffix("  s")
        self._add_full_field(form, 8, "Cycle duration", self.cycle_duration_spin)

        editor_layout.addLayout(form)
        editor_layout.addStretch(1)

        divider = QFrame()
        divider.setObjectName("waveDivider")
        divider.setFrameShape(QFrame.Shape.HLine)
        editor_layout.addWidget(divider)

        action_layout = QHBoxLayout()
        action_layout.setContentsMargins(0, 0, 0, 0)
        action_layout.setSpacing(12)
        self.test_button = QPushButton("▶  Test Waveform")
        self.test_button.setObjectName("wavePrimaryButton")
        self.test_button.setMinimumHeight(46)
        self.stop_button = QPushButton("■  Stop")
        self.stop_button.setObjectName("waveStopButton")
        self.stop_button.setMinimumHeight(46)
        self.stop_button.setEnabled(False)
        action_layout.addWidget(self.test_button, 1)
        action_layout.addWidget(self.stop_button, 1)
        editor_layout.addLayout(action_layout)

        # Right workspace -------------------------------------------------
        right_column = QVBoxLayout()
        right_column.setContentsMargins(0, 0, 0, 0)
        right_column.setSpacing(CARD_GAP)

        self.preview_card = QFrame()
        self.preview_card.setObjectName("waveCard")
        preview_layout = QVBoxLayout(self.preview_card)
        preview_layout.setContentsMargins(CARD_PADDING, CARD_PADDING, CARD_PADDING, 12)
        preview_layout.setSpacing(8)

        preview_header = QHBoxLayout()
        preview_title = QLabel("Waveform Preview")
        preview_title.setObjectName("waveSectionTitle")
        self.preview_context = QLabel("CH2 — Untitled Waveform")
        self.preview_context.setObjectName("wavePreviewContext")
        preview_header.addWidget(preview_title)
        preview_header.addStretch(1)
        preview_header.addWidget(self.preview_context)
        preview_layout.addLayout(preview_header)

        self.preview = WaveformPreview()
        self.preview.setMinimumHeight(245)
        preview_layout.addWidget(self.preview, 1)
        right_column.addWidget(self.preview_card, 11)

        self.steps_card = QFrame()
        self.steps_card.setObjectName("waveCard")
        steps_layout = QVBoxLayout(self.steps_card)
        steps_layout.setContentsMargins(CARD_PADDING, 12, CARD_PADDING, 10)
        steps_layout.setSpacing(7)

        steps_title = QLabel("Generated Steps")
        steps_title.setObjectName("waveSectionTitle")
        steps_layout.addWidget(steps_title)

        self.steps_model = WaveStepTableModel(self)
        self.steps_table = QTableView()
        self.steps_table.setObjectName("waveStepsTable")
        self.steps_table.setModel(self.steps_model)
        self.steps_table.setSelectionMode(QAbstractItemView.SelectionMode.NoSelection)
        self.steps_table.setEditTriggers(QAbstractItemView.EditTrigger.NoEditTriggers)
        self.steps_table.setFocusPolicy(Qt.FocusPolicy.NoFocus)
        self.steps_table.verticalHeader().setVisible(False)
        self.steps_table.horizontalHeader().setStretchLastSection(True)
        self.steps_table.horizontalHeader().setDefaultAlignment(Qt.AlignmentFlag.AlignCenter)
        self.steps_table.setAlternatingRowColors(False)
        self.steps_table.setMinimumHeight(155)
        for column in range(3):
            self.steps_table.horizontalHeader().setSectionResizeMode(
                column,
                QHeaderView.ResizeMode.Stretch,
            )
        steps_layout.addWidget(self.steps_table, 1)

        note = QLabel("ⓘ  These steps are the actual commands that will be sent to the driver.")
        note.setObjectName("waveTableNote")
        steps_layout.addWidget(note)

        stats_layout = QHBoxLayout()
        stats_layout.setContentsMargins(0, 0, 0, 0)
        stats_layout.setSpacing(0)
        self.stats = {}
        for key, title in (
            ("steps_per_cycle", "Steps per cycle"),
            ("total_steps", "Total steps"),
            ("cycle_duration", "Cycle duration"),
            ("total_duration", "Total duration"),
            ("wave_frequency", "Wave frequency"),
        ):
            block = QWidget()
            block_layout = QVBoxLayout(block)
            block_layout.setContentsMargins(8, 3, 8, 0)
            block_layout.setSpacing(3)
            label = QLabel(title)
            label.setObjectName("waveStatLabel")
            label.setAlignment(Qt.AlignmentFlag.AlignCenter)
            value = QLabel("—")
            value.setObjectName("waveStatValue")
            value.setAlignment(Qt.AlignmentFlag.AlignCenter)
            block_layout.addWidget(label)
            block_layout.addWidget(value)
            stats_layout.addWidget(block, 1)
            self.stats[key] = value
        steps_layout.addLayout(stats_layout)
        right_column.addWidget(self.steps_card, 9)

        main_row.addWidget(self.editor_card, 7)
        main_row.addLayout(right_column, 10)
        root.addLayout(main_row, 1)

    def _field_label(self, text: str) -> QLabel:
        label = QLabel(text)
        label.setObjectName("waveFieldLabel")
        return label

    def _spin(self, minimum: int, maximum: int, value: int, suffix: str) -> QSpinBox:
        spin = QSpinBox()
        spin.setObjectName("waveSpin")
        spin.setRange(minimum, maximum)
        spin.setValue(value)
        spin.setFixedHeight(CONTROL_HEIGHT)
        spin.setAccelerated(True)
        if suffix:
            spin.setSuffix(f"  {suffix}")
        return spin

    def _add_full_field(self, layout: QGridLayout, row: int, label: str, control: QWidget):
        layout.addWidget(self._field_label(label), row, 0)
        layout.addWidget(control, row, 1, 1, 3)

    def _connect_signals(self):
        self.saved_combo.currentIndexChanged.connect(self._on_saved_selected)
        self.new_button.clicked.connect(self._on_new)
        self.save_button.clicked.connect(self._on_save)
        self.delete_button.clicked.connect(self._on_delete)
        self.test_button.clicked.connect(self._on_test)
        self.stop_button.clicked.connect(self._on_stop)

        self.template_combo.currentTextChanged.connect(self._on_template_changed)
        self.channel_combo.currentTextChanged.connect(self._editor_changed)
        self.name_edit.textChanged.connect(self._editor_changed)
        for spin in (
            self.min_spin,
            self.max_spin,
            self.increment_spin,
            self.duration_spin,
            self.cycles_spin,
            self.frequency_edit,
        ):
            spin.valueChanged.connect(self._editor_changed)
        self.cycle_duration_spin.valueChanged.connect(self._editor_changed)
        self.frequency_edit.editingFinished.connect(self._apply_live_frequency_if_running)

    # ----------------------------------------------------------- page state
    def set_active_board(self, board):
        # Selecting another board must not silently stop a physical waveform.
        # Runtime execution belongs to the board-level WaveExecutionService.
        self.active_board = board
        self._runtime_signature = None
        self.refresh_preview(mark_dirty=False)

    def set_connected_boards(self, boards):
        # Kept for the common page API; the editor targets the active board.
        pass

    def shutdown(self):
        # app.py stops every board-level WaveExecutionService before closing its
        # serial connection. The page itself owns no hardware thread anymore.
        self._runtime_timer.stop()

    def _wave_service(self):
        return self.active_board.get("wave_execution") if self.active_board else None

    def _ownership_manager(self):
        return self.active_board.get("channel_ownership") if self.active_board else None

    def _current_ownership(self):
        manager = self._ownership_manager()
        return manager.get(self._current_channel()) if manager is not None else None

    def _selected_channel_wave_running(self) -> bool:
        owner = self._current_ownership()
        return bool(owner is not None and owner.kind == "waveform")

    def _sync_runtime_state(self):
        owner = self._current_ownership()
        service = self._wave_service()
        state = service.state(self._current_channel()) if service is not None else None
        manager = self._ownership_manager()
        driver_index = driver_for_channel(self._current_channel())
        group_owners = tuple(
            (channel, getattr(manager.get(channel), "kind", None), getattr(manager.get(channel), "token", None))
            for channel in PUMP_DRIVER_CHANNELS[driver_index]
        ) if manager is not None else ()
        pump_service = self.active_board.get("pump_control") if self.active_board else None
        runtime_frequency = (
            pump_service.get_driver_frequency(driver_index) if pump_service is not None else None
        )
        if (
            owner is not None
            and owner.kind == "waveform"
            and runtime_frequency is not None
            and not self.frequency_edit.hasFocus()
            and self.frequency_edit.value() != runtime_frequency
        ):
            blocked = self.frequency_edit.blockSignals(True)
            self.frequency_edit.setValue(runtime_frequency)
            self.frequency_edit.blockSignals(blocked)
        signature = (
            getattr(owner, "kind", None),
            getattr(owner, "token", None),
            getattr(state, "status", None),
            getattr(state, "message", None),
            group_owners,
            runtime_frequency if owner is not None and owner.kind == "waveform" else None,
        )
        if signature != self._runtime_signature:
            self._runtime_signature = signature
            self.refresh_preview(mark_dirty=False)

    def _current_channel(self) -> int:
        channel = self.channel_combo.currentData()
        return int(channel) if channel is not None else 2

    def _definition_from_editor(self) -> WaveformDefinition:
        return WaveformDefinition(
            id=self._current_saved_id or "draft",
            name=self.name_edit.text().strip(),
            template=self.template_combo.currentText(),
            min_vpp=self.min_spin.value(),
            max_vpp=self.max_spin.value(),
            increment_vpp=self.increment_spin.value(),
            samples_per_cycle=self.samples_spin.value(),
            step_duration_ms=self.duration_spin.value(),
            cycle_duration_ms=int(round(self.cycle_duration_spin.value() * 1000.0)),
            cycles=self.cycles_spin.value(),
            driver_frequency_hz=self.frequency_edit.value(),
        )

    def _on_template_changed(self, template: str):
        if template == "Sine":
            self.dynamic_label.setText("Samples per cycle")
            self.dynamic_stack.setCurrentWidget(self.samples_spin)
            self.samples_spin.setEnabled(False)
        elif template == "Square":
            self.dynamic_label.setText("Increment")
            self.dynamic_stack.setCurrentWidget(self.square_placeholder)
        else:
            self.dynamic_label.setText("Increment")
            self.dynamic_stack.setCurrentWidget(self.increment_spin)
        self._editor_changed()

    def _sync_samples_per_cycle_display(self) -> None:
        definition = self._definition_from_editor()
        samples = timing_samples_per_cycle(definition)
        blocked = self.samples_spin.blockSignals(True)
        self.samples_spin.setValue(max(1, min(self.samples_spin.maximum(), samples)))
        self.samples_spin.blockSignals(blocked)

    def _editor_changed(self, *args):
        if self._loading:
            return
        self._dirty = True
        self._sync_samples_per_cycle_display()
        self.refresh_preview(mark_dirty=False)

    def _sync_frequency_control_state(self) -> None:
        channel = self._current_channel()
        driver_index = driver_for_channel(channel)
        self.frequency_edit.setEnabled(True)
        if self._selected_channel_wave_running():
            self.frequency_edit.setToolTip(
                f"Live F{driver_index} frequency for CH{channel}. Changing it does not restart the Wave."
            )
        else:
            self.frequency_edit.setToolTip(
                f"Initial F{driver_index} frequency used when this Wave starts on CH{channel}."
            )

    def _apply_live_frequency_if_running(self) -> None:
        """Apply Wave-page frequency edits live without blocking Qt's GUI thread."""

        if not self._selected_channel_wave_running() or self.active_board is None:
            return
        service = self.active_board.get("pump_control")
        if service is None:
            return
        channel = self._current_channel()
        driver_index = driver_for_channel(channel)
        value = int(self.frequency_edit.value())

        def apply_frequency():
            try:
                service.set_driver_frequency(driver_index, value)
            except Exception:
                # The 75 ms runtime poll restores the last acknowledged central
                # value after focus leaves the editor if this transaction fails.
                pass

        Thread(
            target=apply_frequency,
            name=f"WaveFrequency-F{driver_index}",
            daemon=True,
        ).start()

    def refresh_preview(self, mark_dirty=False):
        if mark_dirty:
            self._dirty = True
        self._sync_samples_per_cycle_display()
        definition = self._definition_from_editor()
        channel = self._current_channel()
        self.preview_context.setText(f"CH{channel} — {definition.name or 'Untitled Waveform'}")

        valid, reason = validate_definition(definition)
        if valid:
            try:
                steps = generate_steps(definition, channel=channel)
                stats = waveform_stats(definition, channel=channel)
            except ValueError as exc:
                valid = False
                reason = str(exc)
                steps = []
                stats = None
        else:
            steps = []
            stats = None

        if valid:
            self._last_valid_steps = steps
            effective_min = min(step.amplitude_vpp for step in steps)
            effective_max = max(step.amplitude_vpp for step in steps)
            self.preview.set_steps(steps, effective_min, effective_max)
            self.steps_model.set_steps(steps)
            self.stats["steps_per_cycle"].setText(str(stats.steps_per_cycle))
            self.stats["total_steps"].setText(str(stats.total_steps))
            self.stats["cycle_duration"].setText(self._format_duration(stats.cycle_duration_ms))
            self.stats["total_duration"].setText(self._format_duration(stats.total_duration_ms))
            self.stats["wave_frequency"].setText(f"{stats.wave_frequency_hz:.2f} Hz")
        else:
            self._last_valid_steps = []
            self.preview.clear_preview()
            self.steps_model.set_steps([])
            for value in self.stats.values():
                value.setText("—")

        compatibility = check_compatibility(definition, channel)
        connection = self.active_board.get("connection") if self.active_board else None
        board_ready = bool(connection is not None and getattr(connection, "is_open", True))
        owner = self._current_ownership()
        manual_busy = bool(owner is not None and owner.kind == "manual")
        wave_busy = bool(owner is not None and owner.kind == "waveform")
        service = self._wave_service()
        wave_state = service.state(channel) if service is not None else None

        if not valid:
            self._set_compatibility(reason, "invalid")
        elif not compatibility.valid:
            self._set_compatibility(compatibility.reason, "invalid")
        elif manual_busy:
            self._set_compatibility(
                f"CH{channel} is currently in use by manual pump control. "
                "Turn the pump off before starting a waveform.",
                "invalid",
            )
        elif wave_busy:
            label = owner.label or (wave_state.waveform_name if wave_state else "waveform")
            suffix = f" · {wave_state.message}" if wave_state and wave_state.message else ""
            self._set_compatibility(
                f'CH{channel} is controlled by waveform "{label}"{suffix}',
                "invalid",
            )
        elif not board_ready:
            self._set_compatibility(
                f"{compatibility.reason} · Connect/select a board to test",
                "neutral",
            )
        else:
            self._set_compatibility(compatibility.reason, "valid")

        controls_unlocked = not wave_busy
        can_save = valid and controls_unlocked
        can_test = (
            valid and compatibility.valid and board_ready and controls_unlocked
            and not manual_busy
        )
        self._set_running_state(wave_busy)
        self._sync_frequency_control_state()
        self.save_button.setEnabled(can_save)
        self.test_button.setEnabled(can_test)
        self.test_button.setText("Pump in use" if manual_busy else "▶  Test Waveform")
        self.test_button.setProperty("blockedByPump", manual_busy)
        self.test_button.style().unpolish(self.test_button)
        self.test_button.style().polish(self.test_button)
        running_ids = {
            state.waveform_id for state in (service.snapshot().values() if service else ())
            if state.status in {"starting", "running", "error"}
        }
        self.delete_button.setEnabled(
            self._current_saved_id is not None
            and controls_unlocked
            and self._current_saved_id not in running_ids
        )

    @staticmethod
    def _format_duration(milliseconds: int) -> str:
        if milliseconds >= 1000:
            seconds = milliseconds / 1000.0
            return f"{seconds:.1f} s"
        return f"{milliseconds} ms"

    def _set_compatibility(self, text: str, state: str):
        icon = "✓" if state == "valid" else ("!" if state == "invalid" else "ⓘ")
        self.compatibility_label.setText(f"{icon}  {text}")
        self.compatibility_label.setProperty("state", state)
        self.compatibility_label.style().unpolish(self.compatibility_label)
        self.compatibility_label.style().polish(self.compatibility_label)

    # ----------------------------------------------------------- library UI
    def _refresh_library_combo(self, select_id: str | None = None):
        self._loading = True
        self.saved_combo.clear()
        items = self.library.items
        target = select_id or self._current_saved_id
        if not items:
            self.saved_combo.addItem("No saved waveforms", None)
            self.saved_combo.setEnabled(False)
        else:
            self.saved_combo.setEnabled(not self._selected_channel_wave_running())
            if target is None:
                self.saved_combo.addItem("New waveform (unsaved)", None)
            for item in items:
                self.saved_combo.addItem(item.name, item.id)
            if target is not None:
                for index in range(self.saved_combo.count()):
                    if self.saved_combo.itemData(index) == target:
                        self.saved_combo.setCurrentIndex(index)
                        break
        self._loading = False

    def _confirm_discard(self) -> bool:
        if not self._dirty:
            return True
        answer = QMessageBox.question(
            self,
            "Unsaved waveform changes",
            "Discard the current unsaved changes?",
            QMessageBox.StandardButton.Discard | QMessageBox.StandardButton.Cancel,
            QMessageBox.StandardButton.Cancel,
        )
        return answer == QMessageBox.StandardButton.Discard

    def _on_saved_selected(self, index: int):
        if self._loading or index < 0:
            return
        selected_id = self.saved_combo.itemData(index)
        if selected_id is None or selected_id == self._current_saved_id:
            return
        if not self._confirm_discard():
            self._refresh_library_combo(self._current_saved_id)
            return
        definition = self.library.get(selected_id)
        if definition is not None:
            self._load_definition(definition)

    def _on_new(self):
        if not self._confirm_discard():
            return
        self._new_draft()

    def _new_draft(self, initial=False):
        definition = self.library.new_definition()
        self._current_saved_id = None
        self._load_definition(definition, saved=False)
        if not initial:
            self.name_edit.setFocus()
            self.name_edit.selectAll()

    def _load_definition(self, definition: WaveformDefinition, saved=True):
        self._loading = True
        self._current_saved_id = definition.id if saved else None
        self.name_edit.setText(definition.name)
        self.template_combo.setCurrentText(definition.template)
        self.min_spin.setValue(definition.min_vpp)
        self.max_spin.setValue(definition.max_vpp)
        self.increment_spin.setValue(definition.increment_vpp)
        self.samples_spin.setValue(definition.samples_per_cycle)
        self.duration_spin.setValue(definition.step_duration_ms)
        self.cycle_duration_spin.setValue(definition.cycle_duration_ms / 1000.0)
        self.cycles_spin.setValue(definition.cycles)
        self.frequency_edit.setValue(definition.driver_frequency_hz)
        self._loading = False
        self._dirty = False
        self._on_template_changed(definition.template)
        self._dirty = False
        if saved:
            self._refresh_library_combo(definition.id)
        else:
            self._refresh_library_combo(None)
        self.refresh_preview(mark_dirty=False)

    def _on_save(self):
        definition = self._definition_from_editor()
        valid, reason = validate_definition(definition)
        if not valid:
            QMessageBox.warning(self, "Cannot save waveform", reason)
            return
        if self._current_saved_id is None:
            definition = replace(definition, id=self.library.new_definition().id)
        try:
            saved = self.library.save(definition)
        except ValueError as exc:
            QMessageBox.warning(self, "Cannot save waveform", str(exc))
            return
        self._current_saved_id = saved.id
        self._dirty = False
        self._refresh_library_combo(saved.id)
        self.refresh_preview(mark_dirty=False)

    def _on_delete(self):
        if self._current_saved_id is None or self._selected_channel_wave_running():
            return
        definition = self.library.get(self._current_saved_id)
        if definition is None:
            return
        answer = QMessageBox.question(
            self,
            "Delete waveform",
            f'Delete "{definition.name}"?',
            QMessageBox.StandardButton.Yes | QMessageBox.StandardButton.Cancel,
            QMessageBox.StandardButton.Cancel,
        )
        if answer != QMessageBox.StandardButton.Yes:
            return
        self.library.delete(definition.id)
        self._current_saved_id = None
        self._refresh_library_combo()
        self._new_draft()

    def session_runtime_idle(self) -> bool:
        manager = self._ownership_manager()
        return manager is None or manager.all_free

    def export_session_state(self):
        return {
            "editor_waveform_id": self._current_saved_id,
            "test_channel": self._current_channel(),
        }

    def apply_session_state(self, state):
        if not isinstance(state, dict) or not self.session_runtime_idle():
            return (False, ())
        warnings = []
        channel = state.get("test_channel")
        if isinstance(channel, int):
            index = self.channel_combo.findData(channel)
            if index >= 0:
                self.channel_combo.setCurrentIndex(index)
        waveform_id = state.get("editor_waveform_id")
        if waveform_id:
            self.library.load()
            definition = self.library.get(str(waveform_id))
            if definition is not None:
                self._load_definition(definition)
            else:
                warnings.append("The waveform previously open in the Wave editor no longer exists.")
        self.refresh_preview(mark_dirty=False)
        return (True, tuple(warnings))

    # ------------------------------------------------------------ execution
    @staticmethod
    def _driver_data_for_channel_on_board(board, channel: int):
        if board is None:
            return None
        for driver in board.get("drivers", []):
            if any(item.get("channel") == channel for item in driver.get("channels", [])):
                return driver
        return None

    def _driver_data_for_channel(self, channel: int):
        return self._driver_data_for_channel_on_board(self.active_board, channel)

    def _on_test(self):
        definition = self._definition_from_editor()
        channel = self._current_channel()
        compatibility = check_compatibility(definition, channel)
        if not compatibility.valid:
            self.refresh_preview(mark_dirty=False)
            return
        service = self._wave_service()
        if service is None:
            self.refresh_preview(mark_dirty=False)
            return
        driver_data = self._driver_data_for_channel(channel) or {}
        carrier_waveform = driver_data.get("waveform", "Sinus")
        try:
            service.start(
                definition,
                channel,
                carrier_waveform=carrier_waveform,
            )
        except Exception as exc:
            QMessageBox.warning(self, "Cannot start waveform", str(exc))
        self._runtime_signature = None
        self.refresh_preview(mark_dirty=False)

    def _on_stop(self):
        service = self._wave_service()
        if service is not None:
            service.stop(self._current_channel())
        self._runtime_signature = None

    def _set_running_state(self, running: bool):
        editable_controls = (
            self.name_edit,
            self.template_combo,
            self.channel_combo,
            self.min_spin,
            self.max_spin,
            self.increment_spin,
            self.duration_spin,
            self.cycles_spin,
            self.cycle_duration_spin,
            self.new_button,
        )
        for control in editable_controls:
            control.setEnabled(not running)
        if self.template_combo.currentText() == "Sine":
            self.samples_spin.setEnabled(False)
        self.saved_combo.setEnabled((not running) and bool(self.library.items))
        self.save_button.setEnabled(not running)
        self.stop_button.setEnabled(running)

