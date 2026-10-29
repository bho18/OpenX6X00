# Acquisition worker scaling experiment

Status: experimental, not a production mode or a revised acceptance threshold.

The October 27 paired pipeline test showed that synchronized input could keep
pace over a short window, while acquisition still needed about 144 ms for each
120 ms of input. Moving speech synthesis to another worker does not distribute
the acquisition grid itself. This experiment measures that missing parallelism.

## Isolated comparison

Use the same guarded-NEON library baseline, frozen modem waveform, models,
resampler and matching SDK as the prior pipeline package. Add selectable total
acquisition participants of 1, 2, 3 and 4. The calling receiver thread counts as
one participant; persistent helpers account for the rest. The separate speech
worker is additional. Four acquisition participants therefore do not mean the
whole application has only four threads or that cores are dedicated to them.

Calculation reuse remains a separate, unfinished candidate. Exclude it from this
package so that the first scaling measurement isolates worker count. Combining
optimizations later requires its own source manifest and regression evidence.

The parallel kernel must retain each correlation cell's arithmetic order and
the original ordered maxima/threshold reductions. Supported numeric input uses
disjoint grid partitions. Unsupported numeric or floating-point conditions use
the original serial detector. A request for more workers must not conceal a
pool-creation failure by reporting that the requested count is running.

Pool setup and teardown happen outside steady-state processing. No threads or
streaming buffers are created for each received block. Helpers sleep between
jobs; all references to caller input and output grids remain valid until the
job has completed. The experiment has no physical audio or radio-control access.

## Evidence required

Before producing a radio package, retain the existing full V1, traditional
FreeDV and two-stage pipeline checks and add acquisition equivalence checks for
every supported worker count. Exercise actual parallel jobs, scalar fallback,
weak/noisy/offset candidates, interruption/reacquisition, instance isolation,
configuration rejection and lifecycle failures. Worker-count selection alone
is not proof that the parallel path executed. Emulation validates function,
not device throughput.

On the radio, run the same file-only signal, silence and interrupted workloads
for all four counts with the normal GUI/waterfall active and no other digital
application. Preserve source/model/fixture identities, the requested/effective
worker count, actual parallel/fallback job counts, and collection failures.

Compare:

- Receiver wall service, input lateness/backlog and full-path completion latency.
- Caller, acquisition-helper and speech-worker CPU demand, with warm-up scopes
  stated explicitly. The calling thread's CPU timer alone omits helper work.
- Decoded feature/PCM regression hashes and synchronization counters across counts.
- Queue occupancy, saturation/discards and output timing. An empty feature queue
  cannot disprove an incoming-sample backlog.
- GUI observations, available frequency/thermal data and memory use. These are
  not guarantees of thermal stability or physical speaker-underrun behavior.

Select the next sustained-test candidate from observed throughput and latency,
not the largest requested count. Shared memory/cache traffic, coordination and
other radio work can make scaling nonlinear. Keep the one-worker comparison in
the returned bundle and retain failed cases.

## Qualification boundary

A short successful sweep supports choosing a configuration for longer tests.
It does not pass the 30-minute workload requirement, native microphone/speaker
routing, PTT recovery or RF interoperability. The existing original serial 70%
assessment stays recorded separately. A concurrent implementation needs an
explicit sustained-throughput and buffering/latency assessment; per-worker
percentiles cannot silently replace the original whole-block measure.

No inference of whole-chip spare capacity follows from a responsive GUI alone.
The radio result, not host or QEMU timing, determines whether additional workers
resolve the acquisition bottleneck.
