# X6200 sustained synchronized RADE decoding — October 27, 2026

The radio sustained real-time offline RADE speech output for all 30 measured
minutes, with no lost synchronization, dropped blocks or queue overflow. This
supports proceeding to short native speaker/microphone and turnaround tests.
The original 70% processing-margin screen is not met; physical playback and RF
routing were not exercised. No additional long audio run is requested.

The operator confirmed that the normal GUI and waterfall remained active and
responsive throughout, with no other digital application running. The helper
also recorded a GUI-process check before launch; responsiveness throughout is
an operator observation, not a continuously instrumented GUI metric.

## Evidence identity and validation

- Bundle: `freedv-long-sync-results-20261027-202843.zip`.
- ZIP SHA256: `f4bbdd836d4dd4aafe38cfea2f611491769f89dc1d075f1657dd19ebb528f0d7`.
- Twenty-four files, 1,105,732 expanded bytes; Windows path separators normalized.
- Unchanged matching-SDK benchmark package:
  `b9fcea82e0858d9d34694dc0ae8d46057a398104`.
- Package checksum-list SHA256:
  `60e19c4ebe5843d4961b32fa4ef8839025242a91baced823101ad19f34410610`.
- Collector SHA256:
  `79872e8098007830c33aa425a3bd275adf0abd93dd5049092f1ab0cd2ea11923`.
- Continuous modem fixture SHA256:
  `c58f6ba9ed88c785712f4591e42b042ff71e81e376653c277ca8d9d6c7c95e37`.
- Fixture producer commit:
  `1cec1046c873d0376d4215954a44b6ff0b49ed2a`.

Both the 5.04-second smoke control and the 1,800-second measured run passed the
repository's acquisition/pipeline consistency validators and strict continuous
sync/feature/EOO checks. An additional audit passed 64 checks across recorded
identities, hashes of submitted files, pinned sources, native fixture validation,
stdout/result agreement, summaries, events, sample accounting and resources.
Package manifests/checksum lists match the preceding radio evidence byte for byte.

The actual binaries, shared libraries, model and raw waveform are not included
in this upload. Their recorded hashes cannot be independently recomputed from
it. Native fixture validation establishes the generated test input; its output
hashes are not an ARM bit-equivalence claim. No raw per-block timing trace or
speaker recording is present.

## Sustained speech result

The process ran from 20:32:21 to 21:05:05 America/Chicago on October 27
(01:32:21 to 02:05:05 UTC October 28). Its 1,963.862-second elapsed time includes
133.314 seconds preparing the large fixture, 30 seconds of warm-up, the measured
run and shutdown/reporting. Codec initialization was separately reported as
11.917 ms.

The measured population contains 15,000 blocks, each representing 120 ms.
Every measured block was synchronized and produced speech features. Output
contained exactly 28,800,000 native 16-kHz samples and 79,380,000 resampled
44.1-kHz samples: 1,800 seconds at each rate. There were no EOO frames or fixture
wraps, and no injected interruptions. The full stream, including warm-up,
completed all 15,250 submitted blocks.

| Measurement | Result |
| --- | ---: |
| Synchronized / speech-producing measured blocks | 15,000 / 15,000 |
| Mean output completion interval | 119.999 ms |
| p99 output completion interval | 132.371 ms |
| Maximum output completion interval | 152.456 ms |
| Input arrivals more than one audio interval late | 0 |
| Maximum input lateness, including warm-up | 11.731 ms |
| Queue maximum depth / capacity | 1 / 2 |
| Queue-full events / dropped / abandoned blocks | 0 / 0 / 0 |
| p99 queue residence | 0.504 ms |
| Maximum queue residence | 7.507 ms |
| Average measured DSP CPU use | 1.295 CPU cores |

The queue capacity excludes the speech worker's private in-flight record.
Measured DSP CPU use excludes the GUI, other system work and some coordination
overhead; it is not whole-device utilization. Acquisition needed four calls,
all parallel, during warm-up. The acquisition helpers did no measured steady
speech work. All helpers and workers joined at shutdown.

## Throughput, latency and the original margin

| Processing stage | Mean | p99 | Maximum |
| --- | ---: | ---: | ---: |
| Receiver/input stage | 72.760 ms | 82.534 ms | 101.546 ms |
| Speech/output stage | 87.372 ms | 100.077 ms | 128.278 ms |
| Complete path, including queue residence | 160.232 ms | 177.002 ms | 206.071 ms |

The stages overlap: each block takes about 160 ms through the path, while the
pipeline delivers successive blocks every 120 ms on average. The 15,000 reported
whole-path one-interval deadline misses describe that latency, not 15,000 lost
audio blocks. Input handling and sample accounting stayed complete. This supports
sustained throughput with bounded buffering, not an unbuffered 120-ms end-to-end
completion guarantee.

At p99, the receiver stage uses 68.78% of its represented interval, while the
speech stage uses 83.40%. Whole-path latency is 147.50% of one interval. Thus the
original 70% margin is not met. Record this separately from the positive
throughput finding, consistent with the revised proof-of-concept plan.

The longest completed-output interval exceeds the 120-ms audio interval by
32.456 ms. That establishes a need for playback buffering; it is not by itself
a sufficient buffer-size calculation. Startup, scheduling, device writes and
transition behavior must be measured in the real audio adapter. These processing
latencies also exclude intrinsic modem lookahead and physical audio buffering.

## Resources and limitations

There are 1,937 resource samples, with a maximum sampling gap of 1.043 seconds.
All sampled CPU-policy readings show 1.008 GHz and the performance governor;
the reported cooling-device state stays zero. No temperature-zone readings are
available. No frequency reduction was observed, but the summaries do not prove
absence of all thermal effects or expose a per-block timing trend.

The large retained fixture dominates memory: its converted float array occupies
about 313 MiB, with additional temporary arrays during preparation. Reported peak
RSS was 410,152 KiB (400.54 MiB), including preparation. Approximate post-warm-up
samples while all four threads were present rise from 326,784 to 331,052 KiB;
virtual size stays at 384,328 KiB. A final reporting/shutdown sample differs and
must not be treated as steady streaming memory.

The benchmark progressively touches preallocated timing records. Their allocation
is about 4 MiB for this duration and explains a substantial known contribution
to resident growth. This upload does not isolate every contribution or prove
leak freedom. The integrated audio implementation should use bounded streaming
input and should not inherit the whole-fixture loader's large memory footprint.

## Decision and remaining work

| Gate | Result for this evidence |
| --- | --- |
| Continuous synchronized offline speech processing | PASS |
| Sustained input/output throughput without lost blocks or queue overflow | PASS |
| Original 70% processing-margin screen | FAIL |
| Physical speaker continuity and simultaneous receive capture | INCONCLUSIVE — not exercised |
| Native hand-mic TX routing, PTT recovery and RF interoperability | INCONCLUSIVE — not exercised |
| Long-term leak freedom / absolute temperature | INCONCLUSIVE |

Retain the current three-participant acquisition candidate and proceed with the
[short combined validation plan](combined-validation-plan.md). Reuse this run
and the completed zero-input result for the unchanged package. Remaining codec
cases use two measured minutes, integrated speaker output uses two minutes, and
the guided mixed TX/RX exercise takes about 5-10 minutes. Finish the remaining
routing and recovery checks with brief cases in the same diagnostic delivery.

The evidence supports investing in the actual voice paths now. It does not claim
a finished FreeDV mode or RF receive interoperability, and it does not require
another 30-minute audio test. All work remains in the fork.
