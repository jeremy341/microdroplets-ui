"""Composable two-panel experiment workspace.

Workspace is a second *view* over the existing Pumps, Sensors, Wave and Camera
runtime.  It never opens another Multiboard connection or another camera and a
layout change never starts/stops hardware by itself.
"""

from __future__ import annotations

from concurrent.futures import ThreadPoolExecutor

from PyQt6.QtCore import QTimer, Qt, pyqtSignal
from PyQt6.QtWidgets import (
    QAbstractSpinBox,
    QBoxLayout,
    QComboBox,
    QFrame,
    QGridLayout,
    QHBoxLayout,
    QLabel,
    QPushButton,
    QRadioButton,
    QSlider,
    QSizePolicy,
    QSpinBox,
    QSplitter,
    QStackedWidget,
    QVBoxLayout,
    QWidget,
)

from backend.driver_capabilities import capabilities_for_driver_index
from backend.protocol import WAVEFORM_CODES
from backend.pump_control import PumpOperationResult
from backend.sensor_data_hub import SensorDataHub
from backend.waveform_engine import (
    check_compatibility,
    generate_steps,
    waveform_stats,
    SUPPORTED_TEMPLATES,
)
from backend.workspace import PanelType, WorkspaceController, WorkspacePreset, PANEL_REGISTRY
from backend.workspace.presets import PRESET_LABELS, PRESET_TITLES
from ui.pages.Sensors import MEASUREMENTS, SensorChart, ToggleSwitch as SensorToggle
from ui.pages.Waveform import WaveformPreview
from ui.pages.Camera import CameraSlider, CameraSwitch, camera_frame_pixmap
from ui.pages.Pumps import ToggleSwitch as PumpToggleSwitch

PANEL_LABELS = {panel_type: definition.label for panel_type, definition in PANEL_REGISTRY.items()}
PANEL_TITLES = {
    PanelType.PUMPS: "Pump Control",
    PanelType.SENSORS: "Live Sensor Data",
    PanelType.WAVE: "Wave Control",
    PanelType.CAMERA: "Camera",
}



class _AsyncPanel(QFrame):
    """Small worker helper used by compact panels for blocking hardware calls."""

    def __init__(self, parent=None):
        super().__init__(parent)
        self._executor = ThreadPoolExecutor(max_workers=1)
        self._pending = []
        self._async_timer = QTimer(self)
        self._async_timer.setInterval(60)
        self._async_timer.timeout.connect(self._poll_async)
        self._async_timer.start()

    def submit(self, function, callback=None):
        self._pending.append((self._executor.submit(function), callback))

    def _poll_async(self):
        remaining = []
        for future, callback in self._pending:
            if not future.done():
                remaining.append((future, callback))
                continue
            try:
                result = future.result()
            except Exception as exc:  # hardware exceptions become UI state, never Qt crashes
                result = exc
            if callback is not None:
                callback(result)
        self._pending = remaining

    def shutdown(self):
        self._async_timer.stop()
        self._executor.shutdown(wait=False, cancel_futures=True)


class WorkspaceSlot(QFrame):
    """One Workspace side with local EMPTY -> CHOOSER states.

    The chooser intentionally lives *inside* the affected slot instead of
    opening a modal dialog over both sides of the Workspace.
    """

    clicked = pyqtSignal()
    panel_selected = pyqtSignal(object)

    def __init__(self, side: str, parent=None):
        super().__init__(parent)
        self.setObjectName("workspaceEmptySlot")
        self.setProperty("side", side)
        self.side = side
        self._unavailable = None
        self._option_buttons = {}

        outer = QVBoxLayout(self)
        outer.setContentsMargins(0, 0, 0, 0)
        outer.setSpacing(0)

        self.stack = QStackedWidget()
        self.stack.setObjectName("workspaceSlotStack")
        outer.addWidget(self.stack)

        # EMPTY state -------------------------------------------------------
        empty = QWidget()
        empty.setObjectName("workspaceSlotEmptyState")
        empty_layout = QVBoxLayout(empty)
        empty_layout.setContentsMargins(30, 30, 30, 30)
        empty_layout.setSpacing(10)
        empty_layout.addStretch(3)

        plus = QPushButton("+")
        plus.setObjectName("workspaceAddButton")
        plus.setFixedSize(96, 96)
        plus.setCursor(Qt.CursorShape.PointingHandCursor)
        plus.clicked.connect(self.clicked)
        plus_row = QHBoxLayout()
        plus_row.addStretch()
        plus_row.addWidget(plus)
        plus_row.addStretch()
        empty_layout.addLayout(plus_row)

        self.title_label = QLabel(f"Choose {side} panel")
        self.title_label.setObjectName("workspaceSlotTitle")
        self.title_label.setAlignment(Qt.AlignmentFlag.AlignCenter)
        self.helper_label = QLabel(
            f"Select a tool to add to the {side} side of your workspace."
        )
        self.helper_label.setObjectName("workspaceSlotDescription")
        self.helper_label.setAlignment(Qt.AlignmentFlag.AlignCenter)
        empty_layout.addWidget(self.title_label)
        empty_layout.addWidget(self.helper_label)
        empty_layout.addStretch(4)
        self.stack.addWidget(empty)

        # CHOOSER state -----------------------------------------------------
        chooser = QWidget()
        chooser.setObjectName("workspaceSlotChooser")
        chooser_layout = QVBoxLayout(chooser)
        chooser_layout.setContentsMargins(24, 20, 24, 22)
        chooser_layout.setSpacing(10)

        header = QHBoxLayout()
        header.setSpacing(8)
        title_box = QVBoxLayout()
        title_box.setSpacing(2)
        chooser_title = QLabel("Choose panel")
        chooser_title.setObjectName("workspacePickerTitle")
        chooser_subtitle = QLabel(f"Add to {side} panel")
        chooser_subtitle.setObjectName("workspacePickerSubtitle")
        title_box.addWidget(chooser_title)
        title_box.addWidget(chooser_subtitle)
        header.addLayout(title_box)
        header.addStretch()
        close = QPushButton("×")
        close.setObjectName("workspacePickerClose")
        close.setFixedSize(32, 32)
        close.setToolTip("Close panel chooser")
        close.clicked.connect(self.show_empty)
        header.addWidget(close)
        chooser_layout.addLayout(header)
        chooser_layout.addSpacing(4)

        descriptions = {
            PanelType.PUMPS: "Control connected pump channels.",
            PanelType.SENSORS: "View live sensor data and logging.",
            PanelType.WAVE: "Generate and test pump waveforms.",
            PanelType.CAMERA: "Capture and view the live microscope.",
        }
        for panel_type in PanelType:
            definition = PANEL_REGISTRY[panel_type]
            button = QPushButton(
                f"{definition.label}\n{descriptions[panel_type]}"
            )
            button.setObjectName("workspacePickerOption")
            button.setProperty("panelType", panel_type.value)
            button.setMinimumHeight(58)
            button.clicked.connect(
                lambda checked=False, selected=panel_type: self._choose(selected)
            )
            chooser_layout.addWidget(button)
            self._option_buttons[panel_type] = button
        chooser_layout.addStretch()
        self.stack.addWidget(chooser)
        self.show_empty()

    def show_chooser(self, unavailable: PanelType | None = None):
        """Replace only this slot with its local panel chooser."""
        self._unavailable = unavailable
        for panel_type, button in self._option_buttons.items():
            button.setEnabled(panel_type != unavailable)
        self.stack.setCurrentIndex(1)

    def show_empty(self):
        self.stack.setCurrentIndex(0)

    def _choose(self, panel_type):
        if panel_type == self._unavailable:
            return
        self.show_empty()
        self.panel_selected.emit(panel_type)

    def set_panel(self, panel_type: PanelType | None):
        """Keep the builder labels coherent when restoring an empty layout."""
        self.show_empty()
        if panel_type is None:
            self.title_label.setText(f"Choose {self.side} panel")
            self.helper_label.setText(
                f"Select a tool to add to the {self.side} side of your workspace."
            )
            return
        self.title_label.setText(PANEL_LABELS[panel_type])
        self.helper_label.setText("Selected · click + to change")



class WorkspacePanelShell(QFrame):
    close_requested = pyqtSignal(str)

    def __init__(self, side: str, title: str, content: QWidget, parent=None):
        super().__init__(parent)
        self.setObjectName("workspacePanelShell")
        self.side = side
        self.content = content
        root = QVBoxLayout(self)
        root.setContentsMargins(0, 0, 0, 0)
        root.setSpacing(0)

        header = QFrame()
        header.setObjectName("workspacePanelHeader")
        header_layout = QHBoxLayout(header)
        header_layout.setContentsMargins(20, 14, 12, 10)
        header_layout.setSpacing(8)
        label = QLabel(title)
        label.setObjectName("workspacePanelShellTitle")
        header_layout.addWidget(label)
        header_layout.addStretch()
        close = QPushButton("×")
        close.setObjectName("workspacePanelClose")
        close.setToolTip(f"Remove {title} from Workspace")
        close.setFixedSize(30, 30)
        close.clicked.connect(lambda: self.close_requested.emit(self.side))
        header_layout.addWidget(close)
        root.addWidget(header)
        root.addWidget(content, 1)

    def shutdown(self):
        if hasattr(self.content, "shutdown"):
            self.content.shutdown()

    def refresh_from_backend(self):
        if hasattr(self.content, "refresh_from_backend"):
            self.content.refresh_from_backend()


class PumpWorkspacePanel(_AsyncPanel):
    """Compact two-channel manual pump view for Workspace.

    This is deliberately a thin second view over the same board dictionaries,
    PumpControlService and ChannelOwnershipManager used by the full Pumps page.
    It does not own separate pump state and never opens another connection.

    Each of the two slots exposes the same essentials as Pumps: channel,
    driver-level frequency / carrier mode, channel amplitude and the ON/OFF
    switch.  CH1-CH4 therefore naturally share F0 (and carrier mode) because
    both cards read/write the same physical driver state.
    """

    SLOT_COUNT = 2

    def __init__(self, active_board_provider, pumps_page=None, parent=None):
        super().__init__(parent)
        self.setObjectName("workspacePumpPanel")
        self._active_board_provider = active_board_provider
        self.pumps_page = pumps_page
        self._syncing = False
        self._slot_channels = [None] * self.SLOT_COUNT
        self._slots = []

        # Workspace refreshes from the shared backend several times per second.
        # Never let that polling loop fight a control the user is currently
        # manipulating, and keep the requested value visible while an async
        # hardware command is waiting for its ACK.
        self._pending_frequency_by_driver = {}
        self._pending_waveform_by_driver = {}
        self._pending_amplitude_by_channel = {}
        self._pending_running_by_channel = {}

        root = QVBoxLayout(self)
        root.setContentsMargins(20, 8, 20, 18)
        root.setSpacing(10)

        self.capability_label = QLabel("Connect a Multiboard to control pump channels.")
        self.capability_label.setObjectName("workspaceCapabilityBanner")
        self.capability_label.setWordWrap(True)
        root.addWidget(self.capability_label)

        for slot_index in range(self.SLOT_COUNT):
            slot = self._create_channel_slot(slot_index)
            self._slots.append(slot)
            root.addWidget(slot["card"])

        root.addStretch(1)
        root.addWidget(self._separator())

        self.status = QLabel("Status: No board connected")
        self.status.setObjectName("workspaceStatusLabel")
        root.addWidget(self.status)

        self._runtime_timer = QTimer(self)
        self._runtime_timer.setInterval(140)
        self._runtime_timer.timeout.connect(self.refresh_from_backend)
        self._runtime_timer.start()
        self.refresh_from_backend(force=True)

    @staticmethod
    def _separator():
        line = QFrame()
        line.setObjectName("workspaceSeparator")
        line.setFrameShape(QFrame.Shape.HLine)
        return line

    def _create_channel_slot(self, slot_index: int):
        card = QFrame()
        card.setObjectName("workspacePumpChannelCard")
        card.setSizePolicy(QSizePolicy.Policy.Expanding, QSizePolicy.Policy.Fixed)

        layout = QVBoxLayout(card)
        layout.setContentsMargins(16, 14, 16, 14)
        layout.setSpacing(11)

        # Header: Channel [CHx v]                              [toggle] ON/OFF
        header = QHBoxLayout()
        header.setSpacing(10)
        channel_label = QLabel("Channel")
        channel_label.setObjectName("workspaceFieldLabel")
        combo = QComboBox()
        combo.setObjectName("workspacePumpChannelCombo")
        combo.setFixedSize(96, 38)
        combo.currentIndexChanged.connect(
            lambda _index, s=slot_index: self._channel_changed(s)
        )
        toggle = PumpToggleSwitch()
        toggle.setObjectName("workspacePumpToggle")
        toggle.toggled.connect(
            lambda enabled, s=slot_index: self._set_running(s, enabled)
        )
        toggle_state = QLabel("OFF")
        toggle_state.setObjectName("workspacePumpToggleState")
        toggle_state.setFixedWidth(28)

        header.addWidget(channel_label)
        header.addWidget(combo)
        header.addStretch(1)
        header.addWidget(toggle)
        header.addWidget(toggle_state)
        layout.addLayout(header)

        frequency_slider, frequency_spin = self._slider_control(
            layout, "Driver frequency", "Hz"
        )
        frequency_slider.sliderReleased.connect(
            lambda s=slot_index: self._apply_frequency(s)
        )
        frequency_spin.lineEdit().textEdited.connect(
            lambda _text, spin=frequency_spin: spin.setProperty("workspaceUserEditing", True)
        )
        frequency_spin.editingFinished.connect(
            lambda s=slot_index, spin=frequency_spin:
                self._finish_spin_edit(s, "frequency", spin)
        )

        signal_row = QHBoxLayout()
        signal_row.setSpacing(12)
        signal_label = QLabel("Signal mode")
        signal_label.setObjectName("workspaceFieldLabel")
        signal_label.setMinimumWidth(112)
        waveform_combo = QComboBox()
        waveform_combo.setObjectName("workspacePumpSignalCombo")
        waveform_combo.addItems(WAVEFORM_CODES.keys())
        # Match the Driver frequency value box width from the reference.
        waveform_combo.setFixedSize(100, 38)
        waveform_combo.currentTextChanged.connect(
            lambda text, s=slot_index: self._apply_waveform(s, text)
        )
        signal_row.addWidget(signal_label)
        signal_row.addStretch(1)
        signal_row.addWidget(waveform_combo)
        layout.addLayout(signal_row)

        amplitude_slider, amplitude_spin = self._slider_control(
            layout, "Amplitude (Vpp)", "Vpp"
        )
        amplitude_slider.sliderReleased.connect(
            lambda s=slot_index: self._apply_amplitude(s)
        )
        amplitude_spin.lineEdit().textEdited.connect(
            lambda _text, spin=amplitude_spin: spin.setProperty("workspaceUserEditing", True)
        )
        amplitude_spin.editingFinished.connect(
            lambda s=slot_index, spin=amplitude_spin:
                self._finish_spin_edit(s, "amplitude", spin)
        )

        return {
            "card": card,
            "channel_combo": combo,
            "toggle": toggle,
            "toggle_state": toggle_state,
            "frequency_slider": frequency_slider,
            "frequency_spin": frequency_spin,
            "waveform_combo": waveform_combo,
            "amplitude_slider": amplitude_slider,
            "amplitude_spin": amplitude_spin,
        }

    def _slider_control(self, parent_layout, label_text, suffix):
        row = QHBoxLayout()
        row.setSpacing(12)
        label = QLabel(label_text)
        label.setObjectName("workspaceFieldLabel")
        label.setMinimumWidth(112)

        slider = QSlider(Qt.Orientation.Horizontal)
        slider.setObjectName("workspaceSlider")
        slider.setSizePolicy(QSizePolicy.Policy.Expanding, QSizePolicy.Policy.Fixed)

        spin = QSpinBox()
        spin.setObjectName("workspaceSpin")
        spin.setButtonSymbols(QAbstractSpinBox.ButtonSymbols.NoButtons)
        spin.setSuffix(f" {suffix}")
        spin.setFixedSize(100, 38)

        slider.valueChanged.connect(spin.setValue)
        spin.valueChanged.connect(slider.setValue)
        row.addWidget(label)
        row.addWidget(slider, 1)
        row.addWidget(spin)
        parent_layout.addLayout(row)
        return slider, spin

    def _finish_spin_edit(self, slot_index: int, field: str, spin: QSpinBox):
        # editingFinished may fire while the spin box still owns focus (for
        # example after pressing Enter).  Clear our explicit user-edit flag
        # before committing so ACK/failure refreshes can take over normally.
        spin.setProperty("workspaceUserEditing", False)
        if field == "frequency":
            self._apply_frequency(slot_index)
        else:
            self._apply_amplitude(slot_index)

    def _refresh_full_pumps_page(self):
        if self.pumps_page is not None and hasattr(self.pumps_page, "refresh_from_shared_state"):
            self.pumps_page.refresh_from_shared_state()

    def _board(self):
        return self._active_board_provider()

    @staticmethod
    def _available_channels(board):
        if board is None:
            return []
        return [
            int(channel["channel"])
            for driver in board.get("drivers", [])
            for channel in driver.get("channels", [])
        ]

    def _driver_and_channel(self, slot_index: int):
        board = self._board()
        if board is None:
            return None, None
        channel = self._slot_channels[slot_index]
        if channel is None:
            return None, None
        for driver in board.get("drivers", []):
            for item in driver.get("channels", []):
                if int(item.get("channel", 0)) == int(channel):
                    return driver, item
        return None, None

    def _choose_default_channel(self, slot_index: int, channels: list[int]):
        current = self._slot_channels[slot_index]
        other = self._slot_channels[1 - slot_index]
        if current in channels and current != other:
            return current

        preferred = slot_index + 1  # reference/default: first slot CH1, second CH2
        if preferred in channels and preferred != other:
            return preferred
        return next((channel for channel in channels if channel != other), None)

    def _populate_channels(self, board):
        channels = self._available_channels(board)
        # Resolve both defaults before rebuilding either combo so CH1/CH2 are
        # selected deterministically when those channels exist.
        if channels:
            if self._slot_channels[0] not in channels:
                self._slot_channels[0] = 1 if 1 in channels else channels[0]
            if self._slot_channels[1] not in channels or self._slot_channels[1] == self._slot_channels[0]:
                preferred = 2 if 2 in channels and 2 != self._slot_channels[0] else None
                self._slot_channels[1] = preferred or next(
                    (channel for channel in channels if channel != self._slot_channels[0]),
                    None,
                )
        else:
            self._slot_channels = [None, None]

        for slot_index, slot in enumerate(self._slots):
            combo = slot["channel_combo"]
            other = self._slot_channels[1 - slot_index]
            allowed = [channel for channel in channels if channel != other]
            selected = self._slot_channels[slot_index]
            if selected not in allowed:
                selected = self._choose_default_channel(slot_index, channels)
                self._slot_channels[slot_index] = selected

            existing = [combo.itemData(i) for i in range(combo.count())]
            if existing != allowed:
                blocked = combo.blockSignals(True)
                combo.clear()
                for channel in allowed:
                    combo.addItem(f"CH{channel}", channel)
                combo.blockSignals(blocked)

            if selected is not None:
                index = combo.findData(selected)
                if index >= 0 and combo.currentIndex() != index:
                    blocked = combo.blockSignals(True)
                    combo.setCurrentIndex(index)
                    combo.blockSignals(blocked)

    def _channel_changed(self, slot_index: int):
        if self._syncing:
            return
        combo = self._slots[slot_index]["channel_combo"]
        channel = combo.currentData()
        if channel is None:
            return
        channel = int(channel)
        other_index = 1 - slot_index
        if channel == self._slot_channels[other_index]:
            self.refresh_from_backend(force=True)
            return
        self._slot_channels[slot_index] = channel
        # Rebuild the other selector so one physical channel cannot appear in
        # both Workspace slots at once.
        self.refresh_from_backend(force=True)

    @staticmethod
    def _set_value(widget, value):
        blocked = widget.blockSignals(True)
        widget.setValue(int(value))
        widget.blockSignals(blocked)

    @staticmethod
    def _set_combo_text(combo, value):
        blocked = combo.blockSignals(True)
        combo.setCurrentText(str(value))
        combo.blockSignals(blocked)

    @staticmethod
    def _set_toggle(toggle, checked):
        blocked = toggle.blockSignals(True)
        toggle.setChecked(bool(checked))
        toggle.blockSignals(blocked)
        toggle.update()

    @staticmethod
    def _set_pump_stopped_visual(slot, stopped: bool):
        """Mirror the full Pumps-page OFF-state styling in Workspace.

        The full Pumps page deliberately keeps amplitude editable while the
        pump is OFF so a value can be staged before the next start, but it
        visually greys those controls to make it clear that the value is not
        currently being driven.  Workspace uses the same dynamic property and
        the same QSS colours rather than disabling the widgets.
        """
        for key in ("amplitude_slider", "amplitude_spin"):
            control = slot[key]
            control.setProperty("pumpStopped", bool(stopped))
            control.style().unpolish(control)
            control.style().polish(control)
            control.update()

    @staticmethod
    def _driver_pending(driver):
        return any(
            bool(channel.get("_operation_pending", False))
            for channel in driver.get("channels", [])
        )

    def _set_slot_enabled(self, slot, enabled: bool):
        for key in (
            "channel_combo", "frequency_slider", "frequency_spin",
            "waveform_combo", "amplitude_slider", "amplitude_spin", "toggle",
        ):
            slot[key].setEnabled(bool(enabled))

    def refresh_from_backend(self, force=False):
        if self._syncing:
            return
        self._syncing = True
        try:
            board = self._board()
            self._populate_channels(board)

            if board is None:
                self._pending_frequency_by_driver.clear()
                self._pending_waveform_by_driver.clear()
                self._pending_amplitude_by_channel.clear()
                self._pending_running_by_channel.clear()
                for slot in self._slots:
                    self._set_slot_enabled(slot, False)
                    self._set_toggle(slot["toggle"], False)
                    slot["toggle_state"].setText("OFF")
                    self._set_pump_stopped_visual(slot, True)
                    slot["frequency_slider"].setRange(0, 0)
                    slot["frequency_spin"].setRange(0, 0)
                    slot["amplitude_slider"].setRange(0, 0)
                    slot["amplitude_spin"].setRange(0, 0)
                    self._set_value(slot["frequency_slider"], 0)
                    self._set_value(slot["frequency_spin"], 0)
                    self._set_value(slot["amplitude_slider"], 0)
                    self._set_value(slot["amplitude_spin"], 0)
                self.capability_label.setText("Connect a Multiboard to control pump channels.")
                self.status.setText("Status: No board connected")
                return

            any_pending = False
            wave_owned_channels = []
            for slot_index, slot in enumerate(self._slots):
                driver, channel_data = self._driver_and_channel(slot_index)
                if driver is None or channel_data is None:
                    self._set_slot_enabled(slot, False)
                    continue

                channel = int(channel_data["channel"])
                capabilities = capabilities_for_driver_index(int(driver["driver_index"]))
                driver_index = int(driver["driver_index"])
                frequency = int(
                    self._pending_frequency_by_driver.get(
                        driver_index,
                        board["pump_control"].get_driver_frequency(driver_index),
                    )
                )
                amplitude = int(
                    self._pending_amplitude_by_channel.get(
                        channel,
                        channel_data.get("amplitude", capabilities.amplitude_min_vpp),
                    )
                )
                waveform = self._pending_waveform_by_driver.get(
                    driver_index, driver.get("waveform", "Sinus")
                )
                owner = board["channel_ownership"].get(channel)
                running = bool(channel_data.get("enabled", False))
                if owner is not None and owner.kind == "manual":
                    running = True
                running = bool(self._pending_running_by_channel.get(channel, running))

                operation_pending = bool(channel_data.get("_operation_pending", False))
                driver_pending = self._driver_pending(driver)
                any_pending = any_pending or operation_pending or driver_pending

                slot["frequency_slider"].setRange(*capabilities.frequency_limits)
                slot["frequency_spin"].setRange(*capabilities.frequency_limits)
                slot["amplitude_slider"].setRange(*capabilities.amplitude_limits)
                slot["amplitude_spin"].setRange(*capabilities.amplitude_limits)

                # Do not overwrite controls while the user is dragging a
                # slider or typing into its spin box.  Otherwise the 140 ms
                # backend poll visibly pulls the thumb back to the last ACKed
                # value and Qt then jumps it back under the mouse cursor.
                frequency_editing = (
                    slot["frequency_slider"].isSliderDown()
                    or bool(slot["frequency_spin"].property("workspaceUserEditing"))
                )
                amplitude_editing = (
                    slot["amplitude_slider"].isSliderDown()
                    or bool(slot["amplitude_spin"].property("workspaceUserEditing"))
                )
                if not frequency_editing:
                    self._set_value(slot["frequency_slider"], frequency)
                    self._set_value(slot["frequency_spin"], frequency)
                if not amplitude_editing:
                    self._set_value(slot["amplitude_slider"], amplitude)
                    self._set_value(slot["amplitude_spin"], amplitude)

                self._set_combo_text(slot["waveform_combo"], waveform)
                self._set_toggle(slot["toggle"], running)
                slot["toggle_state"].setText("ON" if running else "OFF")

                # Match Pumps-page UX exactly: amplitude stays editable while
                # OFF (staging the next Vpp value), but is visually greyed out.
                # Frequency remains a live driver-level property and therefore
                # does not get the stopped-channel treatment.
                self._set_pump_stopped_visual(slot, not running)

                slot["channel_combo"].setEnabled(not operation_pending)
                slot["frequency_slider"].setEnabled(not driver_pending)
                slot["frequency_spin"].setEnabled(not driver_pending)

                # Match the full Pumps page: Wave ownership remains a backend/
                # command-handler interlock rather than a separate visual lock.
                # If a user touches a Wave-owned channel, _apply_amplitude()/
                # _set_running() reject the change and restore the acknowledged
                # value. Only an in-flight channel command temporarily disables
                # these controls.
                amplitude_editable = not operation_pending
                slot["amplitude_slider"].setEnabled(amplitude_editable)
                slot["amplitude_spin"].setEnabled(amplitude_editable)

                group_busy = any(
                    board["channel_ownership"].get(int(item["channel"])) is not None
                    for item in driver.get("channels", [])
                )
                waveform_editable = (
                    capabilities.supports_carrier_waveform
                    and not group_busy
                    and not driver_pending
                )
                slot["waveform_combo"].setEnabled(waveform_editable)
                if not capabilities.supports_carrier_waveform:
                    slot["waveform_combo"].setToolTip(
                        f"{driver.get('driver_display_name', 'This driver')} does not use "
                        "the Multiboard carrier-shape command."
                    )
                elif group_busy:
                    slot["waveform_combo"].setToolTip(
                        "Stop all pumps on this driver before changing signal mode."
                    )
                elif driver_pending:
                    slot["waveform_combo"].setToolTip(
                        "Waiting for the current driver command."
                    )
                else:
                    slot["waveform_combo"].setToolTip("")

                # Same Pumps-page ownership UX as amplitude: keep the manual
                # toggle visually available and let the shared interlock reject
                # Wave-owned requests, restoring the real state immediately.
                slot["toggle"].setEnabled(not operation_pending)

                if owner is not None and owner.kind == "waveform":
                    wave_owned_channels.append(channel)

            self.capability_label.setText("Control up to two pump channels.")
            if any_pending:
                self.status.setText("Status: Applying command…")
            elif wave_owned_channels:
                names = ", ".join(f"CH{channel}" for channel in sorted(set(wave_owned_channels)))
                self.status.setText(f"Status: {names} controlled by Wave")
            else:
                self.status.setText("Status: Connected")
        finally:
            self._syncing = False

    def _apply_frequency(self, slot_index: int):
        if self._syncing:
            return
        board = self._board()
        driver, _ = self._driver_and_channel(slot_index)
        if board is None or driver is None or self._driver_pending(driver):
            return
        value = int(self._slots[slot_index]["frequency_spin"].value())
        old = int(board["pump_control"].get_driver_frequency(driver["driver_index"]))
        if value == old:
            return

        driver_index = int(driver["driver_index"])
        self._pending_frequency_by_driver[driver_index] = value
        for channel in driver.get("channels", []):
            channel["_operation_pending"] = True
        self.refresh_from_backend(force=True)
        self.submit(
            lambda: board["pump_control"].set_driver_frequency(driver_index, value),
            lambda result, b=board, d=driver, previous=old, desired=value:
                self._frequency_done(result, b, d, previous, desired),
        )

    def _frequency_done(self, result, board, driver, old, value):
        driver_index = int(driver["driver_index"])
        self._pending_frequency_by_driver.pop(driver_index, None)
        for channel in driver.get("channels", []):
            channel["_operation_pending"] = False
        if result is True:
            driver["frequency"] = value
        else:
            driver["frequency"] = old
        self._refresh_full_pumps_page()
        if board is self._board():
            self.refresh_from_backend(force=True)

    def _apply_waveform(self, slot_index: int, text: str):
        if self._syncing:
            return
        board = self._board()
        driver, _ = self._driver_and_channel(slot_index)
        if board is None or driver is None or self._driver_pending(driver):
            return
        if text == driver.get("waveform", "Sinus"):
            return
        capabilities = capabilities_for_driver_index(int(driver["driver_index"]))
        if not capabilities.supports_carrier_waveform:
            self.refresh_from_backend(force=True)
            return
        manager = board["channel_ownership"]
        if any(manager.get(int(item["channel"])) is not None for item in driver.get("channels", [])):
            self.refresh_from_backend(force=True)
            return

        old = driver.get("waveform", "Sinus")
        driver_index = int(driver["driver_index"])
        self._pending_waveform_by_driver[driver_index] = text
        for channel in driver.get("channels", []):
            channel["_operation_pending"] = True
        self.refresh_from_backend(force=True)
        self.submit(
            lambda: board["pump_control"].set_driver_waveform(driver_index, text),
            lambda result, b=board, d=driver, previous=old, desired=text:
                self._waveform_done(result, b, d, previous, desired),
        )

    def _waveform_done(self, result, board, driver, old, value):
        driver_index = int(driver["driver_index"])
        self._pending_waveform_by_driver.pop(driver_index, None)
        for channel in driver.get("channels", []):
            channel["_operation_pending"] = False
        driver["waveform"] = value if result is True else old
        self._refresh_full_pumps_page()
        if board is self._board():
            self.refresh_from_backend(force=True)

    def _apply_amplitude(self, slot_index: int):
        if self._syncing:
            return
        board = self._board()
        driver, channel_data = self._driver_and_channel(slot_index)
        if board is None or driver is None or channel_data is None:
            return
        if bool(channel_data.get("_operation_pending", False)):
            self.refresh_from_backend(force=True)
            return

        value = int(self._slots[slot_index]["amplitude_spin"].value())
        channel = int(channel_data["channel"])
        owner = board["channel_ownership"].get(channel)

        # Same behavior as the full Pumps page: while OFF, amplitude is staged
        # in shared RAM and sent as part of the next confirmed ON transaction.
        if owner is None and not bool(channel_data.get("enabled", False)):
            channel_data["amplitude"] = value
            self._refresh_full_pumps_page()
            self.refresh_from_backend(force=True)
            return
        if owner is None or owner.kind != "manual":
            self.refresh_from_backend(force=True)
            return

        old = int(channel_data.get("amplitude", value))
        self._pending_amplitude_by_channel[channel] = value
        channel_data["_operation_pending"] = True
        self.refresh_from_backend(force=True)
        self.submit(
            lambda: board["pump_control"].set_manual_amplitude(
                driver["driver_index"], channel, value
            ),
            lambda result, b=board, c=channel_data, previous=old, desired=value:
                self._amplitude_done(result, b, c, previous, desired),
        )

    def _amplitude_done(self, result, board, channel_data, old, value):
        channel = int(channel_data["channel"])
        self._pending_amplitude_by_channel.pop(channel, None)
        channel_data["_operation_pending"] = False
        if isinstance(result, PumpOperationResult) and result.success:
            channel_data["amplitude"] = value
        else:
            channel_data["amplitude"] = old
            if isinstance(result, PumpOperationResult) and result.hardware_state == "unknown":
                channel_data["hardware_state"] = "unknown"
        self._refresh_full_pumps_page()
        if board is self._board():
            self.refresh_from_backend(force=True)

    def _set_running(self, slot_index: int, desired: bool):
        if self._syncing:
            return
        board = self._board()
        driver, channel_data = self._driver_and_channel(slot_index)
        if board is None or driver is None or channel_data is None:
            return
        if bool(channel_data.get("_operation_pending", False)):
            self.refresh_from_backend(force=True)
            return

        channel = int(channel_data["channel"])
        owner = board["channel_ownership"].get(channel)
        previous = bool(channel_data.get("enabled", False))
        if owner is not None and owner.kind == "waveform":
            self.refresh_from_backend(force=True)
            return
        if desired == previous and not (owner is not None and owner.kind == "manual"):
            self.refresh_from_backend(force=True)
            return

        slot = self._slots[slot_index]
        service = board["pump_control"]
        self._pending_running_by_channel[channel] = bool(desired)
        channel_data["_operation_pending"] = True
        channel_data["_previous_enabled"] = previous
        self.refresh_from_backend(force=True)

        if desired:
            operation = lambda: service.start_manual(
                driver["driver_index"],
                int(slot["frequency_spin"].value()),
                slot["waveform_combo"].currentText(),
                channel,
                int(slot["amplitude_spin"].value()),
            )
        else:
            operation = lambda: service.stop_manual(channel)

        self.submit(
            operation,
            lambda result, b=board, c=channel_data, d=driver, wanted=bool(desired), s=slot_index:
                self._running_done(result, b, c, d, wanted, s),
        )

    def _running_done(self, result, board, channel_data, driver, desired, slot_index):
        channel = int(channel_data["channel"])
        self._pending_running_by_channel.pop(channel, None)
        channel_data["_operation_pending"] = False
        previous = bool(channel_data.pop("_previous_enabled", channel_data.get("enabled", False)))
        if isinstance(result, PumpOperationResult) and result.success:
            channel_data["enabled"] = desired
            channel_data["hardware_state"] = "on" if desired else "off"
            if desired:
                slot = self._slots[slot_index]
                channel_data["amplitude"] = int(slot["amplitude_spin"].value())
                driver["frequency"] = int(slot["frequency_spin"].value())
                driver["waveform"] = slot["waveform_combo"].currentText()
        elif isinstance(result, PumpOperationResult) and result.hardware_state == "unknown":
            channel_data["enabled"] = True
            channel_data["hardware_state"] = "unknown"
        else:
            channel_data["enabled"] = previous

        self._refresh_full_pumps_page()
        if board is self._board():
            self.refresh_from_backend(force=True)

    def shutdown(self):
        self._runtime_timer.stop()
        super().shutdown()


class SensorWorkspacePanel(QFrame):
    """Compact Workspace view of the shared sensor runtime.

    The panel intentionally has one sensor-selection surface: the Active
    Sensors tiles below the chart.  There is no duplicate Sensor combo box in
    the toolbar. Logging, sample rate, primary measurement selection and the
    chart view mode are shared with the full Sensors page.
    """

    def __init__(self, sensors_page, sensor_hub, active_board_provider, parent=None):
        super().__init__(parent)
        self.setObjectName("workspaceSensorPanel")
        self.sensors_page = sensors_page
        self.sensor_hub = sensor_hub
        self._active_board_provider = active_board_provider
        self._selected_sensor = None
        self._radio_buttons = {}
        self._sensor_columns = None

        root = QVBoxLayout(self)
        root.setContentsMargins(20, 8, 18, 14)
        root.setSpacing(8)

        # Reference toolbar: sample/logging controls live in the upper-right
        # above the local chart navigation buttons. Sensor selection happens
        # only in Active Sensors below the graph.
        logging_row = QHBoxLayout()
        logging_row.setSpacing(10)
        logging_row.addStretch(1)

        rate_label = QLabel("Sample rate")
        rate_label.setObjectName("workspaceFieldLabel")
        self.rate_combo = QComboBox()
        self.rate_combo.setObjectName("workspaceCombo")
        self.rate_combo.setFixedSize(118, 42)
        self.rate_combo.addItems(["1 sec", "2 sec", "3 sec", "5 sec", "10 sec"])
        self.rate_combo.currentTextChanged.connect(self._rate_changed)
        logging_row.addWidget(rate_label)
        logging_row.addWidget(self.rate_combo)

        self.start_log = QPushButton("Start Logging")
        self.start_log.setObjectName("workspaceOutlineAction")
        self.stop_log = QPushButton("Stop")
        self.stop_log.setObjectName("workspaceDangerOutline")
        self.start_log.setFixedSize(132, 42)
        self.stop_log.setFixedSize(86, 42)
        self.start_log.clicked.connect(self.sensors_page.start_logging)
        self.stop_log.clicked.connect(self.sensors_page.stop_logging)
        logging_row.addWidget(self.start_log)
        logging_row.addWidget(self.stop_log)
        root.addLayout(logging_row)

        line = QFrame()
        line.setObjectName("workspaceSeparator")
        line.setFrameShape(QFrame.Shape.HLine)
        root.addWidget(line)

        chart_controls = QHBoxLayout()
        chart_controls.setSpacing(8)
        chart_controls.addStretch(1)
        self.pause = QPushButton("Pause")
        self.fit = QPushButton("Fit")
        self.live = QPushButton("Live")
        for button in (self.pause, self.fit, self.live):
            button.setObjectName("workspaceChartButton")
            button.setFixedHeight(36)
        self.pause.setMinimumWidth(72)
        self.fit.setMinimumWidth(50)
        self.live.setMinimumWidth(58)
        self.pause.clicked.connect(self._toggle_pause)
        self.fit.clicked.connect(self._fit)
        self.live.clicked.connect(self._live)
        chart_controls.addWidget(self.pause)
        chart_controls.addWidget(self.fit)
        chart_controls.addWidget(self.live)
        root.addLayout(chart_controls)

        self.signal_title = QLabel("Select a sensor")
        self.signal_title.setObjectName("workspaceSensorSignalTitle")
        root.addWidget(self.signal_title)

        self.chart = SensorChart()
        self.chart.setMinimumHeight(245)
        # PyQtGraph's automatic SI prefix was visually adding a second scaling
        # factor to an axis that is already labelled in the application's unit.
        # Disable that presentation-only transformation for Workspace.
        value_axis = getattr(self.chart, "_value_axis", None)
        if value_axis is not None and hasattr(value_axis, "enableAutoSIPrefix"):
            value_axis.enableAutoSIPrefix(False)
        root.addWidget(self.chart, 1)

        sensor_list = QFrame()
        sensor_list.setObjectName("workspaceSensorList")
        self._sensor_list = sensor_list
        list_layout = QVBoxLayout(sensor_list)
        list_layout.setContentsMargins(0, 2, 0, 0)
        list_layout.setSpacing(7)

        title_row = QHBoxLayout()
        title_row.setSpacing(4)
        list_title = QLabel("Active Sensors")
        list_title.setObjectName("workspaceSensorListTitle")
        helper = QLabel("(select a sensor to display its signal)")
        helper.setObjectName("workspaceSensorListHelper")
        title_row.addWidget(list_title)
        title_row.addWidget(helper)
        title_row.addStretch(1)
        list_layout.addLayout(title_row)

        self._sensor_grid = QGridLayout()
        self._sensor_grid.setContentsMargins(0, 0, 0, 0)
        self._sensor_grid.setHorizontalSpacing(8)
        self._sensor_grid.setVerticalSpacing(7)
        for sensor_id, definition in MEASUREMENTS.items():
            radio = QRadioButton(definition.label)
            radio.setObjectName("workspaceSensorChoice")
            radio.setProperty("sensorId", sensor_id)
            radio.setMinimumHeight(38)
            radio.clicked.connect(
                lambda checked=False, sid=sensor_id: self._select_sensor(sid)
            )
            self._radio_buttons[sensor_id] = radio
        list_layout.addLayout(self._sensor_grid)

        root.addWidget(sensor_list)

        self._relayout_sensor_choices(3)
        self._timer = QTimer(self)
        self._timer.setInterval(160)
        self._timer.timeout.connect(self.refresh_from_backend)
        self._timer.start()
        self.refresh_from_backend()

    def _board_id(self):
        board = self._active_board_provider()
        return self.sensors_page._board_id(board) if board else None

    def _board_port(self):
        board = self._active_board_provider()
        return str(board.get("port")) if board else None

    def _set_local_sensor_selection(self, sensor_id):
        """Mirror the full Sensors-page primary measurement locally."""
        sensor_id = sensor_id if sensor_id in MEASUREMENTS else None
        self._selected_sensor = sensor_id
        definition = MEASUREMENTS.get(sensor_id) if sensor_id else None
        self.signal_title.setText(definition.label if definition else "Select a sensor")
        for sid, radio in self._radio_buttons.items():
            blocked = radio.blockSignals(True)
            # QRadioButton auto-exclusivity prevents clearing the sole checked
            # radio in some styles, so briefly disable it for the no-selection
            # state used when the full Sensors page has nothing selected.
            was_exclusive = radio.autoExclusive()
            if sensor_id is None:
                radio.setAutoExclusive(False)
            radio.setChecked(sid == sensor_id)
            radio.setAutoExclusive(was_exclusive)
            radio.blockSignals(blocked)

    def _select_sensor(self, sensor_id):
        if sensor_id not in MEASUREMENTS:
            return
        board_id = self._board_id()
        if board_id is not None:
            self.sensors_page.set_primary_measurement(board_id, sensor_id)
        self._set_local_sensor_selection(sensor_id)
        self.refresh_from_backend()

    def _rate_changed(self, text):
        if self.sensors_page.logging:
            return
        index = self.sensors_page.rate_box.findText(text)
        if index >= 0:
            self.sensors_page.rate_box.setCurrentIndex(index)

    def _toggle_pause(self):
        # Chart navigation is shared too: using Pause/Resume in Workspace must
        # leave the full Sensors tab in the same view mode.
        if self.chart.is_live_view:
            self.sensors_page.chart_pause()
            self.chart.pause_view()
            self.pause.setText("Resume")
        else:
            self.sensors_page.chart_auto_scale()
            self.chart.resume_live()
            self.pause.setText("Pause")

    def _fit(self):
        self.sensors_page.chart_fit_data()
        self.chart.fit_data()
        self.pause.setText("Resume")

    def _live(self):
        self.sensors_page.chart_auto_scale()
        self.chart.resume_live()
        self.pause.setText("Pause")

    @staticmethod
    def _chart_mode(chart):
        if bool(getattr(chart, "_follow_live", False)):
            return "live"
        return "paused" if bool(getattr(chart, "_manual_scale", False)) else "fit"

    def _sync_chart_mode_from_page(self):
        source_mode = self._chart_mode(self.sensors_page.chart)
        local_mode = self._chart_mode(self.chart)
        if source_mode == local_mode:
            self.pause.setText("Pause" if source_mode == "live" else "Resume")
            return
        if source_mode == "live":
            self.chart.resume_live()
            self.pause.setText("Pause")
        elif source_mode == "fit":
            self.chart.fit_data()
            self.pause.setText("Resume")
        else:
            self.chart.pause_view()
            self.pause.setText("Resume")

    def refresh_from_backend(self):
        board_id = self._board_id()

        # Sensor choice belongs to the shared SensorsPage model. A selection
        # made on either surface therefore appears on the other within the
        # same event-loop turn / refresh tick.
        shared_sensor = None
        if board_id is not None:
            shared_sensor = self.sensors_page.selected_measurements.get(board_id)
            if shared_sensor is None:
                selections = self.sensors_page.selected_measurement_sets.get(board_id, ())
                shared_sensor = selections[0] if selections else None
        if shared_sensor != self._selected_sensor:
            self._set_local_sensor_selection(shared_sensor)

        self._sync_chart_mode_from_page()

        shared_rate = self.sensors_page.rate_box.currentText()
        if self.rate_combo.currentText() != shared_rate:
            blocked = self.rate_combo.blockSignals(True)
            self.rate_combo.setCurrentText(shared_rate)
            self.rate_combo.blockSignals(blocked)
        self.rate_combo.setEnabled(not self.sensors_page.logging)
        self.start_log.setEnabled(not self.sensors_page.logging)
        self.start_log.setText("Logging…" if self.sensors_page.logging else "Start Logging")
        self.stop_log.setEnabled(self.sensors_page.logging)

        sensor_id = self._selected_sensor
        definition = MEASUREMENTS.get(sensor_id)
        self.signal_title.setText(definition.label if definition else "Select a sensor")
        if definition is None or board_id is None:
            self.chart.set_data([], [], (definition.axis_label if definition else "Value", ""))
        else:
            key = f"{board_id}:{sensor_id}"
            port = self._board_port()
            values, times = self.sensor_hub.snapshot(port, sensor_id) if port else ([], [])
            # Workspace can be opened after the full Sensors page has already
            # drained historical events. Reuse that history until the fan-out
            # hub has new samples of its own.
            if not values:
                values = list(self.sensors_page.histories.get(key, ()))
                times = list(self.sensors_page.history_timestamps.get(key, ()))
            series = []
            if values:
                series.append({
                    "id": key,
                    "values": values,
                    "timestamps": times,
                    "color": definition.color,
                    "axis": "left",
                })
            self.chart.set_data(series, [], (definition.axis_label, ""))

    def _relayout_sensor_choices(self, columns):
        columns = max(1, int(columns))
        if getattr(self, "_sensor_columns", None) == columns:
            return
        self._sensor_columns = columns
        for radio in self._radio_buttons.values():
            self._sensor_grid.removeWidget(radio)
        for index, radio in enumerate(self._radio_buttons.values()):
            row = index // columns
            column = index % columns
            self._sensor_grid.addWidget(radio, row, column)
        for column in range(columns):
            self._sensor_grid.setColumnStretch(column, 1)

    def resizeEvent(self, event):
        super().resizeEvent(event)
        width = self.width()
        # The reference uses a 3×2 selector grid at the normal 1400×800 app
        # size, while still keeping the panel usable when the splitter is
        # dragged much narrower.
        columns = 3 if width >= 500 else (2 if width >= 350 else 1)
        self._relayout_sensor_choices(columns)

    def shutdown(self):
        self._timer.stop()


class WaveWorkspacePanel(QFrame):
    """Compact view over the *existing* WavePage draft and runtime."""

    def __init__(self, wave_page, parent=None):
        super().__init__(parent)
        self.setObjectName("workspaceWavePanel")
        self.wave_page = wave_page
        self._syncing = False

        root = QVBoxLayout(self)
        root.setContentsMargins(18, 8, 18, 16)
        root.setSpacing(9)

        self.compatibility = QLabel("")
        self.compatibility.setObjectName("workspaceCapabilityBanner")
        self.compatibility.setWordWrap(True)
        root.addWidget(self.compatibility)

        top = QGridLayout()
        top.setHorizontalSpacing(9)
        top.setVerticalSpacing(7)
        self.saved_combo = QComboBox(); self.saved_combo.setObjectName("workspaceCombo")
        self.channel_combo = QComboBox(); self.channel_combo.setObjectName("workspaceCombo")
        self.template_combo = QComboBox(); self.template_combo.setObjectName("workspaceCombo")
        for combo in (self.saved_combo, self.channel_combo, self.template_combo):
            combo.setFixedHeight(38)
        self.template_combo.addItems(SUPPORTED_TEMPLATES)
        self.saved_combo.currentIndexChanged.connect(self._saved_selected)
        self.channel_combo.currentIndexChanged.connect(self._push_editor)
        self.template_combo.currentTextChanged.connect(self._push_editor)
        top.addWidget(self._label("Saved waveform"), 0, 0); top.addWidget(self.saved_combo, 0, 1, 1, 3)
        top.addWidget(self._label("Target channel"), 1, 0); top.addWidget(self.channel_combo, 1, 1, 1, 3)
        top.addWidget(self._label("Create from"), 2, 0); top.addWidget(self.template_combo, 2, 1, 1, 3)
        root.addLayout(top)

        content = QHBoxLayout()
        content.setSpacing(12)
        fields = QGridLayout(); fields.setHorizontalSpacing(8); fields.setVerticalSpacing(6)
        self.min_spin = self._spin("Vpp"); self.max_spin = self._spin("Vpp")
        self.increment_spin = self._spin("Vpp"); self.duration_spin = self._spin("ms")
        self.cycles_spin = self._spin(""); self.frequency_spin = self._spin("Hz")
        controls = [
            ("Min amplitude", self.min_spin), ("Max amplitude", self.max_spin),
            ("Increment", self.increment_spin), ("Step duration", self.duration_spin),
            ("Cycles", self.cycles_spin), ("Driver frequency", self.frequency_spin),
        ]
        for row, (text, spin) in enumerate(controls):
            label = self._label(text)
            label.setMinimumWidth(92)
            fields.addWidget(label, row, 0)
            fields.addWidget(spin, row, 1)
            spin.valueChanged.connect(self._push_editor)
        content.addLayout(fields, 5)
        preview_col = QVBoxLayout()
        preview_col.setSpacing(4)
        preview_title = QLabel("Waveform Preview"); preview_title.setObjectName("workspaceSubTitle")
        self.preview = WaveformPreview()
        self.preview.setMinimumHeight(190)
        preview_col.addWidget(preview_title)
        preview_col.addWidget(self.preview, 1)
        content.addLayout(preview_col, 5)
        root.addLayout(content, 1)

        stats_frame = QFrame(); stats_frame.setObjectName("workspaceStatsBox")
        stats_layout = QHBoxLayout(stats_frame); stats_layout.setContentsMargins(12, 8, 12, 8)
        self.total_steps = self._stat(stats_layout, "Total steps")
        self.cycle_duration = self._stat(stats_layout, "Cycle duration")
        self.wave_frequency = self._stat(stats_layout, "Wave frequency")
        root.addWidget(stats_frame)

        actions = QHBoxLayout(); actions.setSpacing(10)
        self.test_button = QPushButton("Test Waveform"); self.test_button.setObjectName("workspacePrimaryAction")
        self.stop_button = QPushButton("Stop"); self.stop_button.setObjectName("workspaceSecondaryAction")
        self.test_button.setFixedHeight(44); self.stop_button.setFixedHeight(44)
        self.test_button.clicked.connect(self.wave_page._on_test)
        self.stop_button.clicked.connect(self.wave_page._on_stop)
        actions.addWidget(self.test_button, 1); actions.addWidget(self.stop_button, 1)
        root.addLayout(actions)

        self._timer = QTimer(self); self._timer.setInterval(180); self._timer.timeout.connect(self.refresh_from_backend); self._timer.start()
        self.refresh_from_backend(force=True)

    @staticmethod
    def _label(text):
        label = QLabel(text); label.setObjectName("workspaceFieldLabel"); return label

    @staticmethod
    def _spin(suffix):
        spin = QSpinBox(); spin.setObjectName("workspaceSpin"); spin.setButtonSymbols(QAbstractSpinBox.ButtonSymbols.NoButtons)
        spin.setRange(0, 100000); spin.setSuffix(f" {suffix}" if suffix else ""); spin.setFixedSize(118, 36); return spin

    @staticmethod
    def _stat(layout, label_text):
        box = QVBoxLayout(); label = QLabel(label_text); label.setObjectName("workspaceStatLabel")
        value = QLabel("—"); value.setObjectName("workspaceStatValue"); value.setAlignment(Qt.AlignmentFlag.AlignCenter)
        box.addWidget(label, alignment=Qt.AlignmentFlag.AlignCenter); box.addWidget(value, alignment=Qt.AlignmentFlag.AlignCenter)
        layout.addLayout(box, 1); return value

    def _push_editor(self, *args):
        if self._syncing:
            return
        page = self.wave_page
        page._loading = True
        try:
            if self.channel_combo.currentData() is not None:
                page.channel_combo.setCurrentIndex(page.channel_combo.findData(self.channel_combo.currentData()))
            page.template_combo.setCurrentText(self.template_combo.currentText())
            page.min_spin.setValue(self.min_spin.value())
            page.max_spin.setValue(self.max_spin.value())
            page.increment_spin.setValue(max(1, self.increment_spin.value()))
            page.duration_spin.setValue(max(1, self.duration_spin.value()))
            page.cycles_spin.setValue(max(1, self.cycles_spin.value()))
            page.frequency_edit.setValue(max(1, self.frequency_spin.value()))
        finally:
            page._loading = False
        page._editor_changed()
        self.refresh_from_backend(force=True)

    def _saved_selected(self, index):
        if self._syncing or index < 0:
            return
        waveform_id = self.saved_combo.itemData(index)
        if not waveform_id:
            return
        definition = self.wave_page.library.get(str(waveform_id))
        if definition is not None:
            self.wave_page._load_definition(definition)
            self.refresh_from_backend(force=True)

    def refresh_from_backend(self, force=False):
        page = self.wave_page
        self._syncing = True
        try:
            page.library.load()
            current_id = getattr(page, "_current_saved_id", None)
            ids = [self.saved_combo.itemData(i) for i in range(self.saved_combo.count())]
            library_ids = [item.id for item in page.library.items]
            if ids != library_ids:
                self.saved_combo.clear()
                if not page.library.items:
                    self.saved_combo.addItem("No saved waveforms", None)
                else:
                    for item in page.library.items:
                        self.saved_combo.addItem(item.name, item.id)
            if current_id:
                index = self.saved_combo.findData(current_id)
                if index >= 0:
                    self.saved_combo.setCurrentIndex(index)

            channels = [page.channel_combo.itemData(i) for i in range(page.channel_combo.count())]
            current_channels = [self.channel_combo.itemData(i) for i in range(self.channel_combo.count())]
            if current_channels != channels:
                self.channel_combo.clear()
                for i in range(page.channel_combo.count()):
                    self.channel_combo.addItem(page.channel_combo.itemText(i), page.channel_combo.itemData(i))
            if page.channel_combo.currentData() is not None:
                idx = self.channel_combo.findData(page.channel_combo.currentData())
                if idx >= 0: self.channel_combo.setCurrentIndex(idx)

            self.template_combo.setCurrentText(page.template_combo.currentText())
            for compact, source in (
                (self.min_spin, page.min_spin),
                (self.max_spin, page.max_spin),
                (self.increment_spin, page.increment_spin),
                (self.duration_spin, page.duration_spin),
                (self.cycles_spin, page.cycles_spin),
                (self.frequency_spin, page.frequency_edit),
            ):
                compact.setRange(int(source.minimum()), int(source.maximum()))
            self.min_spin.setValue(page.min_spin.value()); self.max_spin.setValue(page.max_spin.value())
            self.increment_spin.setValue(page.increment_spin.value()); self.duration_spin.setValue(page.duration_spin.value())
            self.cycles_spin.setValue(page.cycles_spin.value()); self.frequency_spin.setValue(page.frequency_edit.value())

            definition = page._definition_from_editor()
            channel = page._current_channel()
            compatibility = check_compatibility(definition, channel)
            editor_valid = bool(compatibility.valid)
            editor_reason = compatibility.reason
            try:
                steps = generate_steps(definition, channel=channel)
                stats = waveform_stats(definition, channel=channel)
                self.preview.set_steps(steps, min(step.amplitude_vpp for step in steps), max(step.amplitude_vpp for step in steps))
                self.total_steps.setText(str(stats.total_steps))
                self.cycle_duration.setText(f"{stats.cycle_duration_ms / 1000.0:.1f} s")
                self.wave_frequency.setText(f"{stats.wave_frequency_hz:.2f} Hz")
            except (ValueError, TypeError) as exc:
                editor_valid = False
                editor_reason = str(exc)
                self.preview.clear_preview(); self.total_steps.setText("—"); self.cycle_duration.setText("—"); self.wave_frequency.setText("—")

            owner = page._current_ownership()
            running = bool(owner is not None and owner.kind == "waveform")
            manual_busy = bool(owner is not None and owner.kind == "manual")

            # Mirror the full Wave page's actual connection check. Keeping an
            # active_board object around after serial disconnect must not leave
            # the compact Test button looking runnable.
            connection = page.active_board.get("connection") if page.active_board else None
            board_ready = bool(connection is not None and getattr(connection, "is_open", True))

            service = page._wave_service()
            wave_state = service.state(channel) if service is not None else None

            # Keep the compact banner visually identical, but give it the same
            # state-dependent explanation as the full Wave tab.
            if not editor_valid:
                banner_text = editor_reason or "Waveform settings are invalid."
                banner_state = "invalid"
            elif manual_busy:
                banner_text = (
                    f"CH{channel} is currently in use by manual pump control. "
                    "Turn the pump off before starting a waveform."
                )
                banner_state = "invalid"
            elif running:
                label = owner.label or (wave_state.waveform_name if wave_state else "waveform")
                suffix = f" · {wave_state.message}" if wave_state and wave_state.message else ""
                banner_text = f'CH{channel} is controlled by waveform "{label}"{suffix}'
                banner_state = "invalid"
            elif not board_ready:
                reason = compatibility.reason or f"Compatible with CH{channel}"
                banner_text = f"{reason} · Connect/select a board to test"
                banner_state = "neutral"
            else:
                banner_text = compatibility.reason or f"Compatible with CH{channel}"
                banner_state = "valid"

            self.compatibility.setText(banner_text)
            self.compatibility.setProperty("state", banner_state)
            self.compatibility.style().unpolish(self.compatibility)
            self.compatibility.style().polish(self.compatibility)

            # Full Wave-page parity: while the selected channel is Wave-owned,
            # freeze all draft-defining controls except Driver frequency. That
            # prevents Workspace from switching the full Wave tab away from the
            # running channel or mutating the active draft mid-run.
            editor_controls = (
                self.channel_combo,
                self.template_combo,
                self.min_spin,
                self.max_spin,
                self.increment_spin,
                self.duration_spin,
                self.cycles_spin,
            )
            for control in editor_controls:
                control.setEnabled(not running)
            self.saved_combo.setEnabled((not running) and bool(page.library.items))
            # Driver frequency remains live-editable during a
            # running waveform, matching the full Wave page.
            self.frequency_spin.setEnabled(True)

            can_test = editor_valid and compatibility.valid and board_ready and not running and not manual_busy
            self.test_button.setEnabled(can_test)
            self.test_button.setText("Pump in use" if manual_busy else "Test Waveform")
            self.test_button.setProperty("blockedByPump", manual_busy)
            self.test_button.style().unpolish(self.test_button)
            self.test_button.style().polish(self.test_button)
            self.stop_button.setEnabled(running)
        finally:
            self._syncing = False

    def shutdown(self):
        self._timer.stop()


class CameraWorkspacePanel(QFrame):
    """Compact mirror of the existing CameraPage runtime; never opens camera #2."""

    def __init__(self, camera_page, parent=None):
        super().__init__(parent)
        self.setObjectName("workspaceCameraPanel")
        self.camera_page = camera_page
        self._syncing = False

        # This compact mirror must be allowed to shrink to a true half-width
        # Workspace column. The camera toolbar has several controls in one row,
        # so its natural size hint is wider than the 50/50 target at the normal
        # application window size. Ignoring that horizontal size hint lets the
        # splitter own the width while the controls/layout adapt inside it.
        self.setMinimumWidth(0)
        self.setSizePolicy(QSizePolicy.Policy.Ignored, QSizePolicy.Policy.Expanding)

        root = QVBoxLayout(self)
        root.setContentsMargins(16, 8, 16, 14)
        root.setSpacing(10)
        toolbar = QHBoxLayout(); toolbar.setSpacing(8)
        self.device_combo = QComboBox(); self.device_combo.setObjectName("workspaceCombo"); self.device_combo.setFixedHeight(40); self.device_combo.setPlaceholderText("Select camera device")
        self.device_combo.setMinimumWidth(0)
        self.device_combo.setSizePolicy(QSizePolicy.Policy.Ignored, QSizePolicy.Policy.Fixed)
        self.device_combo.currentIndexChanged.connect(self._device_changed)
        self.search = QPushButton("Search"); self.search.setObjectName("workspaceOutlineAction")
        self.open_folder = QPushButton("Open Capture Folder"); self.open_folder.setObjectName("workspaceOutlineAction")
        self.start_camera = QPushButton("Start Camera"); self.start_camera.setObjectName("workspacePrimaryAction")
        for button in (self.search, self.open_folder, self.start_camera): button.setFixedHeight(40)
        self.search.clicked.connect(self.camera_page.refresh_devices)
        self.open_folder.clicked.connect(self.camera_page.open_capture_folder)
        self.start_camera.clicked.connect(self.camera_page.toggle_camera)
        toolbar.addWidget(self.device_combo, 1); toolbar.addWidget(self.search); toolbar.addWidget(self.open_folder); toolbar.addWidget(self.start_camera)
        root.addLayout(toolbar)

        self.preview = QLabel("Camera is stopped")
        self.preview.setObjectName("workspaceCameraPreview")
        self.preview.setAlignment(Qt.AlignmentFlag.AlignCenter)
        self.preview.setMinimumHeight(210)
        root.addWidget(self.preview, 1)

        actions = QHBoxLayout(); actions.setSpacing(10)
        self.capture = QPushButton("Capture"); self.record = QPushButton("Record"); self.stop = QPushButton("Stop")
        for button in (self.capture, self.record, self.stop):
            button.setObjectName("workspaceCameraAction"); button.setFixedHeight(42)
        self.capture.clicked.connect(self.camera_page.capture_photo)
        self.record.clicked.connect(self.camera_page.toggle_recording)
        self.stop.clicked.connect(self.camera_page.stop_camera)
        actions.addWidget(self.capture, 1); actions.addWidget(self.record, 1); actions.addWidget(self.stop, 1)
        root.addLayout(actions)

        controls = QFrame(); controls.setObjectName("workspaceCameraControls")
        controls_layout = QGridLayout(controls); controls_layout.setContentsMargins(14, 10, 14, 10); controls_layout.setHorizontalSpacing(12); controls_layout.setVerticalSpacing(8)
        controls_title = QLabel("Camera Controls"); controls_title.setObjectName("workspaceSubTitle")
        controls_layout.addWidget(controls_title, 0, 0, 1, 4)
        self.resolution = QComboBox(); self.resolution.setObjectName("workspaceCombo"); self.resolution.setFixedHeight(36)
        self.fps = QComboBox(); self.fps.setObjectName("workspaceCombo"); self.fps.setFixedHeight(36)
        self.resolution.currentTextChanged.connect(self._resolution_changed)
        self.fps.currentTextChanged.connect(self._fps_changed)
        controls_layout.addWidget(self._label("Resolution"), 1, 0); controls_layout.addWidget(self.resolution, 1, 1)
        controls_layout.addWidget(self._label("FPS"), 2, 0); controls_layout.addWidget(self.fps, 2, 1)

        self.auto_exposure = CameraSwitch(True); self.exposure = CameraSlider(Qt.Orientation.Horizontal); self.exposure.setRange(0, 100)
        self.brightness = CameraSlider(Qt.Orientation.Horizontal); self.brightness.setRange(0, 100)
        self.led = CameraSwitch(False)
        self.auto_exposure.toggled.connect(self._auto_changed); self.exposure.valueChanged.connect(self._exposure_changed)
        self.brightness.valueChanged.connect(self._brightness_changed); self.led.toggled.connect(self._led_changed)
        controls_layout.addWidget(self._label("Exposure"), 1, 2)
        exposure_box = QHBoxLayout(); exposure_box.setContentsMargins(0, 0, 0, 0); exposure_box.setSpacing(8)
        exposure_box.addWidget(self.exposure, 1); exposure_box.addWidget(QLabel("Auto")); exposure_box.addWidget(self.auto_exposure)
        controls_layout.addLayout(exposure_box, 1, 3)
        controls_layout.addWidget(self._label("Brightness"), 2, 2); controls_layout.addWidget(self.brightness, 2, 3)
        controls_layout.addWidget(self._label("LED"), 3, 2); controls_layout.addWidget(self.led, 3, 3, alignment=Qt.AlignmentFlag.AlignRight)
        note = QLabel("Focus & magnification: Manual"); note.setObjectName("workspaceMutedLabel")
        controls_layout.addWidget(note, 3, 0, 1, 2)
        root.addWidget(controls)

        self._timer = QTimer(self); self._timer.setInterval(110); self._timer.timeout.connect(self.refresh_from_backend); self._timer.start()
        self.refresh_from_backend()

    @staticmethod
    def _label(text):
        label = QLabel(text); label.setObjectName("workspaceFieldLabel"); return label

    def _device_changed(self, index):
        if self._syncing or index < 0: return
        source_index = self.device_combo.itemData(index)
        target = self.camera_page.device_combo.findData(source_index)
        if target >= 0: self.camera_page.device_combo.setCurrentIndex(target)

    def _resolution_changed(self, text):
        if self._syncing: return
        index = self.camera_page.resolution_combo.findText(text)
        if index >= 0: self.camera_page.resolution_combo.setCurrentIndex(index)

    def _fps_changed(self, text):
        if self._syncing: return
        index = self.camera_page.fps_combo.findText(text)
        if index >= 0: self.camera_page.fps_combo.setCurrentIndex(index)

    def _auto_changed(self, checked):
        if not self._syncing: self.camera_page.auto_exposure.setChecked(checked)

    def _exposure_changed(self, value):
        if not self._syncing: self.camera_page.exposure_slider.setValue(value)

    def _brightness_changed(self, value):
        if not self._syncing: self.camera_page.brightness_slider.setValue(value)

    def _led_changed(self, checked):
        if not self._syncing: self.camera_page.led_switch.setChecked(checked)

    @staticmethod
    def _combo_signature(combo):
        return tuple((combo.itemText(i), combo.itemData(i)) for i in range(combo.count()))

    def _sync_combo(self, target, source):
        target.blockSignals(True); target.clear()
        for i in range(source.count()): target.addItem(source.itemText(i), source.itemData(i))
        if source.currentIndex() >= 0: target.setCurrentIndex(source.currentIndex())
        target.setEnabled(source.isEnabled())
        target.blockSignals(False)

    def refresh_from_backend(self, force=False):
        page = self.camera_page
        self._syncing = True
        try:
            if force or self._combo_signature(self.device_combo) != self._combo_signature(page.device_combo):
                self._sync_combo(self.device_combo, page.device_combo)
            elif page.device_combo.currentIndex() != self.device_combo.currentIndex():
                self.device_combo.setCurrentIndex(page.device_combo.currentIndex())
            if force or self._combo_signature(self.resolution) != self._combo_signature(page.resolution_combo):
                self._sync_combo(self.resolution, page.resolution_combo)
            if force or self._combo_signature(self.fps) != self._combo_signature(page.fps_combo):
                self._sync_combo(self.fps, page.fps_combo)

            self.start_camera.setText("Stop Camera" if page.camera.is_open else "Start Camera")
            self.start_camera.setEnabled(page.start_button.isEnabled())
            self.search.setEnabled(page.status.isEnabled())
            self.capture.setEnabled(page.camera.is_open); self.record.setEnabled(page.camera.is_open); self.stop.setEnabled(page.camera.is_open)
            self.record.setText("Recording…" if page.recording else "Record")

            for target, source in (
                (self.auto_exposure, page.auto_exposure), (self.led, page.led_switch),
            ):
                blocked = target.blockSignals(True); target.setChecked(source.isChecked()); target.setEnabled(source.isEnabled()); target.blockSignals(blocked)
            for target, source in (
                (self.exposure, page.exposure_slider), (self.brightness, page.brightness_slider),
            ):
                blocked = target.blockSignals(True); target.setValue(source.value()); target.setEnabled(source.isEnabled()); target.blockSignals(blocked)

            # Read the frame from the shared camera runtime, not from the
            # full Camera page's last rendered frame. Workspace therefore works
            # immediately even while the Camera page itself has never been shown.
            frame = page.camera.latest_frame()
            pixmap = camera_frame_pixmap(frame, self.preview.size()) if page.camera.is_open else None
            if pixmap is not None:
                self.preview.setPixmap(pixmap)
            else:
                self.preview.clear(); self.preview.setText("Camera is stopped" if not page.camera.is_open else "Waiting for camera frame…")
        finally:
            self._syncing = False

    def shutdown(self):
        self._timer.stop()


class WorkspacePage(QWidget):
    title_changed = pyqtSignal(str)

    def __init__(self, *, pumps_page, sensors_page, wave_page, camera_page, active_board_provider, parent=None):
        super().__init__(parent)
        self.setObjectName("workspacePage")
        self.controller = WorkspaceController()
        self.sensor_hub = SensorDataHub()
        self.pumps_page = pumps_page
        self.sensors_page = sensors_page
        self.wave_page = wave_page
        self.camera_page = camera_page
        self.active_board_provider = active_board_provider
        self.current_title = "Workspace"
        self._active_panels = []
        self._builder_visible = True
        self._rendered_key = None

        self.root = QVBoxLayout(self)
        self.root.setContentsMargins(32, 4, 32, 22)
        self.root.setSpacing(12)
        self._build_builder()
        self._active_container = QWidget()
        self._active_container.setObjectName("workspaceActiveContainer")
        self._active_layout = QVBoxLayout(self._active_container)
        self._active_layout.setContentsMargins(0, 0, 0, 0)
        self._active_layout.setSpacing(10)
        self._active_container.hide()
        self.root.addWidget(self._active_container, 1)
        self.show_builder()

    def _build_builder(self):
        self.builder = QWidget(); self.builder.setObjectName("workspaceBuilder")
        layout = QVBoxLayout(self.builder); layout.setContentsMargins(0, 0, 0, 0); layout.setSpacing(12)
        subtitle = QLabel("Combine two tools for one experiment workflow.")
        subtitle.setObjectName("workspaceSubtitle")
        layout.addWidget(subtitle)

        controls = QHBoxLayout(); controls.setSpacing(10)
        preset_label = QLabel("Preset"); preset_label.setObjectName("workspacePresetLabel")
        self.preset_combo = QComboBox(); self.preset_combo.setObjectName("workspacePresetCombo"); self.preset_combo.setFixedSize(240, 44)
        for preset in WorkspacePreset:
            self.preset_combo.addItem(PRESET_LABELS[preset], preset.value)
        self.preset_combo.currentIndexChanged.connect(self._preset_selected)
        self.swap_button = QPushButton("Swap"); self.swap_button.setObjectName("workspaceToolbarButton"); self.swap_button.setFixedSize(120, 44)
        self.clear_button = QPushButton("Clear"); self.clear_button.setObjectName("workspaceToolbarButton"); self.clear_button.setFixedSize(120, 44)
        self.swap_button.clicked.connect(self._swap); self.clear_button.clicked.connect(self._clear)
        controls.addWidget(preset_label); controls.addWidget(self.preset_combo); controls.addWidget(self.swap_button); controls.addWidget(self.clear_button); controls.addStretch()
        layout.addLayout(controls)

        slots = QHBoxLayout(); slots.setSpacing(16)
        self.left_slot = WorkspaceSlot("left"); self.right_slot = WorkspaceSlot("right")
        self.left_slot.clicked.connect(
            lambda: self._choose_panel("left", self.left_slot)
        )
        self.right_slot.clicked.connect(
            lambda: self._choose_panel("right", self.right_slot)
        )
        self.left_slot.panel_selected.connect(
            lambda panel: self._panel_chosen("left", panel)
        )
        self.right_slot.panel_selected.connect(
            lambda panel: self._panel_chosen("right", panel)
        )
        slots.addWidget(self.left_slot, 1); slots.addWidget(self.right_slot, 1)
        layout.addLayout(slots, 1)

        persist = QLabel("Your workspace stays open when you switch pages.")
        persist.setObjectName("workspacePersistenceHint")
        layout.addWidget(persist)
        self.root.addWidget(self.builder, 1)

    def _choose_panel(self, side, slot):
        """Open the chooser *inside* the affected Workspace slot."""
        state = self.controller.state
        unavailable = state.right if side == "left" else state.left
        slot.show_chooser(unavailable)

    def _panel_chosen(self, side, panel):
        try:
            state = self.controller.set_panel(side, panel)
        except ValueError:
            return
        self._sync_builder_controls()
        if state.left is not None or state.right is not None:
            self.show_active(force=True)

    def _preset_selected(self, index):
        value = self.preset_combo.itemData(index)
        if value is None:
            return
        preset = WorkspacePreset(value)
        if preset is WorkspacePreset.CUSTOM:
            self.controller.apply_preset(preset)
            self._sync_builder_controls()
            return
        self.activate_preset(preset)

    def activate_preset(self, preset):
        self.controller.apply_preset(preset)
        self.show_active(force=True)

    def _swap(self):
        state = self.controller.state
        if state.left is None and state.right is None:
            return
        self.controller.swap()
        if (self.controller.state.left is not None or self.controller.state.right is not None) and not self._builder_visible:
            self.show_active(force=True)
        else:
            self._sync_builder_controls()

    def _clear(self):
        self.controller.clear()
        self.show_builder(force=True)

    def _remove_panel(self, side):
        try:
            self.controller.set_panel(side, None)
        except ValueError:
            return
        state = self.controller.state
        if state.left is None and state.right is None:
            self.show_builder(force=True)
        else:
            self.show_active(force=True)

    def _sync_builder_controls(self):
        state = self.controller.state
        blocked = self.preset_combo.blockSignals(True)
        idx = self.preset_combo.findData(state.preset.value)
        self.preset_combo.setCurrentIndex(max(0, idx))
        self.preset_combo.blockSignals(blocked)
        self.left_slot.set_panel(state.left)
        self.right_slot.set_panel(state.right)
        self.swap_button.setEnabled(state.left is not None or state.right is not None)
        self.clear_button.setEnabled(state.left is not None or state.right is not None)

    def show_builder(self, force=False):
        # Returning to the Workspace tab must not reset/rebuild a completed
        # workspace. QStackedWidget already keeps the page alive; preserve it.
        state = self.controller.state
        if (state.left is not None or state.right is not None) and not force:
            if self._builder_visible:
                self.show_active()
            else:
                self.current_title = PRESET_TITLES.get(state.preset, "Workspace")
                self.title_changed.emit(self.current_title)
            return

        self._destroy_active_panels()
        self._builder_visible = True
        self.builder.show(); self._active_container.hide()
        self.current_title = "Workspace"
        self.title_changed.emit(self.current_title)
        self._sync_builder_controls()

    def _active_toolbar(self, state):
        bar = QFrame()
        bar.setObjectName("workspaceActiveToolbar")
        row = QHBoxLayout(bar)
        row.setContentsMargins(0, 0, 0, 0)
        row.setSpacing(10)

        if state.preset in (WorkspacePreset.CUSTOM, WorkspacePreset.CONTROL):
            label = QLabel("Preset"); label.setObjectName("workspacePresetLabel")
            combo = QComboBox(); combo.setObjectName("workspacePresetCombo"); combo.setFixedSize(240, 40)
            for preset in WorkspacePreset:
                combo.addItem(PRESET_LABELS[preset], preset.value)
            combo.setCurrentIndex(max(0, combo.findData(state.preset.value)))
            combo.currentIndexChanged.connect(lambda i, c=combo: self._active_preset_changed(c.itemData(i)))
            swap = QPushButton("Swap"); swap.setObjectName("workspaceToolbarButton"); swap.setFixedSize(108, 40); swap.clicked.connect(self._swap)
            clear = QPushButton("Clear"); clear.setObjectName("workspaceToolbarButton"); clear.setFixedSize(108, 40); clear.clicked.connect(self._clear)
            row.addWidget(label); row.addWidget(combo); row.addWidget(swap); row.addWidget(clear)
        else:
            subtitle_text = {
                WorkspacePreset.WAVE_TEST: "Design and test waveforms while monitoring live sensor data.",
                WorkspacePreset.OBSERVATION: "Observe the microscope while monitoring live sensor data.",
            }.get(state.preset, "")
            subtitle = QLabel(subtitle_text); subtitle.setObjectName("workspaceActiveSubtitle")
            row.addWidget(subtitle)
        row.addStretch()
        hint = QLabel("Workspace stays open when you switch pages.")
        hint.setObjectName("workspacePersistenceHint")
        row.addWidget(hint)
        return bar

    def _active_preset_changed(self, value):
        if value is None:
            return
        preset = WorkspacePreset(value)
        self.controller.apply_preset(preset)
        if self.controller.state.left is not None or self.controller.state.right is not None:
            self.show_active(force=True)
        else:
            self.show_builder(force=True)

    def show_active(self, force=False):
        state = self.controller.state
        if state.left is None and state.right is None:
            self.show_builder(force=True)
            return

        key = (
            state.preset.value,
            None if state.left is None else state.left.value,
            None if state.right is None else state.right.value,
        )
        if not force and not self._builder_visible and self._rendered_key == key:
            self.current_title = PRESET_TITLES.get(state.preset, "Workspace")
            self.title_changed.emit(self.current_title)
            return

        self._destroy_active_panels()
        self._builder_visible = False
        self.builder.hide(); self._active_container.show()
        self.current_title = PRESET_TITLES.get(state.preset, "Workspace")
        self.title_changed.emit(self.current_title)
        self._active_layout.addWidget(self._active_toolbar(state))

        splitter = QSplitter(Qt.Orientation.Horizontal)
        splitter.setObjectName("workspaceSplitter")
        self._active_panels = []

        def build_side(side, panel_type, unavailable):
            if panel_type is None:
                slot = WorkspaceSlot(side)
                slot.clicked.connect(
                    lambda s=side, target=slot: self._choose_panel(s, target)
                )
                slot.panel_selected.connect(
                    lambda panel, s=side: self._panel_chosen(s, panel)
                )
                return slot
            content = self._create_panel(panel_type)
            shell = WorkspacePanelShell(side, PANEL_TITLES[panel_type], content)
            shell.close_requested.connect(self._remove_panel)
            self._active_panels.append(content)
            return shell

        splitter.addWidget(build_side("left", state.left, state.right))
        splitter.addWidget(build_side("right", state.right, state.left))
        splitter.setChildrenCollapsible(False)
        splitter.setStretchFactor(0, 1); splitter.setStretchFactor(1, 1)
        # Start every freshly rendered Workspace at an even split, but keep the
        # divider interactive afterwards. This makes 50/50 the predictable
        # default without fighting a user's manual adjustment.
        splitter.setHandleWidth(5)
        splitter.setOpaqueResize(True)
        handle = splitter.handle(1)
        if handle is not None:
            handle.setEnabled(True)
            handle.setCursor(Qt.CursorShape.SplitHCursor)
        self._active_layout.addWidget(splitter, 1)
        self._rendered_key = key
        QTimer.singleShot(0, lambda s=splitter: self._apply_split_ratio(s, 0.5))

    def _create_panel(self, panel_type):
        if panel_type is PanelType.PUMPS:
            return PumpWorkspacePanel(self.active_board_provider, self.pumps_page)
        if panel_type is PanelType.SENSORS:
            return SensorWorkspacePanel(self.sensors_page, self.sensor_hub, self.active_board_provider)
        if panel_type is PanelType.WAVE:
            return WaveWorkspacePanel(self.wave_page)
        if panel_type is PanelType.CAMERA:
            return CameraWorkspacePanel(self.camera_page)
        raise ValueError(panel_type)

    def _apply_split_ratio(self, splitter, ratio=0.5):
        # Initial layout only. Once shown, QSplitter owns the ratio so dragging
        # the divider is never immediately overwritten by a resize callback.
        width = max(2, splitter.width() - splitter.handleWidth())
        ratio = max(0.0, min(1.0, float(ratio)))
        left = int(round(width * ratio))
        splitter.setSizes([left, width - left])

    def _destroy_active_panels(self):
        while self._active_layout.count():
            item = self._active_layout.takeAt(0)
            widget = item.widget()
            if widget is not None:
                if isinstance(widget, QSplitter):
                    for child_index in range(widget.count()):
                        child = widget.widget(child_index)
                        if hasattr(child, "shutdown"):
                            child.shutdown()
                widget.deleteLater()
        self._active_panels = []
        self._rendered_key = None

    def set_active_board(self, board):
        for panel in self._active_panels:
            if hasattr(panel, "refresh_from_backend"):
                panel.refresh_from_backend()

    def set_connected_boards(self, boards):
        self.sensor_hub.set_connected_boards(boards)

    def shutdown(self):
        self._destroy_active_panels()
        self.sensor_hub.shutdown()

