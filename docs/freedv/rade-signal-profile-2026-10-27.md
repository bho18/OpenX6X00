# X6200 RADE signal-input sampling profile

The operator's second profile exercises RADE reception and speech synthesis.
Its displayed hot addresses resolve to Opus neural-network matrix kernels that
already use NEON. This is a different workload from the scalar acquisition loop
that dominated the silence capture. Overall RADE qualification remains
**INCONCLUSIVE**, and this unchanged build still exceeds real-time processing.

## Evidence and measurements

Evidence currently consists of the operator's pasted terminal output. The new
`rade-rx-sync-profile.data` has not yet been returned. Addresses are mapped using
the exact library and executable returned with the preceding no-sync profile,
from the same `/tmp/freedv-benchmark` package. These mappings should be checked
against the new capture's recorded paths when it arrives.

The run used `fixtures/rade-modem.raw`, 20 seconds unpaced, 3 seconds warm-up,
continuous workload, and `cpu-clock:u` sampling at 99 Hz. The command collected
2960 samples, with zero lost samples and approximate event count 29898989600.

| Benchmark metric | Returned value |
| --- | --- |
| Measured blocks / represented input | 167 / 20.04 s |
| Synchronized blocks / feature blocks | 159 / 157 |
| Mean processing time / represented audio | 1.261194 |
| p99 / maximum processing time / block interval | 1.527039 / 1.535758 |
| p99 / maximum block duration | 183.245 / 184.291 ms |
| Speech / resampled output samples | 301440 / 830844 |
| Nonzero resampled output samples | 830061 |
| Initialization, separate from steady-state timing | 11.687 ms |
| Peak RSS | 8740 KiB |
| Deadline misses | null (unpaced) |

The full workload averages about 151.34 ms per nominal 120 ms block. This run
includes fixture-loop acquisition/transitions: 159 of 167 measured blocks are
synchronized. It is not an isolated steady-state synchronized profile. Sampling
also covers setup and warm-up. These numbers do not measure live queues, audio
underruns, RF interoperability, intrinsic latency or sustained thermal behavior.

## Address mapping

The report hides rows below 1%. The sums below include only the printed rows;
they are rounded lower-bound contributions, not complete function percentages.

| Address group | Exact function mapping | Sum of displayed percentages |
| --- | --- | --- |
| Library `52468`, `524dc`, `524d8`, `52470`, `524d4`, `52458`, `52494`, `524d0`, `52588` | `cgemv8x4` in Opus `dnn/vec_neon.h` | 22.29% |
| Library `5354c`, `5361c`, `53624`, `53644`, `53384` | `compute_linear_neon`, including inlined dense/sparse float matrix kernels | 10.71% |
| Worker `bb3c`, `bba4` | `calc_output_single` inlined in libsamplerate `sinc_mono_vari_process` | 2.12% |

The displayed neural-kernel rows total 33.00%; the entire displayed table totals
35.12%. The remaining samples cannot be assigned from the thresholded transcript.
`cgemv8x4` is an int8-weight matrix/vector kernel, not complex multiplication.
Its disassembly includes vector loads, `vmull.s8`, pairwise additions and widening
accumulation. Cortex-A7 lacks newer ARM dot-product instructions. The largest
individual site is a vector load, but sampling skid and missing cache/IPC counters
prevent concluding that memory bandwidth is the bottleneck.

These instructions demonstrate actual execution of optimized Opus kernels;
`opus_arch=0` does not indicate wholly scalar neural processing on this build.

## Why this does not isolate FARGAN or the RADE decoder

The benchmark's FARGAN implementation calls exported `compute_generic_dense`,
`compute_generic_gru`, `compute_generic_conv1d` and `compute_glu` through the PLT
into the shared library. The library's RADE core decoder calls the same helpers.
Those helpers reach its local `compute_linear_neon` and `cgemv8x4` functions.
Thus a sample inside these library kernels can originate from either stage,
even though FARGAN's caller code is in the executable. The profile has no call
chain; attributing the entire hotspot specifically to FARGAN or specifically to
RADE neural decoding would be unsupported.

Return the small binary capture for complete function sample counts using the
existing matching binaries. Stage timing or a suitable call-chain measurement
is then needed to split the shared neural cost. The guarded acquisition candidate
remains worth measuring for no-sync and transition behavior, but its scope does
not establish a speedup in these shared neural kernels or qualify the full speech
receiver.
