"""Home/dashboard page for the currently selected Multiboard.

The application shell supplies active-board state through ``set_active_board``.
See ``docs/USER_GUIDE.md`` for the shared page contract.
"""

from PyQt6.QtCore import Qt
from PyQt6.QtWidgets import (
    QFrame,
    QGridLayout,
    QGroupBox,
    QHBoxLayout,
    QLabel,
    QSizePolicy,
    QTextEdit,
    QVBoxLayout,
    QWidget,
)

from ui.design_tokens import CARD_GAP, CARD_PADDING, PAGE_BOTTOM, PAGE_GUTTER, PAGE_TOP, SECTION_GAP


class HomePage(QWidget):
    def __init__(self):
        super().__init__()

        self.setObjectName("homePage")
        self.active_board = None

        # Main layout
        main_layout = QVBoxLayout(self)
        main_layout.setContentsMargins(PAGE_GUTTER, PAGE_TOP, PAGE_GUTTER, PAGE_BOTTOM)
        main_layout.setSpacing(18)

        # Empty state
        self.empty_state = QWidget()
        self.empty_state.setObjectName("emptyState")

        empty_state_layout = QVBoxLayout(
            self.empty_state
        )
        empty_state_layout.setContentsMargins(
            CARD_PADDING,
            CARD_PADDING,
            CARD_PADDING,
            CARD_PADDING,
        )
        empty_state_layout.setSpacing(8)
        empty_state_layout.setAlignment(
            Qt.AlignmentFlag.AlignCenter
        )

        empty_state_title = QLabel(
            "No device connected"
        )
        empty_state_title.setObjectName(
            "emptyStateTitle"
        )
        empty_state_title.setAlignment(
            Qt.AlignmentFlag.AlignCenter
        )

        empty_state_description = QLabel(
            "Use Connect in the top-right to add "
            "a multiboard."
        )
        empty_state_description.setObjectName(
            "emptyStateDescription"
        )
        empty_state_description.setAlignment(
            Qt.AlignmentFlag.AlignCenter
        )

        empty_state_layout.addWidget(
            empty_state_title
        )
        empty_state_layout.addWidget(
            empty_state_description
        )

        # Device card
        self.device_card = QGroupBox()
        self.device_card.setObjectName("deviceCard")
        self.device_card.setSizePolicy(
            QSizePolicy.Policy.Expanding,
            QSizePolicy.Policy.Fixed,
        )
        self.device_card.setMinimumHeight(206)
        self.device_card.setMaximumHeight(206)

        device_layout = QVBoxLayout(
            self.device_card
        )
        device_layout.setContentsMargins(28, 20, 28, 20)
        device_layout.setSpacing(14)

        self.device_name = QLabel("Multiboard")
        self.device_name.setObjectName("deviceName")

        device_information_layout = QGridLayout()
        device_information_layout.setContentsMargins(
            0,
            0,
            0,
            0,
        )
        device_information_layout.setHorizontalSpacing(CARD_GAP)
        device_information_layout.setVerticalSpacing(
            6
        )

        status_label = QLabel("Status")
        status_label.setObjectName(
            "informationLabel"
        )

        port_label = QLabel("COM port")
        port_label.setObjectName(
            "informationLabel"
        )

        firmware_label = QLabel("Firmware")
        firmware_label.setObjectName(
            "informationLabel"
        )

        pumps_label = QLabel("Pump drivers")
        pumps_label.setObjectName(
            "informationLabel"
        )

        valves_label = QLabel("Valve drivers")
        valves_label.setObjectName(
            "informationLabel"
        )

        sensors_label = QLabel("Sensors")
        sensors_label.setObjectName(
            "informationLabel"
        )

        self.device_status = QLabel()
        self.device_status.setObjectName("informationValue")
        self.device_status.setTextFormat(Qt.TextFormat.RichText)
        self._set_status_display(True)

        self.device_port = QLabel("—")
        self.device_port.setObjectName(
            "informationValue"
        )

        self.device_firmware = QLabel("—")
        self.device_firmware.setObjectName(
            "informationValue"
        )

        self.device_pumps = QLabel("—")
        self.device_pumps.setObjectName(
            "informationValue"
        )

        self.device_valves = QLabel("—")
        self.device_valves.setObjectName(
            "informationValue"
        )

        self.device_sensors = QLabel("—")
        self.device_sensors.setObjectName(
            "informationValue"
        )

        device_information_layout.addWidget(
            status_label,
            0,
            0,
        )
        device_information_layout.addWidget(
            self.device_status,
            0,
            1,
        )

        device_information_layout.addWidget(
            port_label,
            1,
            0,
        )
        device_information_layout.addWidget(
            self.device_port,
            1,
            1,
        )

        device_information_layout.addWidget(
            firmware_label,
            2,
            0,
        )
        device_information_layout.addWidget(
            self.device_firmware,
            2,
            1,
        )

        device_divider = QFrame()
        device_divider.setObjectName("deviceInfoDivider")
        device_divider.setFrameShape(QFrame.Shape.VLine)
        device_information_layout.addWidget(device_divider, 0, 2, 3, 1)

        device_information_layout.addWidget(pumps_label, 0, 3)
        device_information_layout.addWidget(self.device_pumps, 0, 4)
        device_information_layout.addWidget(valves_label, 1, 3)
        device_information_layout.addWidget(self.device_valves, 1, 4)
        device_information_layout.addWidget(sensors_label, 2, 3)
        device_information_layout.addWidget(self.device_sensors, 2, 4)

        device_information_layout.setColumnMinimumWidth(0, 110)
        device_information_layout.setColumnMinimumWidth(2, 34)
        device_information_layout.setColumnMinimumWidth(3, 110)
        device_information_layout.setColumnStretch(1, 1)
        device_information_layout.setColumnStretch(4, 1)

        device_layout.addWidget(self.device_name)
        device_layout.addLayout(
            device_information_layout
        )

        # Overview cards
        self.overview_container = QWidget()

        overview_layout = QHBoxLayout(
            self.overview_container
        )
        overview_layout.setContentsMargins(
            0,
            0,
            0,
            0,
        )
        overview_layout.setSpacing(CARD_GAP)

        (
            pumps_card,
            self.pumps_overview_value,
        ) = self.create_overview_card(
            title="Pumps",
            value="0",
            description="reported",
        )

        (
            valves_card,
            self.valves_overview_value,
        ) = self.create_overview_card(
            title="Valves",
            value="0",
            description="reported",
        )

        (
            sensors_card,
            self.sensors_overview_value,
        ) = self.create_overview_card(
            title="Sensors",
            value="0",
            description="reported",
        )

        overview_layout.addWidget(pumps_card, 1)
        overview_layout.addWidget(valves_card, 1)
        overview_layout.addWidget(sensors_card, 1)

        # Activity card
        self.activity_card = QGroupBox(
            "Recent activity"
        )
        self.activity_card.setObjectName(
            "activityCard"
        )
        self.activity_card.setSizePolicy(
            QSizePolicy.Policy.Expanding,
            QSizePolicy.Policy.Expanding,
        )

        activity_layout = QVBoxLayout(
            self.activity_card
        )
        activity_layout.setContentsMargins(20, 46, 20, 20)

        self.activity_log = QTextEdit()
        self.activity_log.setObjectName(
            "activityLog"
        )
        self.activity_log.setReadOnly(True)

        activity_layout.addWidget(
            self.activity_log
        )

        # Add sections
        main_layout.addWidget(
            self.empty_state,
            1,
        )
        main_layout.addWidget(self.device_card)
        main_layout.addWidget(
            self.overview_container
        )
        main_layout.addWidget(
            self.activity_card,
            1,
        )

        self.update_homepage_state()

    def create_overview_card(
        self,
        title,
        value,
        description,
    ):
        card = QGroupBox(title)
        card.setObjectName("overviewCard")
        card.setSizePolicy(
            QSizePolicy.Policy.Expanding,
            QSizePolicy.Policy.Fixed,
        )
        card.setMinimumHeight(164)
        card.setMaximumHeight(164)

        card_layout = QVBoxLayout(card)
        card_layout.setContentsMargins(28, 50, 28, 22)
        card_layout.setSpacing(6)

        value_label = QLabel(value)
        value_label.setObjectName(
            "overviewValue"
        )

        description_label = QLabel(description)
        description_label.setObjectName(
            "overviewDescription"
        )

        card_layout.addWidget(value_label)
        card_layout.addWidget(
            description_label
        )

        return card, value_label

    @classmethod
    def get_board_counts(cls, board):
        inventory = board.get("hardware_inventory")
        if not isinstance(inventory, dict):
            inventory = {}

        # These are physical-hardware counts, not counts of cards or sensor
        # definitions created by the UI.  Until the board exposes an explicit
        # inventory response, keep them unknown rather than hardcoding 3/0/1.
        driver_count = inventory.get("pump_drivers")
        valve_count = inventory.get("valve_drivers")
        sensor_count = inventory.get("sensors")
        pump_channel_count = inventory.get("pump_channels")

        return {
            "drivers": driver_count,
            "pump_channels": pump_channel_count,
            "valves": valve_count,
            "sensors": sensor_count,
        }

    @staticmethod
    def display_count(value):
        return "—" if value is None else str(value)

    def _set_status_display(self, connected):
        if connected:
            self.device_status.setText(
                '<span style="color:#2FA77B;">●</span>&nbsp;&nbsp;Connected'
            )
        else:
            self.device_status.setText(
                '<span style="color:#AAB5B0;">●</span>&nbsp;&nbsp;Disconnected'
            )

    def set_active_board(self, board):
        self.active_board = board

        if board is None:
            self.clear_device_information()
        else:
            counts = self.get_board_counts(board)

            self.device_name.setText(
                board.get("name", "Multiboard")
            )
            self._set_status_display(True)
            self.device_port.setText(
                board.get("port", "—")
            )
            self.device_firmware.setText(
                board.get("firmware", "Unknown")
            )

            self.device_pumps.setText(self.display_count(counts["drivers"]))
            self.device_valves.setText(self.display_count(counts["valves"]))
            self.device_sensors.setText(self.display_count(counts["sensors"]))

            # The overview card labelled "Pumps" represents usable channels,
            # while the device details separately report driver modules.
            self.pumps_overview_value.setText(
                self.display_count(counts["pump_channels"])
            )
            self.valves_overview_value.setText(
                self.display_count(counts["valves"])
            )
            self.sensors_overview_value.setText(
                self.display_count(counts["sensors"])
            )

        self.update_homepage_state()

    def clear_device_information(self):
        self.device_name.setText("Multiboard")
        self._set_status_display(False)
        self.device_port.setText("—")
        self.device_firmware.setText("—")
        self.device_pumps.setText("—")
        self.device_valves.setText("—")
        self.device_sensors.setText("—")

        self.pumps_overview_value.setText("0")
        self.valves_overview_value.setText("0")
        self.sensors_overview_value.setText("0")

    def add_activity(self, message):
        self.activity_log.append(message)

    def update_homepage_state(self):
        has_board = self.active_board is not None

        self.empty_state.setVisible(
            not has_board
        )

        self.device_card.setVisible(
            has_board
        )

        self.overview_container.setVisible(
            has_board
        )

        self.activity_card.setVisible(
            has_board
        )
