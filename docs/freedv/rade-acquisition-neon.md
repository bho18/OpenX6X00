# Experimental ARM acquisition acceleration

The [returned X6200 profile](rade-radio-profile-2026-10-27.md) attributes 93.53%
of no-sync samples to `rade_acq_detect_pilots`. Its hottest instructions compute
two complex correlations with scalar floating-point arithmetic. This experiment
adds an optional ARM NEON implementation for that function. It is disabled by
default and is not a production voice mode or a hardware feasibility PASS.

## Preserved behavior

The candidate processes four independent timing positions in SIMD lanes. Each
lane keeps the original sample accumulation order and the fused complex-product
structure observed in the SDK binary. The complete time/frequency grid remains
in place. A subsequent scan uses the original time-then-frequency traversal for
maxima and ties; noise estimation and detection thresholds retain their original
order and formulas. There is no silence shortcut, reduced search, model change
or alteration of the synchronized speech decoder.

ARMv7 NEON and scalar arithmetic differ for subnormal values and non-default
rounding modes. The candidate therefore checks its arithmetic domain and uses
the original scalar calculation outside it. The fast path requires round to
nearest and finite components that are either zero or have these magnitudes:

| Operand | Minimum nonzero magnitude | Maximum magnitude |
| --- | --- | --- |
| RX samples | `2^-40` | `2^16` |
| Active frequency-shifted pilot samples | `2^-32` | `2^16` |

Within these bounds, binary32 operands lie on grids no finer than `2^-63` and
`2^-55`; their products, fused cancellations and ordered correlation sums cannot
produce a nonzero result below `2^-118`, which remains normal. Upper bounds avoid
overflow in the vector correlation. Magnitudes and thresholds are still computed
by the unchanged scalar code. Tests must demonstrate that ordinary cases select
NEON and exceptional cases select scalar, not merely observe equal outputs while
accidentally exercising only the fallback. Floating-point environment settings
are not changed globally.

The implementation copies the bounded input window into a flat float scratch
array before loading interleaved vectors. This avoids pointer traversal across
C structure members and requires 16,632 bytes of scratch storage (the development compiler reports 16,840 bytes
for the helper stack frame).
The standalone benchmark has that space; a future production worker must have
its stack budget qualified. No heap allocation is introduced in acquisition.

## Source and build controls

`tools/freedv/patches/rade-acq-neon.patch` applies only to the exact source pinned
in `sources.json`. The package builder verifies the original file hash before
applying it to a disposable source copy. `--rade-acq-neon` requires the matching
X6200 Buildroot SDK. It enables the macro and ARM options only for `rade_acq.c`;
the host fixture generator retains the default scalar implementation. The model
hash/TLS checks and the other codecs' source pins remain in force.

The package manifest records the patch, original/patched source, source-local
CMake configuration, compiler options, differential harness and validation
summary hashes. The corresponding-source archive includes the modified sources
and the patch/build tools needed to reproduce them. Generated binaries, model
caches and raw radio evidence stay outside Git.

## Validation and remaining measurements

The differential harness compares every correlation-grid component as well as
the detection decision, timing, frequency, maximum and threshold. It covers
deterministic noise, silence, valid pilots with frequency/timing offsets, ties,
timing tails, near-threshold cases and exceptional arithmetic inputs. Both the
default-off source and enabled candidate must pass using the matching SDK under
QEMU Cortex-A7. The emitted library is inspected for vector FMA instructions;
setting a macro alone is insufficient evidence that the candidate was built.

Before the builder creates a runtime archive, it also requires the six upstream
V1 text cases, a complete speech round trip, the pinned V1 feature-loss check and
the seven benchmark workloads including interrupted RX/reacquisition. Speech
duration and finite feature checks prevent a truncated or NaN result from being
counted as a loss-test pass. Validation logs accompany the artifact.

These checks establish software behavior under the tested conditions. QEMU
timings do not establish X6200 speed, GUI headroom, thermal behavior or stack
requirements in production. A successful SDK artifact must undergo a new short
on-radio benchmark before any 30-minute qualification. Keep the existing package
and its failure results for comparison. The synchronized workload still requires
separate profiling; accelerating acquisition alone does not establish that the
full RADE receiver can meet the performance gate.

No changed library is inserted into the normal GUI, and no route or RF control
is part of this benchmark experiment. Native microphone/speaker routing and PTT
qualification remain separate unfinished milestone gates.

## Development check record

Before the matching SDK build, a development-only GCC 14 ARM build executed
under QEMU Cortex-A7. All 47 differential cases matched bit-for-bit: 34 selected
NEON and 13 selected scalar fallback. The default-off build matched all 47 and
selected scalar every time. This includes the formerly differing subnormal
probe, nonfinite inputs, signed zero, magnitude boundaries, non-default rounding,
noise, pilots, ties, timing tails and near-threshold cases. These development
binaries are not deployable substitutes for the matching Buildroot SDK. The SDK
build repeats the checks and inspects the actual packaged library.
