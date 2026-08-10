from __future__ import annotations

import unittest

from backend.protocol import Calibration, SENSORS, encode_command, parse_reply


class ProtocolTests(unittest.TestCase):
    def test_commands_use_one_crlf(self) -> None:
        self.assertEqual(encode_command(" DFON "), b"DFON\r\n")
        with self.assertRaises(ValueError):
            encode_command("DFON\nDFOFF")

    def test_captured_v_format_requires_active_sensor(self) -> None:
        unknown = parse_reply("V=10.125")
        self.assertEqual(unknown.kind, "unknown")

        reply = parse_reply("V=10.125", "liquid_flow")
        self.assertEqual(reply.kind, "measurement")
        self.assertEqual(reply.measurement.sensor_id, "liquid_flow")
        self.assertEqual(reply.measurement.value, 10125.0)
        self.assertEqual(reply.measurement.unit, "µL/min")
        self.assertEqual(reply.measurement.raw_value_ml_min, 10.125)

    def test_named_markers_select_their_sensor(self) -> None:
        self.assertEqual(parse_reply("RSLF=-0.014").measurement.sensor_id, "liquid_flow")
        self.assertEqual(parse_reply("RSDPC: 101.2").measurement.sensor_id, "pressure")
        self.assertEqual(parse_reply("CO2=431").measurement.sensor_id, "co2")
        self.assertEqual(parse_reply("VOC=93").measurement.sensor_id, "voc")

    def test_unknown_and_malformed_lines_are_not_guessed(self) -> None:
        self.assertEqual(parse_reply("temperature 23.1", "liquid_flow").kind, "unknown")
        self.assertEqual(parse_reply("V=NaN", "liquid_flow").kind, "unknown")
        self.assertEqual(parse_reply("V=1.2 extra", "liquid_flow").kind, "unknown")

    def test_registry_has_start_and_stop_for_every_sensor(self) -> None:
        for definition in SENSORS.values():
            self.assertTrue(definition.start_command.endswith("ON"))
            self.assertTrue(definition.stop_command.endswith("OFF"))
        self.assertEqual(Calibration.WATER.value, "water")


if __name__ == "__main__":
    unittest.main()
