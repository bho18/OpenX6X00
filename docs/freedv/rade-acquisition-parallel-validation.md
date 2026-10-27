# Parallel acquisition: development validation, 2026-10-27

Status: **development correctness PASS; matching SDK and radio capacity pending**.
This records the optional implementation introduced in `5683bd6`, using patch
SHA256 `4343c892e60199677fe363940f144566229c14dd77c79bab14f1e9a1c1fe4ef3`.
The source, model and frozen waveform pins remain in the package manifest.

These checks used Debian GCC 14.2 and QEMU's Cortex-A7 model. The development
binaries must not be deployed to the radio. No QEMU elapsed time is used as
evidence of X6200 speed, scheduling margin or thermal behavior.

## Detector and receiver comparisons

Every ARM detector run exercised 34 guarded NEON cases and 13 scalar fallback
cases. Each receiver run exercised 19 named cases, 247 calls, all three receiver
states, weak/noisy/offset inputs and interruption/recovery. Correlation grids,
selected metrics/decisions and per-call receiver evidence matched the reference;
same-toolchain state totals also matched across counts.

| Acquisition participants | Detector cases | Detector parallel cases | Receiver calls | Receiver parallel calls | Result |
|---|---|---|---|---|---|
| 1 | 47 | 0 | 247 | 0 | PASS |
| 2 | 47 | 30 | 247 | 162 | PASS |
| 3 | 47 | 27 | 247 | 162 | PASS |
| 4 | 47 | 27 | 247 | 162 | PASS |

One participant executes the serial reference. Higher counts must show actual
helper work; merely accepting the count is insufficient. The host runs also
passed all 47 detector and 247 receiver cases for each count, but exercise serial
fallback because this experiment's parallel kernel requires ARM NEON.

On both host and emulated ARM, 130 lifecycle checks across 11 cases passed,
including invalid/V2 requests, reconfiguration rules, partial thread-creation
failure, multiple live instances and repeated close. The owned-helper counter
returned to zero; this is not a general heap-leak analysis. Fourteen disabled-API
checks passed on each platform. Complete native library builds with the option
ON and OFF each passed upstream V1 text checks (6/6), with consistent compilation
definitions across all 21 library translation units.

## Full pipeline and tooling

The ARM development package ran all 12 unpaced combinations of workers 1–4 and
synchronized, zero-input and interrupted reception through the SDK validator's
pipeline orchestration. All passed. Full/measured counters and feature/PCM/
metadata regression hashes matched the one-participant run; helpers executed for
counts 2–4 and were joined. These finite, noncryptographic hash comparisons are
regression evidence, not proof for arbitrary input or all waveforms.

The new package, collection, evidence and SDK-parser tests passed 36 checks.
Existing pipeline collection, benchmark runner, stage validation and pinned
fixture tests passed 23 checks. The unchanged-option host pipeline retained its
prior synchronized functional output. Independent review found no remaining
implementation blocker.

## Required next evidence

The manually triggered matching-SDK workflow must still pass all existing
acquisition, V1 speech/text/loss and pipeline gates, plus the new worker-count
corpora and full-pipeline comparisons, before producing a radio archive. The
disabled-API SDK check does not claim a complete SDK library build with the
option OFF. The full upstream V1 loss gate retains its default one-participant
scope; the additional fixture pipeline comparisons cover all four counts.

Then run the [radio sweep](rade-acquisition-parallel-radio-test.md) with GUI and
waterfall active. The short sweep selects a candidate for sustained testing; it
does not qualify the original serial 70% gate, 30-minute workloads, native audio
routing, PTT or RF interoperability. No upstream submission was made.
