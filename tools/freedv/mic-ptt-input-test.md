# Mic PTT input discovery — existing 20261105.2 image

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
