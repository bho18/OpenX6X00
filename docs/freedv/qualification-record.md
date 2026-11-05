# X6200 FreeDV qualification record

## Corrected virtual-device probe — 2026-11-03 UTC

**PASS for four retained eight-second TX turns on the X6200 using private
virtual devices.** Original operator bundle:
`freedv-backlog-20261102-214901-09123f9e.zip`, SHA256
`18e7f9a91d9cc89f87bba7791b0bdbdd76a4bd25bb26cba5e2f25ab418616af0`.
All helper hashes, build identities, WAV formats/sample counts and trace
generation/accounting checks were verified. Installed firmware remains
`6279ae76e15c368a507bfcf8cef93bb0445130c9`; the staged probe is from
`75a2317222ca787d8cb551a2f7a47587a78d8234`, binary SHA256
`bc5d0308a9f727459afb0cfa88cbce04fd78d300358b239c8826b1e3692f3789`.

PulseAudio 16.1 used the corrected null-sink fixture (`norewinds=1`, maximum
50 ms scheduling block). All turns had zero reported faults/capture holes,
acknowledged flushes and empty final engine queues. RADE creation remained one;
EOO counts advanced 1, 2, 3, 4. Cumulative output-queue waiting was zero.
Combined capture/engine input backlog in the 100 ms sampled traces reached
at most 8,838 samples (200.41 ms); this is a sampled maximum, not an exact peak.
Per-turn process CPU time was 5.93–6.09 seconds for 7.91–8.01 seconds of
accepted silent input. This does not replace the historical 70% margin failure.

The earlier default-null-sink first-turn failure remains FAIL against its
original probe and fixture. The corrected result closes that fixture regression.
The physical-route second-turn abort, analog microphone isolation, RF receive
survival, intelligibility and independent physical PTT qualification remain open.
No RF was operated and no manual-case coordinator results were present. Next
targeted measurement: one locally armed two-turn USB-DIG batch using the
installed image, without repeating the virtual probe or the basic tone matrix.

The [current validation protocol](combined-validation-plan.md) retains the
remaining coverage below while shortening audio runs to two measured minutes
and a 5-10-minute mixed TX/RX exercise. Demonstrate audible decoding and hand-mic
encoding early, then finish the remaining routing/recovery checks. This replaces
the original 30-minute duration requirement at the user's direction; it does
not claim endurance or thermal qualification from the shorter measurements.

The diagnostic release is implemented and has returned physical evidence. See
[the native radio results](native-radio-results-2026-10-28.md) and the current
[Session 1 procedure](native-session-1.md). The tables below retain the original
evidence requirements; pending entries are not a replacement for the newer
case-specific results. Functional Stop recovery passed; exact external 500 ms
RF timing remains separate. The operator selected the short TX proof before
that precision measurement, without relabeling the timing gate PASS.

Record the radio and baseband versions, firmware and benchmark source hashes,
SDK, audio endpoints and channel mapping, mixer settings, CPU configuration,
available memory, operator, date and test setup with each result bundle. Save
settings before using a spare diagnostic card, and verify ordinary radio
operation on that card before starting experiments. Preserve the working card.

## Commanded route matrix

R, M and P mean the cached RECORD, MODEM and software PTT commands. They are
commands, not independent measurements of the baseband's actual state. Each
combination needs its own observation for the hand microphone first and, where
relevant, the built-in microphone under actual keyed operation. Treat every
combination as potentially capable of keying RF until measured. Use a dummy
load and receive-only SDR; require local arming, request 1 W, and limit each
automatically controlled transmission to 10 seconds.

| Case | R | M | P | Hand mic | Built-in mic |
| --- | --- | --- | --- | --- | --- |
| R0-M0-P0 | 0 | 0 | 0 | INCONCLUSIVE | INCONCLUSIVE |
| R0-M0-P1 | 0 | 0 | 1 | INCONCLUSIVE | INCONCLUSIVE |
| R0-M1-P0 | 0 | 1 | 0 | INCONCLUSIVE | INCONCLUSIVE |
| R0-M1-P1 | 0 | 1 | 1 | INCONCLUSIVE | INCONCLUSIVE |
| R1-M0-P0 | 1 | 0 | 0 | INCONCLUSIVE | INCONCLUSIVE |
| R1-M0-P1 | 1 | 0 | 1 | INCONCLUSIVE | INCONCLUSIVE |
| R1-M1-P0 | 1 | 1 | 0 | INCONCLUSIVE | INCONCLUSIVE |
| R1-M1-P1 | 1 | 1 | 1 | INCONCLUSIVE | INCONCLUSIVE |

For each case, record the commanded bits and timestamped observed TX state
separately. Save independent, distinguishable microphone and generated signals,
raw Linux capture channels, local playback observations, and SDR RF captures.
Note whether capture and playback were concurrent, any feedback or analog speech
leakage, audio failures, and the post-test restoration comparison. Inspect
individual left/right ALSA channels and supported mixer alternatives before
concluding that a native route is unusable.

For receive, compare a stable off-air signal against the SDR while playing a
distinct local signal. Record whether receive capture continues and whether
playback contaminates it. A receive-only SDR cannot provide controlled RF input;
interoperability with a peer transmitter or suitable signal source remains a
separate, later test.

## PTT and recovery observations

| Case | Result | Required evidence |
| --- | --- | --- |
| GUI/front-panel PTT press and release | INCONCLUSIVE | Command and observed TX transitions, SDR recording, first/last-frame leakage |
| Hand-mic PTT press, hold and release | INCONCLUSIVE | Observed TX and RF while held and after release |
| Audio-worker failure | INCONCLUSIVE | Command clearing and RF cessation times, restoration comparison |
| Software timeout | INCONCLUSIVE | Command clearing and RF cessation times, restoration comparison |
| Stop while hand-mic PTT held | INCONCLUSIVE | Release prompt, restoration blocked until observed RX, RF after release |
| Controlled app restart | INCONCLUSIVE | Supervisor behavior, RF and analog-mic route while PTT remains held |

For software-owned TX, measure from abort to both command clearing and observed
RF cessation; the proposed acceptance threshold is 500 ms under the tested
conditions. A physical PTT held at the baseband is a separate result and cannot
pass this software guarantee. Do not restore ordinary analog routing until
observed TX is false. Use the service stop/start mechanism for controlled
recovery; the supervisor restarts an individually killed GUI process.

## Codec workloads

| Mode | TX | Synchronized RX | Acquisition/no-sync RX |
| --- | --- | --- | --- |
| RADE V1 | Short file timing PASS | File throughput PASS; original 70% margin FAIL | Zero-input and short synthetic-noise timing PASS |
| 700E | Short file timing PASS | Short file timing/sync PASS | Short silence timing/no-sync PASS |

These are separate narrow findings, not an overall hardware qualification.
See the [sustained speech report](rade-long-sync-radio-2026-10-27.md) and the
[zero-input report](rade-acquisition-endurance-2026-10-27.md) for measured scope,
versions and limitations. Live capture/playback and physical underruns remain
INCONCLUSIVE.
The [short codec report](short-codec-radio-2026-10-27.md) completes the five
remaining file-based cases for the tested SDK package. Synthetic noise does not
qualify off-air sensitivity, and the 700E no-sync API's output content was not
captured. No routine repeat of the unchanged codec batch is required.

Each result needs pinned fixture and model hashes, warm-up and initialization
times, then two measured minutes of paced input with the normal GUI and
waterfall active. Retain completed measurements for unchanged builds and record
the 5-10-minute mixed TX/RX exercise separately.
Record real-time factor, p99 and maximum codec-plus-resampling block time,
represented audio intervals, deadline misses, dropped blocks, queue depth,
underruns, peak and evolving RSS, CPU frequency and available thermal/throttling
observations. Include signal interruptions in receive testing and distinguish
acquisition from synchronized reception. Record intrinsic codec latency separately
from added buffering.

The proposed gate is p99 processing time at most 70% of represented audio time,
with no sustained deadline failures, dropped blocks, growing queues, underruns
after warm-up, progressive memory growth or thermal slowdown invalidating the
result. Host and QEMU results do not substitute for this radio measurement.

## Capture isolation iteration, 2026-11-03

The `49de1f86` physical collection is **FAIL** for repeated USB-DIG TX: first turn completed, second capture/engine backlog before any encoding. Detailed cause limits and evidence hashes are in `capture-fix-findings.md`. Corrected first-turn SDR backend decoding is not listening acceptance. The earlier corrected virtual four-turn result remains separate.

The next candidate uses retained bounded capture, skips competing GUI audio consumers while it owns capture, and exports capture delivery/pump/PA observation timing. All 30 host tests passed, including 100 actual-codec diagnostic lifecycle turns; eight real-PulseAudio host turns passed, four with 20-second idle gaps and injected legacy-consumer delay. CI: https://github.com/bho18/OpenX6X00/actions/runs/41578267334 . These are software checks, not physical second-turn qualification. Current operator workflow is `candidate-test.md`: two locally armed USB-DIG turns only, with read-only SSH observation and collection. Original physical PTT, bidirectional RF, intelligibility and historical processing-margin gates are unchanged.

## Morning capture-fix retest, November 3, 2026 (America/Chicago)

Collection `freedv-collect-20261103-075527-d81efcdd.zip` SHA256 `a28408054ac701ae0b638dda6f9490fccdf2f9930747496fb18f688bc2c33490`: source `6976603`, two completed USB-DIG turns, retained resources and EOO totals 1/2. Overall voice result FAIL: microphone PCM is near DC 149 with AC RMS about 1; turn one also has an early underflow before drain. Capture delivery gaps 762/773 ms versus pump gaps 9/10 ms and input lengths 7.434/7.425 s do not establish lossless eight-second capture. The metric includes the initial callback delay. PulseAudio 16.1 record-queue overflow may discard data without a capture-hole notification, so disappearance of the prior backlog abort is not proof of resolution. RF outputs match SDR at +88.55/+84.55 Hz, coherence 0.957/0.950; speech acceptance remains unassessed. AF 0 versus prior speech-bearing AF 4 is an unconfirmed route dependency. Next scope is one AF-4 turn with an independent, bounded source recording on the existing image; no repeated basic matrix or reflash.

User listening update for d81efcdd: FreeDV produced audio from the SDR recording, but it was unintelligible. Listening FAIL for the supplied SDR artifact; GUI version/settings and correction status were not supplied. Derived offset-corrected copies remain unassessed.

## Confirmed capture deletion and unresolved input route, 2026-11-03

The `88ac01ab` collection (ZIP SHA256 `a24e22e717a44f833e83accbd3cdfc6df9ee70ec840edbb4e7eb4f8654613019`) contains two new AF-4 USB-DIG turns on source `6976603018dcaed94cb2024d06f234efd9fe0c01`. All 63 evidence checksums and ZIP CRCs passed. Both turns mechanically completed, but both physical-source and GUI TX capture remained near DC 149 with AC RMS approximately one count. Raising AF to 4 did not restore input. Physical hand-PTT gating and USB/USB-DIG route differences remain unassessed.

Case 003 matches its independent capture contiguously, 352,568 samples. Case 004 matches exactly before and after one deletion of **25,520 samples / 578.684807 ms**. Its 327,296 GUI samples map to reference offset 1,637,752 before and 1,663,272 after; identical neighboring ADC values allow splice positions 8,344–8,346. The independent recorder preserved all samples through a 773.089691 ms delivery pause; GUI callback gap was 772 ms with a 200 ms server queue. Zero reported holes/drops did not detect the deletion. This is **FAIL**, not a completed audio-path acceptance. `check_capture_continuity.py` reproduces the all-sample comparison, with known-deletion/ambiguity/corruption regression tests. Private recordings remain unpublished.

The earlier `d81efcdd` SDR was reported by the operator as producing unintelligible decoded audio; that listening failure belongs to that recording, not to the new unassessed SDR. Neither recording qualifies a profile. The original 70% processing-margin failure and independent physical PTT/RX/RF gates remain unchanged.

The continuity candidate requests one second of transport capacity, rejects smaller grants, retains the 500 ms processing-backlog limit, separates initial capture delay from later gaps, and adds a latched capture-stall fault. Playback stays uncorked and empty across fenced turns to prevent allowing idle suspension. The origin of the physical pause remains a hardware-test question. The current targeted procedure is `candidate-test.md`; older operator checklists are historical evidence only.


The continuity-fix candidate is published at https://github.com/bho18/OpenX6X00/releases/tag/freedv-continuity-fix-20261103 . Source `7fb07274529d06ac1b078e37e2878faeea3ef968` adds a launcher-message quoting correction to the tested compiled-code revision `84883da69641157ddaa8206ad3acf8050832e51c`; binaries are unchanged. Thirty-two host tests, 20 collector/analysis tests, normal/diagnostic X6200 and full X6100 builds passed. The real-PulseAudio CI at https://github.com/bho18/OpenX6X00/actions/runs/41659501606 completed 12 turns and explicitly faulted the injected 750 ms pause with fault 16; the retained sink stayed RUNNING with idle suspension enabled. This is virtual-device evidence only.

Publication run https://github.com/bho18/OpenX6X00/actions/runs/41662657687 passed Windows parser/mock-SSH tests, full source-file hashes, reconstructed image identity, ZIP CRC and enclosed-image verification. Published ZIP SHA256 `f93add221d7197a4dabd263f77e7d5f3ce0bcf87c0e3d369f69a9269d2d7d3ab` matches GitHub's asset digest; enclosed image SHA256 `6726b88e5186364c3116e8696d28267911c03189f60bec8f845bc418e5c6f5e8`. The ZIP contains one complete 571,473,920-byte image. Physical continuity, microphone/PTT routing and RF qualification remain pending the current guide's four short turns. No private recordings were published.


## Continuity candidate physical results, 2026-11-03 12:45 America/Chicago

Collection `28b2170f`, ZIP SHA256 `d07cad0c7bd26c49f775aa5f0dd2f32e6f4726c62893bb6ea88fc6bf1791a3ac`, matches source `7fb07274529d06ac1b078e37e2878faeea3ef968`. All four turns completed with no capture/backlog/engine faults, short 10–16 ms callback gaps and retained codecs within each pair. Exact independent comparison verifies all 352,576 and 352,512 samples in the USB pair with no deletion. No independent recording covers the USB-DIG pair, so its exact comparison is BLOCKED despite healthy timing. This is a narrow USB capture-continuity PASS, not native-voice qualification.

USB one and USB-DIG two have sustained input activity ending near 5.35 and 6.16 seconds; USB two and USB-DIG one have only brief transients over nearly constant input. Physical hand-PTT edges are not independently recorded under MODEM; actual operator button timing and analog leakage observations remain pending. Both modes show the input problem, so the mode label alone is not an adequate explanation. Cached AF is 1 for all cases, not the requested 4; no AF-only repeat is proposed because earlier AF-4 evidence also failed to restore input.

All four modem files match SDR at approximately +62 Hz offset. Corrected derived excerpts yield 63/64/64/63 valid frames in the pinned RADE V1 backend, versus 43/44/44/43 without correction. Listening and complete GUI acceptance remain UNASSESSED. Three cases report an early pre-drain underflow after 5,623 accepted samples; this remains a failed playback gate, separate from drain-tail events. Its causal relationship to RF disturbances is unresolved.

The collector's transient-only activity misclassification is corrected and tested: 23 analysis/collection tests pass, with explicit late-spike and quiet-continuous-signal coverage. No firmware image was changed and no new RF batch is requested pending the button observations. Private originals and derived recordings remain unpublished; full analysis is retained in the workspace evidence directory `freedv-continuity-20261103-analysis`.

## Shared-route candidate — November 3, 2026

Implemented a shared ordered hardware route driver for diagnostics and native voice,
retained route settings between turns, RECORD-before-gain restoration, a bounded
control-write/bulk-resend trace, and rejection of incompatible retained capture
formats. Added named record-only comparisons and a separately armed pre-key gain
comparison. The diagnostic interface stages cases and supports observations,
abort, incremental verified collection and assessment. RF remains locally armed
for each transmission. Record-only five-case repetition cannot assert MODEM/PTT.

Software evidence is under the route candidate's verification manifest. The host
suite passes 34 checks, including the reproduced stereo/mono transition and shared
route failure handling. Additional Python checks cover staging and stereo source
chunks. The retained engine processed 100 distinct public speech excerpts through
TX/RX, with 100 EOO emissions, one RADE/encoder/decoder set, four resamplers and
zero engine faults. These excerpts are not a numbered-speech listening assessment.
A separate accelerated run processed 600 seconds of valid modem input with 4,948
synchronized blocks, 4,935 synthesis blocks and zero faults; this is represented
input duration, not ten minutes of paced physical operation. Actual ARM processing
under QEMU completed 33 modem blocks and 29 synthesis blocks without faults.

The documented X6200 API provides aggregate TX and hand-key codes but no
independent hand-PTT release observation while MODEM remains active. Its hardware
backend continues to report that capability unsupported. Production profile
selection remains empty. This is a control-interface dependency; microphone
silence, aggregate TX and radio-side PTT are not substitutes.

Physical microphone capture under MODEM, analog isolation, intelligibility,
receive survival during decoded-speaker playback, both physical PTT controls,
complete official-GUI interoperability and final RF acceptance remain unqualified.
The historical 70% processing-margin failure and independent RF-off timing gate
remain open. No new physical pass is inferred from virtual devices or QEMU.
