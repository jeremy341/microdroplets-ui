from __future__ import annotations

import unittest

from backend.protocol import (
    Calibration,
    SENSORS,
    driver_frequency_command,
    driver_waveform_command,
    encode_command,
    parse_reply,
    pump_amplitude_command,
    pump_start_commands,
    pump_state_command,
)


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

    def test_board_rejections_are_reported_as_errors(self) -> None:
        self.assertEqual(parse_reply("FAIL", "liquid_flow").kind, "error")
        self.assertEqual(
            parse_reply("Wrong command (�L0)", "liquid_flow").kind,
            "error",
        )

    def test_registry_has_start_and_stop_for_every_sensor(self) -> None:
        for definition in SENSORS.values():
            self.assertTrue(definition.start_command.endswith("ON"))
            self.assertTrue(definition.stop_command.endswith("OFF"))
        self.assertEqual(Calibration.WATER.value, "water")

    def test_documented_pump_commands(self) -> None:
        self.assertEqual(pump_state_command(1, True), "P1ON")
        self.assertEqual(pump_state_command(6, False), "P6OFF")
        self.assertEqual(pump_amplitude_command(4, 250), "P4V250")
        self.assertEqual(driver_frequency_command(0, 100), "F0=100")
        self.assertEqual(driver_frequency_command(2, 200), "F2=200")
        self.assertEqual(driver_waveform_command(0, "Sinus"), "CS0=0")
        # Default CH5 is mp-Lowdriver, which does not use the Multiboard CS1 command.
        with self.assertRaises(ValueError):
            driver_waveform_command(1, "Rectangular")
        self.assertEqual(
            pump_start_commands(0, 100, "Sinus", 4, 250),
            ("F0=100", "CS0=0", "P4V250", "P4ON"),
        )
        self.assertEqual(
            pump_start_commands(1, 175, "Sinus", 5, 25),
            ("F1=175", "P5V25", "P5ON"),
        )
        self.assertEqual(
            pump_start_commands(2, 100, "Sinus", 6, 120),
            ("F2=100", "P6V120", "P6ON"),
        )

    def test_invalid_pump_commands_are_rejected(self) -> None:
        with self.assertRaises(ValueError):
            pump_state_command(7, True)
        with self.assertRaises(ValueError):
            pump_amplitude_command(1, 251)
        with self.assertRaises(ValueError):
            driver_frequency_command(2, 227)
        with self.assertRaises(ValueError):
            driver_waveform_command(2, "Sinus")
        with self.assertRaises(ValueError):
            pump_start_commands(0, 100, "Sinus", 5, 100)


if __name__ == "__main__":
    unittest.main()
