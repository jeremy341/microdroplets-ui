from PyQt6.QtCore import Qt
from PyQt6.QtWidgets import QLabel, QVBoxLayout, QWidget

from ui.design_tokens import PAGE_BOTTOM, PAGE_GUTTER, PAGE_TOP


class ValvesPage(QWidget):
    """Placeholder for valve controls outside the current pump MVP."""

    def __init__(self):
        super().__init__()

        self.setObjectName("valvesPage")
        self.active_board = None

        main_layout = QVBoxLayout(self)
        main_layout.setContentsMargins(PAGE_GUTTER, PAGE_TOP, PAGE_GUTTER, PAGE_BOTTOM)

        self.empty_state = QWidget()
        self.empty_state.setObjectName("emptyState")

        empty_layout = QVBoxLayout(self.empty_state)
        empty_layout.setSpacing(8)
        empty_layout.setAlignment(Qt.AlignmentFlag.AlignCenter)

        self.empty_title = QLabel("Valves are not part of the current MVP")
        self.empty_title.setObjectName("emptyStateTitle")
        self.empty_title.setAlignment(Qt.AlignmentFlag.AlignCenter)

        self.empty_description = QLabel(
            "This page is reserved for valve hardware and controls later."
        )
        self.empty_description.setObjectName("emptyStateDescription")
        self.empty_description.setAlignment(Qt.AlignmentFlag.AlignCenter)
        self.empty_description.setWordWrap(True)

        empty_layout.addWidget(self.empty_title)
        empty_layout.addWidget(self.empty_description)

        main_layout.addWidget(self.empty_state, 1)

    def set_active_board(self, board):
        self.active_board = board
