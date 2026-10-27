# Timing margin, throughput and latency

Status: interpretation and proposed experimental measurements; the existing
70% result screening is unchanged. No production acceptance rule is approved
by this document.

## Meaning of the original threshold

The proposed p99 processing ratio of 0.70 is an engineering margin, not a codec
requirement or a measurement of 70% of the radio's four-core processor. For a
120 ms block it permits 84 ms of measured processing in the original serial
benchmark. It leaves time for variation and a future live capture/playback path.
The margin can be revised based on evidence; it is not a physical limit.

A serial worker with sustained average processing time above its input interval
cannot keep up indefinitely. More buffering postpones overflow but does not
remove the deficit. Occasional slow blocks can be tolerated if average service
capacity and a bounded playback buffer absorb them. Accordingly, neither one
p99 number nor an occasional deadline miss fully characterizes a live system.

The returned stage run is not a borderline 70% result: synchronized RX averages
154.921 ms per 120 ms of input (1.291008), p99 is 167.331 ms, and all 25 measured
deadlines are missed. It produces 48,000 native speech samples representing
three seconds, but spends 3.873025062 seconds in measured processing. It decodes
the fixture too slowly for the present serial streaming arrangement.

The timings are monotonic elapsed processing time, including scheduler delays.
The worker has one thread; the inventory exposes four CPUs. Neither a
responsive GUI nor these measurements prove that all four cores are saturated.

## What a concurrent benchmark should establish

The measured synchronized stage means suggest two ownership groups:

- Input resampling plus the RADE receiver: about 71.255 ms per block.
- Speech reconstruction plus output resampling: about 83.617 ms per block.

Overlapping work on different blocks could improve sustained throughput. The
same block still traverses both groups, and its completion latency includes
handoff and queue waiting. These averages do not predict tail latency, spare
capacity under contention, or a passing target result. No-sync acquisition alone
is still too slow; moving speech synthesis to another worker cannot fix it.

An experimental benchmark must preserve the original sequential mode, use the
same fixed inputs for both modes, and report separately:

- Full per-block latency from its nominal input release to output completion,
  including queueing and late producer starts.
- Processing/service time for each worker, elapsed versus thread CPU time, and
  end-to-end processing latency without relabeling it as throughput.
- Sustained input/output rate, ingress backlog, bounded queue occupancy/waits,
  dropped data, missed playback deadlines and warm-up handling.
- Bitwise feature/PCM and synchronization/EOO comparisons on identical inputs,
  interrupted streams, no-sync inputs, queue saturation and shutdown behavior.

Do not replace the original whole-block p99 ratio with the smaller per-worker
ratio and call it the same pass. A parallel implementation needs an explicit
throughput and added-latency budget alongside the existing reference results.

## Later acceptance decision

A result above the 70% goal may warrant continued evaluation if actual paced
streaming sustains input, queues remain bounded, latency stays within an agreed
budget and no audio gaps occur. The final margin should be supported by the
30-minute workloads with normal GUI/waterfall active, thermal/memory observations
and actual audio-path behavior. It must account for audio/radio work that these
in-memory benchmarks do not yet perform.

The current result remains codec timing FAIL and hardware feasibility
INCONCLUSIVE. The next experiment is intended to measure available multicore
capacity, not to obtain a pass by changing a threshold.
