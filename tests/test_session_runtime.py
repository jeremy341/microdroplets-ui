from backend.protocol import DRIVER_AMPLITUDE_LIMITS, DRIVER_FREQUENCY_LIMITS
from backend.session_manager import create_default_session, validate_session
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


def test_capture_clamps_out_of_range_live_values_into_a_saveable_session():
    live = {
        "name": "MB1", "port": "COM3", "firmware": "v1",
        "drivers": [
            # CH6 runs on an mp-Driver, whose amplitude range starts at 85 Vpp.
            {"driver_index": 2, "frequency": 100, "waveform": "Sinus", "channels": [
                {"channel": 6, "amplitude": 10, "enabled": True},
            ]},
            {"driver_index": 0, "frequency": 5000, "waveform": "Sine", "channels": [
                {"channel": 1, "amplitude": 999, "enabled": False},
                {"channel": 9, "amplitude": 100, "enabled": False},
            ]},
        ],
    }
    profile = build_board_profile(live)
    session = create_default_session("Clamped Capture")
    session["board_profiles"] = [profile]

    # Capture must never produce a session the manager refuses to save.
    result = validate_session(session)
    assert result.valid, result.errors

    channels = {item["channel"]: item for item in profile["pump_configuration"]["channels"]}
    assert sorted(channels) == [1, 6]
    assert channels[6]["amplitude_vpp"] == DRIVER_AMPLITUDE_LIMITS[2][0]
    assert channels[1]["amplitude_vpp"] == DRIVER_AMPLITUDE_LIMITS[0][1]
    assert channels[1]["frequency_hz"] == DRIVER_FREQUENCY_LIMITS[0][1]
    assert channels[1]["waveform"] == "Sinus"
    for item in channels.values():
        amplitude_min, amplitude_max = DRIVER_AMPLITUDE_LIMITS[item["driver_index"]]
        frequency_min, frequency_max = DRIVER_FREQUENCY_LIMITS[item["driver_index"]]
        assert amplitude_min <= item["amplitude_vpp"] <= amplitude_max
        assert frequency_min <= item["frequency_hz"] <= frequency_max


def test_apply_does_not_raise_for_an_unknown_driver_index():
    profile = {"pump_configuration": {"channels": [
        {"channel": 6, "driver_index": 2, "frequency_hz": 120,
         "amplitude_vpp": 150, "waveform": "Rect-Like"},
    ]}}
    for driver_index in (7, -1, "not-a-number", None):
        target = {
            "name": "MB1", "port": "COM3", "drivers": [
                {"driver_index": driver_index, "frequency": 100, "waveform": "Sinus", "channels": [
                    {"channel": 6, "amplitude": 100, "enabled": True},
                ]},
            ],
        }
        warnings = apply_board_profile(profile, target)
        driver = target["drivers"][0]
        # Values inside the widest configured range are still applied, and the
        # channel is forced off regardless of the unknown index.
        assert driver["frequency"] == 120
        assert driver["waveform"] == "Rect-Like"
        assert driver["channels"][0]["amplitude"] == 150
        assert driver["channels"][0]["enabled"] is False
        assert any("Unknown driver index" in warning for warning in warnings)
