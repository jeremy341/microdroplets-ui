# Firmware-only board status

## Board card

The connected-board card shows the real firmware returned by the `V` command.
For the tested board this becomes:

```text
Firmware v2.20251002#0   COM3
```

The firmware reader updates the card asynchronously after the serial response
arrives, so it no longer remains stuck at `Detecting...`.

## Pump cards

All pump-driver cards remain visible and interactive. The old amplitude probes
(`P1V?` through `P6V?`) and their disabled overlay were removed. Those commands
return configured amplitude values, not reliable physical-driver presence.

## Hardware counts

The Home device summary now shows `—` for pump-driver, valve-driver, and sensor
counts until the board provides an explicit inventory response. It no longer
pretends that the number of UI cards or configured sensor definitions is a
physical hardware count. The current Multiboard blank-settings response gives
firmware/settings data, but not a reliable complete valve/sensor inventory.
