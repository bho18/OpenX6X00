# FreeDV codec benchmark package

This package runs in-memory codec workloads with no radio-control or physical
audio-device access. It measures the speech/modem pipeline and resampling while
the normal firmware GUI and waterfall remain running. It cannot establish audio
routing, PTT behavior, RF interoperability or a full feasibility PASS.

The package manifest identifies either `host-validation` (computer only) or
`x6200-buildroot` (built with the matching SDK). Verify the downloaded archive's
published checksum before unpacking. The runner verifies all package files again.
Use an SDK package only with the baseline recorded in its manifest. Preserve the
working firmware card and settings; this benchmark does not require flashing or
stopping the radio service. Do not replace any system libraries with package files.

Copy and unpack the SDK package on a writable data partition of the diagnostic
card. Keep the GUI/waterfall active, stay in receive, and close unrelated digital
applications. Record the firmware/baseband versions and displayed waterfall
settings with your results. From the unpacked directory, run:

```sh
python3 tools/freedv/run_benchmarks.py --radio --output /mnt/freedv-smoke-01
```

A smoke suite takes approximately one minute, plus model initialization. It runs
700E and RADE TX, synchronized RX, no-sync RX and interrupted RADE RX. It produces
versioned manifests, hashes, benchmark JSON, resource samples and events locally.
If all cases execute, run the longer suite (seven 30-minute workloads plus warm-up):

```sh
python3 tools/freedv/run_benchmarks.py --radio --suite qualification --output /mnt/freedv-long-01
```

Keep the normal GUI/waterfall active throughout. Ctrl-C stops the benchmark;
it does not change radio routing or PTT. No results are uploaded automatically.
Return the complete result directory together with operator observations.
Successful execution alone does not pass the performance gate: p99 processing
must be <=70% of its represented audio interval, with stable memory and thermal
behavior. Live queues/underruns and routing remain separate incomplete gates.
Do not interpret ARM emulation or host timing as X6200 performance.

The runner reports workload execution and timing separately. `complete` and
`execution_complete` describe successful workload execution/validation, not a
timing pass. Each case has a `timing` assessment, and `codec_timing_check`
summarizes the measured checks: `FAIL` when p99 exceeds 0.70 (without rounding),
`REVIEW` for reported deadline misses when p99 is otherwise within target,
`WITHIN_LIMITS` for complete cases within the measured limits, and `INCONCLUSIVE`
for missing/invalid evidence. A short run within limits still cannot establish
30-minute stability or a full hardware PASS. Historical packages lack these
additional fields; inspect their raw p99 ratios and deadline counts directly.

For a host-validation package, omit `--radio` and use a new local result directory.
The qualification suite refuses a host-validation package.

Builders use `tools/freedv/build_benchmark_package.py --work /new/build --sdk
/path/to/relocated/sdk --sdk-manifest /baseline/buildroot-commit.txt`. A host
validation build uses `--host-validation` instead. The builder needs Git, CMake,
Make, C/C++ compilers, autoconf, automake, libtool, pkg-config and libsamplerate
headers/libraries. TLS and Opus model SHA256 verification remain enabled.
Source, compiler and model identities are recorded; generated output stays
outside Git. Debug information remains in package binaries.

Instrumented RADE builds also report `stage_timing` and
`fixture_preparation_ms`. Their package manifest includes
`rade_benchmark_timing`, and `STAGE-TIMING.md` describes the measured regions,
warm-up exclusion and instrumentation overhead. Stage sums partition the full
processing total; stage percentiles must not be added together. The full
pipeline timing gate still applies.
