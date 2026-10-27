# Experimental two-worker RADE RX benchmark

This optional executable measures whether independent RX stages can overlap on different CPU cores. It does not access radio controls, ALSA or RF. It does not change the original `rade-frame-bench`, production firmware, or the proposed 70% serial processing target.

Worker A performs input resampling and RADE RX. Worker B performs FARGAN and output resampling, with an independent output-resampler state. A two-record bounded queue contains copied features and metadata; B may also own one private record. Each worker's codec state belongs to that worker. Both serial and parallel variants process the same pinned 44.1 kHz input and retain timing instrumentation and regression hashing costs.

## Package and validation

The package builder's `--rade-pipeline` defaults OFF. Enable it only together with `--sdk`, `--rade-acq-neon`, and that experiment's existing reference-validation arguments. The manual package workflow has a matching default-OFF `rade_pipeline` input, requiring `rade_acq_neon=true`. This option adds an executable and collection helpers; it does not replace an existing executable or modify an image workflow.

All existing acquisition, upstream V1, and original benchmark gates still execute. Additional matching-SDK/QEMU gates run serial and parallel synchronized, silence and interrupted inputs unpaced, compare all functional counters and noncryptographic regression hashes, and require complete processing with joined workers and no dropped/abandoned records. The original serial benchmark also runs on those same inputs; its functional counters, represented durations and stage accounting must agree. Deterministic queue-full and worker-failure tests must return exit 3 with joined workers and consistent failure accounting. Emulation checks function only; its timings cannot establish radio capacity. SDK validation reports and source hashes are included in the package manifest/build artifact.

## Short radio collection

Use the matching SDK package and keep the normal GUI/waterfall active, without another digital application. These tests use files in memory and need no active radio channel or transmission.

```sh
cd /tmp/freedv-pipeline-benchmark
chmod +x bin/rade-pipeline-bench
python3 tools/freedv/run_pipeline_benchmarks.py --radio --output /tmp/freedv-pipeline-smoke-01
```

The helper verifies all listed package SHA256 checksums, captures inventory and resources, and runs six paced cases: serial/parallel pairs for signal (3 seconds each), silence (3 seconds each), and signal interruption (14 seconds each), all after a one-second audio warm-up. It saves the exact package manifest/checksum list. The list identifies files on disk, not observed runtime library mappings. The GUI process check cannot establish waterfall responsiveness; report that observation separately.

The runner continues collecting other cases if one returns a structured failure. A nonzero final exit means execution/equivalence needs review; return the complete output directory, including failed cases. Data stays local. There is no automatic upload, microphone capture, remote arming or PTT access.

For individual file-only experiments, `collect_benchmark.py --mode RADE-V1 --direction rx --execution serial|parallel` selects the pipeline CLI; omit `--execution` to retain the original collector behavior. Keep the original executable for an ordinary serial-reference run.

## Reading the evidence

A complete execution and matching hashes establish a limited regression check, not proof of waveform identity or a real-time qualification. The report always leaves capacity and hardware qualification **INCONCLUSIVE**. The original 70% result must still come from the original serial benchmark.

- Worker service times show time spent in A and B. Their sum is processing demand; neither sum nor one worker's percentile is full-path latency.
- Full-path timing includes A, queue residence and B for the same input block. Overlap may improve sustained throughput without reducing that latency.
- Paced input uses a fixed cumulative input clock. Producer lateness covers measured blocks; the separate maximum virtual input backlog also includes warm-up; an empty internal queue cannot hide ingress overload.
- Paced queue saturation aborts with structured failure and no silent overwrite. Unpaced throughput tests may block the producer; their waiting time is reported separately.
- Output completion intervals concern produced speech blocks only. Silence/acquisition has no decoded-speech cadence. These offline counters are not hardware audio underrun measurements.
- Initialization/fixture preparation are reported outside steady-state timing. Hashing/instrumentation costs remain in service times; nothing is subtracted.

Longer sustained tests, real microphone/speaker routing, and ordinary production buffering remain later qualification work. A short, responsive-GUI smoke run cannot settle those questions.

See [timing definitions and worker ownership](rade-pipeline-design.md) for the queue, fixed input clock, finite-window effects and failure-test details.
