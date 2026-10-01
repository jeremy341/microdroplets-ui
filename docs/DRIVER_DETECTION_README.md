# Driver detection

When an MP-Multiboard2 connects, the app performs three read-only amplitude
queries before exposing pump controls:

| Driver group | Probe | Channels |
| --- | --- | --- |
| mp-Highdriver4 | `P1V?` | CH1–CH4 |
| mp-Highdriver / mp-Lowdriver | `P5V?` | CH5 |
| mp-Driver | `P6V?` | CH6 |

A numeric response marks the driver as available. An error or timeout marks it
as unavailable. The detection never sends `PON`, `POFF`, `P<p>ON`, `P<p>OFF`, or
any amplitude/frequency write command.

Unavailable driver cards remain visible for orientation but are disabled and
show a clear message, matching the behavior of Bartels FluidicStudio.
