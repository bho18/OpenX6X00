# Experimental offline RADE RX pipeline

This is an optional offline benchmark, disabled in ordinary packages. It does
not change firmware or the existing 70% benchmark assessment. No radio-control,
RF, physical audio or network APIs are used. The separate executable requires
an explicit `serial` or `parallel` mode. See [package and collection instructions](rade-pipeline-benchmark.md)
for the matching-SDK build and mandatory correctness gates.

## Question and expected capacity

The radio's recent synchronized RX averages for a 120 ms block were:

- input resampling + RADE: 6.982 + 64.273 = 71.255 ms;
- FARGAN + output resampling: 74.489 + 9.128 = 83.617 ms;
- existing serial full block: 154.921 ms, p99 167.331 ms, 25/25 misses.

Two workers could overlap different blocks. Ideal steady completion spacing is
limited by the slower worker, about 83.6 ms under those *serially measured*
averages. Each block still needs both workers: its latency remains roughly the
sum plus queue/scheduling delay. Concurrent memory/cache/scheduler behavior can
change both services; this estimate is not a radio result. B's average is only
0.383 ms below an 84 ms (70% of 120 ms) service budget, and means say nothing about
p99. This experiment cannot convert the existing serial gate into a pass.

Sustained completion at the offered rate, finite latency/backlog, and worker
service budgets must be evaluated separately from the original per-block gate.
Changing an accepted product margin or buffering/latency budget is a separate
recorded decision. UI responsiveness alone does not demonstrate that speech can
keep up. Physical audio underruns and radio routing remain unmeasured.

## Ownership and queue

Both variants use the same `run_a()` and `run_b()` functions. Model initialization
happens before creating the worker; destruction happens after joining it.

- A (main thread): input SRC, deterministic fixture cursor/muting, IQ, all RADE
  state/API calls, sync/EOO, and input sequence accounting.
- B: independent FARGAN state, its five-frame continuation, output SRC, output
  buffers, result hashes and completed-frame counters.
- The FIFO holds two waiting metadata/feature records. B copies a dequeued record
  to private storage before releasing its slot. Therefore at most two records
  are waiting, one is being processed by B, and one can be under construction by
  A. Payloads never point into A's changing feature buffer after publication.
- No state is reset at the warm-up boundary or after loss/reacquisition. Every
  block, including zero-feature/no-sync blocks, traverses the FIFO in order.
- Streaming buffers and measured-record statistics are allocated before workers
  start. No file writes occur in the processing loop. Statistics keep metadata,
  not full feature payloads. The fixture remains preloaded as in the reference.

Paced mode advances a CLOCK_MONOTONIC release timeline by each actual `rade_nin`
interval (100/120/140 ms). Release represents the last sample becoming available.
Delays never reset the timeline. A full feature queue fails the run, then drains
accepted records and joins B. `queue.full_events` counts observations of a full
queue (including rechecks after wakes), not distinct saturation episodes or
dropped records. Unpaced mode may wait for a slot to measure bounded
pipeline throughput; its capacity waiting is reported and excluded from A's
service time, while full-path time still includes it. Unpaced completion alone
is never a real-time pass.

The source fixture is virtual: no physical input FIFO capacity is claimed.
Ingress lateness and more-than-one-input-interval backlog are reported rather
than concealed by delaying the source clock. There is no simulated or physical
speaker drain, so output-completion intervals are not speaker-underrun counts.
Output pauses across loss of sync are preserved, not filled with synthetic PCM.

## Timing interpretation

All distributions cover measured completed records, excluding records tagged as
warm-up. Warm records may still be queued when the first measured record starts;
there is no boundary flush or codec reset. Full counters/hashes also include warm
records, enabling independent comparison of startup and steady-state work.

- `a_service_ms`: A start through feature publication, less unpaced capacity wait.
- `b_service_ms`: B begins copying the queued record through finished output.
- `full_path_ms`: A start through B finish, including producer/queue wait.
- `nominal_release_to_completion_ms`: fixed input release through B finish,
  including input backlog; available only in paced mode.
- `queue_residence_ms`: publication through B dequeue/copy start.
- `a_service_input_ratio`: A service divided by that block's actual input interval.
- `b_service_feature_ratio`: B service divided by feature-represented speech time;
  only feature-bearing records enter this distribution.
- `full_path_input_ratio`: full path divided by the input interval. This is
  intentionally not replaced by the maximum of the two worker service times.
- `summed_service_ns`: A plus B service across records. Concurrent service can
  exceed measured wall duration. It is neither latency nor elapsed throughput.
- `wall_ns_including_drain`: first measured A start through final measured B
  completion; it excludes collection of the first measured input block and
  retains finite-window/pipeline fill/drain effects. Even paced 3 s can appear
  above 44,100 samples/s solely from those window edges; this is not stable
  throughput evidence. Completion periods plus the fixed-clock backlog are
  stronger evidence.
- `offline_output_samples_per_wall_second`: completed measured PCM / that wall
  duration. Do not interpret it as real-time capacity for no-sync/interrupted
  input, which intentionally produces fewer speech samples.
- `output_completion_period_ms`: spacing between records producing PCM. This
  includes intentional no-sync gaps; it is not a device callback deadline.
- One-input-interval completion misses are diagnostic latency-budget counts.
  No configurable playout buffer is modeled, and these are not hardware
  underruns or a pass/fail test for an agreed added-audio-latency budget.

Thread CPU-time totals cover DSP worker functions; wall service includes queue
publication/copy and scheduling around them. Common closing statistics are
updated after the per-record completion timestamp, as in the prior benchmark;
inter-record overhead remains visible in wall throughput. Input, RADE, FARGAN
and output stage distributions are also reported. Percentiles are never added.
Checksum computation is always enabled here and its cost is included in service.

## Invocation and checks

```sh
cd /tmp/freedv-pipeline-benchmark
./bin/rade-pipeline-bench parallel fixtures/rade-modem.raw 3 paced 1 continuous
```

`serial` selects the same DSP functions on one thread. The original CLI's 3 s
measurement + 1 s warm-up and 14 s interrupted input semantics are preserved.
Warm-up advances in whole input intervals, exactly as the existing benchmark.
The interruption remains two seconds muted every twelve input seconds.

The deterministic `--test-hold-b-until-full` hook prevents B dequeue until A
encounters a full queue. It must return failure, drain accepted records and join.
`--test-delay-a-ms N` adds an explicitly labeled A wall-time delay for testing
that source backlog remains visible. A 250 ms delay exceeds the longest 140 ms
input interval, so even completed runs must report growing ingress lateness.
`--test-fail-b-after N` injects a B failure after N consumed records; queued records
are accounted as abandoned and A is awakened. The two B hooks require parallel
mode; the A delay works in either execution mode. Every injected run sets
`test_hooks_used=true`.
SIGINT/SIGTERM set a compile-time-verified lock-free atomic stop flag. Queue
waits poll it at most every 100 ms; ordinary paced source sleeps are at most one
input interval. A DSP call already in progress is not interrupted, and accepted
records may still drain, so no hard shutdown-latency bound is claimed. Unexpected impossible
codec/resampler/programming errors still terminate with stderr via the inherited
fatal helper; those are not represented as successful or complete results.

Full and measured result dictionaries contain FNV-1a-64 regression hashes of the
actual RADE feature bytes, native floating speech bytes and resampled int16 PCM,
plus input sequence/sync/EOO metadata. These noncryptographic hashes are useful
same-build differential evidence, not a proof for every possible input. The
serial/parallel DSP functions and arithmetic are identical. No numeric tolerance
is silently substituted if a hash differs.

The host build uses the existing pinned RADE/Opus/libsamplerate reference build,
with the generated Opus config force-included and no fast-math/CPU-policy changes.
A matching-SDK build, target thread/CPU measurements, long paced runs under the
normal GUI, fixed waveform identity, and review of any added buffer-latency
budget are still needed before any radio capacity conclusion.

## Recorded host validation

Final source SHA256:
`824d5301b41d1c4dd2665fe680ebe8abfe3f721665af58f98d877bee7f3e96c4`.
The package manifest records the source, toolchain, model and library identities
for each SDK build. This source hash identifies the original host validation.

```sh
python3 tools/freedv/test_rade_pipeline.py \
  --binary /path/to/host/rade-pipeline-bench \
  --fixtures /path/to/verified/fixtures \
  --original-stage-binary /path/to/host/rade-frame-bench \
  --output /tmp/rade-pipeline-host-results
```

All **18 initial host executions passed**. Each rerun needs a new output directory. Four workloads (synchronized 3 s + 1 s warm-up, silence,
14 s interruption/reacquisition and zero warm-up) have matching serial/parallel
full and measured counters and regression hashes. Refactored serial functional
counters match the original stage executable on the same pinned waveform.

Deterministic full queues during measured/warm-up windows and B failures after
zero/one records produce incomplete results, conserved work accounting and
joined workers. Injecting 250 ms A delay produces nine diagnostic deadline misses
and visible growing input backlog despite finishing all offline work. A readiness-
anchored SIGTERM check produces structured startup cancellation with joined
workers; it does not test interruption inside a DSP call. Tests assert no host
performance targets and retain `full_pipeline_qualified=false` throughout.
