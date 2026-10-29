# X6200 acquisition worker sweep — October 27, 2026

The subsequent [30-minute follow-up report](rade-acquisition-endurance-2026-10-27.md)
records the completed endurance and steady-speech control results.

Parallel acquisition removes the demonstrated short zero-input throughput
deficit. Three acquisition participants are the provisional choice for the next
endurance test. This is a positive scaling result, not completion of the FreeDV
feasibility milestone or qualification of a production audio path.

The operator confirmed the normal GUI and waterfall stayed active and responsive,
with no other digital application running. Application version was 0.3.6; the
previously reported baseband version was 1.0.7 (this collector does not query it).

## Evidence and provenance

- Submitted bundle: `freedv-acquisition-parallel-results-20261027-165525.zip`.
- ZIP SHA256: `5f0d7b6de8f359122348572beac602f4178553ad7153b508dccb432e7ce67214`.
- 76 files, 379,957 uncompressed bytes; Windows path separators normalized during extraction.
- Package source: `b9fcea82e0858d9d34694dc0ae8d46057a398104`, clean worktree.
- Matching Buildroot GCC 12.3.0; pinned SDK, RADE, Opus/models and guarded kernel
  source records are consistent with the implementation in the fork.
- Modem fixture SHA256: `f4b0ce17951109d33abbf72f865bdec9e718044e869cde3ffdbdcf3191592d21`.
- 318 independent consistency checks passed across manifests, source hashes,
  results/stdout, event records, summary comparisons and embedded SDK records.

The three embedded SDK summaries reproduce their recorded hashes. They record
the existing acquisition/V1/pipeline checks and the new 47-case detector and
247-call receiver corpora at all four counts, 130 lifecycle checks, 14 disabled-API
checks and twelve full pipeline comparisons. Runtime binaries, models and raw
SDK logs were not included in this result upload, so their payload hashes cannot
be independently recomputed from this bundle. This is verification of consistent
submitted build records and trusted source identities, not an independent rebuild.

## No-signal acquisition

Each measured zero-input case contains 84 blocks representing 10.08 seconds,
after three seconds of warm-up. Every block represents 120 ms. The figures below
are stage A, including input resampling and the RADE receiver; stage B has no
speech to synthesize in these cases.

| Acquisition participants | Mean A time | p99 A time | Maximum measured input lateness | Inputs more than 120 ms late |
|---|---:|---:|---:|---:|
| 1 | 142.99 ms | 148.75 ms | 2541.16 ms | 84 |
| 2 | 83.84 ms | 92.42 ms | 0.77 ms | 0 |
| 3 | 65.55 ms | 72.39 ms | 0.61 ms | 0 |
| 4 | 62.51 ms | 88.87 ms | 3.84 ms | 0 |

Three participants reduce mean processing time by about 54%, a 2.18-fold speedup
over one. Four are slightly faster on average but had a worse tail in this sweep.
The maximum backlog including warm-up was 0.78 ms with three participants. All
multicore zero-input cases completed without queue saturation or dropped blocks.

The three-participant full-path p99 was 72.50 ms, below 84 ms (70% of 120 ms) for
this short, zero-input workload. Two and four did not meet that particular p99
screen. This does not change the previously recorded serial result or establish
the full sustained acceptance gate.

Measured caller/helper/speech CPU work is about 140 CPU-ms per zero-input block
with three participants, versus 143 CPU-ms with one. The improvement mainly comes
from distributing work, not from omitting searches. The measured DSP work is
about 1.17 CPU-seconds per second of represented audio; it excludes some helper
coordination overhead and is not whole-chip utilization or a spare-capacity claim.

## Signal, interruption and correctness

All nine multicore cases completed. All synchronized and zero-input functional
comparisons matched the one-participant control. Interrupted runs at counts 2–4
also matched each other in full/measured counters, feature/PCM/metadata hashes and
both recovery windows. These are finite, noncryptographic regression comparisons.

| Interrupted reception | Completion | Maximum measured input lateness | Inputs more than 120 ms late | Queue maximum / dropped blocks |
|---|---|---:|---:|---|
| 1 participant | Stopped: queue full | 589.26 ms | 27 | 2 / 1 |
| 2 participants | Complete | 131.75 ms | 1 | 1 / 0 |
| 3 participants | Complete | 156.75 ms | 1 | 1 / 0 |
| 4 participants | Complete | 125.65 ms | 1 | 1 / 0 |

The one-participant interrupted run completed only 90 measured blocks before
stopping; the others completed all 217. Consequently the suite correctly reports
`execution_complete: false`, and its three comparisons against that incomplete
control remain INCONCLUSIVE. This is a capacity failure of the control, not an
observed decoded-data mismatch between complete runs.

Multicore synchronized cases had no inputs more than one interval late, but
maximum input lateness was still about 60–77 ms. Interrupted cases each had one
larger transient delay. Four participants had the best interrupted tails in this
sample, so retain four as an alternative rather than treating three as a proven
optimum. Counts were run in ascending order without repeated or randomized trials.

## Remaining transition question

The 9.984-second fixture is a finite transmission ending with an end-of-over
(EOO) frame. The benchmark repeats it. `continuous` means no synthetic mute; it
does not mean uninterrupted synchronization across the fixture boundary.
Synchronized cases reported one EOO; complete interrupted cases reported two.
Output-completion periods skip records without speech, so the roughly 0.7-second
gaps in the synchronized sweep include over-ending/reacquisition behavior and
cannot be called measured speaker underruns.

At counts 2–4, synchronized runs had seven parallel acquisition calls and one
serial fallback; interrupted runs had 28 parallel calls and four fallbacks.
Zero-input runs had 109 parallel calls and no fallback. The radio bundle lacks
per-block state/path events, so it cannot identify which operation caused each
timing maximum. Candidate-to-sync decoder initialization and fine refinement are
also possible transition costs outside the distributed coarse-search grid.

An isolated ARM development replay reproduced a scalar fallback immediately after
EOO: three very small nonzero RX components were outside the guarded NEON numeric
domain. Its structural counters matched the radio run, but development/SDK output
hashes differ, and it did not record the radio's event timing. This establishes a
mechanism to investigate, not exact attribution of the radio outlier. Do not
remove the numeric guard or discard low-level input to hide the delay.

## Resource observations and next tests

The 219 collected resource samples report 1.008 GHz CPU frequency, cooling state
zero and no temperature-zone readings. Each benchmark reports peak RSS 12,980 KiB;
sampled process high-water values are lower and should be kept distinct. The short
sweep cannot establish long-term memory or thermal stability. Physical audio and
radio controls were not accessed.

Proceed with three acquisition participants for a 30-minute paced zero-input
test with the normal GUI/waterfall active. A separate five-second synchronized
control with three seconds of warm-up ends before the fixture's first EOO/wrap
and helps separate steady synchronized processing from transition behavior.
Both can use the existing matching-SDK package.

The [follow-up test guide](rade-acquisition-followup-radio-test.md) provides a
small standalone helper and Windows commands for both cases. It verifies the
tested package, records its own source hash, and preserves incomplete or failed
results. Ten focused helper tests passed, with independent review of package
verification and qualification boundaries; no SDK binary changes are required.

Before qualifying sustained synchronized/interrupted reception, add per-block
transition/path observations and a suitable continuous-transmission fixture.
Evaluate transition latency and buffering explicitly; per-stage timing or a
responsive GUI cannot silently replace the original whole-block criterion.
Native microphone/speaker routing, PTT recovery, physical underruns and RF
interoperability remain unqualified. No upstream submission was made.
