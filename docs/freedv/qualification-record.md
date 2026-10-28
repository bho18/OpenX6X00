# X6200 FreeDV qualification record

The [current validation protocol](combined-validation-plan.md) retains the
remaining coverage below while shortening audio runs to two measured minutes
and a 5-10-minute mixed TX/RX exercise. Demonstrate audible decoding and hand-mic
encoding early, then finish the remaining routing/recovery checks. This replaces
the original 30-minute duration requirement at the user's direction; it does
not claim endurance or thermal qualification from the shorter measurements.

This is the planned evidence record, not a procedure for the current read-only
diagnostic page. Do not perform these cases until guarded route experiments and
the diagnostic image have been implemented and tested. Physical routing and
PTT observations remain uncollected; the codec evidence below is file-based
processing on the radio and does not qualify the physical audio path.

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
