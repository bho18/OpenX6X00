# X6200 RADE signal-input sampling profile

The operator's second profile exercises RADE reception and speech synthesis.
The full returned capture attributes **64.5608% of samples to two shared Opus
neural-network matrix kernels**, **10.4054% to resampling**, and **6.7905% to coarse
pilot acquisition**. These matrix kernels already use NEON. This is a different
workload from the scalar acquisition loop that dominated the silence capture.
Overall RADE qualification remains
**INCONCLUSIVE**, and this unchanged build still exceeds real-time processing.

## Evidence and measurements

Evidence consists of the operator's terminal output and returned
`rade-rx-sync-profile.data` (123168 bytes), SHA256
`0068a0f0f0fefcace11b292e943fd5492ad69de0460843a4394b33bc1f98b133`.
Addresses are mapped using the exact library and executable returned with the
preceding no-sync profile. The new capture records the same package paths and
mapping lengths. The worker's ASLR base is now `0x410000`; ELF segment offsets
are applied to each mapping, not assumed from the earlier process. No build IDs
or fresh binary hashes are embedded in this capture, so identity relies on the
previously returned, checksum-verified package and operator continuity.

The run used `fixtures/rade-modem.raw`, 20 seconds unpaced, 3 seconds warm-up,
continuous workload, and `cpu-clock:u` sampling at 99 Hz. The command collected
2960 samples, with zero lost samples and approximate event count 29898989600.
Independent binary decoding reproduces all three values, with no unmapped
samples. First-to-last sample span is 30.047625219 seconds.

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

## Complete function shares

| Function or group | Self samples | Share of all samples |
| --- | --- | --- |
| `cgemv8x4` | 1115 | 37.6689% |
| `compute_linear_neon` | 796 | 26.8919% |
| `sinc_mono_vari_process` | 308 | 10.4054% |
| `rade_acq_check_pilots` | 227 | 7.6689% |
| `rade_acq_detect_pilots` | 201 | 6.7905% |
| Other functions and system libraries | 313 | 10.5743% |

The top two functions total 1911 of 2960 samples, or 64.5608%. These are
whole-command self samples, including startup and warm-up. They are not
inclusive stage timings and cannot be multiplied directly by the steady-state
RTF to predict an optimized run. The acquisition candidate changes only the
coarse-search function; the majority of this signal workload's cost lies in
other functions.

The new capture stores its MMAP records after most samples in physical file
order, although their timestamps precede those samples. Perf drains CPU buffers
independently. The offline helper now resolves samples by timestamp and process
exec epoch, rejecting future or ambiguous mappings. Eleven parser tests pass,
including out-of-order metadata, remaps, exec barriers and timestamp ties; both
real captures reproduce their original totals after the correction.

## Original thresholded address report

The report hides rows below 1%. The sums below include only the printed rows;
they are partial contributions rounded by perf, not complete function percentages.

| Address group | Exact function mapping | Sum of displayed percentages |
| --- | --- | --- |
| Library `52468`, `524dc`, `524d8`, `52470`, `524d4`, `52458`, `52494`, `524d0`, `52588` | `cgemv8x4` in Opus `dnn/vec_neon.h` | 22.29% |
| Library `5354c`, `5361c`, `53624`, `53644`, `53384` | `compute_linear_neon`, including inlined dense/sparse float matrix kernels | 10.71% |
| Worker `bb3c`, `bba4` | `calc_output_single` inlined in libsamplerate `sinc_mono_vari_process` | 2.12% |

The displayed neural-kernel rows total 33.00%; the entire displayed table totals
35.12%. The full capture above supplies the previously hidden function shares.
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

Complete function counts are now available. Stage timing or a suitable call-chain
measurement is still needed to split the shared neural cost. The guarded acquisition candidate
remains worth measuring for no-sync and transition behavior, but its scope does
not establish a speedup in these shared neural kernels or qualify the full speech
receiver.
