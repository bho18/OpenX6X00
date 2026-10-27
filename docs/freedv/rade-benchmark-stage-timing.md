# RADE benchmark stage timing

The signal-input radio profile places most sampled time in neural matrix
kernels shared by the RADE decoder and FARGAN. Self samples cannot distinguish
their callers. This instrumented benchmark measures the two API regions and
resampling separately, retaining the complete pipeline timer and its existing
performance threshold.

This is a separate diagnostic change from the acquisition optimization. The
previously published acquisition-candidate package remains the next radio test.
The stage instrumentation has passed host checks; target SDK execution and
radio measurements have not yet been completed. It introduces no radio control,
physical audio-device access or model changes.

## Measurements and JSON additions

The existing schema version stays 1; two additive top-level fields are emitted:

- `fixture_preparation_ms`: fixture read, PCM conversion and one-time fixture
  conversion to 44.1 kHz. The original `initialization_ms` retains its exact
  model/encoder/FARGAN initialization boundary. These two fields do not claim
  all startup time: streaming resampler creation and remaining buffer allocation
  are still outside them.
- `stage_timing`: nested schema version 1 with integer nanosecond measurements,
  exact `total_processing_ns`, stage definitions below, and explicit semantics.

Each stage reports `calls`, `active_blocks`, `sum_ns`, `p99_block_ns` and
`max_block_ns`. Calls count timed-region invocations, **not** individual neural
layers, LPCNet/FARGAN calls or internal resampler iterations. The implementation
currently enters each applicable region at most once per block, so calls and
active-block counts coincide. `other` counts residual evaluations.

| Stage | Measured work |
|---|---|
| `input_resampling` | Stateful input conversion, including repeated fixture chunk copies |
| `tx_speech_processing` | Float-to-PCM conversion and all LPCNet feature extraction for the block |
| `rade_tx` | The RADE TX API call |
| `rade_rx` | The RADE RX API call, including demodulation and its neural decoder |
| `rx_speech_processing` | Feature continuation buffering/packing, `fargan_cont` and speech synthesis |
| `output_resampling` | Native-to-44.1 kHz conversion, PCM conversion and nonzero sample counting |
| `other` | Full block elapsed minus the non-overlapping measured regions; includes IQ preparation/TX scaling, interruption muting, checks, synchronization query and timing/bookkeeping overhead |

Percentiles use **every measured block, including inactive zero values**.
Unused directions and RX speech/output regions without features/output remain
zero. Do not sum stage p99 or maximum values: those peaks can occur in different
blocks. Stage *sums* equal `total_processing_ns` exactly.

All seven per-block timing arrays are preallocated before streaming, bounded by
the existing maximum duration/input-size limits. At 1800 seconds RX they add
1,008,112 bytes (approximately 0.96 MiB); no per-block allocation or file I/O was
introduced. The original full-block timing bracket includes added timestamp
calls and ordinary stage storage/aggregation, and no instrumentation time is
subtracted. Closing-timestamp residual/statistic assignments and final sorting
and reporting remain outside the bracket, like the original benchmark's final
statistics updates. Instrumented timings must therefore be identified as such;
the change does not claim to measure every diagnostic instruction or establish
radio qualification.

Warm-up still performs the original processing and timing boundaries, but none
of its stage durations/calls enter measured statistics. The code preserves
codec/model calls, resampling, interruption sequence and pacing.

## Validation

Both original and candidate were compiled with the same host compiler, pinned
RADE/Opus generated config and static libsamplerate archive. Four tests passed
across **six original/candidate workload pairs**:

- TX, synchronized RX and no-sync silence RX, each 3 seconds after 1-second
  requested warm-up.
- Interrupted RX for 14 seconds after 1-second requested warm-up.
- Synchronized RX for 3 seconds with no warm-up, covering initial continuation.
- Paced TX for 1 second after 1-second requested warm-up.

Every original non-timing functional field and counter matched exactly. Stage
sums equal total processing nanoseconds; all values are finite/nonnegative;
individual stage p99/max stay within the total p99/max accounting for the old
JSON formatting precision; skipped stages are all zero. Calls equal measured
frames for input/core/other, and RX speech calls equal `feature_blocks`, proving
warm-up and no-feature blocks are excluded from those counters.

Examples: synchronized RX has 25 measured core/speech blocks; silence RX has 25
core blocks and zero speech/output blocks; interrupted RX has 117 core blocks
and 96 speech/output blocks. All reports retain `radio_io=false` and
`full_pipeline_qualified=false`. Host timing proportions are not radio evidence.

The differential tests compare every original functional JSON field and
counter, not PCM samples. Code review verifies unchanged DSP call order;
unchanged counts alone are not proof of identical decoded audio.

The matching-SDK package gate also requires valid stage timing in all four RADE
workloads. It checks schema and units, nonnegative integer counters, exact summed
time, rounded full-timer agreement, direction/feature activation, inactive zeros
and percentile bounds. Missing or inconsistent stage evidence prevents archive
release. The SDK validation summary records the checked workloads; QEMU timing
does not qualify radio performance.

## Reproduction and package identification

The `rade-prototype` job in `.github/workflows/freedv-checks.yml` builds both
versions with the same compiler, pinned RADE/Opus and generated Opus config,
then generates the pinned speech/modem fixtures and runs the existing streaming
checks plus `tools/freedv/test_stage_timing.py`. The pre-instrumentation source is
pinned at `cd6a672311e85a68feb97f24f65653a29791b1e0`, SHA256
`c9756bd241fc409bab7c04753d99dd69267c87efff4153e0f3c5761fe8f4827d`.

With prebuilt host binaries and the package's fixtures, repeat the stage checks:

```sh
python3 tools/freedv/test_stage_timing.py \
  --baseline /path/to/original-rade-frame-bench \
  --candidate /path/to/instrumented-rade-frame-bench \
  --fixtures /path/to/package/fixtures \
  --output /new/stage-validation-results
```

Use a new output directory. Keep these host binaries off the radio. A subsequent
deployable package must use the matching Buildroot SDK and record the changed
worker hash, source revision and `rade_benchmark_timing` metadata in its manifest.
The package includes this document as `STAGE-TIMING.md`. Existing collectors
retain the additive JSON fields without interpreting host results as target
qualification. Compare instrumented and original totals as different benchmark
builds: the extra clocks and bookkeeping may affect processing time.
