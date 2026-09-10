"""FluidicStudio desktop application entry point and board/page coordinator.

The root window owns navigation, connected-board state, the global page header,
and shutdown ordering.  Page-specific hardware logic stays in ``ui/pages`` and
protocol/transport logic stays in ``backend``.

Developer documentation:
* ``docs/DEVELOPER_GUIDE.md`` - component map and ownership boundaries.
* ``docs/DEVELOPER_GUIDE.md`` - physical drivers, frequency domains and safety.
"""

from pathlib import Path
import copy
import sys
from threading import Thread

from PyQt6.QtCore import QTimer, Qt
from PyQt6.QtGui import QKeySequence, QShortcut
from PyQt6.QtWidgets import (
    QApplication,
    QButtonGroup,
    QFrame,
    QFileDialog,
    QHBoxLayout,
    QInputDialog,
    QLabel,
    QLayout,
    QMainWindow,
    QMenu,
    QMessageBox,
    QPushButton,
    QSizePolicy,
    QStackedWidget,
    QToolButton,
    QVBoxLayout,
    QWidget,
)
from serial.tools import list_ports


PROJECT_ROOT = Path(__file__).resolve().parent

if str(PROJECT_ROOT) not in sys.path:
    sys.path.insert(0, str(PROJECT_ROOT))


from backend.serial_manager import MultiboardConnection
from backend.channel_ownership import ChannelOwnershipManager
from backend.pump_control import PumpControlService
from backend.driver_config import get_driver_config
from backend.driver_capabilities import capabilities_for_driver_index
from backend.wave_execution import WaveExecutionService
from backend.application_paths import DRIVER_CONFIG_PATH, SESSIONS_DIR, ensure_runtime_directories
from backend.session_manager import SCHEMA_VERSION, SessionError, SessionManager, create_default_session
from backend.session_runtime import apply_board_profile, build_board_profile, find_profile_for_board
from ui.pages.Home import HomePage
from ui.pages.Pumps import PumpsPage
from ui.pages.Sensors import SensorsPage
from ui.pages.Valves import ValvesPage
from ui.pages.Camera import CameraPage
from ui.pages.Waveform import WavePage
from ui.pages.Analytics import AnalyticsPage
from ui.pages.Workspace import WorkspacePage
from ui.design_tokens import BOARD_CONTROL_HEIGHT, CONTROL_HEIGHT, NAV_ROW_HEIGHT, SIDEBAR_WIDTH


ensure_runtime_directories()
driver_configuration = get_driver_config()
app = QApplication(sys.argv)

style_path = PROJECT_ROOT / "style.qss"

with style_path.open("r", encoding="utf-8") as file:
    app.setStyleSheet(file.read())


# Window
window = QMainWindow()
window.setMinimumSize(1100, 700)
window.resize(1400, 800)
window.setWindowTitle("FluidicStudio")

central = QWidget()
window.setCentralWidget(central)

layout = QHBoxLayout(central)
layout.setContentsMargins(0, 0, 0, 0)
layout.setSpacing(0)


# Sidebar
sidebar = QWidget()
sidebar.setObjectName("sidebar")
sidebar.setFixedWidth(SIDEBAR_WIDTH)

sidebar_layout = QVBoxLayout(sidebar)
sidebar_layout.setContentsMargins(24, 26, 24, 24)
sidebar_layout.setSpacing(8)


# Sidebar buttons
Home_button = QPushButton("Home")
pumps_button = QPushButton("Pumps")
valves_button = QPushButton("Valves")
sensors_button = QPushButton("Sensors")
camera_button = QPushButton("Camera")
Waveform_button = QPushButton("Wave")
analytics_button = QPushButton("Analytics")
workspace_button = QPushButton("Workspace")


navigation_buttons = (
    Home_button,
    pumps_button,
    sensors_button,
    Waveform_button,
    camera_button,
    analytics_button,
    valves_button,
    workspace_button
)

for button in navigation_buttons:
    button.setCheckable(True)
    button.setFixedHeight(NAV_ROW_HEIGHT)


# Sensor-session manager footer
session_manager_footer = QFrame()
session_manager_footer.setObjectName("sessionManagerFooter")

session_manager_layout = QVBoxLayout(session_manager_footer)
session_manager_layout.setContentsMargins(0, 0, 0, 0)
session_manager_layout.setSpacing(8)

session_manager_title = QLabel("Session Management")
session_manager_title.setObjectName("sessionManagerTitle")

session_actions_layout = QHBoxLayout()
session_actions_layout.setContentsMargins(0, 0, 0, 0)
session_actions_layout.setSpacing(8)

save_session_button = QPushButton("Save")
save_session_button.setObjectName("saveSessionButton")
save_session_button.setFixedHeight(CONTROL_HEIGHT)
save_session_button.setEnabled(True)

load_session_button = QPushButton("Load")
load_session_button.setObjectName("loadSessionButton")
load_session_button.setFixedHeight(CONTROL_HEIGHT)
load_session_button.setEnabled(True)

session_actions_layout.addWidget(save_session_button, 1)
session_actions_layout.addWidget(load_session_button, 1)

session_manager_layout.addWidget(session_manager_title)
session_manager_layout.addSpacing(10)
session_manager_layout.addLayout(session_actions_layout)


# Navigation group
navigation_group = QButtonGroup(window)
navigation_group.setExclusive(True)

for button in navigation_buttons:
    navigation_group.addButton(button)


# Board group
board_button_group = QButtonGroup(window)
board_button_group.setExclusive(True)


# Build sidebar
# A fixed top gap keeps navigation near the upper part of the window,
# while the session manager always stays at the bottom.
sidebar_layout.addSpacing(92)
sidebar_layout.addWidget(Home_button)
sidebar_layout.addWidget(pumps_button)
sidebar_layout.addWidget(sensors_button)
sidebar_layout.addWidget(Waveform_button)
sidebar_layout.addWidget(camera_button)
sidebar_layout.addWidget(analytics_button)
sidebar_layout.addWidget(valves_button)
sidebar_layout.addWidget(workspace_button)
sidebar_layout.addStretch()
sidebar_layout.addWidget(session_manager_footer)


# Right side
content = QWidget()

content_layout = QVBoxLayout(content)
content_layout.setContentsMargins(0, 0, 0, 0)
content_layout.setSpacing(0)


# Universal controls
global_controls = QWidget()
global_controls.setObjectName("globalControls")
global_controls.setFixedHeight(88)

global_controls_layout = QHBoxLayout(global_controls)
global_controls_layout.setContentsMargins(24, 16, 24, 10)
global_controls_layout.setSpacing(10)

# Universal page title
page_title_label = QLabel("Dashboard")
page_title_label.setObjectName("globalPageTitle")
page_title_label.setAlignment(Qt.AlignmentFlag.AlignVCenter)


# Board button area
board_buttons_container = QWidget()
board_buttons_container.setObjectName(
    "boardButtonsContainer"
)
board_buttons_container.setSizePolicy(
    QSizePolicy.Policy.Maximum,
    QSizePolicy.Policy.Fixed
)


board_buttons_layout = QHBoxLayout(
    board_buttons_container
)
board_buttons_layout.setContentsMargins(0, 0, 0, 0)
board_buttons_layout.setSpacing(10)

board_buttons_layout.setSizeConstraint(
    QLayout.SizeConstraint.SetFixedSize
)


# Connect dropdown button
connect_board_button = QToolButton()
connect_board_button.setObjectName("connectBoardButton")
connect_board_button.setText("Connect")
connect_board_button.setFixedSize(130, BOARD_CONTROL_HEIGHT)

connect_board_button.setPopupMode(
    QToolButton.ToolButtonPopupMode.InstantPopup
)

connect_menu = QMenu(connect_board_button)
connect_board_button.setMenu(connect_menu)


# Display the configured CH5 driver beside the connection controls.
ch5_driver_capabilities = capabilities_for_driver_index(1, driver_configuration)
ch5_driver_flag = QLabel(f"CH5 · {ch5_driver_capabilities.display_name}")
ch5_driver_flag.setToolTip(
    f"Configured in {DRIVER_CONFIG_PATH}. Restart FluidicStudio after changing CH5."
)

# Add universal controls
global_controls_layout.addWidget(page_title_label)
global_controls_layout.addStretch()
global_controls_layout.addWidget(ch5_driver_flag)
global_controls_layout.addWidget(board_buttons_container)
global_controls_layout.addWidget(connect_board_button)


# Pages
pages = QStackedWidget()

Home_page = HomePage()
pumps_page = PumpsPage()
valves_page = ValvesPage()
sensors_page = SensorsPage()
camera_page = CameraPage()
wave_page = WavePage()
analytics_page = AnalyticsPage()
workspace_page = WorkspacePage(
    pumps_page=pumps_page,
    sensors_page=sensors_page,
    wave_page=wave_page,
    camera_page=camera_page,
    active_board_provider=lambda: get_active_board(),
)
session_manager = SessionManager()

pages.addWidget(Home_page)
pages.addWidget(pumps_page)
pages.addWidget(valves_page)
pages.addWidget(sensors_page)
pages.addWidget(camera_page)
pages.addWidget(wave_page)
pages.addWidget(analytics_page)
pages.addWidget(workspace_page)

page_titles = {
    0: "Dashboard",
    1: "Pumps",
    2: "Valves",
    3: "Sensors",
    4: "Camera",
    5: "Wave",
    6: "Analytics",
    7: "Workspace"
}

# Sidebar order is intentionally different from the internal QStackedWidget
# order.  Keep an explicit page->button mapping so programmatic navigation
# (for example session restore) always highlights the correct sidebar entry.
page_navigation_buttons = {
    0: Home_button,
    1: pumps_button,
    2: valves_button,
    3: sensors_button,
    4: camera_button,
    5: Waveform_button,
    6: analytics_button,
    7: workspace_button,
}

# Keyboard navigation follows the visible sidebar order rather than the
# internal QStackedWidget order (Valves is internally index 2).
navigation_page_order = (0, 1, 3, 5, 4, 6, 2, 7)

navigation_shortcut_targets = {
    "Shift+1": 0,  # Home
    "Shift+2": 1,  # Pumps
    "Shift+3": 3,  # Sensors
    "Shift+4": 5,  # Wave
    "Shift+5": 4,  # Camera
    "Shift+6": 6,  # Analytics
    "Shift+7": 2,  # Valves
}


def navigate_relative_page(step):
    """Move through pages in visible sidebar order and wrap at the ends."""
    current_index = pages.currentIndex()

    try:
        current_position = navigation_page_order.index(current_index)
    except ValueError:
        current_position = 0

    target_position = (
        current_position + int(step)
    ) % len(navigation_page_order)

    pages.setCurrentIndex(
        navigation_page_order[target_position]
    )


def update_page_title(page_index):
    """Update the universal title for the currently visible page."""
    if page_index == 7:
        page_title_label.setText(workspace_page.current_title)
    else:
        page_title_label.setText(
            page_titles.get(page_index, "FluidicStudio")
        )
    # Every page uses the same global application header.
    global_controls.setVisible(True)


def update_navigation_selection(page_index):
    """Keep sidebar selection aligned with the actual visible page."""
    button = page_navigation_buttons.get(page_index)
    if button is not None:
        button.setChecked(True)


pages.currentChanged.connect(update_page_title)
pages.currentChanged.connect(update_navigation_selection)
workspace_page.title_changed.connect(
    lambda title: page_title_label.setText(title) if pages.currentIndex() == 7 else None
)


# Add controls and pages
content_layout.addWidget(global_controls)
content_layout.addWidget(pages, 1)


def create_configured_drivers():
    """Return the configured Multiboard pump groups shown by the existing UI.

    CH1-CH4 and CH6 are fixed for this project.  CH5 is resolved from
    data/driver_config.json at process startup.  The configuration is descriptive
    rather than automatic hardware detection.
    """

    driver0 = capabilities_for_driver_index(0, driver_configuration)
    driver1 = capabilities_for_driver_index(1, driver_configuration)
    driver2 = capabilities_for_driver_index(2, driver_configuration)

    return [
        {
            "name": "mp-Highdriver4 Control",
            "driver_index": 0,
            "driver_type": driver0.driver_type,
            "driver_display_name": driver0.display_name,
            "supports_carrier_waveform": driver0.supports_carrier_waveform,
            "frequency": 100,
            "waveform": "Sinus",
            "channels": [
                {"channel": 1, "amplitude": 100, "enabled": False},
                {"channel": 2, "amplitude": 100, "enabled": False},
                {"channel": 3, "amplitude": 100, "enabled": False},
                {"channel": 4, "amplitude": 100, "enabled": False},
            ],
        },
        {
            # Keep the card structure unchanged; the global topbar flag is the
            # visible source for the configured CH5 driver type.
            "name": "CH5 Driver Control",
            "driver_index": 1,
            "driver_type": driver1.driver_type,
            "driver_display_name": driver1.display_name,
            "supports_carrier_waveform": driver1.supports_carrier_waveform,
            "frequency": 100,
            "waveform": "Sinus",
            "channels": [
                {"channel": 5, "amplitude": 100, "enabled": False},
            ],
        },
        {
            "name": "mp-Driver Control",
            "driver_index": 2,
            "driver_type": driver2.driver_type,
            "driver_display_name": driver2.display_name,
            "supports_carrier_waveform": driver2.supports_carrier_waveform,
            "frequency": 100,
            "waveform": "Sinus",
            "channels": [
                {"channel": 6, "amplitude": 100, "enabled": False},
            ],
        },
    ]


# Runtime board registry. Every connected board owns a real MultiboardConnection.
connected_boards = []
# Remembers names even after a board is disconnected
saved_board_names = {}

active_port = None
next_board_number = 1


def get_board(port):
    for board in connected_boards:
        if board["port"] == port:
            return board

    return None


def get_active_board():
    if active_port is None:
        return None

    return get_board(active_port)


def apply_loaded_profile_to_board(board):
    """Restore only saved RAM configuration for a newly connected board."""
    profile = find_profile_for_board(
        session_manager.current_session.get("board_profiles", []), board
    )
    if profile is None:
        return ()
    return apply_board_profile(profile, board)


def update_pages():
    active_board = get_active_board()

    application_pages = (
        Home_page,
        pumps_page,
        valves_page,
        sensors_page,
        camera_page,
        wave_page,
        analytics_page,
        workspace_page,
    )

    for page in application_pages:
        if hasattr(page, "set_connected_boards"):
            page.set_connected_boards(connected_boards)

        if hasattr(page, "set_active_board"):
            page.set_active_board(active_board)


def select_board(port):
    global active_port

    board = get_board(port)

    if board is None:
        return

    active_port = port

    update_board_buttons()
    update_pages()
    update_firmware_timer_state()

    Home_page.add_activity(
        f"Selected {board['name']}"
    )


def connect_board(port):
    global active_port
    global next_board_number

    existing_board = get_board(port)

    # Select it when it is already connected
    if existing_board is not None:
        select_board(port)
        return

    # Give a new COM port a default name
    if port not in saved_board_names:
        saved_board_names[port] = (
            f"MB{next_board_number}"
        )

        next_board_number += 1

    try:
        connection = MultiboardConnection(port)
        connection.open()
    except Exception as exc:
        QMessageBox.critical(
            window,
            "Could not connect Multiboard2",
            f"Could not open {port}.\n\n{exc}\n\n"
            "Close FluidicStudio and check the CP210x driver, then try again.",
        )
        return

    drivers = create_configured_drivers()
    channel_ownership = ChannelOwnershipManager()
    pump_control = PumpControlService(connection, channel_ownership)
    wave_execution = WaveExecutionService(pump_control)

    firmware = "Detecting…"
    firmware_reply = str(getattr(connection, "firmware", "")).strip()
    if firmware_reply and firmware_reply.lower() != "unknown":
        firmware = firmware_reply.removeprefix("Multiboard ")

    board = {
        "name": saved_board_names[port],
        "port": port,
        "firmware": firmware,
        "connection": connection,
        # Runtime coordination is per physical board. Pages share these backend
        # objects instead of keeping separate channel state.
        "channel_ownership": channel_ownership,
        "pump_control": pump_control,
        "wave_execution": wave_execution,
        "sensor_status": "Starting liquid-flow stream…",
        "drivers": drivers,
        "valves": [],
        # Multiboard2 does not expose a reliable inventory for these device
        # categories through the commands used by this app.  Keep unknown
        # values unknown instead of presenting the UI card configuration as
        # detected hardware.
        "hardware_inventory": {
            "pump_drivers": None,
            "valve_drivers": None,
            "sensors": None,
        },
        "sensors": [
            {
                "id": "liquid_flow",
                "label": "Liquid Flow Rate",
                "unit": "µL/min",
                "precision": 2,
                # Presence is established by valid samples, not by the board
                # merely having a sensor connector in its UI definition.
                "active": False,
                "available": False,
            }
        ],
    }

    # A loaded session may contain configuration for this port. Applying it
    # here changes only RAM/UI values; no command is sent and every channel
    # remains OFF.
    apply_loaded_profile_to_board(board)
    pump_control.seed_driver_frequencies({
        int(driver["driver_index"]): int(driver.get("frequency", 100))
        for driver in drivers
    })

    connected_boards.append(board)
    active_port = port

    Home_page.add_activity(
        f"Connected {board['name']} on {port}"
    )

    update_board_buttons()
    update_pages()
    update_firmware_timer_state()

    # The board card is visible before the acknowledged detection loop starts.
    # Serial retries therefore never freeze the Qt event loop.
    if connection is not None:
        initializer = Thread(
            target=connection.initialize_liquid_flow,
            name=f"flow-sensor-init-{port}",
            daemon=True,
        )
        connection.attach_initializer(initializer)
        initializer.start()


def disconnect_board(port):
    global active_port

    board = get_board(port)

    if board is None:
        return

    board_name = board["name"]
    board_port = board["port"]

    connection = board.get("connection")
    wave_execution = board.get("wave_execution")
    if wave_execution is not None:
        # Stop timed wave workers before closing the serial transport. Their
        # normal finalizer attempts the affected channel's acknowledged OFF.
        wave_execution.stop_all(timeout=2.0)
    if connection is not None:
        try:
            if connection.close() is False:
                QMessageBox.warning(
                    window,
                    "Disconnect blocked",
                    "The board did not acknowledge POFF. The board remains connected so you can retry safely.",
                )
                return
        except Exception as exc:
            QMessageBox.warning(
                window,
                "Disconnect failed",
                f"The board could not be stopped safely:\n\n{exc}",
            )
            return
    if wave_execution is not None:
        # Only a confirmed closed connection may forcibly clear ownership.
        wave_execution.force_release_after_disconnect()

    connected_boards.remove(board)

    if active_port == port:
        if connected_boards:
            active_port = connected_boards[0]["port"]
        else:
            active_port = None

    Home_page.add_activity(
        f"Disconnected {board_name} from {board_port}"
    )

    update_board_buttons()
    update_pages()


def disconnect_active_board():
    active_board = get_active_board()

    if active_board is None:
        return

    disconnect_board(active_board["port"])


def rename_board(port):
    board = get_board(port)

    if board is None:
        return

    old_name = board["name"]

    new_name, accepted = QInputDialog.getText(
        window,
        "Rename board",
        "Enter a new board name:",
        text=old_name,
    )

    # User pressed Cancel
    if not accepted:
        return

    new_name = new_name.strip()

    # Prevent empty names
    if not new_name:
        QMessageBox.warning(
            window,
            "Invalid name",
            "The board name cannot be empty.",
        )
        return

    # Prevent two connected boards from having the same name
    for other_board in connected_boards:
        same_board = other_board["port"] == port

        same_name = (
            other_board["name"].casefold()
            == new_name.casefold()
        )

        if not same_board and same_name:
            QMessageBox.warning(
                window,
                "Name already used",
                "Another connected board already "
                "uses this name.",
            )
            return

    board["name"] = new_name

    # Remember the name after disconnecting
    saved_board_names[port] = new_name

    update_board_buttons()
    update_pages()

    Home_page.add_activity(
        f"Renamed {old_name} to {new_name}"
    )


def create_board_menu(port, parent):
    menu = QMenu(parent)

    select_action = menu.addAction(
        "Select board"
    )

    select_action.setEnabled(
        port != active_port
    )

    select_action.triggered.connect(
        lambda checked=False,
        selected_port=port:
        select_board(selected_port)
    )

    rename_action = menu.addAction(
        "Rename"
    )

    rename_action.triggered.connect(
        lambda checked=False,
        selected_port=port:
        rename_board(selected_port)
    )

    menu.addSeparator()

    disconnect_action = menu.addAction(
        "Disconnect"
    )

    disconnect_action.triggered.connect(
        lambda checked=False,
        selected_port=port:
        disconnect_board(selected_port)
    )

    return menu


def show_board_context_menu(
    port,
    board_button,
    position,
):
    menu = create_board_menu(
        port,
        board_button,
    )

    global_position = board_button.mapToGlobal(
        position
    )

    menu.exec(global_position)


def update_board_buttons():
    # Remove buttons from the exclusive group
    for button in board_button_group.buttons():
        board_button_group.removeButton(button)

    # Remove old board islands
    while board_buttons_layout.count():
        item = board_buttons_layout.takeAt(0)
        island = item.widget()

        if island is not None:
            island.deleteLater()

    # Create one island for every connected board
    for board in connected_boards:
        port = board["port"]
        is_active = port == active_port

        # Complete island
        island = QFrame()
        island.setObjectName("boardIsland")
        island.setFixedSize(220, 54)
        island.setProperty(
            "active",
            is_active,
        )

        island_layout = QHBoxLayout(island)
        island_layout.setContentsMargins(
            1,
            1,
            1,
            1,
        )
        island_layout.setSpacing(0)

        # Main board button
        board_button = QPushButton(
            f"{board['name']}\n"
            f"Firmware {board['firmware']}   "
            f"{port}"
        )

        board_button.setObjectName("boardButton")
        board_button.setCheckable(True)
        board_button.setChecked(is_active)

        board_button.clicked.connect(
            lambda checked=False,
            selected_port=port:
            select_board(selected_port)
        )

        # Right-click menu
        board_button.setContextMenuPolicy(
            Qt.ContextMenuPolicy.CustomContextMenu
        )

        board_button.customContextMenuRequested.connect(
            lambda position,
            button=board_button,
            selected_port=port:
            show_board_context_menu(
                selected_port,
                button,
                position,
            )
        )

        # Vertical dots button
        options_button = QToolButton()
        options_button.setObjectName(
            "boardOptionsButton"
        )
        options_button.setText("⋮")
        options_button.setFixedWidth(34)

        options_button.setPopupMode(
            QToolButton.ToolButtonPopupMode.InstantPopup
        )

        options_menu = create_board_menu(
            port,
            options_button,
        )

        options_button.setMenu(options_menu)

        # Build the island
        island_layout.addWidget(
            board_button,
            1,
        )
        island_layout.addWidget(
            options_button,
        )

        board_button_group.addButton(
            board_button
        )

        board_buttons_layout.addWidget(
            island
        )


def update_firmware_timer_state():
    """Poll cached firmware only while at least one board is connected."""
    timer = globals().get("firmware_refresh_timer")
    if timer is not None:
        if connected_boards:
            if not timer.isActive():
                timer.start()
        elif timer.isActive():
            timer.stop()


def refresh_firmware_display():
    """Refresh only the firmware text in the connected-board islands."""

    changed = False
    for board in connected_boards:
        connection = board.get("connection")
        if connection is None:
            continue
        firmware = str(getattr(connection, "firmware", "")).strip()
        if not firmware or firmware.lower() == "unknown":
            continue
        firmware = firmware.removeprefix("Multiboard ")
        if board.get("firmware") != firmware:
            board["firmware"] = firmware
            changed = True

    if changed:
        update_board_buttons()
        active_board = get_active_board()
        if active_board is not None:
            Home_page.set_active_board(active_board)

def _session_runtime_is_idle():
    if not sensors_page.session_runtime_idle():
        return False, "Stop sensor logging before loading a session."
    if not camera_page.session_runtime_idle():
        return False, "Stop the camera/recording before loading a session."
    for board in connected_boards:
        ownership = board.get("channel_ownership")
        if ownership is not None and not ownership.all_free:
            return False, "Turn off all pumps and waveforms before loading a session."
        for driver in board.get("drivers", []):
            if any(bool(channel.get("enabled", False)) for channel in driver.get("channels", [])):
                return False, "Turn off all pumps and waveforms before loading a session."
    return True, ""


def _collect_session_from_ui():
    session = copy.deepcopy(session_manager.current_session)
    if not isinstance(session, dict) or session.get("schema_version") != SCHEMA_VERSION:
        session = create_default_session()
    session["board_profiles"] = [build_board_profile(board) for board in connected_boards]
    ui_state = session.setdefault("ui_state", {})
    ui_state["page_index"] = pages.currentIndex()
    ui_state["sensors"] = sensors_page.export_session_state()
    ui_state["camera"] = camera_page.export_session_state()
    ui_state["wave"] = wave_page.export_session_state()
    sensor_state = ui_state["sensors"]
    try:
        sample_rate = float(str(sensor_state.get("sample_rate", "1 sec")).split()[0])
    except (TypeError, ValueError):
        sample_rate = 1.0
    session["logging"] = {
        "directory": str(Path(sensor_state.get("log_path", "")).expanduser().parent)
        if sensor_state.get("log_path") else "",
        "sample_rate_seconds": sample_rate,
    }
    # No runtime activity flag is stored anywhere in this object.
    return session


def save_global_session():
    idle, reason = _session_runtime_is_idle()
    if not idle:
        QMessageBox.warning(window, "Session save blocked", reason)
        return
    target = session_manager.session_path
    if target is None:
        SESSIONS_DIR.mkdir(parents=True, exist_ok=True)
        path, _ = QFileDialog.getSaveFileName(
            window,
            "Save FluidicStudio session",
            str(SESSIONS_DIR / "session.json"),
            "FluidicStudio sessions (*.json);;JSON files (*.json)",
        )
        if not path:
            return
        target = Path(path)
    session = _collect_session_from_ui()
    session["session"]["name"] = target.stem
    session_manager.current_session = session
    session_manager.mark_dirty()
    try:
        session_manager.save(target)
    except SessionError as exc:
        QMessageBox.warning(window, "Could not save session", str(exc))


def load_global_session():
    idle, reason = _session_runtime_is_idle()
    if not idle:
        QMessageBox.warning(window, "Session load blocked", reason)
        return
    SESSIONS_DIR.mkdir(parents=True, exist_ok=True)
    path, _ = QFileDialog.getOpenFileName(
        window,
        "Load FluidicStudio session",
        str(SESSIONS_DIR),
        "FluidicStudio sessions (*.json);;JSON files (*.json)",
    )
    if not path:
        return
    try:
        result = session_manager.load(path)
    except SessionError as exc:
        QMessageBox.warning(window, "Could not load session", str(exc))
        return

    warnings = list(result.warnings)
    for board in connected_boards:
        profile = find_profile_for_board(result.session.get("board_profiles", []), board)
        if profile is not None:
            warnings.extend(apply_board_profile(profile, board))

    ui_state = result.session.get("ui_state", {})
    sensors_page.apply_session_state(ui_state.get("sensors", {}))
    camera_page.apply_session_state(ui_state.get("camera", {}))
    _, wave_warnings = wave_page.apply_session_state(ui_state.get("wave", {}))
    warnings.extend(wave_warnings)
    pumps_page.rebuild_driver_cards()
    pumps_page._sync_runtime_states()

    page_index = ui_state.get("page_index", pages.currentIndex())
    if isinstance(page_index, int) and 0 <= page_index < pages.count():
        pages.setCurrentIndex(page_index)

    if warnings:
        QMessageBox.information(
            window,
            "Session loaded with notes",
            "\n".join(dict.fromkeys(str(item) for item in warnings if item)),
        )


save_session_button.clicked.connect(save_global_session)
load_session_button.clicked.connect(load_global_session)


def refresh_connect_menu():
    connect_menu.clear()

    ports = list_ports.comports()

    if not ports:
        no_ports_action = connect_menu.addAction(
            "No COM ports found"
        )
        no_ports_action.setEnabled(False)

    for port in ports:
        board = get_board(port.device)

        if board is None:
            action_text = (
                f"{port.device}  {port.description}"
            )
        else:
            action_text = (
                f"{port.device}  Connected"
            )

        port_action = connect_menu.addAction(
            action_text
        )

        port_action.triggered.connect(
            lambda checked=False,
            selected_port=port.device:
            connect_board(selected_port)
        )

    active_board = get_active_board()

    if active_board is not None:
        connect_menu.addSeparator()

        disconnect_action = connect_menu.addAction(
            f"Disconnect {active_board['name']}"
        )

        disconnect_action.triggered.connect(
            lambda checked=False:
            disconnect_active_board()
        )


# Connect menu
connect_menu.aboutToShow.connect(
    refresh_connect_menu
)


# Navigation
Home_button.clicked.connect(
    lambda: pages.setCurrentIndex(0)
)

pumps_button.clicked.connect(
    lambda: pages.setCurrentIndex(1)
)

valves_button.clicked.connect(
    lambda: pages.setCurrentIndex(2)
)

sensors_button.clicked.connect(
    lambda: pages.setCurrentIndex(3)
)

camera_button.clicked.connect(
    lambda: pages.setCurrentIndex(4)
)
Waveform_button.clicked.connect(
    lambda: pages.setCurrentIndex(5)
)
analytics_button.clicked.connect(
    lambda: pages.setCurrentIndex(6)
)

def open_workspace_builder():
    """Open the Workspace builder without affecting any running hardware."""
    workspace_page.show_builder()
    pages.setCurrentIndex(7)

workspace_button.clicked.connect(open_workspace_builder)

# Global keyboard page navigation. The shortcuts are owned by the main window
# and retained for the whole application lifetime.
navigation_shortcuts = []

for key_sequence, page_index in navigation_shortcut_targets.items():
    shortcut = QShortcut(
        QKeySequence(key_sequence),
        window,
    )
    shortcut.setContext(
        Qt.ShortcutContext.WindowShortcut
    )
    shortcut.setAutoRepeat(False)
    shortcut.activated.connect(
        lambda page_index=page_index:
        pages.setCurrentIndex(page_index)
    )
    navigation_shortcuts.append(shortcut)

next_page_shortcut = QShortcut(
    QKeySequence("Ctrl+Tab"),
    window,
)
next_page_shortcut.setContext(
    Qt.ShortcutContext.WindowShortcut
)
next_page_shortcut.setAutoRepeat(False)
next_page_shortcut.activated.connect(
    lambda: navigate_relative_page(1)
)
navigation_shortcuts.append(next_page_shortcut)

previous_page_shortcut = QShortcut(
    QKeySequence("Ctrl+Shift+Tab"),
    window,
)
previous_page_shortcut.setContext(
    Qt.ShortcutContext.WindowShortcut
)
previous_page_shortcut.setAutoRepeat(False)
previous_page_shortcut.activated.connect(
    lambda: navigate_relative_page(-1)
)
navigation_shortcuts.append(previous_page_shortcut)

# Default page
Home_button.setChecked(True)
pages.setCurrentIndex(0)
update_page_title(pages.currentIndex())

update_board_buttons()
update_pages()

# Firmware arrives asynchronously from the board reader.  Refresh the board
# island without rebuilding pump pages or performing any driver detection.
firmware_refresh_timer = QTimer(window)
firmware_refresh_timer.setInterval(250)
firmware_refresh_timer.timeout.connect(refresh_firmware_display)
update_firmware_timer_state()


def close_all_connections():
    """Stop active workers and put each connected board into a safe state."""
    sensors_page.shutdown_logging()
    analytics_page.shutdown()
    workspace_page.shutdown()
    camera_page.shutdown()
    wave_page.shutdown()
    if hasattr(pumps_page, "shutdown"):
        pumps_page.shutdown()
    for board in list(connected_boards):
        wave_execution = board.get("wave_execution")
        if wave_execution is not None:
            wave_execution.stop_all(timeout=2.0)
        connection = board.get("connection")
        closed = True
        if connection is not None:
            try:
                closed = connection.close() is not False
            except Exception:
                closed = False
        if closed and wave_execution is not None:
            wave_execution.force_release_after_disconnect()


app.aboutToQuit.connect(close_all_connections)

# Assemble window
layout.addWidget(sidebar)
layout.addWidget(content, 1)


window.show()
sys.exit(app.exec())
