# X6200: Stop, stereo and built-in-microphone assessment

Evidence: `freedv-results-20261028-182756.zip`, SHA256
`d2cde94465d80647b002a5f19bf4c70acf914606e5e917501bcc737f5ec412c5`.
31 files, 5,971,572 expanded bytes. Checked extraction normalized Windows path
separators and rejected traversal, duplicate paths and links. No archive
instructions were executed. Recordings remain private in the workspace.

The image/build manifest is byte-identical to the preceding microphone test;
source `2083e3e7f819e2b3fc4feaf27e5c9a007c996308`, application 0.3.6, baseband
1.0.7, Linux 6.1.82 ARMv7. The source-manifest hash matches the pinned checkout.

## Recovery results

All three cases used R1 M1 P0 / Hand mic / Existing mono / Keep mixer and observed
packet TX. They stopped for the intended first cause, acknowledged playback flush,
received fresh packet RX with a sequence greater than the command-clear sequence,
and completed restoration. Selected radio and ALSA state matched before/after.

| Case | Test | Run before abort | Command clear after abort request | Complete restoration after abort request | Result |
|---|---|---:|---:|---:|---|
| 1 | Manual Stop | 5.200 s | 48 ms | 216 ms | PASS for tested software/packet recovery |
| 2 | Watchdog timeout | 3.001 s | 42 ms | 230 ms | PASS for tested software/packet recovery |
| 3 | Injected audio failure | 1.553 s | 44 ms | 188 ms | PASS for tested software/packet recovery |

The manual case was stopped later than the suggested two seconds, but still
exercised manual Stop before automatic expiry. Case 3's one audio failure was
intentional and its first stop cause was audio failure. No unexpected
audio/transport/restoration failures or capture drops, holes, capture errors or
playback write errors occurred in the other cases. Playback flush acknowledgement
is present in all seven cases.

The operator reports no GUI slowdown and prompt RF cutoff in the expected tests.
That supports observed RF recovery. No SDR recording, timing video or notes file
is included in the archive; command clearing and restoration times are not
external RF cessation measurements. The formal abort-to-RF-off <=500 ms gate
remains INCONCLUSIVE pending correlated evidence and timing uncertainty.

Each case has one startup-inclusive playback underflow. There are no repeated
underflows in these counters, but their event timing is not exported, so they
cannot be conclusively classified as startup-only. Recorder-discarded samples
after the bounded excerpt are intentional and distinct from capture drops.

## Stereo results

Both cases used R1 M0 P0 / Hand mic / Left capture and remained packet RX.
Raw stereo clips are four seconds at 44.1 kHz, signed 16-bit. The analysis excludes
the first 0.5 seconds of route-switching transients and removes DC before spectral
measurement. Tone energy is integrated within 10 Hz either side of 735 Hz.

| Case | Mixer | Left tone energy | Right tone energy | Finding |
|---|---|---:|---:|---|
| 4 | Keep mixer | 99.87% | 99.87% | Both channels dominated by generated playback |
| 5 | Stereo | 99.81% | 99.81% | Both channels dominated by generated playback |

Stereo selection was accepted and restored. Neither tested configuration exposes
a demonstrated independent receive channel during speaker playback. Selecting
Right capture from these same configurations would use a channel already present
in the saved stereo evidence; that alone is not a new separation experiment.

These recordings establish strong playback contamination, not its electrical,
digital or acoustic coupling mechanism. They contain no controlled independent
RF input, so they do not establish that received RF disappears entirely, nor that
all supported native audio paths fail. Remaining supported mixer/topology
alternatives and an identifiable RF-input test remain unresolved.

## Explicit built-in selection

Cases 6/7 correctly selected Built-in mic (exported microphone enum 0), Existing
mono / Keep mixer on R1 M0 P1 and R1 M1 P0 respectively. Both observed packet TX
and restored matching selected radio/ALSA settings without transport errors.

The operator reports hearing the hand-mic tests rather than the radio-mic phrase.
The clips contain weaker activity early and stronger activity around three
seconds, consistent with the requested radio-near then hand-near phrase order.
Phrase identity is the operator's listening observation, not an automated
transcription. A DC offset around 149 counts is excluded from AC-level comparisons.

Deliberately selecting Built-in mic has therefore not demonstrated capture of the
built-in microphone on either tested keyed route; hand-mic capture remains the
supported observation. This does not prove a software enum inversion, a broken
physical microphone, or failure of every native path. The control backend maps
the selected enum directly and has compile-time checks against the control
library; transport success is still not physical microphone-source readback.

## Updated gates and next work

- Software Stop/timeout/audio-failure recovery: PASS for these tested cases.
- RF cutoff: prompt per operator; numerical <=500 ms bound INCONCLUSIVE.
- Speaker fixture demonstration and hand-mic capture retain their prior results.
- Receive capture independence: unresolved; both tested stereo channels contain playback.
- Built-in microphone capture: INCONCLUSIVE; not demonstrated on these selected routes.
- Live hand-mic RADE RF transmission and reference-decoded speech: not yet tested.
- Physical held PTT, restart behavior and controlled RF receive interoperability:
  not established by this bundle.

Do not repeat these unchanged seven cases merely to obtain more of the same
evidence. The next work is to close external stop-timing evidence, qualify the
hand-mic TX profile for a short RADE RF proof, and investigate the remaining
supported native receive paths. Any qualification cleared by Close/release must
be re-established locally before a dependent live TX case. Preserve the original
70% processing-margin result independently of audible or recovery success.
