from PyQt6.QtCore import Qt
from PyQt6.QtWidgets import (
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
        main_layout.setSpacing(SECTION_GAP)

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
            QSizePolicy.Policy.Maximum,
        )

        device_layout = QVBoxLayout(
            self.device_card
        )
        device_layout.setContentsMargins(
            CARD_PADDING,
            CARD_PADDING,
            CARD_PADDING,
            14,
        )
        device_layout.setSpacing(10)

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

        self.device_status = QLabel("Connected")
        self.device_status.setObjectName(
            "informationValue"
        )

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

        device_information_layout.addWidget(
            pumps_label,
            0,
            2,
        )
        device_information_layout.addWidget(
            self.device_pumps,
            0,
            3,
        )

        device_information_layout.addWidget(
            valves_label,
            1,
            2,
        )
        device_information_layout.addWidget(
            self.device_valves,
            1,
            3,
        )

        device_information_layout.addWidget(
            sensors_label,
            2,
            2,
        )
        device_information_layout.addWidget(
            self.device_sensors,
            2,
            3,
        )

        device_information_layout.setColumnMinimumWidth(
            0,
            90,
        )
        device_information_layout.setColumnMinimumWidth(
            2,
            90,
        )
        device_information_layout.setColumnStretch(
            1,
            1,
        )
        device_information_layout.setColumnStretch(
            3,
            1,
        )

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
            description="available",
        )

        (
            valves_card,
            self.valves_overview_value,
        ) = self.create_overview_card(
            title="Valves",
            value="0",
            description="available",
        )

        (
            sensors_card,
            self.sensors_overview_value,
        ) = self.create_overview_card(
            title="Sensors",
            value="0",
            description="available",
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
        activity_layout.setContentsMargins(
            CARD_PADDING,
            34,
            CARD_PADDING,
            16,
        )

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
            QSizePolicy.Policy.Maximum,
        )

        card_layout = QVBoxLayout(card)
        card_layout.setContentsMargins(
            CARD_PADDING,
            32,
            CARD_PADDING,
            14,
        )
        card_layout.setSpacing(0)

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

    @staticmethod
    def count_items(value):
        """Count list-based hardware data while supporting older saved values."""

        if isinstance(value, int):
            return value

        if value is None:
            return 0

        try:
            return len(value)
        except TypeError:
            return 0

    @classmethod
    def get_board_counts(cls, board):
        drivers = board.get("drivers", [])

        driver_count = cls.count_items(drivers)
        pump_channel_count = sum(
            cls.count_items(driver.get("channels", []))
            for driver in drivers
            if isinstance(driver, dict)
        )

        # Compatibility with boards created by older versions of the UI.
        if not drivers:
            driver_count = cls.count_items(
                board.get("pump_drivers", 0)
            )
            pump_channel_count = driver_count

        valve_count = cls.count_items(
            board.get(
                "valves",
                board.get("valve_drivers", 0),
            )
        )
        sensor_count = cls.count_items(
            board.get("sensors", 0)
        )

        return {
            "drivers": driver_count,
            "pump_channels": pump_channel_count,
            "valves": valve_count,
            "sensors": sensor_count,
        }

    def set_active_board(self, board):
        self.active_board = board

        if board is None:
            self.clear_device_information()
        else:
            counts = self.get_board_counts(board)

            self.device_name.setText(
                board.get("name", "Multiboard")
            )
            self.device_status.setText("Connected")
            self.device_port.setText(
                board.get("port", "—")
            )
            self.device_firmware.setText(
                board.get("firmware", "Unknown")
            )

            self.device_pumps.setText(
                str(counts["drivers"])
            )
            self.device_valves.setText(
                str(counts["valves"])
            )
            self.device_sensors.setText(
                str(counts["sensors"])
            )

            # The overview card labelled "Pumps" represents usable channels,
            # while the device details separately report driver modules.
            self.pumps_overview_value.setText(
                str(counts["pump_channels"])
            )
            self.valves_overview_value.setText(
                str(counts["valves"])
            )
            self.sensors_overview_value.setText(
                str(counts["sensors"])
            )

        self.update_homepage_state()

    def clear_device_information(self):
        self.device_name.setText("Multiboard")
        self.device_status.setText("Disconnected")
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
