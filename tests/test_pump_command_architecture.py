"""Static checks for the pump UI's asynchronous command boundary."""

import ast
from pathlib import Path
import unittest


PUMPS_SOURCE = Path(__file__).resolve().parents[1] / "ui" / "pages" / "Pumps.py"
MAIN_SOURCE = Path(__file__).resolve().parents[1] / "app.py"


class PumpCommandArchitectureTests(unittest.TestCase):
    @classmethod
    def setUpClass(cls):
        cls.source = PUMPS_SOURCE.read_text(encoding="utf-8")
        cls.tree = ast.parse(cls.source)

    def _method_source(self, name):
        for node in self.tree.body:
            if isinstance(node, ast.ClassDef) and node.name == "PumpsPage":
                for child in node.body:
                    if isinstance(child, ast.FunctionDef) and child.name == name:
                        return ast.get_source_segment(self.source, child)
        self.fail(f"PumpsPage.{name} was not found")

    def test_on_sequence_is_submitted_as_one_transaction(self):
        source = self._method_source("update_pump_enabled")
        self.assertIn("pump_start_commands", source)
        self.assertIn("send_sequence", source)
        self.assertIn("rollback_command", source)

    def test_toggle_state_changes_only_in_result_handler(self):
        request = self._method_source("update_pump_enabled")
        result = self._method_source("_pump_result")
        self.assertNotIn('channel_data["enabled"] = enabled', request)
        self.assertIn('channel_data["enabled"] = bool(desired)', result)
        self.assertIn('channel_data["hardware_state"] = "unknown"', result)

    def test_board_card_is_created_before_initializer_thread_starts(self):
        main_source = MAIN_SOURCE.read_text(encoding="utf-8")
        append_at = main_source.index("connected_boards.append(board)")
        initialize_at = main_source.index("target=connection.initialize_liquid_flow")
        self.assertLess(append_at, initialize_at)


if __name__ == "__main__":
    unittest.main()
