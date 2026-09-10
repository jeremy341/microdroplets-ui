"""Pump-control page.

The page translates user controls into command builders from ``backend.protocol``
and sends them through the active ``MultiboardConnection``.  The driver-level
carrier waveform control here is distinct from saved amplitude-over-time waves.
See ``docs/DEVELOPER_GUIDE.md`` and ``docs/DEVELOPER_GUIDE.md``.
"""

from concurrent.futures import ThreadPoolExecutor

from PyQt6.QtCore import QTimer, QRectF, QSize, Qt
from PyQt6.QtGui import QColor, QPainter
from PyQt6.QtWidgets import (
    QAbstractSpinBox,
    QCheckBox,
    QComboBox,
    QFrame,
    QGridLayout,
    QGroupBox,
    QHBoxLayout,
    QLabel,
    QScrollArea,
    QSizePolicy,
    QSlider,
    QSpinBox,
    QVBoxLayout,
    QWidget,
)

from backend.protocol import (
    DRIVER_AMPLITUDE_LIMITS,
    DRIVER_FREQUENCY_LIMITS,
    WAVEFORM_CODES,
    driver_amplitude_command,
    driver_frequency_command,
    driver_waveform_command,
    pump_amplitude_command,
    pump_start_commands,
    pump_state_command,
)
from backend.pump_control import PumpOperationResult




class ToggleSwitch(QCheckBox):
    """Simple ON/OFF switch."""

    def __init__(self):
        super().__init__()

        self.setText("")
        self.setFixedSize(48, 26)
        self.setCursor(Qt.CursorShape.PointingHandCursor)
        self.toggled.connect(self.update)

    def hitButton(self, position):
        return self.rect().contains(position)

    def sizeHint(self):
        return QSize(48, 26)

    def paintEvent(self, event):
        painter = QPainter(self)
        painter.setRenderHint(QPainter.RenderHint.Antialiasing)

        track = QRectF(
            1,
            1,
            self.width() - 2,
            self.height() - 2,
        )

        if self.isChecked():
            track_color = QColor("#67BE98")
        else:
            track_color = QColor("#D9E4DF")

        painter.setPen(Qt.PenStyle.NoPen)
        painter.setBrush(track_color)

        radius = track.height() / 2
        painter.drawRoundedRect(track, radius, radius)

        knob_size = track.height() - 6

        if self.isChecked():
            knob_x = track.right() - knob_size - 3
        else:
            knob_x = track.left() + 3

        knob = QRectF(
            knob_x,
            track.top() + 3,
            knob_size,
            knob_size,
        )

        painter.setBrush(QColor("#FFFFFF"))
        painter.drawEllipse(knob)


class PumpsPage(QWidget):
    def __init__(self):
        super().__init__()

        self.setObjectName("pumpsPage")
        self.active_board = None
        self._executor = ThreadPoolExecutor(max_workers=4, thread_name_prefix="fluidic-pump")
        self._pending_operations = []
        self._runtime_timer = QTimer(self)
        self._runtime_timer.setInterval(75)
        self._runtime_timer.timeout.connect(self._poll_runtime)
        self._runtime_timer.start()

        # The page fills the available workspace so both driver columns align
        # with the shared desktop-page gutters used by the reference layout.
        main_layout = QVBoxLayout(self)
        main_layout.setContentsMargins(32, 16, 32, 24)
        main_layout.setSpacing(0)

        # Empty state
        self.empty_state = QWidget()
        self.empty_state.setObjectName("emptyState")

        empty_layout = QVBoxLayout(self.empty_state)
        empty_layout.setAlignment(Qt.AlignmentFlag.AlignCenter)

        empty_title = QLabel("No board selected")
        empty_title.setObjectName("emptyStateTitle")
        empty_title.setAlignment(Qt.AlignmentFlag.AlignCenter)

        empty_description = QLabel(
            "Connect or select a board to control its pump channels."
        )
        empty_description.setObjectName("emptyStateDescription")
        empty_description.setAlignment(Qt.AlignmentFlag.AlignCenter)

        empty_layout.addWidget(empty_title)
        empty_layout.addWidget(empty_description)

        # Scrollable content area. It stays centered on wide screens and
        # becomes scrollable when the window is too small.
        self.driver_scroll = QScrollArea()
        self.driver_scroll.setObjectName("driverScroll")
        self.driver_scroll.setWidgetResizable(True)
        self.driver_scroll.setFrameShape(QScrollArea.Shape.NoFrame)
        self.driver_scroll.setAlignment(
            Qt.AlignmentFlag.AlignTop
            | Qt.AlignmentFlag.AlignHCenter
        )
        self.driver_scroll.setHorizontalScrollBarPolicy(
            Qt.ScrollBarPolicy.ScrollBarAlwaysOff
        )

        self.driver_container = QWidget()
        self.driver_container.setObjectName("driverContainer")
        self.driver_container.setMaximumWidth(16777215)
        self.driver_container.setSizePolicy(
            QSizePolicy.Policy.Expanding,
            QSizePolicy.Policy.Maximum,
        )

        self.drivers_layout = QGridLayout(self.driver_container)
        self.drivers_layout.setContentsMargins(0, 0, 0, 0)
        self.drivers_layout.setHorizontalSpacing(24)
        self.drivers_layout.setVerticalSpacing(24)
        self.drivers_layout.setAlignment(Qt.AlignmentFlag.AlignTop)
        self.drivers_layout.setColumnStretch(0, 1)
        self.drivers_layout.setColumnStretch(1, 1)

        self.driver_scroll.setWidget(self.driver_container)

        main_layout.addWidget(self.empty_state, 1)
        main_layout.addWidget(self.driver_scroll, 1)

        self.update_page_state()

    def set_active_board(self, board):
        self.active_board = board
        self.rebuild_driver_cards()
        self.update_page_state()
        self._sync_runtime_states()

    def shutdown(self):
        self._runtime_timer.stop()
        self._executor.shutdown(wait=True, cancel_futures=True)

    def _submit_operation(self, function, callback):
        future = self._executor.submit(function)
        self._pending_operations.append((future, callback))

    def _poll_runtime(self):
        remaining = []
        for future, callback in self._pending_operations:
            if not future.done():
                remaining.append((future, callback))
                continue
            try:
                result = future.result()
            except Exception as exc:
                result = PumpOperationResult(False, message=str(exc), hardware_state="unknown")
            callback(result)
        self._pending_operations = remaining
        self._sync_runtime_states()

    def update_page_state(self):
        has_board = self.active_board is not None

        self.empty_state.setVisible(not has_board)
        self.driver_scroll.setVisible(has_board)

    def clear_driver_cards(self):
        while self.drivers_layout.count():
            item = self.drivers_layout.takeAt(0)
            widget = item.widget()

            if widget is not None:
                widget.deleteLater()

    def rebuild_driver_cards(self):
        self.clear_driver_cards()

        if self.active_board is None:
            return

        drivers = self.active_board.get("drivers", [])

        if not drivers:
            message = QLabel(
                "No pump drivers are available on this board."
            )
            message.setObjectName("emptyStateDescription")
            message.setAlignment(Qt.AlignmentFlag.AlignCenter)

            self.drivers_layout.addWidget(
                message,
                0,
                0,
                1,
                2,
            )
            return

        # Bartels-style arrangement:
        # a tall four-channel driver on the left and two compact drivers
        # stacked on the right.
        if len(drivers) == 3 and len(drivers[0]["channels"]) >= 4:
            large_driver = self.create_driver(drivers[0])
            upper_driver = self.create_driver(drivers[1])
            lower_driver = self.create_driver(drivers[2])

            self.drivers_layout.addWidget(
                large_driver,
                0,
                0,
                2,
                1,
            )
            self.drivers_layout.addWidget(
                upper_driver,
                0,
                1,
            )
            self.drivers_layout.addWidget(
                lower_driver,
                1,
                1,
            )
            return

        # Generic two-column fallback for other board configurations.
        for index, driver_data in enumerate(drivers):
            row = index // 2
            column = index % 2

            driver_card = self.create_driver(driver_data)
            self.drivers_layout.addWidget(
                driver_card,
                row,
                column,
            )

    @staticmethod
    def create_parameter_card():
        parameter_card = QFrame()
        parameter_card.setObjectName("parameterCard")
        parameter_card.setMinimumHeight(96)
        parameter_card.setSizePolicy(
            QSizePolicy.Policy.Expanding,
            QSizePolicy.Policy.Expanding,
        )

        parameter_layout = QVBoxLayout(parameter_card)
        parameter_layout.setContentsMargins(20, 14, 20, 14)
        parameter_layout.setSpacing(10)

        return parameter_card, parameter_layout

    def create_frequency_row(self, driver_data):
        parameter_card, row_layout = self.create_parameter_card()

        channels = driver_data["channels"]

        if len(channels) == 1:
            channel_number = channels[0]["channel"]
            label_text = f"Frequency CH{channel_number}"
        else:
            label_text = "Frequency"

        label = QLabel(label_text)
        label.setObjectName("parameterLabel")

        controls_layout = QHBoxLayout()
        controls_layout.setSpacing(14)
        controls_layout.setAlignment(Qt.AlignmentFlag.AlignVCenter)

        slider = QSlider(Qt.Orientation.Horizontal)
        slider.setObjectName("parameterSlider")
        driver_index = driver_data["driver_index"]
        minimum_frequency, maximum_frequency = DRIVER_FREQUENCY_LIMITS[driver_index]
        driver_data["frequency"] = max(
            minimum_frequency,
            min(maximum_frequency, driver_data.get("frequency", 100)),
        )
        slider.setRange(minimum_frequency, maximum_frequency)
        slider.setValue(driver_data.get("frequency", 100))
        slider.setMinimumWidth(140)

        spinbox = QSpinBox()
        spinbox.setObjectName("parameterSpinBox")
        spinbox.setRange(minimum_frequency, maximum_frequency)
        spinbox.setValue(driver_data.get("frequency", 100))
        spinbox.setSuffix(" Hz")
        spinbox.setFixedWidth(104)
        spinbox.setButtonSymbols(
            QAbstractSpinBox.ButtonSymbols.NoButtons
        )

        waveform = None
        if driver_index in (0, 1):
            waveform = QComboBox()
            waveform.setObjectName("waveformBox")
            waveform.addItems(list(WAVEFORM_CODES))
            waveform.setCurrentText(driver_data.get("waveform", "Sinus"))
            waveform.setFixedWidth(92)

        slider.valueChanged.connect(spinbox.setValue)
        spinbox.valueChanged.connect(slider.setValue)

        # Keep the two controls visually synchronized while dragging, but send
        # only once when the user commits the value.  Sending on every slider
        # tick can flood a 115200-baud serial connection.
        slider.sliderReleased.connect(
            lambda current_driver=driver_data, current_slider=slider:
            self.update_frequency(current_driver, current_slider.value())
        )
        spinbox.editingFinished.connect(
            lambda current_driver=driver_data, current_spinbox=spinbox:
            self.update_frequency(current_driver, current_spinbox.value())
        )

        if waveform is not None:
            waveform.currentTextChanged.connect(
                lambda waveform_name, current_driver=driver_data:
                self.update_waveform(current_driver, waveform_name)
            )

        # Frequency is a live driver-level setting: CH1-CH4 share F0 while CH5
        # and CH6 use independent F1/F2 domains.  Runtime ownership does not
        # disable these existing controls; the backend serializes live writes.
        driver_data["_frequency_value_controls"] = (slider, spinbox)
        driver_data["_waveform_control"] = waveform
        driver_data["_frequency_controls"] = tuple(
            control for control in (slider, spinbox, waveform) if control is not None
        )
        self._sync_driver_control_state(driver_data)

        controls_layout.addWidget(slider, 1)
        controls_layout.addWidget(spinbox)
        if waveform is not None:
            controls_layout.addWidget(waveform)

        row_layout.addWidget(label)
        row_layout.addLayout(controls_layout)

        return parameter_card

    def create_amplitude_row(self, driver_data, channel_data):
        parameter_card, row_layout = self.create_parameter_card()

        channel_number = channel_data["channel"]

        label = QLabel(
            f"Amplitude CH{channel_number}"
        )
        label.setObjectName("parameterLabel")

        controls_layout = QHBoxLayout()
        controls_layout.setSpacing(14)
        controls_layout.setAlignment(Qt.AlignmentFlag.AlignVCenter)

        slider = QSlider(Qt.Orientation.Horizontal)
        slider.setObjectName("parameterSlider")
        minimum_amplitude, maximum_amplitude = DRIVER_AMPLITUDE_LIMITS[
            driver_data["driver_index"]
        ]
        channel_data["amplitude"] = max(
            minimum_amplitude,
            min(maximum_amplitude, channel_data.get("amplitude", 100)),
        )
        slider.setRange(minimum_amplitude, maximum_amplitude)
        slider.setValue(max(
            minimum_amplitude,
            min(maximum_amplitude, channel_data.get("amplitude", 100)),
        ))
        slider.setMinimumWidth(140)

        spinbox = QSpinBox()
        spinbox.setObjectName("parameterSpinBox")
        spinbox.setRange(minimum_amplitude, maximum_amplitude)
        spinbox.setValue(max(
            minimum_amplitude,
            min(maximum_amplitude, channel_data.get("amplitude", 100)),
        ))
        spinbox.setSuffix(" Vpp")
        spinbox.setFixedWidth(104)
        spinbox.setButtonSymbols(
            QAbstractSpinBox.ButtonSymbols.NoButtons
        )

        toggle = ToggleSwitch()
        toggle.setChecked(
            channel_data.get("enabled", False)
        )

        toggle_slot = QWidget()
        toggle_slot.setFixedWidth(72)

        toggle_slot_layout = QHBoxLayout(toggle_slot)
        toggle_slot_layout.setContentsMargins(0, 0, 0, 0)
        toggle_slot_layout.setSpacing(0)
        toggle_slot_layout.setAlignment(
            Qt.AlignmentFlag.AlignCenter
        )
        toggle_slot_layout.addWidget(toggle)

        slider.valueChanged.connect(spinbox.setValue)
        spinbox.valueChanged.connect(slider.setValue)

        slider.sliderReleased.connect(
            lambda current_channel=channel_data, current_slider=slider:
            self.update_amplitude(current_channel, current_slider.value())
        )
        spinbox.editingFinished.connect(
            lambda current_channel=channel_data, current_spinbox=spinbox:
            self.update_amplitude(current_channel, current_spinbox.value())
        )

        toggle.toggled.connect(
            lambda enabled,
            current_channel=channel_data:
            self.update_pump_enabled(
                current_channel,
                enabled,
            )
        )

        # Keep the original Pump UI. Runtime ownership and ACK state are
        # tracked behind the widgets; no Wave-specific controls live here.
        channel_data["_amplitude_controls"] = (slider, spinbox)
        channel_data["_toggle_control"] = toggle
        channel_data["_operation_pending"] = False
        self._sync_channel_control_state(channel_data)

        controls_layout.addWidget(slider, 1)
        controls_layout.addWidget(spinbox)
        controls_layout.addWidget(toggle_slot)

        row_layout.addWidget(label)
        row_layout.addLayout(controls_layout)

        return parameter_card

    def create_driver(self, driver_data):
        # Driver cards remain visible and interactive.  The Multiboard
        # firmware's amplitude queries are not a presence check, so this UI
        # deliberately does not block cards based on them.
        driver = QGroupBox(driver_data["name"])
        driver.setObjectName("driverCard")

        driver.setSizePolicy(
            QSizePolicy.Policy.Expanding,
            QSizePolicy.Policy.Maximum,
        )

        channel_count = len(driver_data["channels"])

        if channel_count >= 4:
            driver.setMinimumHeight(650)
        elif channel_count == 1:
            driver.setMinimumHeight(306)
        else:
            row_count = channel_count + 1
            estimated_height = (
                48
                + row_count * 82
                + max(0, row_count - 1) * 10
            )
            driver.setMinimumHeight(estimated_height)

        driver_layout = QVBoxLayout(driver)
        driver_layout.setContentsMargins(18, 24, 18, 18)
        driver_layout.setSpacing(14)

        frequency_row = self.create_frequency_row(
            driver_data
        )
        driver_layout.addWidget(frequency_row)

        for channel_data in driver_data["channels"]:
            amplitude_row = self.create_amplitude_row(
                driver_data,
                channel_data
            )
            driver_layout.addWidget(amplitude_row)

        return driver

    def _ownership_for_channel(self, channel: int):
        if self.active_board is None:
            return None
        manager = self.active_board.get("channel_ownership")
        return manager.get(channel) if manager is not None else None

    def _sync_channel_control_state(self, channel_data):
        """Keep Pump ownership backend-only while preserving normal control affordances.

        A Wave-owned channel is protected by the command handlers below, not
        by extra Pump-page controls or colors. This keeps the Pump UI exactly
        focused on manual control.
        """

        controls = channel_data.get("_amplitude_controls")
        if controls is None:
            return

        pump_is_running = bool(channel_data.get("enabled", False))

        for control in controls:
            # Editable/staged values stay visible
            # while OFF. Any attempt to change a Wave-owned channel is rejected
            # in update_amplitude() before a serial command can be sent.
            control.setEnabled(True)
            control.setProperty("pumpStopped", not pump_is_running)
            control.style().unpolish(control)
            control.style().polish(control)

        toggle = channel_data.get("_toggle_control")
        if toggle is not None:
            toggle.setEnabled(True)
            toggle.setToolTip("")
            toggle.update()

    def _sync_driver_control_state(self, driver_data):
        """Keep frequency live while preserving carrier-shape ownership safety."""

        frequency_controls = driver_data.get("_frequency_value_controls")
        if frequency_controls is None:
            return

        channels = driver_data.get("channels", [])
        operation_pending = any(
            bool(channel.get("_operation_pending", False)) for channel in channels
        )
        runtime_active = any(
            bool(channel.get("enabled", False))
            or self._ownership_for_channel(channel["channel"]) is not None
            for channel in channels
        )

        # F0/F1/F2 are live hardware properties.  Only an in-flight transaction
        # briefly disables the widgets so two writes cannot race from this page.
        frequency_editable = not operation_pending
        for control in frequency_controls:
            control.setEnabled(frequency_editable)
            control.setToolTip(
                "" if frequency_editable else "Waiting for the current driver command."
            )

        # Carrier shape remains different: it is shared by Highdriver-capable
        # groups and is not part of live tuning. Lowdriver has no CS1 serial
        # command, so the existing control stays visible but disabled there.
        waveform = driver_data.get("_waveform_control")
        if waveform is not None:
            supports_waveform = bool(driver_data.get("supports_carrier_waveform", True))
            waveform_editable = supports_waveform and not operation_pending and not runtime_active
            waveform.setEnabled(waveform_editable)
            if not supports_waveform:
                waveform.setToolTip(
                    f"{driver_data.get('driver_display_name', 'This driver')} does not use "
                    "the Multiboard CS carrier-shape command."
                )
            elif runtime_active:
                waveform.setToolTip(
                    "Stop all pumps on this driver before changing carrier waveform."
                )
            else:
                waveform.setToolTip("")

    def _sync_driver_control_values(self, driver_data):
        """Mirror shared driver values without fighting an active edit.

        Workspace and the full Pumps page are two views over the same driver
        model.  The old runtime poll only synchronized enabled/disabled styles,
        which meant a value changed from Workspace could leave the full Pumps
        widgets stale indefinitely.  Pull the shared values into the widgets on
        every idle poll, but never overwrite a slider/spinbox the user is
        currently manipulating or a driver with an in-flight command.
        """
        channels = driver_data.get("channels", [])
        if any(bool(channel.get("_operation_pending", False)) for channel in channels):
            return

        controls = driver_data.get("_frequency_value_controls")
        if controls is not None:
            slider, spinbox = controls
            if not slider.isSliderDown() and not spinbox.hasFocus():
                value = int(driver_data.get("frequency", 100))
                slider.blockSignals(True); spinbox.blockSignals(True)
                slider.setValue(value); spinbox.setValue(value)
                slider.blockSignals(False); spinbox.blockSignals(False)

        waveform = driver_data.get("_waveform_control")
        if waveform is not None and not waveform.hasFocus():
            try:
                popup_open = waveform.view().isVisible()
            except Exception:
                popup_open = False
            if not popup_open:
                waveform.blockSignals(True)
                waveform.setCurrentText(driver_data.get("waveform", "Sinus"))
                waveform.blockSignals(False)

    def _sync_channel_control_values(self, channel_data):
        """Mirror amplitude/output state from the shared channel model."""
        if bool(channel_data.get("_operation_pending", False)):
            return

        controls = channel_data.get("_amplitude_controls")
        if controls is not None:
            slider, spinbox = controls
            if not slider.isSliderDown() and not spinbox.hasFocus():
                value = int(channel_data.get("amplitude", spinbox.value()))
                slider.blockSignals(True); spinbox.blockSignals(True)
                slider.setValue(value); spinbox.setValue(value)
                slider.blockSignals(False); spinbox.blockSignals(False)

        toggle = channel_data.get("_toggle_control")
        if toggle is not None:
            toggle.blockSignals(True)
            toggle.setChecked(bool(channel_data.get("enabled", False)))
            toggle.blockSignals(False)

    def refresh_from_shared_state(self):
        """Public sync hook used by secondary views such as Workspace."""
        self._sync_runtime_states()

    def _sync_runtime_states(self):
        """Synchronize the full Pumps tab with the shared pump runtime."""

        board = self.active_board
        if board is None:
            return
        wave_service = board.get("wave_execution")
        if wave_service is not None:
            wave_service.cleanup_finished()
        pump_service = board.get("pump_control")
        for driver in board.get("drivers", []):
            if pump_service is not None:
                current_frequency = pump_service.get_driver_frequency(driver["driver_index"])
                if int(driver.get("frequency", current_frequency)) != current_frequency:
                    driver["frequency"] = current_frequency

            self._sync_driver_control_values(driver)
            for channel_data in driver.get("channels", []):
                self._sync_channel_control_values(channel_data)
                self._sync_channel_control_state(channel_data)
            self._sync_driver_control_state(driver)

    def update_frequency(self, driver_data, value):
        if self.active_board is None:
            return
        if any(bool(ch.get("_operation_pending", False)) for ch in driver_data.get("channels", [])):
            self._restore_driver_controls(driver_data)
            return
        board = self.active_board
        service = board.get("pump_control")
        if service is None:
            return
        previous = driver_data.get("frequency", 100)
        for channel in driver_data.get("channels", []):
            channel["_operation_pending"] = True
        self._sync_driver_control_state(driver_data)
        self._submit_operation(
            lambda: service.set_driver_frequency(driver_data["driver_index"], int(value)),
            lambda success, d=driver_data, old=previous, new=int(value), b=board:
                self._driver_setting_result(b, d, "frequency", old, new, bool(success)),
        )

    def _restore_driver_controls(self, driver_data):
        controls = driver_data.get("_frequency_controls", ())
        if len(controls) >= 2:
            slider, spinbox = controls[:2]
            value = int(driver_data.get("frequency", 100))
            slider.blockSignals(True); spinbox.blockSignals(True)
            slider.setValue(value); spinbox.setValue(value)
            slider.blockSignals(False); spinbox.blockSignals(False)
        if len(controls) >= 3:
            waveform = controls[2]
            waveform.blockSignals(True)
            waveform.setCurrentText(driver_data.get("waveform", "Sinus"))
            waveform.blockSignals(False)

    def _driver_setting_result(self, board, driver_data, key, previous, new, success):
        if success:
            driver_data[key] = new
        else:
            driver_data[key] = previous
        for channel in driver_data.get("channels", []):
            channel["_operation_pending"] = False
        if board is self.active_board:
            self._restore_driver_controls(driver_data)
            self._sync_driver_control_state(driver_data)

    def update_waveform(self, driver_data, waveform):
        if self.active_board is None:
            return
        if any(bool(ch.get("_operation_pending", False)) for ch in driver_data.get("channels", [])):
            self._restore_driver_controls(driver_data)
            return
        if any(self._ownership_for_channel(ch["channel"]) is not None for ch in driver_data.get("channels", [])):
            self._restore_driver_controls(driver_data)
            return
        driver_index = driver_data["driver_index"]
        if not bool(driver_data.get("supports_carrier_waveform", driver_index in (0, 1))):
            self._restore_driver_controls(driver_data)
            return
        if driver_index not in (0, 1):
            return
        board = self.active_board
        service = board.get("pump_control")
        if service is None:
            return
        previous = driver_data.get("waveform", "Sinus")
        for channel in driver_data.get("channels", []):
            channel["_operation_pending"] = True
        self._sync_driver_control_state(driver_data)
        self._submit_operation(
            lambda: service.set_driver_waveform(driver_index, waveform),
            lambda success, d=driver_data, old=previous, new=waveform, b=board:
                self._driver_setting_result(b, d, "waveform", old, new, bool(success)),
        )

    def update_amplitude(self, channel_data, value):
        if self.active_board is None:
            return
        if bool(channel_data.get("_operation_pending", False)):
            controls = channel_data.get("_amplitude_controls")
            if controls is not None:
                slider, spinbox = controls
                current = int(channel_data.get("amplitude", value))
                slider.blockSignals(True); spinbox.blockSignals(True)
                slider.setValue(current); spinbox.setValue(current)
                slider.blockSignals(False); spinbox.blockSignals(False)
            return
        channel = int(channel_data["channel"])
        owner = self._ownership_for_channel(channel)
        if owner is not None and owner.kind == "waveform":
            # Pumps has no Wave UI. Reject the manual edit in the backend and
            # restore the existing staged manual value so the widget never
            # suggests that a serial command was accepted.
            controls = channel_data.get("_amplitude_controls")
            if controls is not None:
                slider, spinbox = controls
                current = int(channel_data.get("amplitude", value))
                slider.blockSignals(True); spinbox.blockSignals(True)
                slider.setValue(current); spinbox.setValue(current)
                slider.blockSignals(False); spinbox.blockSignals(False)
            return
        if owner is None and not channel_data.get("enabled", False):
            channel_data["amplitude"] = int(value)
            return
        if owner is None or owner.kind != "manual":
            return
        driver_data = self.driver_for_channel(channel)
        service = self.active_board.get("pump_control")
        if driver_data is None or service is None:
            return
        previous = int(channel_data.get("amplitude", value))
        channel_data["_operation_pending"] = True
        self._sync_channel_control_state(channel_data)
        board = self.active_board
        self._submit_operation(
            lambda: service.set_manual_amplitude(driver_data["driver_index"], channel, int(value)),
            lambda result, c=channel_data, old=previous, new=int(value), b=board:
                self._amplitude_result(b, c, old, new, result),
        )

    def _amplitude_result(self, board, channel_data, previous, desired, result):
        channel_data["_operation_pending"] = False
        if result.success:
            channel_data["amplitude"] = desired
        else:
            channel_data["amplitude"] = previous
            if result.hardware_state == "unknown":
                channel_data["hardware_state"] = "unknown"
        if board is self.active_board:
            controls = channel_data.get("_amplitude_controls")
            if controls is not None:
                slider, spinbox = controls
                slider.blockSignals(True); spinbox.blockSignals(True)
                slider.setValue(channel_data["amplitude"]); spinbox.setValue(channel_data["amplitude"])
                slider.blockSignals(False); spinbox.blockSignals(False)
            self._sync_channel_control_state(channel_data)

    def update_pump_enabled(self, channel_data, enabled):
        if self.active_board is None:
            return
        if bool(channel_data.get("_operation_pending", False)):
            toggle = channel_data.get("_toggle_control")
            if toggle is not None:
                toggle.blockSignals(True)
                toggle.setChecked(bool(channel_data.get("enabled", False)))
                toggle.blockSignals(False)
            return
        channel_number = int(channel_data["channel"])
        owner = self._ownership_for_channel(channel_number)
        previous = bool(channel_data.get("enabled", False))
        toggle = channel_data.get("_toggle_control")

        # A Wave owns this output until it is stopped from the Wave controls.
        if owner is not None and owner.kind == "waveform":
            if toggle is not None:
                toggle.blockSignals(True); toggle.setChecked(previous); toggle.blockSignals(False)
            self._sync_channel_control_state(channel_data)
            return

        if toggle is not None:
            toggle.blockSignals(True)
            toggle.setChecked(previous)
            toggle.blockSignals(False)

        board = self.active_board
        service = board.get("pump_control")
        driver_data = self.driver_for_channel(channel_number)
        if service is None or driver_data is None:
            return

        channel_data["_operation_pending"] = True
        channel_data["_previous_enabled"] = previous
        self._sync_channel_control_state(channel_data)

        if enabled:
            # Keep this transaction visible here for maintainers and the static
            # safety test: PumpControlService sends pump_start_commands through
            # connection.send_sequence with this per-channel rollback_command.
            commands = pump_start_commands(
                driver_data["driver_index"],
                driver_data.get("frequency", 100),
                driver_data.get("waveform", "Sinus"),
                channel_number,
                channel_data.get("amplitude", 100),
            )
            rollback_command = pump_state_command(channel_number, False)
            _ = (commands, rollback_command, getattr(board.get("connection"), "send_sequence", None))
            operation = lambda: service.start_manual(
                driver_data["driver_index"],
                driver_data.get("frequency", 100),
                driver_data.get("waveform", "Sinus"),
                channel_number,
                channel_data.get("amplitude", 100),
            )
        else:
            operation = lambda: service.stop_manual(channel_number)

        self._submit_operation(
            operation,
            lambda result, c=channel_data, desired=bool(enabled), b=board:
                self._pump_result(c, desired, result, b),
        )

    def _pump_result(self, channel_data, desired, result, board=None):
        """Apply UI/model state only after the backend transaction finishes."""
        channel_data["_operation_pending"] = False
        previous = bool(channel_data.pop("_previous_enabled", channel_data.get("enabled", False)))
        if result.success:
            channel_data["enabled"] = bool(desired)
            channel_data["hardware_state"] = "on" if desired else "off"
        else:
            if result.hardware_state == "unknown":
                channel_data["hardware_state"] = "unknown"
                # Treat an unconfirmed start/stop as potentially ON so the user
                # gets a deliberate OFF retry instead of a second owner.
                channel_data["enabled"] = True
            else:
                channel_data["enabled"] = previous

        if board is None or board is self.active_board:
            toggle = channel_data.get("_toggle_control")
            if toggle is not None:
                toggle.blockSignals(True)
                toggle.setChecked(bool(channel_data.get("enabled", False)))
                toggle.blockSignals(False)
            self._sync_channel_control_state(channel_data)
            driver_data = self.driver_for_channel(channel_data["channel"])
            if driver_data is not None:
                self._sync_driver_control_state(driver_data)

    def stop_all(self):
        if self.active_board is None:
            return
        board = self.active_board
        wave_service = board.get("wave_execution")
        if wave_service is not None:
            wave_service.stop_all(timeout=2.0)
        connection = board.get("connection")
        if connection is None:
            return
        result = connection.send_sequence(("POFF",), timeout=1.0)
        if not result.success:
            return
        ownership = board.get("channel_ownership")
        if ownership is not None:
            ownership.force_release_all()
        for driver_data in board.get("drivers", []):
            for channel_data in driver_data.get("channels", []):
                channel_data["enabled"] = False
                channel_data["hardware_state"] = "off"
        self._sync_runtime_states()

    def driver_for_channel(self, channel_number):
        if self.active_board is None:
            return None
        for driver_data in self.active_board.get("drivers", []):
            if any(
                channel["channel"] == channel_number
                for channel in driver_data.get("channels", [])
            ):
                return driver_data
        return None

    def send_command(self, command):
        """Send a pump command to the active physical Multiboard."""

        # Command validation/building happens in backend.protocol. Keep this
        # method as the transport boundary; see docs/DEVELOPER_GUIDE.md.
        if self.active_board is None:
            return False
        connection = self.active_board.get("connection")
        if connection is None:
            return False
        connection.send(command)
        return True
