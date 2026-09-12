# Troubleshooting

Use the lowest layer that can explain the symptom. Do not start by changing UI
code for a physical or transport problem.

## No board connection

1. Check the USB cable, Windows port, and board power.
2. Confirm the selected port and `data/driver_config.json`.
3. Run the diagnostic guidance in [Testing and Diagnostics](../TESTING_DIAGNOSTICS.md).
4. Check the serial/ACK evidence before retrying a pump command.
5. If the reader failed, assume the physical output state is unknown.

## Pump will not start or stop

Check whether the channel is owned by a Wave run. A channel cannot be manually
controlled while that same channel is Wave-owned. An action can also be rejected
after the UI has been rendered, so read the status message and verify the board.

If OFF is not acknowledged, stop interacting with the experiment and verify the
physical system. Reconnecting or closing the application is not proof of OFF.

## Sensor values are missing or stale

Confirm the sensor stream is enabled and that the board is returning the expected
measurement type. After disconnect/reconnect, verify that the chart and latest
value are refreshed rather than relying on an old visible value.

## Camera preview or recording is unreliable

Confirm that the external DNX64 runtime is installed when a Dino-Lite-specific
control is required. Stop/start transitions can race with the camera worker;
after an error, stop the camera, wait for the status to settle, and restart it.
Capture and Record are safe no-ops while stopped, but their current presentation
does not always make that state obvious.

## Analytics results look stale or incomplete

Check the source video and Analytics cache. Cancellation or truncated input can
produce partial work, and cache keys currently depend heavily on the video stem.
Use a unique input filename and clear/inspect the matching cache when results do
not correspond to the selected parameters.

## Need developer help

Include the application log, the exact page/action sequence, hardware model and
firmware context, whether the issue reproduces without hardware, and the relevant
diagnostic output. Do not attach vendor DLLs or real experiment recordings.
