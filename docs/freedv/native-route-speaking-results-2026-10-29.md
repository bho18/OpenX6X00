# Speaking trials after the beep-only route checks

Evidence: `freedv-route-check-20261029-213314.zip`, SHA256
`c2e213795e67fe7071f1b0a2ffb5e0e069ab6ff28b90b07e6e9d92e0af5d370f`.
Image source remains `21df019884da3e8cdc1b4521a4b4f8a51ca210e1` (`.2`).
The operator session was October 29 in America/Chicago; radio timestamps are UTC.
Raw recordings and analysis artifacts remain private.

## What changed in this session

The operator reran D1 and D2 while speaking and confirms **physical hand-mic PTT
was released**. The SDR now received the generated tone on D1. On D2 it received
the tone and the operator could vaguely hear analog voice alongside it, with
speech-like waterfall activity. This is a possible analog-bypass observation,
not a qualified independent microphone/generated-RF route.

The archive contains the same radio process/session as the previous export.
Cases 1–4 are byte-identical to the earlier bundle, whose diagnostic trials had
no speech input. Five additional cases, 5–9, are present. There are no SDR WAVs
or normal MSG recorder MP3s in this archive; operator notes contain only a header.

| Case | Named route | Input interpretation | Raw capture AC RMS after 0.5 s |
|---|---|---|---:|
| 5 | R1 M0 P0 | Additional unkeyed tone test; speech timing unconfirmed | 5046.9 |
| 6 | R1 M0 P0 | Additional unkeyed tone test; speech timing unconfirmed | 5075.6 |
| 7 | R1 M0 P1 | Operator thinks speech was supplied only in the following D1 run | 0.68 |
| 8 | R1 M0 P1 | Second new D1, with microphone speech | 225.5 |
| 9 | R1 M1 P0 | D2, with microphone speech | 851.0 |

Cases 8 and 9 contain sustained, varying, broadband activity rather than just
a startup click or the generated 735 Hz tone. In the 0.5–7.5-second interval,
the 730–740 Hz band accounts for about 1.77% and 0.31% of their capture spectral
power respectively. Case 7 stays essentially flat throughout that interval.
This agrees with the operator's recollection of speaking only during the second
new D1 run. The capture levels alone do not establish intelligibility or justify
automatic gain changes.

## State comparison and generated output

All nine cases have the same saved settings: normal microphone Auto, hand gain
20, internal gain 30, monitor 0, line-in/out 10, speaker level 20, saved power 5 W,
14.100 MHz USB and 100–2900 Hz filters. Each selected diagnostic profile is Hand,
Existing mono, Keep mixer. Diagnostics requests 1 W transiently.

Active cached route bits and fresh observed packet TX match the named cases in
the sampled resource records. New R1 M0 P0 cases stay RX; new P1/M1 cases report
TX. These cached commands and packet flags do not measure internal audio routing.

Every case produces the same generated output WAV, SHA256
`d9207f71abd74b715f65a997c9d3b73ed6752c268f38d1a3b656b207475710d9`,
and hands all 351,477 tone samples to playback. Full ALSA mixer dumps match the
earlier export. Stable PulseAudio source/sink identities, volume/mute, channel
maps and formats also match; only measured latency values differ.

There is consequently **no logged configuration change explaining D1's changed
RF observation**. Speaking was an intentional changed input, but this evidence
does not prove that speech caused generated-tone RF delivery to start. Physical
PTT was released, so this is not evidence that pressing it was required here.
The internal baseband mux/command acceptance is not directly observed.

All nine selected-state restoration comparisons pass. New case command-clear
times are 34, 33, 42, 44 and 44 ms. Capture drops/holes/errors, playback write
errors, transport failures and restoration failures are zero. Each single
underflow event occurs after cancellation was requested. Fresh RX follows
command clearing before restoration. External RF cutoff within 500 ms remains
INCONCLUSIVE; these are not new manual Stop or held-PTT tests.

## Offline codec check using the actual captured microphone input

The unchanged original 44.1 kHz PCM from cases 8 and 9 was fed through the shared
TX engine compiled from the integration source. No input gain, DC removal or
filtering was added to that replay. The host engine used the pinned codec/models
and its normal stateful input/output resampling, then completed end-of-over.

Each replay consumed all 351,600 input samples, encoded 67 blocks and delivered
360,914 output samples with zero engine failures. The pinned reference decoder
produced 7.63 seconds of non-silent audio from each generated waveform.
**The operator confirms intelligible words in both offline round trips.** D1
sounds more analog/natural and quieter; D2 sounds more digital and louder.
Both supplied clips passed through RADE encoding and reference decoding; the
subjective description of D1 does not establish an analog codec bypass. The
decoded AC RMS is approximately 224 for D1 and 674 for D2, consistent with the
reported loudness difference. The original captured input also differs in level.
These were not calibrated identical-input trials, so the difference does not
identify a gain-control fault or justify an automatic gain adjustment.

Separate listening-only capture copies
remove DC and add bounded constant gain; these are distinct from unchanged codec
input and original evidence.

This is a host correctness/content experiment, not a radio throughput benchmark
or a decode of transmitted RF. It bypasses physical playback and RF, so even
intelligible offline speech would not establish live RF delivery or isolation.
The original real-time/margin findings remain unchanged.

## Qualification and next evidence

**Capture-to-offline-codec intelligibility: PASS for these two captured inputs.**
The speaking trials recover usable microphone speech during software-owned TX
with physical PTT released on both tested routes. They invalidate a blanket
claim that this image cannot capture microphone input during software-owned TX.
Repeatability across sessions, real-time integrated microphone encoding/playback,
live RF speech and RF isolation are not established by the offline experiment.

The D2 observation raises a separate gate: does unencoded microphone speech also
reach RF while the generated waveform is transmitted? **Analog isolation remains
INCONCLUSIVE, with suspected leakage.** A tone can coexist with such leakage;
hearing the tone is insufficient to qualify FreeDV routing. Do not confirm the
independent microphone/TX route solely from this run.

Use the already captured D1/D2 SDR WAVs, if available, to examine residual speech
after accounting for the generated tone. Operator listening of the two offline
round trips is now complete; RF evidence is the next comparison. No new image,
automatic gain change, routine long-duration run or unchanged codec batch is
justified by these results alone. The earlier beep-only assessment remains
corrected: missing speech where none was supplied is not a capture failure.
