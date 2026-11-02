# X6200 acquisition endurance and steady speech control — October 27, 2026

Follow-up: the [subsequent sustained speech report](rade-long-sync-radio-2026-10-27.md)
now supplies the continuous synchronized measurement identified below as next
work. The observations in this earlier report remain scoped to their own runs.

The three-participant RADE acquisition implementation meets the narrow
30-minute zero-input timing screen. The short synchronized control also keeps
pace, with a separate, measurable pipeline latency. Sustained synchronized
speech, transitions and physical audio/radio qualification remain outstanding.

The operator confirmed the normal GUI and waterfall remained active and
responsive throughout, with no other digital application running.

## Evidence identity

- Bundle: `freedv-acq-followup-results-20261027-172102.zip`.
- ZIP SHA256: `44298b1f61a16ac2b6ae64bea572d4f3aa76a67c5ad91819803d938d5f735068`.
- Twenty files, 963,373 expanded bytes; Windows ZIP path separators normalized.
- Both cases used the unchanged matching-SDK package from
  `b9fcea82e0858d9d34694dc0ae8d46057a398104`.
- Standalone helper SHA256:
  `37ab64011732a5db446b8d1d2a7714c02366d1268f580a7f23d9c1bd4e69ffda`,
  matching source commit `c7a9f41a4a8206b4c0f7953de3e77ca2690203b2`.
- Ninety-four independent consistency checks passed across both cases' manifests,
  package/source identities, stdout/results, commands, summaries, event timestamps
  and resource records. Package manifests and checksum lists match the earlier
  audited sweep byte for byte.

Actual runtime binaries, model payloads and raw SDK build logs are not in this
upload. Their recorded hashes cannot be independently recomputed from it; the
audit verifies submitted records and trusted source identities, not a rebuild.

## Thirty-minute zero-input acquisition

The benchmark ran for 1,830.426 wall-clock seconds, including 30 seconds of
warm-up and 1,800 measured seconds. It processed 15,250 input blocks, of which
15,000 were measured. Every block represented 120 ms. The two acquisition helpers
each completed 15,250 jobs; all acquisition calls used the parallel path and all
helpers joined at shutdown.

| Measurement | Result |
|---|---:|
| Mean complete block processing time | 65.765 ms |
| p99 complete block processing time | 77.053 ms |
| Maximum complete block processing time | 105.609 ms |
| p99 / represented input interval | 64.21% |
| Original 70% screen for this interval | 84 ms |
| Maximum input lateness | 6.319 ms |
| Input arrivals more than one interval late | 0 |
| Processing / nominal completion deadline misses | 0 / 0 |
| Queue-full events / dropped / abandoned blocks | 0 / 0 / 0 |

This meets the selected workload's timing screen without changing the threshold.
The source was literal zero input, not off-air noise. There was no speech output,
FARGAN synthesis or physical playback in this case, so this result does not
qualify those workloads or prove absence of speaker underruns.

Measured caller/helper/speech CPU work totaled 2,098.915 CPU-seconds over 1,800
seconds of input, about 1.166 CPU cores on average. This excludes some helper
coordination overhead and the rest of the radio software; it is not whole-chip
utilization. The mean receiver-stage time, 65.657 ms, is close to the earlier
short run's 65.552 ms. A larger p99 from the larger sample population alone is
not evidence of thermal slowdown.

## Short steady synchronized control

The control represented 5.04 measured seconds after three seconds of warm-up.
All 42 measured input blocks were synchronized and produced speech features.
There was no EOO frame, fixture wrap, dropped block or queue saturation. Acquisition
completed during warm-up; no acquisition-helper CPU was needed in the measured
steady-speech population.

| Measurement | Result |
|---|---:|
| Mean interval between completed output blocks | 119.840 ms |
| Maximum interval between completed output blocks | 125.683 ms |
| Maximum input lateness | 0.207 ms |
| Mean receiver/input stage | 70.440 ms |
| Mean speech/output stage | 85.653 ms |
| p99 speech/output stage | 92.419 ms |
| Mean full-path processing latency | 156.171 ms |
| p99 full-path processing latency | 162.662 ms |

The overlapping stages deliver successive speech blocks approximately every
120 ms despite a block taking about 156 ms to traverse both stages. This explains
the 42 reported one-interval full-path misses: the latency exceeds one input
interval, while input handling and output cadence keep pace in this short control.
The processing latency excludes intrinsic modem lookahead and physical audio
buffering, and must not be presented as total microphone-to-speaker delay.

The original whole-block 70% criterion is not met by this speech control. Even
the speech/output stage's p99 is 77.0% of its interval. The positive cadence
observation does not silently replace that requirement; sustained throughput,
buffering/latency and any proposed concurrent-processing acceptance criterion
need an explicit assessment after the remaining measurements.

## Memory, frequency and observational limits

The endurance run retained 1,816 resource samples spanning almost the entire
1,830-second process, with a maximum gap of 1.027 seconds. Every sample reports
1.008 GHz, the performance governor and cooling state zero. No temperature zones
were available. No frequency reduction was observed, but absolute temperature
and per-block timing trends are not recorded.

Sampled RSS grew from 4,940 to 8,104 KiB while virtual size stayed at 64,060 KiB
after initialization. This is consistent with the benchmark touching its bounded,
preallocated timing records: the ARM record layout is 232 bytes, and the measured
RSS trend corresponds to approximately 231 bytes per 120 ms input block. The
15,000 measured records represent 3,480,000 bytes within a fixed 4,176,464-byte
allocation. Thus RSS was not flat, but the observed growth has a known
instrumentation explanation; it is not sufficient evidence of a codec leak or
a general proof of leak freedom. Reported `ru_maxrss` was 12,980 KiB and remains
distinct from sampled process RSS/high-water observations.

## Decision and next evidence

Retain three acquisition participants. The previously demonstrated no-sync
capacity deficit is now resolved for this 30-minute zero-input workload with
the normal GUI/waterfall active.

Next, measure 30-minute synchronized speech using a single long transmission,
without the short fixture's intervening EOO/wrap. Keep the existing fixture as
a separate transition/reacquisition stress case. Per-block transition/path
observations are still needed to attribute its occasional delays precisely.

The [sustained speech test guide](rade-long-sync-radio-test.md) uses a separately
generated continuous waveform and the unchanged SDK runtime. It includes a
short radio smoke check before the endurance case and records fixture storage
separately from codec memory. Native generation/validation does not qualify radio
timing or replace the radio measurements.

Native microphone/speaker routing, PTT recovery, RF interoperability, physical
underruns, TX endurance and the overall feasibility decision remain outstanding.
All work and results remain in the fork; no upstream submission was made.
