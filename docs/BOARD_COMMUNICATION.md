# Multiboard Communication and Serial Protocol

This document describes how FluidicStudio communicates with a Bartels
mp-Multiboard2 from UI request to bytes on the wire and back to backend state.

The production sources of truth are:

- `backend/protocol.py` — command builders and reply parser;
- `backend/serial_manager.py` — transport, ACK handling, state machine and sensor stream.

Evidence note: command syntax and parser behavior are software-tested in the
repository; physical command behavior remains bench-dependent. See the
[hardware validation matrix](developer/HARDWARE_VALIDATION_MATRIX.md).

## 1. Physical transport

Current production configuration:

```text
baud rate: 115200
bytes:     8
parity:    none
stop bits: 1
timeout:   0.1 s
```

The current workstation setup has used the Multiboard through a CP210x USB-UART
bridge. COM port identity can change between PCs, so no production logic should
hard-code `COM3` or another specific port.

## 2. Command framing

Every production command goes through:

```python
backend.protocol.encode_command(command)
```

Encoding is:

```text
ASCII command + CRLF
```

Example:

```text
Python: "P1ON"
Wire:   P1ON\r\n
```

`encode_command()` rejects empty/multiline commands. Keep this framing rule in
one place.

## 3. Reader framing

The reader thread continuously reads chunks from the serial port and buffers
them until `\n` is found. Each complete line is decoded as UTF-8 with replacement
for invalid bytes, stripped of CR/LF and passed to `parse_reply()`.

Raw lines are published before interpretation. This is intentional: unknown
firmware text must remain visible to diagnostics instead of being silently
ignored.

## 4. Production command categories

### Board identity

| Command | Purpose | Production status |
| --- | --- | --- |
| `V` | request Multiboard firmware/version/identification | used |

The firmware can also emit a boot banner such as `Multiboard Ready`.

### Global pump safety

| Command | Purpose | Production status |
| --- | --- | --- |
| `POFF` | globally disable pump outputs | used during safe disconnect/shutdown |

`POFF` is intentionally not the normal per-channel stop path. Normal pump/wave
stops use the affected channel's `P<n>OFF` so unrelated channels remain running.

### Per-channel pump state

```text
P1ON ... P6ON
P1OFF ... P6OFF
```

Built by `pump_state_command(channel, enabled)`.

### Per-channel amplitude

```text
P1V120
P2V80
...
```

Built by `driver_amplitude_command()` after driver/channel capability checks.
The serial representation uses integer Vpp values.

### Shared driver frequency

```text
F0=100   → driver domain for CH1–CH4
F1=100   → CH5 driver domain
F2=100   → CH6 driver domain
```

Frequency belongs to the **driver**, not to one channel.

### Carrier/signal waveform

For driver types that support it:

```text
CS<driver>=<code>
```

Current code mapping:

| UI name | Code |
| --- | ---: |
| Sinus | 0 |
| Sinus-Like | 1 |
| Rect-Like | 2 |
| Rect. | 3 |

Carrier shape is shared at driver level. Explicit carrier changes through
`PumpControlService.set_driver_waveform()` are allowed only while all channels
on that physical driver are idle. Configure-then-start sequences also transmit
the currently selected carrier value, so sibling views must remain synchronized.

### Sensor/calibration commands

The protocol table currently defines:

| Sensor | Start | Stop | Unit in FluidicStudio | Notes |
| --- | --- | --- | --- | --- |
| liquid flow | `DFON` | `DFOFF` | µL/min | software path; liquid-flow bench evidence must be recorded separately |
| pressure | `DPON` | `DPOFF` | mbar | protocol-defined, hardware validation may be incomplete |
| gas flow | `DGON` | `DGOFF` | mL/min | protocol-defined |
| analog 1 | `DA1ON` | `DA1OFF` | raw | protocol-defined |
| analog 2 | `DA2ON` | `DA2OFF` | raw | protocol-defined |
| analog 3 | `DA3ON` | `DA3OFF` | raw | protocol-defined |
| thermal conductivity | `DCON` | `DCOFF` | raw | firmware-dependent |
| CO2 | `DCO2ON` | `DCO2OFF` | ppm | firmware-dependent |
| VOC | `DVOCON` | `DVOCOFF` | raw | firmware-dependent |

Liquid-flow calibration:

```text
L0 → water
L1 → IPA
```

Do not interpret this table as proof that every listed sensor has been validated
with current physical hardware. See [Hardware Limitations](HARDWARE_LIMITATIONS.md).

## 5. ACK transactions

For hardware-changing commands, the important distinction is:

```text
write succeeded != hardware action confirmed
```

`send_and_wait_for_ack()`:

1. appends the command to a FIFO of outstanding commands;
2. writes the command;
3. waits for the reader thread to parse `OK` or an error;
4. returns success only after the ACK is matched.

Several acknowledged commands may be outstanding at once. Every `OK`/`FAIL`/
`ERR` line is attributed to the **oldest** outstanding command, because the
board answers in wire order. A command that timed out stays in the queue, so a
reply that arrives late is consumed by that command's own entry instead of
acknowledging the next one. Outdated entries stop correlating replies after
5 s (`STALE_REPLY_SECONDS`), which also bounds queue growth.

The ACK wait happens outside the command lock, so a slow board blocks only the
waiting caller, never other senders.

Known limitation: correlation is positional, not a firmware transaction id. If
a board reply is *lost* rather than late, every following rapid transaction can
be mis-attributed until the stale head is pruned (5 s of send silence). Pump
sequences sent less than a second apart can therefore fail spuriously after a
single lost ACK. The failure direction is a false failure plus rollback, not a
false success, unless replies are both lost and delayed beyond the prune window.
A firmware build that echoes the command text would remove this class entirely.

## 6. Atomic command sequences

Pump startup requires multiple commands:

```text
frequency
→ optional carrier waveform
→ channel amplitude
→ channel ON
```

`send_sequence()` sends its commands one by one, each with its own attributed
ACK, and applies the caller-supplied rollback when a command is not
acknowledged. Transport-level attribution is per command through the ACK FIFO,
so another sender may interleave at the wire level; the business-level
serialization (one pump operation at a time) lives in
`backend/pump_control.py`.

The normal start helper is:

```python
pump_start_commands(...)
```

For a supported carrier driver it produces conceptually:

```text
F0=100
CS0=0
P1V120
P1ON
```

## 7. Rollback on failed startup

Pump/wave startup supplies a per-channel rollback command:

```text
P<channel>OFF
```

If a startup step is not acknowledged, the backend attempts that OFF.

Three states matter:

```text
startup success
→ output known ON

startup failure + OFF acknowledged
→ output known OFF; ownership can be released

startup failure + OFF not acknowledged
→ physical state UNKNOWN; ownership is retained
```

Retaining ownership in the unknown case prevents another control path from
taking over a channel that might still be physically active.

## 8. Reply parsing

`parse_reply()` recognizes these classes:

### ACK

```text
OK
```

The board may prefix console output with `<<`; the intended contract is to
normalize it before classification. The current implementation does not apply
that normalization consistently to every error/measurement path, so raw lines
must remain available when debugging a prefixed response.

### Error

Lines starting with forms such as:

```text
FAIL
ERR
ERROR
WRONG COMMAND
```

are treated as command errors.

### Firmware/boot

```text
Multiboard Ready
Multiboard ...
```

are used for handshake/firmware state.

### Sensor measurement

The parser accepts marked forms such as:

```text
RSLF <number>
RSDPC <number>
CO2 <number>
VOC <number>
```

and plain:

```text
V=<number>
```

when exactly one active sensor stream gives the line an unambiguous meaning.

Unknown lines remain `unknown` rather than having arbitrary numbers extracted.

### ESP32/I2C diagnostics

Known `Wire.cpp`/I2C diagnostics are preserved as diagnostics, not mistaken for
Multiboard command rejections.

## 9. Liquid-flow unit boundary

The firmware liquid-flow numeric value is treated as mL/min. FluidicStudio
normalizes it **once** in `parse_reply()`:

```text
mL/min × 1000 → µL/min
```

Everything downstream — charts, history, CSV and volume integration — uses the
normalized `µL/min` value.

Do not multiply by 1000 again in a page.

## 10. Flow-sensor initialization state machine

Board connection does not immediately claim a sensor is present.

`initialize_liquid_flow()` performs:

```text
request V / identify board
→ DFOFF
→ L0
→ DFON
→ wait for 2 valid finite measurements
```

A liquid-flow sensor is intended to be considered available only after real
liquid-flow samples arrive. Zero and negative readings are valid numeric readings
and are not treated as absence. The current implementation audit found that a
generic parsed measurement can satisfy this count, so adding another stream
requires a regression test proving measurement-type filtering.

If no valid samples arrive, the initializer retries instead of freezing Qt.

After success it also acts as a lightweight watchdog. If the stream stops
producing valid samples for about five seconds, the full setup sequence is
retried.

## 11. Flow volume integration

Liquid-flow samples are integrated with a trapezoidal rule:

```text
average(flow_previous, flow_current) × Δt / 60
```

Because flow is in µL/min, the resulting accumulated volume is µL.

If a receive gap exceeds two seconds, FluidicStudio starts a new integration
segment rather than pretending the previous flow continued through missing data.

## 12. Event publication

Every connection publishes `BackendEvent` objects.

Important kinds include:

```text
connected
command
raw
ack
error
firmware
boot
state
measurement
sensor_retry
disconnected
```

The connection supports:

- a normal event queue (`drain_events()`);
- non-consuming subscribers (`subscribe_events()`).

This allows multiple views to observe the same stream without stealing events
from each other.

Both queues are bounded. `drain_events()` holds at most `EVENT_QUEUE_LIMIT`
entries and sheds the oldest on overflow, counting every shed event in
`dropped_events()`; because a resumed consumer then sees current board state
rather than a stale backlog, subscribers (`subscribe_events()`) still receive
every event. The reader's unframed byte buffer is capped at
`MAX_PENDING_LINE_BYTES`; a longer newline-free run is discarded whole and
counted in `discarded_rx_bytes()`/`rx_buffer_overflows()` so framing resumes at
the next delimiter and no partial run is ever parsed as a protocol line.

A reader failure can still leave the connection marked open while command paths
remain callable, so consumers must treat reader-error/disconnected events as a
transport fault rather than a recoverable notification.

## 13. Connection close behavior

`MultiboardConnection.close()` always finishes the teardown:

```text
cancel sensor initializer
 request POFF and wait for ACK
 retry POFF once if necessary
 stop sensor stream
 stop reader thread and initializer
 close serial port
 return whether POFF was acknowledged
```

A failed or unacknowledged POFF never leaves a running stream, a live reader or
an open port behind. The return value reports the power-off fact only: `False`
means "teardown finished, but the board never confirmed the global pump
shutdown". Calling `close()` again on an already-disconnected connection returns
`True` without side effects, so a caller can retry and then finish its own
cleanup. Treat an unconfirmed POFF as an unknown physical output state; it is
reported, not silently swallowed.

The application shutdown path currently does not surface every failed `close()`
result before allowing Qt to terminate. A process exit therefore cannot by
itself be used as proof that `POFF` was acknowledged; verify the physical setup
when shutdown is interrupted or unconfirmed.

## 14. Read-only/diagnostic commands

Driver reconnaissance uses separate tools and must not be confused with
production driver selection.

`tools/driver_detection_probe.py` can use:

- `V`;
- a blank CRLF to request settings on firmware that supports it;
- optional amplitude queries such as `P1V?` ... `P6V?`.

The returned `Driver:` text is stored as an opaque fingerprint. See
[Driver Detection](DRIVER_DETECTION.md).

## 15. Adding a new command safely

1. Verify the command in manufacturer documentation or a controlled probe.
2. Add validation/building to `backend/protocol.py`.
3. Decide whether the command requires ACK confirmation.
4. Decide whether it changes a channel resource or a shared driver resource.
5. Route it through the appropriate backend service.
6. Add parser support only for documented/observed replies.
7. Add a regression test.
8. Update this document and relevant page/engine docs.

Do not make a UI widget write bytes directly to `serial.Serial`.

## 16. Relevant tests

```text
test_protocol.py
test_serial_manager.py
test_raw_serial_capture.py
test_sensor_stream_capture.py
test_pump_control.py
test_live_frequency.py
test_driver_configuration.py
```
