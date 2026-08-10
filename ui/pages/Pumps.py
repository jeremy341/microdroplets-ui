from PyQt6.QtCore import QRectF, QSize, Qt
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

        # The page fills the available area, but the pump controls have a
        # maximum useful width so they do not become excessively stretched.
        main_layout = QVBoxLayout(self)
        main_layout.setContentsMargins(28, 16, 28, 24)
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
        self.driver_container.setMaximumWidth(1120)
        self.driver_container.setSizePolicy(
            QSizePolicy.Policy.Expanding,
            QSizePolicy.Policy.Maximum,
        )

        self.drivers_layout = QGridLayout(self.driver_container)
        self.drivers_layout.setContentsMargins(0, 0, 0, 0)
        self.drivers_layout.setHorizontalSpacing(16)
        self.drivers_layout.setVerticalSpacing(28)
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
        parameter_card.setMinimumHeight(72)
        parameter_card.setSizePolicy(
            QSizePolicy.Policy.Expanding,
            QSizePolicy.Policy.Expanding,
        )

        parameter_layout = QVBoxLayout(parameter_card)
        parameter_layout.setContentsMargins(14, 10, 14, 10)
        parameter_layout.setSpacing(6)

        return parameter_card, parameter_layout

    @staticmethod
    def lift_spinbox(spinbox, offset=5):
        """Nudge the taller native spinbox upward within compact control rows."""

        slot = QWidget()
        slot.setSizePolicy(QSizePolicy.Policy.Fixed, QSizePolicy.Policy.Fixed)
        slot_layout = QVBoxLayout(slot)
        slot_layout.setContentsMargins(0, 0, 0, offset)
        slot_layout.setSpacing(0)
        slot_layout.addWidget(spinbox)
        return slot

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
        controls_layout.setSpacing(10)

        slider = QSlider(Qt.Orientation.Horizontal)
        slider.setObjectName("parameterSlider")
        slider.setRange(0, 500)
        slider.setValue(driver_data.get("frequency", 100))
        slider.setMinimumWidth(140)

        spinbox = QSpinBox()
        spinbox.setObjectName("parameterSpinBox")
        spinbox.setRange(0, 500)
        spinbox.setValue(driver_data.get("frequency", 100))
        spinbox.setSuffix(" Hz")
        spinbox.setFixedWidth(88)
        spinbox.setButtonSymbols(
            QAbstractSpinBox.ButtonSymbols.NoButtons
        )

        waveform = QComboBox()
        waveform.setObjectName("waveformBox")
        waveform.addItems([
            "Sinus",
            "Square",
            "Triangle",
        ])
        waveform.setCurrentText(
            driver_data.get("waveform", "Sinus")
        )
        waveform.setFixedWidth(86)

        slider.valueChanged.connect(spinbox.setValue)
        spinbox.valueChanged.connect(slider.setValue)

        spinbox.valueChanged.connect(
            lambda value,
            current_driver=driver_data:
            self.update_frequency(
                current_driver,
                value,
            )
        )

        waveform.currentTextChanged.connect(
            lambda waveform_name,
            current_driver=driver_data:
            self.update_waveform(
                current_driver,
                waveform_name,
            )
        )

        controls_layout.addWidget(slider, 1)
        controls_layout.addWidget(self.lift_spinbox(spinbox))
        controls_layout.addWidget(waveform)

        row_layout.addWidget(label)
        row_layout.addLayout(controls_layout)

        return parameter_card

    def create_amplitude_row(self, channel_data):
        parameter_card, row_layout = self.create_parameter_card()

        channel_number = channel_data["channel"]

        label = QLabel(
            f"Amplitude CH{channel_number}"
        )
        label.setObjectName("parameterLabel")

        controls_layout = QHBoxLayout()
        controls_layout.setSpacing(10)

        slider = QSlider(Qt.Orientation.Horizontal)
        slider.setObjectName("parameterSlider")
        slider.setRange(0, 250)
        slider.setValue(
            channel_data.get("amplitude", 100)
        )
        slider.setMinimumWidth(140)

        spinbox = QSpinBox()
        spinbox.setObjectName("parameterSpinBox")
        spinbox.setRange(0, 250)
        spinbox.setValue(
            channel_data.get("amplitude", 100)
        )
        spinbox.setSuffix(" V")
        spinbox.setFixedWidth(88)
        spinbox.setButtonSymbols(
            QAbstractSpinBox.ButtonSymbols.NoButtons
        )

        toggle = ToggleSwitch()
        toggle.setChecked(
            channel_data.get("enabled", False)
        )

        toggle_slot = QWidget()
        toggle_slot.setFixedWidth(86)

        toggle_slot_layout = QHBoxLayout(toggle_slot)
        toggle_slot_layout.setContentsMargins(0, 0, 0, 0)
        toggle_slot_layout.setSpacing(0)
        toggle_slot_layout.setAlignment(
            Qt.AlignmentFlag.AlignCenter
        )
        toggle_slot_layout.addWidget(toggle)

        slider.valueChanged.connect(spinbox.setValue)
        spinbox.valueChanged.connect(slider.setValue)

        spinbox.valueChanged.connect(
            lambda value,
            current_channel=channel_data:
            self.update_amplitude(
                current_channel,
                value,
            )
        )

        toggle.toggled.connect(
            lambda enabled,
            current_channel=channel_data:
            self.update_pump_enabled(
                current_channel,
                enabled,
            )
        )

        controls_layout.addWidget(slider, 1)
        controls_layout.addWidget(self.lift_spinbox(spinbox))
        controls_layout.addWidget(toggle_slot)

        row_layout.addWidget(label)
        row_layout.addLayout(controls_layout)

        return parameter_card

    def create_driver(self, driver_data):
        driver = QGroupBox(driver_data["name"])
        driver.setObjectName("driverCard")

        driver.setSizePolicy(
            QSizePolicy.Policy.Expanding,
            QSizePolicy.Policy.Maximum,
        )

        channel_count = len(driver_data["channels"])

        if channel_count >= 4:
            driver.setMinimumHeight(528)
        elif channel_count == 1:
            driver.setMinimumHeight(250)
        else:
            row_count = channel_count + 1
            estimated_height = (
                48
                + row_count * 82
                + max(0, row_count - 1) * 10
            )
            driver.setMinimumHeight(estimated_height)

        driver_layout = QVBoxLayout(driver)
        driver_layout.setContentsMargins(12, 18, 12, 12)
        driver_layout.setSpacing(10)

        frequency_row = self.create_frequency_row(
            driver_data
        )
        driver_layout.addWidget(frequency_row)

        for channel_data in driver_data["channels"]:
            amplitude_row = self.create_amplitude_row(
                channel_data
            )
            driver_layout.addWidget(amplitude_row)

        return driver

    def update_frequency(
        self,
        driver_data,
        value,
    ):
        if self.active_board is None:
            return

        driver_data["frequency"] = value

        print(
            f"{self.active_board['name']} - "
            f"driver {driver_data['driver_index']} "
            f"frequency: {value} Hz"
        )

    def update_waveform(
        self,
        driver_data,
        waveform,
    ):
        if self.active_board is None:
            return

        driver_data["waveform"] = waveform

        print(
            f"{self.active_board['name']} - "
            f"driver {driver_data['driver_index']} "
            f"waveform: {waveform}"
        )

    def update_amplitude(
        self,
        channel_data,
        value,
    ):
        if self.active_board is None:
            return

        channel_data["amplitude"] = value

        print(
            f"{self.active_board['name']} - "
            f"CH{channel_data['channel']} "
            f"amplitude: {value} V"
        )

    def update_pump_enabled(
        self,
        channel_data,
        enabled,
    ):
        if self.active_board is None:
            return

        channel_data["enabled"] = enabled

        state = "ON" if enabled else "OFF"

        print(
            f"{self.active_board['name']} - "
            f"CH{channel_data['channel']}: {state}"
        )

    def stop_all(self):
        if self.active_board is None:
            return

        for driver_data in self.active_board.get(
            "drivers",
            [],
        ):
            for channel_data in driver_data["channels"]:
                channel_data["enabled"] = False

        self.rebuild_driver_cards()

        print(
            f"Stopped all pumps on "
            f"{self.active_board['name']}"
        )
