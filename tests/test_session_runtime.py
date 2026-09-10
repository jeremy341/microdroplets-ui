from backend.session_runtime import apply_board_profile, build_board_profile


def board():
    return {
        "name": "MB1", "port": "COM3", "firmware": "v1",
        "drivers": [
            {"driver_index": 0, "frequency": 100, "waveform": "Sinus", "channels": [
                {"channel": 1, "amplitude": 120, "enabled": True, "selected_waveform_id": "w1"},
                {"channel": 2, "amplitude": 130, "enabled": False},
            ]},
            {"driver_index": 2, "frequency": 100, "waveform": "Sinus", "channels": [
                {"channel": 6, "amplitude": 100, "enabled": False},
            ]},
        ],
    }


def test_export_never_persists_running_hardware_state():
    profile = build_board_profile(board())
    assert all(not item["enabled"] for item in profile["pump_configuration"]["channels"])
    ch1 = next(item for item in profile["pump_configuration"]["channels"] if item["channel"] == 1)
    assert "selected_waveform_id" not in ch1


def test_apply_restores_configuration_only_and_ignores_legacy_wave_reference():
    original = board()
    profile = build_board_profile(original)
    target = board()
    target["drivers"][0]["channels"][0]["amplitude"] = 100
    warnings = apply_board_profile(profile, target, available_waveform_ids=())
    ch1 = target["drivers"][0]["channels"][0]
    assert ch1["amplitude"] == 120
    assert ch1["enabled"] is False
    # A legacy in-memory field is not touched or used by the Pump session path.
    # Saved Waves belong to WavePage/the Wave library only.
    assert ch1.get("selected_waveform_id") == "w1"
    assert not any("waveform" in warning.lower() for warning in warnings)


def test_apply_rejects_driver_specific_unsafe_values_without_clamping():
    target = board()
    profile = build_board_profile(target)
    ch6 = next(item for item in profile["pump_configuration"]["channels"] if item["channel"] == 6)
    ch6["amplitude_vpp"] = 50
    before = target["drivers"][1]["channels"][0]["amplitude"]
    warnings = apply_board_profile(profile, target, available_waveform_ids=())
    assert target["drivers"][1]["channels"][0]["amplitude"] == before
    assert any("Ignored unsafe CH6 amplitude" in warning for warning in warnings)
