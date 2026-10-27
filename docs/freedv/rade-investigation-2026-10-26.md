# RADE build audit and deeper performance investigation

The first radio measurements show failure of the current RADE implementation's
timing check, not proof that the X6200 cannot support RADE. Investigate the DSP
implementation and end-to-end stage costs before making the separate product
decision. The operator requested this investigation after noting ESP32-S3 FreeDV
examples and subsequently identified ezDV, while acknowledging it may not use
RADE.

## Returned build-log audit

The operator returned `freedv-package-build.log`, 632733 bytes,
SHA256 `61457ef10be495a7b9a3ba031e4ca0d76dca35a4bfb7c3cd4a86471ffa189a11`.
It contains an ARM target build followed by a separate x86 host fixture-generator
build. Keep those sections distinct. The raw uploaded log remains outside Git.

| ARM build evidence | Location in supplied log |
| --- | --- |
| Host/target `arm-buildroot-linux-gnueabihf`, cross-compiling `yes` | Lines 4007, 4014 |
| SDK ARM GCC selected by absolute path | Line 4009 |
| Compiler supports NEON; NE10 found | Lines 4093–4094 |
| Inline/external assembly: ARM EDSP, Media, NEON | Lines 4134–4135 |
| Intrinsics: ARM NEON and NE10 | Line 4136 |
| Run-time CPU detection: no | Line 4137 |
| Benchmark compiled with `-O2 -g`, generated Opus `config.h` force-included | Line 8766 |

The ARM configuration presumes supported SIMD features, so disabling runtime
selection is consistent with direct NEON dispatch. In the pinned Opus,
`opus_select_arch()` can therefore return 0 while DNN macros select NEON
directly. This evidence rules out assuming that `opus_arch=0` means a wholly
scalar Opus build. It does not establish that every RADE DSP loop is vectorized.
The later SSE/AVX warnings belong to the x86 fixture-generator build, not the
deployed ARM benchmark. ARMv8 dot-product intrinsics being unavailable is expected
for this ARMv7 target.

The log also contains upstream configure/deprecation warnings, including a
nonfatal stray-comma shell command at line 4100. Opus continued to generate its
configuration and build. These warnings are not evidence that NEON was disabled
and have not been established as the cause of the timing failure. Preserve
configuration/compiler/model hashes with any subsequent corrected build.

## What ezDV establishes

Inspected [ezDV commit b853e208fcba7a582d128ad215fb68f117dfc021](https://github.com/tmiw/ezDV/tree/b853e208fcba7a582d128ad215fb68f117dfc021):

- [Mode enumeration](https://github.com/tmiw/ezDV/blob/b853e208fcba7a582d128ad215fb68f117dfc021/firmware/main/audio/FreeDVMessage.h#L67)
  lists analog, FreeDV 700D, 700E and 1600.
- [Mode selection](https://github.com/tmiw/ezDV/blob/b853e208fcba7a582d128ad215fb68f117dfc021/firmware/main/audio/FreeDVTask.cpp#L234)
  maps those modes to the Codec2 FreeDV API, with no RADE case.
- [Build configuration](https://github.com/tmiw/ezDV/blob/b853e208fcba7a582d128ad215fb68f117dfc021/firmware/main/CMakeLists.txt#L104)
  selects those modes, fetches Codec2 tag 1.2.0, and disables LPCNet. It does not
  establish a RADE modem plus FARGAN speech pipeline on the ESP32-S3.
- [Math adapters](https://github.com/tmiw/ezDV/blob/b853e208fcba7a582d128ad215fb68f117dfc021/firmware/main/audio/FreeDVTask.cpp#L382)
  explicitly call Espressif DSP dot-product routines, including strided real
  operations for complex multiplication. This is useful implementation evidence
  for acceleration at the kernel level, beyond top-level compiler flags.

Thus ezDV is a useful reference for audio ownership, buffering, task design,
traditional FreeDV and optimized DSP. It is not an equivalent RADE performance
demonstration. The X6200's own short 700E results are consistent with traditional
FreeDV having substantial headroom. Other search results examined, such as
`sh123/esp32_codec2_arduino`, describe traditional Codec2; mentioning RADEV1 in a
README is not evidence that its full pipeline executes there. No equivalent
ESP32-S3 RADE implementation was verified in this investigation; this does not
claim that none exists.

## Concrete DSP cost to investigate

The pinned `rade_c` V1 acquisition searches 960 time positions, 40 frequency
positions and 160 pilot samples, with two complex correlations per position.
That is **12,288,000 complex multiply-accumulate iterations per 120 ms input
block**, before other processing. These are source-level iteration counts, not
measured CPU instructions. It uses ordinary C complex arithmetic loops separate
from the optimized Opus neural functions. The frequency-shifted pilot vectors
are already precomputed; proposing that precomputation again would not solve it.

The returned no-sync case produced no features or speech: FARGAN synthesis was
not executing on those measured blocks. Its slow timing therefore cannot be
attributed solely to speech synthesis. Synchronized RX is a separate workload
and may have different dominant costs.

## Host localization experiment

Without changing the host-validation codec/model binaries, a Linux `LD_PRELOAD`
interposer timed exported RADE functions during 20-second signal and silence
runs, each with three seconds of warm-up. All measured stage calls include
warm-up and fixture loop transitions. The worker is single-threaded and the
interposer forwards arguments/results unchanged. Original and interposed runs
matched functional frame, feature, synchronization, output/nonzero-sample and
duration counters. This comparison is not a waveform/loss or target-performance
qualification.

| Function | Silence: calls / inclusive time | Signal: calls / inclusive time |
| --- | --- | --- |
| Entire `rade_rx` | 192 / 7030.358 ms | 192 / 1152.979 ms |
| Coarse pilot search | 192 / 6941.513 ms | 12 / 455.244 ms |
| Fine timing/frequency refinement | 0 / 0 ms | 183 / 36.634 ms |
| Pilot validity/noise update | 0 / 0 ms | 180 / 376.790 ms |
| Receive BPF | 192 / 88.139 ms | 192 / 85.356 ms |
| Neural core decoder | 0 / 0 ms | 534 / 162.215 ms |

Coarse pilot search accounted for **98.74% of time inside `rade_rx` on silence**
in this host experiment. This is a concrete candidate for on-radio confirmation,
not a measured ARM percentage. `rade_rx` includes the other rows; do not add
parent/child times. FARGAN and resampling outside `rade_rx` are not in the table.
The signal run includes acquisition/fixture-loop transitions and is not an
isolated synchronized steady-state profile. Host CPU/dispatch/scheduling differ
from the radio, so neither its timings nor its stage percentages qualify ARM.

The host profiler is reviewable on `topic/rade-stage-profiling`, with source and
limitations in `tools/freedv/rade_stage_profile.c` and
`docs/freedv/rade-profiling.md`. No modified library has been deployed to the
radio or inserted into its GUI process.

## Next test and optimization criteria

Use the existing radio binary under low-rate user-space `perf` sampling, starting
with silence and then synchronized input. The pinned kernel enables
`CONFIG_PERF_EVENTS`; installed tool availability/sampling permissions remain
unmeasured. The prepared procedure uses `cpu-clock:u`, no build-ID cache writes,
RAM output and no RF/audio-device access. If perf is unavailable, add stage
timing to an SDK-built diagnostic benchmark rather than replacing system tools
or inferring a hardware limit from host measurements.

If target profiling confirms correlation kernels dominate, evaluate explicit
ARM NEON kernels and cache-friendly batching while preserving the complete
time/frequency search and detection thresholds. A possible implementation can
vectorize independent time/frequency positions rather than alter the reduction
order. Compare scalar and optimized correlation grids/detection decisions with
deterministic noise, offsets, interruptions and near-threshold cases, and retain
the upstream correctness/loss checks. Do not silently shrink the acquisition
range, skip no-sync work, change model/waveform, lower speech quality, suppress
deadline failures or hide the cost in unbounded buffering.

Profile synchronized RX separately for inference, FARGAN, filters and resampling.
Four cores permit investigating bounded pipeline stages later, but do not make
a stateful serial codec automatically four times faster. Every changed build
requires recorded patches/flags/config hashes, correctness validation and a new
on-radio smoke measurement before the 30-minute runs. The original RADE failures
remain in the evidence record; no optimization or final feasibility PASS has
been established, and no legacy-only or external-audio product decision is made.
