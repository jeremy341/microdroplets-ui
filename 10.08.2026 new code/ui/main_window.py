import sys
from pathlib import Path

from PyQt6.QtCore import Qt
from PyQt6.QtWidgets import (
    QApplication,
    QButtonGroup,
    QFrame,
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


PROJECT_ROOT = Path(__file__).resolve().parents[1]

if str(PROJECT_ROOT) not in sys.path:
    sys.path.insert(0, str(PROJECT_ROOT))


from backend.serial_manager import open_and_start_liquid_flow
from ui.pages.Home import HomePage
from ui.pages.Pumps import PumpsPage
from ui.pages.Sensors import SensorsPage
from ui.pages.Valves import ValvesPage
from ui.pages.Camera import CameraPage
from ui.design_tokens import BOARD_CONTROL_HEIGHT, CONTROL_HEIGHT, NAV_ROW_HEIGHT, SIDEBAR_WIDTH


app = QApplication(sys.argv)

style_path = PROJECT_ROOT / "style.qss"

with style_path.open("r", encoding="utf-8") as file:
    app.setStyleSheet(file.read())


# Window
window = QMainWindow()
window.setMinimumSize(1100, 700)
window.resize(1400, 800)
window.setWindowTitle("Control Panel")

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

navigation_buttons = (
    Home_button,
    pumps_button,
    valves_button,
    sensors_button,
    camera_button,
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
sidebar_layout.addWidget(valves_button)
sidebar_layout.addWidget(sensors_button)
sidebar_layout.addWidget(camera_button)
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


# Add universal controls
global_controls_layout.addWidget(page_title_label)
global_controls_layout.addStretch()
global_controls_layout.addWidget(board_buttons_container)
global_controls_layout.addWidget(connect_board_button)


# Pages
pages = QStackedWidget()

Home_page = HomePage()
pumps_page = PumpsPage()
valves_page = ValvesPage()
sensors_page = SensorsPage()
camera_page = CameraPage()

save_session_button.clicked.connect(sensors_page.save_session)
load_session_button.clicked.connect(sensors_page.load_session)

pages.addWidget(Home_page)
pages.addWidget(pumps_page)
pages.addWidget(valves_page)
pages.addWidget(sensors_page)
pages.addWidget(camera_page)

page_titles = {
    0: "Dashboard",
    1: "Pumps",
    2: "Valves",
    3: "Sensors",
    4: "Camera",
}


def update_page_title(page_index):
    """Update the universal title for the currently visible page."""
    page_title_label.setText(
        page_titles.get(page_index, "Control Panel")
    )
    # The camera page owns its complete reference-style header.  Keeping the
    # shell header visible here created a duplicate "Camera" title and left
    # the unrelated board Connect dropdown above the camera workspace.
    global_controls.setVisible(page_index != 4)


pages.currentChanged.connect(update_page_title)


# Add controls and pages
content_layout.addWidget(global_controls)
content_layout.addWidget(pages, 1)


def create_demo_drivers():
    """Return the simulated pump configuration used before hardware detection."""

    return [
        {
            "name": "mp-Highdriver4 Control",
            "driver_index": 0,
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
            "name": "mp-Highdriver / mp-Lowdriver Control",
            "driver_index": 1,
            "frequency": 100,
            "waveform": "Sinus",
            "channels": [
                {"channel": 5, "amplitude": 100, "enabled": False},
            ],
        },
        {
            "name": "mp-Driver Control",
            "driver_index": 2,
            "frequency": 100,
            "waveform": "Sinus",
            "channels": [
                {"channel": 6, "amplitude": 100, "enabled": False},
            ],
        },
    ]


# Runtime board registry. Real COM boards own a MultiboardConnection; the
# explicit demo entry remains available for UI-only development.
connected_boards = []
DEMO_PORT = "DEMO"
# Keep normal application behavior filtered.  The raw comparison run is
# opt-in, so a normal launch cannot accidentally bypass noise protection.
RAW_FLOW_TEST_MODE = "--raw-flow-test" in sys.argv

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


def update_pages():
    active_board = get_active_board()

    application_pages = (
        Home_page,
        pumps_page,
        valves_page,
        sensors_page,
        camera_page,
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

    is_demo = port == DEMO_PORT
    connection = None
    if not is_demo:
        try:
            connection = open_and_start_liquid_flow(
                port,
                filter_enabled=not RAW_FLOW_TEST_MODE,
            )
        except Exception as exc:
            QMessageBox.critical(
                window,
                "Could not connect Multiboard2",
                f"Could not open {port}.\n\n{exc}\n\n"
                "Close FluidicStudio and check the CP210x driver, then try again.",
            )
            return

    board = {
        "name": saved_board_names[port],
        "port": port,
        "firmware": "Demo" if is_demo else "Detecting…",
        "mode": "demo" if is_demo else "serial",
        "signal_mode": "demo" if is_demo else (
            "raw" if RAW_FLOW_TEST_MODE else "filtered"
        ),
        "connection": connection,
        "sensor_status": "Demo data" if is_demo else "Starting liquid-flow stream…",
        "drivers": create_demo_drivers(),
        "valves": [],
        "sensors": [] if is_demo else [
            {
                "id": "liquid_flow",
                "label": "Liquid Flow Rate",
                "unit": "µL/min",
                "precision": 3,
                "active": True,
                "available": True,
            }
        ],
    }

    connected_boards.append(board)
    active_port = port

    Home_page.add_activity(
        f"Connected {board['name']} on {port}"
    )

    update_board_buttons()
    update_pages()


def disconnect_board(port):
    global active_port

    board = get_board(port)

    if board is None:
        return

    board_name = board["name"]
    board_port = board["port"]

    connection = board.get("connection")
    if connection is not None:
        connection.close()

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

def refresh_connect_menu():
    connect_menu.clear()

    connect_menu.addSeparator()
    demo_board = get_board(DEMO_PORT)
    simulate_action = connect_menu.addAction(
        "Select simulated MB2" if demo_board is not None else "Simulate MB2"
    )
    simulate_action.triggered.connect(
        lambda checked=False: connect_board(DEMO_PORT)
    )

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


# Default page
Home_button.setChecked(True)
pages.setCurrentIndex(0)
update_page_title(pages.currentIndex())

update_board_buttons()
update_pages()


def close_all_connections():
    """Stop every active sensor stream before the process exits."""
    sensors_page.shutdown_logging()
    camera_page.shutdown()
    for board in list(connected_boards):
        connection = board.get("connection")
        if connection is not None:
            connection.close()


app.aboutToQuit.connect(close_all_connections)

# Assemble window
layout.addWidget(sidebar)
layout.addWidget(content, 1)


window.show()
sys.exit(app.exec())
