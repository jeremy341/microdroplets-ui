# Bartels mp-Multiboard2 pump-control research

Research date: 2026-08-10

## Scope

This note records public, documented information needed to implement pump
control through the mp-Multiboard2 USB serial interface. It does not describe
license bypassing or decompilation of Bartels FluidicStudio.

## Correct Google syntax

Google's `site:` operator takes a hostname without square brackets, Markdown,
or a space after the colon. `content:documents` is not a Google operator.

Use:

```text
site:bartels-mikrotechnik.de
site:bartels-mikrotechnik.de filetype:pdf
site:bartels-mikrotechnik.de (manual OR datasheet OR documentation)
site:bartels-mikrotechnik.de/support-center/ (Multiboard OR pump OR driver)
site:bartels-mikrotechnik.de/wp-content/uploads/ filetype:pdf Multiboard
site:bartels-mikrotechnik.de/wp-content/uploads/ filetype:zip Multiboard
site:bartels-mikrotechnik.de/wp-content/uploads/ filetype:zip pumpdriver
site:bartels-mikrotechnik.de/wp-content/uploads/ (firmware OR source-code OR Arduino OR ESP32)
site:bartels-mikrotechnik.de ("mp-Multiboard2" OR "Bartels FluidicStudio")
site:bartels-mikrotechnik.de "USB/Serial Communication Protocol"
site:bartels-mikrotechnik.de ("P1ON" OR "P1V250" OR "F0=100" OR "CS0=0")
```

For indexed code and mirrors outside the Bartels domain:

```text
("Bartels Mikrotechnik" OR "mp-Multiboard2") (GitHub OR GitLab)
site:github.com "mp-Multiboard2"
site:github.com "I2C_HIGHDRIVER4_ADRESS"
site:github.com "I2C_LOWDRIVER_ADRESS"
site:github.com "P1V250" Multiboard
"Pumpdriver_Arduino_Example_Code.zip"
"mp-Highdriver4Demo.ino"
"Firmware Paket 20230317.zip"
```

Google can find only indexed files. A dynamically generated download, account
download, robots-blocked file, or unlinked WordPress media item may not appear.

## Primary public sources

1. Software Manual v1.6 (December 2025):
   https://bartels-mikrotechnik.de/wp-content/uploads/2025/03/software-manual.pdf
2. Electronic Driver Datasheet v1.7 (August 2025):
   https://bartels-mikrotechnik.de/wp-content/uploads/2025/03/datasheet-electronic-driver.pdf
3. mp-Multiboard2 product page:
   https://bartels-mikrotechnik.de/product/mp-multiboard2-en/
4. Support Center / downloads and knowledge base:
   https://bartels-mikrotechnik.de/support-center/
5. Older Software Manual v1.3 mirror (use only for historical comparison):
   https://darwin-microfluidics.com/content/pdfs/Software-Manual-v1.3.pdf

The current official manual explicitly says that FluidicStudio communicates
with the ESP32 through USB Serial and that Python, MATLAB, LabVIEW, or a serial
terminal can use the same protocol. Serial settings are 115200 baud, 8 data
bits, no parity, one stop bit, and every command ends in CRLF (`\r\n`).

## Documented USB serial commands relevant to this project

| Purpose | Command | Example / notes |
|---|---|---|
| Start all pumps | `PON` | Manual action only |
| Stop all pumps | `POFF` | Safe shutdown command |
| Start one pump | `P<p>ON` | `P1ON` |
| Stop one pump | `P<p>OFF` | `P1OFF` |
| Set amplitude | `P<p>V<a>` | `P1V250` means 250 Vpp |
| Read amplitude | `P<p>V?` | Returns configured amplitude, not presence |
| Set driver frequency | `F<d>=<f>` | `F0=100` |
| Set signal shape | `CS<d>=<s>` | `CS0=0` means sinusoid |
| Firmware | `V` | Returns firmware identity/version |
| Current settings | blank Enter | Includes lines such as `Driver:4D` |

Driver indices are documented as:

- driver `0`: CH1-CH4 (`mp-Highdriver4`)
- driver `1`: CH5 (`mp-Highdriver` or `mp-Lowdriver`)
- driver `2`: CH6 (`mp-Driver`)

Signal-shape values are:

- `0`: sinusoid
- `1`: first sine-to-rectangular degree
- `2`: second sine-to-rectangular degree
- `3`: rectangular

The `CS` command applies only to Highdriver4 and Highdriver hardware. Frequency
and signal shape are shared driver-level settings; amplitude and ON/OFF state
are channel-level settings.

## Findings from the supplied Arduino examples

The uploaded `Pumpdriver_Arduino_Example_Code.zip` contains four official-style
Arduino sketches dated 2022-2023.

### mp-Highdriver4

- Multiboard2 I2C address: `0x79`
- device ID register: `0x00`
- power/enable register: `0x01`
- common frequency register: `0x02`
- common signal-shape register: `0x03`
- boost register: `0x04`
- audio register: `0x05` (not supported; keep at zero)
- CH1-CH4 amplitude registers: `0x06` through `0x09`
- voltage update command/register: `0x0A`
- amplitude resolution: five bits (`0x00`-`0x1F`) for 0-250 Vpp
- `Highdriver_check()` reads register `0x00` and accepts device IDs whose upper
  nibble is `0xB`.

The current datasheet gives the reset device/revision value as `0xB2`. This is
strong evidence that Bartels firmware detects the Highdriver4 by reading its
I2C device-ID register. The serial settings string `Driver:4D` is a higher-level
firmware representation, not the physical device-ID byte.

### mp-Highdriver

- Multiboard2 I2C address: `0x7A`
- register layout is shared with Highdriver4
- only the fourth output slot/register is used by the single-channel driver
- amplitude range in the demo: 0-250 Vpp

### mp-Lowdriver

- I2C address: `0x59`
- register page is selected through `0xFF`
- waveform playback is stopped before changing amplitude/frequency and started
  again afterward
- demo amplitude conversion uses 0-150 Vpp to `0x00`-`0xFF`
- frequency byte uses approximately `frequency / 7.8125`
- the demo calls initialization twice, which should be treated as a hardware
  workaround until Bartels documents why it is required

### mp-Driver

- DAC I2C address: `0x61`
- ESP32 clock GPIO: 27
- ESP32 shutdown GPIO: 14
- frequency is generated with an ESP32 timer
- the demo's stated frequency range is 1-200 Hz
- amplitude conversion targets about 80-250 Vpp; zero disables the clock,
  shutdown pin, and DAC output

## Important safety and code-quality observations

- Do not upload/run the supplied demo sketches unchanged on connected hardware.
  Their `loop()` functions automatically start pumps, including a 250 Vpp test.
- Never connect/disconnect pumps or driver modules while the board is powered.
- `P<p>V?` reads a configured amplitude. It does not detect a physical pump or
  driver; a zero reply is valid when a channel is off.
- The examples are useful hardware references, not production application code.
  For example, the mp-Driver sketch contains an extra `Wire.write()` before
  `Wire.beginTransmission()`, and the examples lack robust I2C error handling.
- The desktop app should use the documented USB serial protocol. Direct I2C
  control would require replacing/customizing board firmware and adds avoidable
  risk for this project.

## Implementation decisions in this project version

- Pump controls now send documented serial commands instead of only printing.
- Slider movement is committed on release/edit completion to avoid serial
  command flooding.
- Enabling a pump sends: driver frequency, supported signal shape, channel
  amplitude, then channel ON.
- Disabling a pump sends the channel OFF command immediately.
- Closing the board connection sends `POFF` before closing the sensor stream.
- UI amplitude units are `Vpp`, not generic volts.
- UI waveform choices match codes 0-3 from the official manual.

Hardware testing should begin at a low, approved amplitude with correct tubing,
liquid, and pump orientation. The automatic test must never start a pump without
an explicit user action.
