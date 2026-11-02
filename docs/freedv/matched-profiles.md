# Matched USB and USB-DIG qualification

The supplied source review supersedes a general USB-DIG exclusion. USB-DIG is
used by internal digital applications; TCI's application-specific allowlist is
not a general audio-route restriction. USB and USB-DIG have distinct baseband
mode values and SSB/DIGI settings groups. The audio device and MODEM/RECORD/PTT
controls are separate concerns. Neither label proves bypass of compression, NR,
notch, EQ or microphone mixing. This incorporates source findings only: there
is no new hardware comparison and no changed measured result.

Keep USB as the measured comparison baseline. Use the same identified candidate
image, frequency, waveform, approximately 3 kHz bandwidth, effective RX/TX
filters, AGC, explicit processing settings, microphone/line/monitor gains,
mixer, route bits, devices/channels, power and SDR setup. Read effective settings
**after** each mode switch; do not rely on equal slider defaults in the two
saved groups. Apply explicit filters after switching. Begin at the existing
1 W diagnostic setting, retain protection controls and the digital power cap.

For each profile run the four separate cases in the acceptance matrix:

1. Native known-modem waveform fidelity at RF.
2. Usable hand-mic capture concurrently with modem output to RF, with no direct
   analog speech leakage (include the first and last frames).
3. Identifiable controlled RF input captured while decoded audio plays locally.
   Compare playback off/on, speaker levels and supported mixer alternatives.
   Record raw channels and distinguish the RF marker from the local speaker.
4. Radio-side and hand-mic PTT independently: press, hold, release and overlap.
   Document hand-mic release under active MODEM. Aggregate TX does not identify
   an independent release; do not guess register bits or reuse radio-side PTT.

Retain original speech, generated waveform, continuous IQ, demodulated audio,
raw capture channels, local playback, effective settings and observations, with
hashes and case identities. Keep large IQ on the computer and reference it from
the small evidence bundle. Diagnostic captures are explicit and bounded;
production processing does not routinely record turns.

Selection is fixed: both complete profiles pass → USB-DIG; only USB-DIG passes →
USB-DIG; only USB passes → USB; neither passes completely → release BLOCKED.
One immutable complete profile governs the entire session. Upper-sideband
orientation is a separate field from the local profile. No hybrid or per-turn
profile selection is allowed.

Preserve the analog-leakage failures in [the first TX results](native-first-tx-2026-10-28.md)
and subsequent route-specific evidence against their exact tested USB routes.
Do not generalize them to every USB route or transfer them to USB-DIG. The
historical synchronized-RX 70% processing-margin failure remains FAIL.
