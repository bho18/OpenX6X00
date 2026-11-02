# X6200 short remaining-codec validation — October 27, 2026

All five selected file-based workloads completed their two measured minutes
and met the original p99 processing-margin screen, with no reported deadline
misses. The operator confirmed that the normal GUI and waterfall remained active
and responsive throughout, with no other digital application running. These
remaining short codec cases are complete for the tested package; proceed to
native audio integration without another routine benchmark batch.

## Evidence and consistency

- Bundle: `freedv-short-results-20261027-213636.zip`.
- ZIP SHA256: `d57cc6205ee06d1a398814508d43b7aa79081737f934e83558d30bcd1d87927b`.
- Thirty-six files, 411,279 expanded bytes; Windows ZIP paths normalized.
- Matching-SDK benchmark package:
  `b9fcea82e0858d9d34694dc0ae8d46057a398104`.
- Package checksum-list SHA256:
  `60e19c4ebe5843d4961b32fa4ef8839025242a91baced823101ad19f34410610`.
- Corrected standalone runner SHA256:
  `a94e567ba6d7f3cd9e633f74c4931ebf42a0f47195d995fde8a82b9ea27d5bda`.
- Noise payload SHA256:
  `2218159f25b72c1b6986b3fe56c7806c5437e1884b1971728a274678168a4af9`.

Sixty-eight checks passed across the submitted records: package and source
identities, actual included noise bytes, fixture/executable hash agreement,
stdout/result agreement, requested durations, execution status, timing summaries,
acquisition validation, event durations and resource sequences. Binaries, shared
libraries, models and the packaged speech/modem fixtures are not included; their
recorded hashes cannot be independently recomputed from this upload.

The batch ran from approximately 21:36:44 to 21:47:15 America/Chicago on October
27 (02:36:44 to 02:47:15 UTC October 28). Each case excluded about five seconds
of warm-up, then measured exactly 120 seconds. Cases were sequential.

## Measurements

| Workload | Measured blocks | p99 processing / audio interval | Maximum ratio | Deadline misses |
| --- | ---: | ---: | ---: | ---: |
| RADE V1 TX, speech processing and resampling | 1,000 | 65.53% | 70.92% | 0 |
| 700E TX and resampling | 1,500 | 24.68% | 25.82% | 0 |
| 700E synchronized RX and resampling | 1,500 | 21.94% | 24.12% | 0 |
| 700E no-sync RX and resampling | 1,500 | 32.19% | 42.46% | 0 |
| RADE synthetic-noise RX, three acquisition participants | 1,000 | 61.66% | 67.70% | 0 |

The 70% screen applies to p99, so RADE TX's maximum above 70% does not fail this
screen. Its p99 block time was 78.635 ms for a 120-ms interval; maximum was
85.105 ms. The RADE noise complete-path p99 was 73.988 ms, maximum 81.235 ms,
also for 120-ms intervals.

RADE TX and the two 700E TX/synchronized-RX cases each generated 120 seconds of
native modem/speech samples and 5,292,000 resampled 44.1-kHz samples. Every one
of the 1,500 measured 700E synchronized-RX blocks reported sync. The 700E silence
case reported no sync; its API still returned output sample buffers. Those
buffers were not captured, so this is not evidence of their audible content or
production squelch behavior.

The RADE noise case reported no sync, features or speech output. All 1,042
acquisition calls, including warm-up, used the parallel path; both helpers
completed 1,042 jobs and joined at shutdown. Queue maximum depth was one of two,
with no full events, drops or abandoned blocks. There were no full-path or
nominal one-interval completion misses, and maximum input lateness was 2.288 ms.
Measured DSP CPU work averaged about 1.167 cores, excluding GUI/system work and
some coordination overhead.

Noise was deterministic filtered synthetic PCM, not off-air input or calibrated
RF. The four frame-benchmark cases report processing/deadline statistics but do
not instrument physical audio queues or speaker underruns. Zero drops are
therefore claimed only for the instrumented RADE noise pipeline.

## Resources and decision

All 625 resource observations report 1.008 GHz, the performance governor and
cooling state zero. No temperature-zone readings were available. These short
runs do not qualify absolute temperature, long-term memory stability or every
possible acquisition input. GUI responsiveness is the operator's observation;
the helper checks process presence between cases.

The five short codec execution/timing gates pass under the stated conditions.
Together with the earlier zero-input acquisition and sustained synchronized
speech reports, this is sufficient computational evidence to proceed with
native microphone/speaker diagnostics. The earlier synchronized RADE speech
pipeline still exceeds the original 70% margin despite sustaining throughput;
these new passes do not erase that separate finding.

Remaining work is the complete radio-owned snapshot/restoration and exclusive
session backend, integration of the codec with bounded capture/playback, guarded
local controls, a matching-SDK diagnostic image, then the short speaker, routing,
PTT/recovery and mixed-turnaround session. Physical audio and controlled RF
receive interoperability remain INCONCLUSIVE. No repeat of these unchanged
file-based cases is requested. All changes and reports remain in the fork.
