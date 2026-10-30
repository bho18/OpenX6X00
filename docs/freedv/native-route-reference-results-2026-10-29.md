# Normal recorder and diagnostic route comparison

Evidence: `freedv-route-check-20261029-210846.zip`, SHA256
`50e1b6c1e70dd846eb436da44d0af67d2b5fcd5d8594380031b34e9f1f723946`.
The radio's build manifest identifies source
`21df019884da3e8cdc1b4521a4b4f8a51ca210e1`, the existing `.2` image.
Radio UTC timestamps cross into October 30; the operator session was October 29
in America/Chicago. Raw recordings remain private.

## Findings

**Correction following operator clarification:** the operator did not speak into
the microphone during D1 or D2, and answered no when asked whether they spoke
during D0. These diagnostic runs were beep-only observations. Their lack of
recorded speech **does not establish a microphone-capture failure**. The initial
assessment incorrectly assumed speech had been supplied; that conclusion is
withdrawn for this batch. Earlier microphone-test findings remain separate.

The normal Auto/Hand MSG recordings contain clear speech according to operator
playback. Normal recording is therefore a working microphone reference on this
card. The MP3 references are not in the ZIP, so their samples and levels have not
yet been independently compared.

The diagnostic cases ran tone generation, not RADE encoding or decoding. They
measure generated-audio routing and selected-state recovery. Microphone capture
during diagnostic TX, and the cause of earlier missing microphone input, remain
INCONCLUSIVE from this batch. It supplies no new codec-performance result.

| Exported case | Commanded route | Observed packet state during sampled active seconds | Capture after the first 0.5 s | Operator RF observation |
|---|---|---|---|---|
| 1 | R1 M0 P0 | Fresh RX | AC RMS 5049.8; dominated by generated 735 Hz tone | D0 behaved as expected |
| 2 | R1 M0 P1 | Fresh TX | AC RMS 48.1; no speech supplied | No SDR audio or waterfall signal; near-zero indicated output |
| 3 | R1 M0 P1 | Fresh TX | AC RMS 38.8; no speech supplied | Repeat to verify the same D1 result |
| 4 | R1 M1 P0 | Fresh TX | AC RMS 40.4; no speech supplied | Operator says final “D3” behaved as expected; guide calls the final route D2 |

The case numbers and route names above come directly from the export, regardless
of the checklist labels. The exact meaning of the operator's “D3” label is not
independently confirmed by SDR files in this bundle.

Between 0.5 and 7.5 seconds, approximately **99.93% of case 1's capture spectral
power lies within 730–740 Hz**, around the generated 735 Hz tone. Its high level
does not establish microphone capture. A listening-only bandpass/notch analysis
leaves substantially less signal; it is not substituted for original samples or
reported as intelligible speech. No microphone speech was deliberately supplied
in these diagnostic runs, so this is a tone-routing observation only.

## What D1 establishes

Both R1 M0 P1 trials set software PTT and received fresh TX packets. Each handed
351,477 generated samples (7.97 seconds at 44.1 kHz) to playback without write
errors. All four output WAVs have the same SHA256:
`d9207f71abd74b715f65a997c9d3b73ed6752c268f38d1a3b656b207475710d9`.
There were no capture holes, capture drops, capture errors or recording discards.

Those facts establish application output and baseband TX status, not RF
modulation. The operator heard the speaker tone stop when TX began and observed
no usable SDR signal in two trials. **Generated-waveform RF delivery failed the
operator observation on R1 M0 P1 under these conditions; microphone capture was
not tested.** Near-zero SSB power
is consistent with missing modulation, but the cause is not identified.

Diagnostics requests **1 W**, even though the saved normal setting is **5 W**.
The saved 5 W is restored afterward. This explains the requested-power difference;
it does not explain absent modulation or convert a TX status bit into proof of RF.
Do not increase power to compensate for a routing failure.

## State and recovery

The full ALSA mixer dump is identical before and after. PulseAudio source/sink
identities, stream formats, channel maps, mute/volume settings and capture-source
association also match; the changed fields are measured latencies. The main
capture stream remains unmuted mono s16le at 44.1 kHz on the physical capture
source, not a PulseAudio monitor source. This does not measure the instantaneous
baseband audio mux during a case.

All four selected radio/ALSA snapshots restore successfully, with a fresh RX
packet subsequent to software command clearing. Clear times are 33, 42, 44 and
44 ms. Transport failures, audio failures and restoration failures are zero.
Each single playback-underflow event occurs after cancellation was requested;
these do not show repeated active-playback starvation.

These are successful selected-state restoration checks, not new manual Stop,
held-PTT or restart tests. External abort-to-RF cessation within 500 ms remains
INCONCLUSIVE without correlated external timing evidence.

## Remaining comparison

Normal MSG recording uses the existing capture stream after the application's
decimator and recording filter. Its radio recipe sets RECORD, temporarily sets
internal-mic gain to zero, leaves hand-mic gain unchanged, and does not start a
generated playback stream or software transmission. The diagnostics raw tap is
ahead of that decimator. Diagnostic route probes select Hand, retain the saved
internal-mic gain of 30 and hand-mic gain of 20, and play the generated tone.
The two paths are consequently not equivalent just because both have RECORD set.

The normal Auto and Hand reference recordings are both clear according to the
operator. A global dead microphone or globally muted capture source is therefore
not supported by this evidence. Neither does this establish simultaneous
microphone capture and generated RF: that remains unqualified.

The **already recorded** FDVAUTO/FDVHAND MP3 references and two existing D1 SDR
recordings can supplement the working normal-recorder and failed D1 tone-delivery
evidence. The ZIP contains only inventories, a blank notes header and four radio
case directories; those media files were not included. Comparing normal speech
to diagnostic runs with no speech input cannot diagnose a microphone regression.
Any further microphone comparison must explicitly record when speech is supplied
and the physical PTT state. The recipe differences above remain hypotheses, not
demonstrated causes or a reason by themselves to change gains or deploy an image.
No unchanged codec batch or routine long-duration run is warranted.

Preserve earlier evidence: the original `.1` image already contains both silent
TX13 and intelligible local TX14 on the same selected route. The new findings
do not establish a `.2`-only regression, and do not erase that earlier successful
capture. No production physical-PTT contract, built-in microphone support or
controlled RF receive interoperability is qualified by this session.
