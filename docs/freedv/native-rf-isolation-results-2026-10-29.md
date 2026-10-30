# Unencoded microphone audio accompanies the generated RF tone

The operator supplied two SDR++ audio recordings after confirming that both
offline RADE round trips are intelligible and sound codec-processed. D2 is
louder. The operator also hears faint voice under the generated tone in both
SDR recordings. Physical hand-mic PTT was released during these speaking trials.

**Independent generated RF and microphone routing: FAIL for these tested
profiles.** Microphone speech is available to Linux capture, but an unencoded
component also accompanies the generated RF tone. This is a failure of isolation
on the selected routes, not a failure of all supported native audio alternatives.

## Evidence and case association

| SDR++ WAV | SHA256 | Radio case matched by waveform |
|---|---|---|
| `audio_14100000Hz_21-25-07_29-10-2026.wav` | `c801d483ac0e60e2233507565d8daea846af68136a129da24357642b2725674d` | 8: D1, R1 M0 P1 |
| `audio_14100000Hz_21-25-53_29-10-2026.wav` | `3b672a019847396435bdfe582d13028286f2f6220cd01badc206c18d6be8abb1` | 9: D2, R1 M1 P0 |

Both are mono 48 kHz signed 16-bit PCM. The radio evidence is the preceding
`freedv-route-check-20261029-213314.zip`, SHA256
`c2e213795e67fe7071f1b0a2ffb5e0e069ab6ff28b90b07e6e9d92e0af5d370f`.
Its actual application source is
`21df019884da3e8cdc1b4521a4b4f8a51ca210e1`, the `.2` diagnostic image.
Both cases selected Hand, Existing mono and Keep mixer, with hand gain 20,
internal gain 30 and monitor 0. No new source, gain or image change was made.
Raw recordings remain private.

## Distinguishing voice from the tone

The generated radio output is the same 735 Hz tone in both cases. No RADE voice
encoder ran during these physical transmissions: they were tone route probes.
The separately generated offline RADE round trips were not the transmitted
waveforms in these SDR files.

The SDR tone is shifted in frequency relative to the generated tone and drifts
slightly. Analysis resamples to 8 kHz, derives common frequency/phase correction
from that received tone, then compares the residual with the corresponding raw
radio microphone capture. Both sides are limited to 250–2600 Hz and exclude
680–790 Hz after correction, removing the tone from the comparison.

The first SDR recording matches case 8 over multiple consecutive 0.7-second
speech windows. The first seven windows align at approximately 5.417 seconds
of file offset, within about 0.7 ms. The second recording matches case 9 across
all twelve inspected windows at approximately 1.7474–1.7486 seconds of file
offset. Wrong-case references do not produce this consistent alignment.

A separate six-second comparison gives:

| Recording / microphone reference | Normalized correlation magnitude | Largest of 100 same-spectrum random-phase controls |
|---|---:|---:|
| D1 / case 8 | 0.092 | 0.036 |
| D2 / case 9 | 0.311 | 0.050 |

The controls retain the reference spectrum, randomize its phases with a fixed
seed and search all candidate lags on each trial. These are checks against
accidental waveform matching, not formal population-level statistical claims.
D1's match is weaker than D2's, but its repeatable speech-window alignment and
whole-window result agree with the operator hearing voice in both files.

Because the physical test generated only a tone, matched microphone speech in
the SDR recording is an unencoded component, not decoded RADE speech. The exact
baseband mixing point is not identified by this experiment. Listening aids use
only the frequency-corrected, tone-rejected SDR audio with bounded constant gain;
no microphone reference audio or synthesized speech is mixed into them.

The offsets above align audio files. They do not synchronize clocks or establish
abort-to-RF timing. The external 500 ms cutoff gate remains separate.

## What is now established

- Both tested routes can supply intelligible microphone input while software
  owns TX and physical hand-mic PTT is released, for these captured trials.
- Both unchanged microphone captures survive the shared TX engine and reference
  decoder offline with intelligible words, confirmed by the operator.
- Generated-tone RF delivery is observed in the latest D1 and D2 trials.
- Both tested profiles permit an unencoded microphone component alongside that
  generated waveform. They do not qualify for a production FreeDV TX route.

This does not prove live radio RADE encoding/playback quality or physical PTT
startup behavior. Earlier intermittent capture observations remain part of the
record; the earlier beep-only trials remain excluded as microphone tests.

## Next implementation target

The [current-card comparison guide](native-microphone-muting-session.md) runs
the remaining two gain profiles using supported Audio settings between released
diagnostic sessions. Because those operator changes are saved preferences, it
explicitly requires restoring the originals manually after the tests. The
diagnostic backend still applies and restores its acquired snapshot under its
existing owner; no external register writer or runtime patch is used.

The existing `play_mode_apply(AUDIO_PLAY_ON)` helper sets both microphone gains
to zero and RECORD on for generated playback. The diagnostic route experiments
deliberately retain the saved gains to test simultaneous microphone capture.
That is a concrete existing suppression mechanism to investigate, rather than
assuming MODEM alone blocks the analog microphone path.

The next controlled comparison should keep R1 M1 P0, Hand, channel selection,
tone, power and speech procedure fixed, and vary the transient microphone-gain
recipe inside the diagnostic owner. Compare saved gains, internal gain zero,
and both gains zero. Capture microphone PCM and SDR audio together to establish
whether analog suppression preserves usable capture. Apply suppression before
keying; retain the same local Arm/Run, bounded TX, Stop and snapshot restoration.
Do not assume that a zero-gain command preserves the capture branch, or claim a
working fix until that is measured. A failed capture at zero gain requires
investigating supported channel/mixer alternatives, not amplifying silence.

No further unchanged RADE benchmark or repetition of the same tone/gain profile
is needed to establish the leakage already observed. Native routing remains
the primary approach; these two failed isolation profiles do not justify an
external-audio fallback or a global feasibility failure.
