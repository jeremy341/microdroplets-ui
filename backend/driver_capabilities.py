"""Authoritative pump-driver capabilities for Pumps, Waves and sessions."""

from __future__ import annotations

from dataclasses import dataclass

from backend.driver_config import DriverConfiguration, get_driver_config


DRIVER_CHANNELS = {
    0: range(1, 5),
    1: range(5, 6),
    2: range(6, 7),
}


@dataclass(frozen=True)
class DriverCapabilities:
    driver_type: str
    display_name: str
    driver_index: int
    amplitude_min_vpp: int
    amplitude_max_vpp: int
    frequency_min_hz: int
    frequency_max_hz: int
    hardware_frequency_min_hz: float
    hardware_frequency_max_hz: float
    amplitude_control_bits: int | None
    supports_carrier_waveform: bool
    wave_quantization: str

    @property
    def amplitude_limits(self) -> tuple[int, int]:
        return self.amplitude_min_vpp, self.amplitude_max_vpp

    @property
    def frequency_limits(self) -> tuple[int, int]:
        return self.frequency_min_hz, self.frequency_max_hz


# The normal app uses integer Vpp serial commands.  Lowdriver itself has an
# 8-bit amplitude engine, but the public Multiboard command is still P5V<a> in
# integer Vpp, so we do not invent fractional-Vpp serial commands.
_CAPABILITY_TEMPLATES = {
    "highdriver4": dict(
        display_name="mp-Highdriver4",
        amplitude_min_vpp=10,
        amplitude_max_vpp=250,
        frequency_min_hz=50,
        frequency_max_hz=800,
        hardware_frequency_min_hz=50.0,
        hardware_frequency_max_hz=800.0,
        amplitude_control_bits=5,
        supports_carrier_waveform=True,
        wave_quantization="highdriver4_5bit",
    ),
    "highdriver": dict(
        display_name="mp-Highdriver",
        amplitude_min_vpp=10,
        amplitude_max_vpp=250,
        frequency_min_hz=50,
        frequency_max_hz=800,
        hardware_frequency_min_hz=50.0,
        hardware_frequency_max_hz=800.0,
        amplitude_control_bits=None,
        supports_carrier_waveform=True,
        wave_quantization="integer_vpp",
    ),
    "lowdriver": dict(
        display_name="mp-Lowdriver",
        amplitude_min_vpp=0,
        amplitude_max_vpp=150,
        # The Lowdriver electronics extend to about 2 kHz.  FluidicStudio caps
        # normal BP7 operation at 800 Hz in line with Bartels' pump guidance.
        frequency_min_hz=8,
        frequency_max_hz=800,
        hardware_frequency_min_hz=7.8125,
        hardware_frequency_max_hz=1992.1875,
        amplitude_control_bits=8,
        supports_carrier_waveform=False,
        wave_quantization="integer_vpp",
    ),
    "mp_driver": dict(
        display_name="mp-Driver",
        amplitude_min_vpp=85,
        amplitude_max_vpp=250,
        frequency_min_hz=25,
        frequency_max_hz=226,
        hardware_frequency_min_hz=25.0,
        hardware_frequency_max_hz=226.0,
        amplitude_control_bits=None,
        supports_carrier_waveform=False,
        wave_quantization="integer_vpp",
    ),
}


def driver_index_for_channel(channel: int) -> int:
    if type(channel) is not int:
        raise ValueError("Pump channel must be an integer")
    for driver_index, channels in DRIVER_CHANNELS.items():
        if channel in channels:
            return driver_index
    raise ValueError(f"Unsupported pump channel: CH{channel}")


def driver_type_for_index(
    driver_index: int,
    config: DriverConfiguration | None = None,
) -> str:
    config = config or get_driver_config()
    try:
        return {
            0: config.ch1_4,
            1: config.ch5,
            2: config.ch6,
        }[int(driver_index)]
    except (KeyError, ValueError) as exc:
        raise ValueError("Driver index must be 0, 1, or 2") from exc


def capabilities_for_driver_index(
    driver_index: int,
    config: DriverConfiguration | None = None,
) -> DriverCapabilities:
    driver_index = int(driver_index)
    driver_type = driver_type_for_index(driver_index, config)
    values = _CAPABILITY_TEMPLATES[driver_type]
    return DriverCapabilities(
        driver_type=driver_type,
        driver_index=driver_index,
        **values,
    )


def capabilities_for_channel(
    channel: int,
    config: DriverConfiguration | None = None,
) -> DriverCapabilities:
    return capabilities_for_driver_index(driver_index_for_channel(channel), config)


def configured_capabilities(
    config: DriverConfiguration | None = None,
) -> dict[int, DriverCapabilities]:
    return {
        index: capabilities_for_driver_index(index, config)
        for index in DRIVER_CHANNELS
    }


def configured_amplitude_limits(
    config: DriverConfiguration | None = None,
) -> dict[int, tuple[int, int]]:
    return {
        index: capability.amplitude_limits
        for index, capability in configured_capabilities(config).items()
    }


def configured_frequency_limits(
    config: DriverConfiguration | None = None,
) -> dict[int, tuple[int, int]]:
    return {
        index: capability.frequency_limits
        for index, capability in configured_capabilities(config).items()
    }
