# Mic PTT signal follow-up — 2026-11-05

Scope is PTT only. Mic UP/DOWN mapping is explicitly deferred.

The latest physical capture monitored six evdev devices and sampled the GUI's
MCU packet buffer for 55 seconds. All 2,383 sampled packet buffers match an
independently recomputed word-wise CRC; repeat reads were equal. Panel PTT
produced evdev code 707 press/release. Panel and both mic PTT intervals changed
status flags from 0x18 to 0x1a and back: aggregate TX bit 1. During those PTT
intervals hkey remained zero; status bytes 528..543 stayed constant. This is a
bounded sampled observation, not every UART packet or physical RF measurement.

The sysfs GPIO observation was empty. That does not inventory all Linux GPIO:
the pinned X6200Control `src/low/gpio.c` uses libgpiod character-device requests
for Wi-Fi, USB routing, backlight, CW output and baseband reset. These known
library pins are outputs; their existence supplies no independent mic PTT API.

## Targeted independent firmware check

The supplied XGF SHA256 is
`9a19f97a5ad9e141a3663d222f984073bf0b18b38d4662604372a0036f9daa23`.
Offline decryption reproduces the full report's 524,288-byte plaintext SHA256
`c3f7d028bc63e43fbb478a09e1032d46059b71efe217a60b576231866fbc2c0a`.
This follow-up used Capstone 5.0.6 Thumb/M-class disassembly of selected blocks;
it did not recreate or claim the report's complete Ghidra project/analysis.

| Address in 1.0.8 image | Checked behavior |
| --- | --- |
| Vector 39 / 0x0802369c | EXTI9_5 handler dispatches pin mask 0x40. |
| 0x0802a080 | Checks/acknowledges EXTI pending state then calls 0x08023cc4. |
| 0x08023ccc..0x08023ce6 | Mask 0x40 reaches 0x0803dd20 behind a runtime gate. |
| 0x0803dd72..0x0803dd80 | Reads GPIOH base 0x58021c00, mask 0x40, inverts active-low level. |
| 0x0802a06c | Pin-read helper reads GPIO IDR at base + 0x10. |
| 0x0803dd20..0x0803dd70 | Host/frequency/mode admission can return before the raw pin sample. |
| 0x0803dd8a..0x0803ddce | Compares/stores admitted input in shared byte 0x24025074 and reconciles TX. |
| 0x0803791c..0x08037924 | Software-TX aggregation clears that shared byte. |

This independently checks positive paths in the supplied report. It does not
prove that every other interface is absent. The connector-to-PH6 board net is
still unmeasured, and the installed MCU identity was not acquired in the latest
Linux-only capture. The report's targeted 1.0.7 equivalence is supplied evidence,
not a newly performed binary comparison in this follow-up.

## Next discriminating observation

Look for an R16-side copy of the mic PTT signal that was outside evdev, sysfs and
the normal MCU status packet. Helper .3 `-PttPins` reads only the validated
R16/A33 PIO data/mux registers and the existing GUI packet buffer while the GUI
keeps operating. Unknown SoC mapping or denied `/dev/mem` access stops the
prerequisite check before any PTT prompts. The helper never guesses register
addresses on an unmatched device tree or reconfigures pins to make them readable.

Compare panel versus repeated mic presses, pin mux direction, and timing against
aggregate TX. Any candidate must subsequently be distinguished from a derived
TX output and electrically mapped to the connector; correlation alone does not
qualify raw mic release, retained events, or operation under software TX.

No candidate would narrow the search toward MCU-only wiring or another host
interface, without proving either. Physical RJ45-to-board continuity/signal
measurement or access to the MCU input through a verified diagnostic interface
would then be more useful than repeating status logging. No baseband patch was
made or flashed, and no raw MCU GPIO read interface was established here.

References:

- [Pinned X6200Control GPIO implementation](https://github.com/gdyuldin/X6200Control/blob/0ee1ae3d4d9d74cbac7e349147ebc440eed8fad8/src/low/gpio.c)
- [Linux v6.1 PIO register layout](https://github.com/torvalds/linux/blob/v6.1/drivers/pinctrl/sunxi/pinctrl-sunxi.h)
- [A33 pin descriptions](https://github.com/torvalds/linux/blob/v6.1/drivers/pinctrl/sunxi/pinctrl-sun8i-a33.c)
- [Always-on pin descriptions](https://github.com/torvalds/linux/blob/v6.1/drivers/pinctrl/sunxi/pinctrl-sun8i-a23-r.c)
- [Device-tree register ranges](https://github.com/torvalds/linux/blob/v6.1/arch/arm/boot/dts/sun8i-a23-a33.dtsi)
