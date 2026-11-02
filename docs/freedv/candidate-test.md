# Persistent FreeDV: spare-card test candidate

This candidate provides **APP → Modes → FreeDV test** and the existing
Settings → Info → Open FreeDV Diagnostics entry. It contains the persistent
RADE V1 engine, production audio adapter and normal mode integration. The test
entry opens locally armed hardware cases; it is not a qualified native release.
No profile is falsely marked passed to make the menu appear.

## Install and recover

Use the delivered `x6200-persistent-voice-test.img.xz` and `SHA256SUMS`.
Verify the downloaded archive with `sha256sum` (Linux) or
`Get-FileHash -Algorithm SHA256` (PowerShell). Write it to a **separate spare
card**, using an image writer that supports `.xz`, and enable write verification.
Preserve the working `.2` card and settings backup. Do not format any partition
on that working card. This image changes neither the baseband nor update key.

Boot with PTT released and a dummy load connected. Check ordinary RX and the
displayed frequency/VFO before opening FreeDV test. Stop cancels software TX;
release physical PTT and wait for fresh RX before Close/restoration. If recovery
cannot complete, power down, remove the candidate card and boot the preserved
working card. The computer tools cannot arm or key a test.

## First hardware session

1. Select USB, the test frequency, approximately 3 kHz filtering, explicit
   processing/gain settings and a low normal power setting. Record those
   settings. Entry never chooses a frequency for you. Connect the receive SDR
   through suitable coupling/attenuation and start continuous IQ/demod recording.
2. Open **APP → Modes → FreeDV test**, then Acquire locally. The diagnostic
   RF setting is 1 W; individual transmissions cannot exceed 10 seconds.
   Hand mic / Existing mono / Keep mixer is the initial comparison setup.
3. Use the existing route and Capture cases to identify the external receive
   signal and the hand-mic capture/RF route. Confirm only what the recordings
   establish. Perform Manual Stop, Watchdog timeout and Audio failure cases,
   then record the exact-route Stop confirmation. The guide in
   [Session 2](native-session-2.md) explains these controls and evidence.
4. Select **Persistent hand-mic TX 8s + finish**, Arm, then Run. Speak a
   numbered phrase with recognizable first and last words. Speech admission
   closes after eight seconds; the actual persistent adapter finishes accepted
   speech, emits one EOO and the silence tail, and drains before normal unkey.
   Stop and the ten-second RF watchdog remain independent. Repeat several turns
   without Close: one live codec set, four resamplers and the playback stream
   remain allocated. Changing capture channel/mixer requires Close/reacquire.
5. With a transmitting peer or controlled RF source, confirm the exact unkeyed
   RX route and run **Persistent live receive 120s**. This is the production
   adapter's live RF capture/decode/speaker path. Compare speaker off/on and
   identify the external signal separately from local playback. A receive-only
   SDR cannot provide this RF input. Stop may end the case early.
6. Export, Close and copy the displayed `/tmp/freedv-native-*` directory before
   reboot. Each persistent case saves bounded `capture.wav`, `output.wav`, optional
   raw `capture-stereo.wav`, coordinator/settings evidence and lifetime counters.
   TX recordings allow 9.5 seconds to retain the finishing output; RX excerpts
   are four seconds while processing runs for the full case. Continuous SDR
   recordings stay on the computer, referenced by hash. Counter totals such as
   EOO and turns are cumulative within the retained session; compare deltas.
7. Close, select USB-DIG, record its effective settings and repeat matched cases
   on the same candidate. Do not transfer a result between profiles or combine
   successful parts of different profiles. Keep failed/inconclusive recordings.

Both physical PTT controls and independent hand-mic release under MODEM still
need documented hardware support and measurement. Unknown hand-mic state is not
treated as released. These bounded cases provide test access while that issue
is investigated; the normal automatic-PTT mode remains qualification-gated.

## Software-only reproduction and evidence

The delivery includes its actual source commit/tree identity, source archive,
GUI/runner/model/library hashes, build caches, host tests, actual edited-image
ABI dependency/loader checks and filesystem validation. Use
`tools/freedv/build_voice_candidate.py --help` for reproducible image assembly.
The source tree must be committed; host verification binds its per-file hashes.
The web wheel must match the complete current web module. Image assembly uses
the existing offline workflow, preserves every byte outside the root partition
and verifies the signing key. It never flashes a device or keys RF.

Use the [test-kit workflow](../../tools/freedv/qualification/README.md) to bind
recordings and assessments. Listening, physical RF timing and full bidirectional
qualification remain unassessed until their actual evidence is collected.
