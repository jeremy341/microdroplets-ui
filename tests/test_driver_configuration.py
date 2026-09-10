import json

import pytest

from backend.driver_capabilities import (
    capabilities_for_channel,
    capabilities_for_driver_index,
    driver_index_for_channel,
)
from backend.driver_config import DriverConfigError, DriverConfiguration, load_driver_config
from backend.session_runtime import apply_board_profile, build_board_profile


def write_config(path, *, ch1_4="highdriver4", ch5="lowdriver", ch6="mp_driver"):
    path.write_text(
        json.dumps(
            {
                "schema_version": 1,
                "drivers": {
                    "ch1_4": ch1_4,
                    "ch5": ch5,
                    "ch6": ch6,
                },
            }
        ),
        encoding="utf-8",
    )


def test_missing_config_uses_intended_lowdriver_default(tmp_path):
    config = load_driver_config(tmp_path / "missing.json")
    assert config == DriverConfiguration(ch5="lowdriver")


def test_lowdriver_and_highdriver_are_the_only_ch5_choices(tmp_path):
    low_path = tmp_path / "low.json"
    high_path = tmp_path / "high.json"
    write_config(low_path, ch5="lowdriver")
    write_config(high_path, ch5="highdriver")
    assert load_driver_config(low_path).ch5 == "lowdriver"
    assert load_driver_config(high_path).ch5 == "highdriver"


@pytest.mark.parametrize(
    "overrides",
    [
        {"ch5": "none"},
        {"ch5": "banana"},
        {"ch1_4": "lowdriver"},
        {"ch6": "highdriver"},
    ],
)
def test_invalid_physical_driver_layout_is_rejected(tmp_path, overrides):
    path = tmp_path / "driver_config.json"
    write_config(path, **overrides)
    with pytest.raises(DriverConfigError):
        load_driver_config(path)


def test_malformed_config_is_rejected_instead_of_guessed(tmp_path):
    path = tmp_path / "driver_config.json"
    path.write_text("{not-json", encoding="utf-8")
    with pytest.raises(DriverConfigError):
        load_driver_config(path)


def test_physical_frequency_domains_are_fixed_by_channel():
    assert [driver_index_for_channel(ch) for ch in (1, 2, 3, 4)] == [0, 0, 0, 0]
    assert driver_index_for_channel(5) == 1
    assert driver_index_for_channel(6) == 2


def test_default_ch5_lowdriver_capabilities_are_not_highdriver4_quantization():
    cap = capabilities_for_channel(5, DriverConfiguration(ch5="lowdriver"))
    assert cap.driver_type == "lowdriver"
    assert cap.display_name == "mp-Lowdriver"
    assert cap.amplitude_limits == (0, 150)
    assert cap.frequency_limits == (8, 800)
    assert cap.amplitude_control_bits == 8
    assert cap.supports_carrier_waveform is False
    assert cap.wave_quantization == "integer_vpp"


def test_ch5_highdriver_alternative_changes_capabilities_but_not_domain():
    config = DriverConfiguration(ch5="highdriver")
    cap = capabilities_for_driver_index(1, config)
    assert cap.driver_type == "highdriver"
    assert cap.display_name == "mp-Highdriver"
    assert cap.amplitude_limits == (10, 250)
    assert cap.frequency_limits == (50, 800)
    assert cap.supports_carrier_waveform is True
    assert cap.driver_index == 1


def test_sessions_do_not_persist_or_override_physical_ch5_driver_type():
    board = {
        "name": "MB1",
        "port": "COM3",
        "drivers": [
            {
                "driver_index": 1,
                "driver_type": "lowdriver",
                "frequency": 175,
                "waveform": "Sinus",
                "channels": [{"channel": 5, "amplitude": 25, "enabled": False}],
            }
        ],
    }
    profile = build_board_profile(board)
    serialized_channel = profile["pump_configuration"]["channels"][0]
    assert "driver_type" not in serialized_channel

    board["drivers"][0]["driver_type"] = "highdriver"
    apply_board_profile(profile, board)
    assert board["drivers"][0]["driver_type"] == "highdriver"
