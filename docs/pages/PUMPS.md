# Pumps Page

The Pumps page is the complete manual-control interface for pump channels and their physical driver groups.


For backend ownership, shared-driver state and ACK/rollback behavior, see [Pump + Wave Engine](../PUMP_WAVE_ENGINE.md) and [Multiboard Communication](../BOARD_COMMUNICATION.md).

## User model

The most important distinction is between **driver-level** and **channel-level** settings.

```text
Driver 0 / F0
├─ CH1
├─ CH2
├─ CH3
└─ CH4
```

For CH1–CH4:

```text
Shared:
- frequency
- supported carrier/signal mode

Independent per channel:
- amplitude (Vpp)
- ON/OFF
- channel ownership
```

This means CH1 and CH2 can both be ON at different amplitudes. Changing F0 changes the frequency for both.

## Driver controls

The page builds driver cards from the configured capability model.

Driver-level controls include:

- Driver frequency;
- signal/carrier mode where the configured driver supports it.

Frequency is a live shared property and can be changed while pumps/waves on that driver are active.

Carrier mode is stricter: the backend changes it only when the complete driver group is idle.

## Channel controls

Each channel provides:

- amplitude slider;
- numeric Vpp input;
- carrier/signal context when supported by the driver;
- ON/OFF switch.

The saved waveform library belongs to the Wave page. Pumps exposes the current
driver carrier/signal setting, not a saved Wave-program selector.

When a channel is OFF, amplitude controls look muted but remain editable. This is intentional: the user can stage the next amplitude without activating the pump.

When ON, amplitude changes are sent through the manual pump backend.

If the channel is currently Wave-owned, manual edits can be rejected by the
backend while the control is still rendered. Read the ownership/status message;
do not treat an unchanged control as proof that a command was accepted.

## Ownership/interlocks

A manual pump and generated waveform may not own the **same channel** simultaneously.

Valid:

```text
CH1 manual + CH2 manual
CH1 manual + CH2 waveform
```

Invalid:

```text
CH1 manual + CH1 waveform
```

This rule is implemented centrally by `ChannelOwnershipManager` and `PumpControlService`. The Pumps page should not create a second interlock model.

## Shared frequency synchronization

Driver frequency state comes from the shared pump service. A view for CH1 and a view for CH2 must never show two different acknowledged F0 values.

UI polling must not overwrite a slider/spinbox while the user is actively interacting with it. Preserve the existing editing/drag guards when changing refresh code.

## Acknowledgements and rollback

Starting/stopping a pump is not treated as successful merely because a serial string was written.

`backend/pump_control.py` uses acknowledged command sequences and safe rollback behavior. If a transport error makes physical state unknown, ownership can be retained to prevent another control path from taking over an output that may still be active.

## Workspace relationship

Pump Workspace is a compact two-channel view of the same pump state.

Workspace-specific presentation state may include which two channels are shown, but the following are shared with Pumps:

- frequency;
- signal mode;
- amplitude;
- ON/OFF;
- ownership;
- pending/acknowledged state.

## Relevant files

```text
ui/pages/Pumps.py
backend/pump_control.py
backend/channel_ownership.py
backend/driver_frequency_state.py
backend/driver_capabilities.py
backend/driver_config.py
backend/protocol.py
```

## Relevant tests

```text
test_pump_control.py
test_pump_command_architecture.py
test_channel_ownership.py
test_live_frequency.py
test_driver_configuration.py
test_workspace_pumps_*.py
```

## Navigation

```text
Shift+2
```

For physical driver layout and limits, also read [Hardware and Board Architecture](../HARDWARE_ARCHITECTURE.md) and [Hardware Limitations](../HARDWARE_LIMITATIONS.md).
