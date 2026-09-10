from tools.driver_detection_probe import build_report, parse_settings_lines


def test_parse_driver_fingerprint_without_guessing_meaning():
    parsed = parse_settings_lines(
        [
            "Driver:4D ",
            "Frequency1: 100Hz",
            "Frequency2: 100Hz",
            "Frequency3: 100Hz",
            "Pump1: 250V (OFF)",
            "Pump6: 85V (OFF)",
        ]
    )
    assert parsed["driver_fingerprint"] == "4D"
    assert parsed["frequencies_hz"] == {"1": 100, "2": 100, "3": 100}
    assert parsed["pump_settings"]["1"] == "250V (OFF)"
    assert parsed["pump_settings"]["6"] == "85V (OFF)"


def test_unknown_settings_lines_are_preserved():
    parsed = parse_settings_lines(["Driver:D", "FutureField: 123"])
    assert parsed["driver_fingerprint"] == "D"
    assert parsed["unknown_settings_lines"] == ["FutureField: 123"]


def test_report_marks_fingerprint_mapping_unverified():
    report = build_report(
        port="COM3",
        label="known_setup",
        firmware_lines=["Multiboard Ready"],
        settings_lines=["Driver:4D"],
    )
    assert report["interpretation"]["driver_detection_evidence_present"] is True
    assert report["interpretation"]["mapping_status"] == "unverified"
    assert report["interpretation"]["physical_pump_count"] == "unknown"
