# X6200 paired RADE RX pipeline result — 2026-10-27

The two-worker receiver kept pace with the short synchronized fixture while
preserving the serial receiver's functional counters and regression hashes.
No-sync acquisition still exceeded its input interval. This supports continued
multicore development; it does not qualify sustained real-time audio or change
the original 70% acceptance margin.

The operator confirmed the normal GUI and waterfall stayed active and responsive,
with no other digital application running. This is an operator observation;
the helper separately checked that the GUI process existed. No physical audio,
RF transmission, microphone routing or controlled RF reception was tested.

## Evidence and reproducibility

- Uploaded result: `freedv-pipeline-results-20261027-135659.zip`, 52,030 bytes,
  SHA256 `17e1aec45a4e2ad846d88287fbd589b416faf839cef0fe5256f268dcabad56ff`.
- Six cases recorded on the radio at approximately 1:58–1:59 p.m. America/Chicago,
  October 27. These are the radio's reported timestamps, not independently
  calibrated clock measurements.
- Package source: `88db96a0b80b5ccbd36eb84f6b2a6f2364ca12dd`, matching Buildroot SDK.
  [Successful SDK build](https://github.com/bho18/OpenX6X00/actions/runs/40555263608).
- Pipeline source SHA256:
  `824d5301b41d1c4dd2665fe680ebe8abfe3f721665af58f98d877bee7f3e96c4`.
- Frozen modem fixture SHA256:
  `f4b0ce17951109d33abbf72f865bdec9e718044e869cde3ffdbdcf3191592d21`.
  Serial and parallel use identical bytes. This is a new frozen fixture relative
  to the previous stage package, so cross-package signal timings do not isolate
  a single change. The within-package comparison is the relevant experiment.
- Returned manifest, checksum-list cross-references, source/tool/fixture hashes,
  embedded SDK records, collector arguments, exit events, stdout/result equality,
  counter conservation and pair comparisons pass 127 independent audit checks.
- The uploaded bundle contains results and copied package metadata, not the
  executable/library/model payloads or runtime library mappings. Hash consistency
  is verified where data is available; it is not independent attestation of the
  loaded binaries. The Actions artifact ZIP was not downloaded during this audit.

Both full-stream and measured dictionaries match in each serial/parallel pair,
including feature/native-float/PCM/metadata FNV-1a-64 regression hashes. These
finite noncryptographic comparisons support equivalence for the tested inputs;
they are not a proof for arbitrary input or an independent raw-PCM comparison.
All six cases completed with joined workers and no discarded or abandoned blocks.

## Synchronized reception

Each case measured 25 blocks representing 3.00 seconds of audio, following
1.08 seconds of audio warm-up. Each block represents 120 ms. Both executions
produced 48,000 native speech samples and 132,300 resampled PCM samples.

| Measurement | Serial | Parallel |
|---|---:|---:|
| Mean output completion spacing | 149.727 ms | 117.167 ms |
| Maximum output completion spacing | 157.299 ms | 145.142 ms |
| Maximum measured input lateness | 950.692 ms | 3.097 ms |
| Measured inputs more than one interval late | 25/25 | 0/25 |
| A service mean / p99 | 69.187 / 71.408 ms | 81.185 / 103.412 ms |
| B service mean / p99 | 80.569 / 88.630 ms | 90.983 / 142.323 ms |
| Full-path latency mean / p99 | 149.757 / 157.273 ms | 172.567 / 226.227 ms |
| Nominal input release to completion, maximum | 1096.789 ms | 226.391 ms |
| Maximum queued waiting records | 0 | 1 |
| Queue-full events / discarded blocks | 0 / 0 | 0 / 0 |

The parallel case accepted measured input on time and produced matching speech
blocks at approximately the offered rate. Its 117 ms average output spacing
must not be interpreted as spare capacity or a 2.4% speed surplus: there are
only 24 completion intervals, and finite-window/initial/final latency effects
influence that average. Likewise, 3.038 seconds of wall time for three seconds
of audio includes pipeline drain and is not a sustained overload result.

Each block still passes through A then B. Concurrent workers took longer per
stage than in the serial run, and full-path latency increased. The observed
B maximum of 142 ms and output gap of 145 ms require further jitter/buffering
analysis; they are not measured speaker underruns. All 25 blocks exceeded the
experimental one-input-interval full-path latency reference, even though input
kept pace. Throughput and latency must be assessed separately.

The parallel warm-up-inclusive maximum input backlog was 100.521 ms; the measured
maximum was only 3.097 ms. Thus the result does not establish zero startup backlog.
The short p99 populations are too small to characterize sustained tail behavior.

## Acquisition and interruption

| Measurement | Serial silence | Parallel silence | Serial interrupted | Parallel interrupted |
|---|---:|---:|---:|---:|
| Represented measured audio | 3.00 s | 3.00 s | 14.04 s | 14.04 s |
| A service mean | 143.371 ms | 143.899 ms | 82.282 ms | 84.475 ms |
| A service p99 | 145.505 ms | 145.838 ms | 180.881 ms | 180.699 ms |
| Maximum measured input lateness | 766.601 ms | 786.910 ms | 3478.880 ms | 570.946 ms |
| Inputs more than one interval late | 25/25 | 25/25 | 117/117 | 27/117 |
| Maximum queued waiting records | 0 | 1 | 0 | 2 |
| Queue-full events / discarded blocks | 0 / 0 | 0 / 0 | 0 / 0 | 0 / 0 |

Without synchronization, B has no speech to synthesize. A alone still needs
about 144 ms for each 120 ms of input, so it accumulates virtual input backlog.
The internal feature queue being nearly empty does not imply that incoming
samples can be serviced in real time. Literal-zero silence remains a diagnostic
fixture, not a characterization of off-air noise, interference or weak-signal
acquisition.

The interrupted pair muted 16,000 modem samples and reacquired, with identical
97 synchronized blocks, 96 feature blocks and 23 synchronized blocks after the
first interruption in each measured result. Parallel nominal-release-to-output
latency reached 731.840 ms and queue residence reached 146.077 ms. A two-record
queue was reached but no producer encountered it full. This is recovery evidence,
not a queue-headroom guarantee. There is no per-block trace to establish the
precise backlog trajectory or when recovery to nominal timing occurred.

The approximately 3.21-second maximum output-completion gap includes intentional
muting and reacquisition; treating it as a continuous decoded-speech underrun
would be incorrect. The 14.077-second parallel wall window for 14.04 seconds of
input does not erase the observed 571 ms temporary input backlog.

## Resources and qualification

The 59 resource samples show one thread for serial and up to two for parallel,
1.008 GHz CPU frequency, cooling state 0, and no available thermal-zone samples.
Reported peak RSS is about 12.6–12.8 MiB. These short observations cannot establish
thermal stability or absence of progressive memory growth.

Synchronized measured DSP thread CPU time totals 3.716 seconds serial and
3.990 seconds parallel. Against their respective wall windows this corresponds
to approximately 0.99 and 1.31 CPU cores. The CPU timers cover DSP worker bodies;
they omit some queue/bookkeeping overhead and other radio processes. This is
evidence of overlapping work, not total four-core utilization or measured spare
capacity for the entire application.

| Gate | Result |
|---|---|
| Six-case execution and serial/parallel functional regression | PASS for tested fixtures |
| Short synchronized input pacing | Favorable evidence; sustained qualification INCONCLUSIVE |
| Current no-sync input service capacity | FAIL in both executions |
| Interruption recovery | Functional PASS; timing/backlog limitation remains |
| Original serial 70% assessment | Unchanged; this experiment does not replace it |
| Long-duration CPU/thermal/audio qualification | INCONCLUSIVE |
| Native microphone/speaker routing and RF interoperability | INCONCLUSIVE / not tested |

Next, validate the separate acquisition-overlap reuse candidate with the matching
SDK, retaining the original detector as a reference and preserving weak-signal
search behavior. Keep the pipeline executable, models and frozen waveform
unchanged for the next radio comparison. Record the extra library patch explicitly
and require the existing V1 and new streaming differential gates before packaging.
Long paced runs and actual audio-path tests remain necessary after the known
acquisition bottleneck is addressed. No threshold adjustment is made here.
