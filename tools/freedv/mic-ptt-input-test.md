# Broad mic-button/PTT discovery — helper .2, existing 20261105.2 image

Use `Invoke-MicPttInputWatch.ps1 -RadioIp 192.168.4.210 -Wide` for the current
55-second round. No image update, GUI restart or baseband update is needed.

The source has a separate mic-key path: `x6200_flow_read` supplies `pack->hkey`
in `src/hw/x6200/hw_x6200.c`, `radio_tick` forwards changes in `src/radio.c`, and
`hkey_put` in `src/mic_keys.c` dispatches mic-button events. Evdev alone does not
observe that channel. Whether PTT also appears there remains a measured question.

The wide logger retains Linux input events and samples the GUI's existing
552-byte packet buffer at approximately 50 Hz, including all flags, hkey and
reserved/status bytes. It also watches already-exported Linux GPIO values at
approximately 10 Hz. It does not export/claim GPIOs, attach a debugger, open
UART/I2C devices, stop the GUI, or make radio control calls.

The narrowly bounded memory reader uses `/proc/PID/mem` read-only. It checks the
actual running GUI's SHA256, the exact ELF symbol-derived packet-pointer offset,
PIE mappings, readable ranges, packet magic and process identity. Permission or
layout failure blocks the prerequisite check BEFORE any press test. Do not press
buttons if it reports BLOCKED. There is no fallback to guessed memory addresses.

Each row preserves the full packet bytes and two-read equality. These are
sampled application-memory observations, not every UART packet, a guaranteed
coherent snapshot, independent RF evidence or retained physical PTT history.
The control library can alter its packet representation; wire CRC is not
claimed verified. Process restart or lost memory access stops collection.
Absence from evdev and sampled status does not rule out another board input.

Preparation: suitable dummy load, ordinary USB mode, lowest power, FreeDV/test
modes closed, transmitting CAT/TCI/modem applications closed, PTT released.
Choose a frequency away from a band edge because mic UP/DOWN can change it.
If TX persists after release, switch off the radio; do not wait for the logger.
Type READY after preparing, then follow the prompts:

| Time | Action |
| --- | --- |
| 0 seconds | All controls released. |
| 5 seconds | Briefly tap mic UP once. |
| 12 seconds | Briefly tap mic DOWN once. |
| 20 seconds | Radio side PTT, hold one second, release. |
| 30 seconds | Mic PTT, hold one second, release. |
| 40 seconds | Repeat mic PTT, hold one second, release. |
| 50 seconds | All controls released. |
| 55 seconds | Collection ends. |

Report whether UP/DOWN worked, whether each PTT press/release followed the TX
indicator, and any missed prompts. Confirm/restore the original frequency after
the test. Return the printed ZIP, including status-samples.jsonl and notes.
UP/DOWN are positive controls for the MCU mic-key channel; side PTT is a positive
control for evdev. Keep software/MODEM TX off throughout. A later overlapping
software-TX test requires a separately reviewed procedure.

## Original evdev-only mode (without -Wide)

No image or baseband update is needed. This is a 40-second observation of
ordinary physical PTT. It does not assert software PTT or MODEM, alter routing,
grab input devices, or establish RF inhibition. Opening a separate evdev reader
leaves the GUI's own event queue intact.

Connect a suitable dummy load, select ordinary USB and the lowest transmit
power, close transmitting CAT/TCI/modem applications, and leave both PTT controls
released. Keep FreeDV/test modes closed. The physical controls can transmit.
If TX persists after release, switch the radio off; do not wait for collection.

Extract the package, then run `Invoke-MicPttInputWatch.ps1 -RadioIp 192.168.4.210`.
The prerequisite check verifies the installed GUI and opens every listed evdev
device read-only. It reports READY without keying RF. Type READY at the runner's
prompt only after preparing the radio. No speech or WAV file is required.

Follow the on-screen prompts relative to the beginning of observation:

| Time | Action |
| --- | --- |
| 0 seconds | Both PTT controls released. |
| 5 seconds | Press the radio side PTT for one second, then release. |
| 15 seconds | Press hand-microphone PTT for one second, then release. |
| 25 seconds | Repeat hand-microphone PTT for one second, then release. |
| 35 seconds | Both controls released; no further actions. |
| 40 seconds | Collection ends. |

Watch the radio's TX indicator for each press and release. The runner asks for
these observations afterward. State whether all three presses keyed normally,
whether each release returned to RX, and whether any prompt was missed. The
indicator is aggregate GUI telemetry, not an independent RF measurement.

Return the printed mic-ptt-results ZIP. It contains raw numeric input events,
device identities, operator prompt timestamps, completeness information and
your notes. The native input_event format follows the running Python/Linux
word size (16 bytes on the image's 32-bit ARM ABI). An event queue overflow,
disconnect, partial event or event limit marks the run INCOMPLETE.

A repeatable event at both mic presses is a candidate input mapping. No mic
event, despite a valid panel positive control and normal mic keying, establishes
only that no corresponding event was observed on the enumerated evdev devices.
Neither result proves retained release reporting while software TX is held.
That later test remains outside this procedure. OBSERVED means collection
completed, not that mic PTT is independently exposed or hardware is qualified.

Helper-only package; existing firmware image hashes and identity are unchanged.
