# Optional parallel acquisition benchmark

The existing two-stage receiver overlaps RADE input processing with FARGAN speech
synthesis, but acquisition itself still occupied one worker. This optional
experiment partitions independent acquisition correlation cells among persistent
workers while preserving each cell's arithmetic and the original ordered
maximum/threshold reduction. It uses the guarded NEON baseline; acquisition
reuse is deliberately absent from this comparison.

The option defaults OFF. It does not implement a production voice mode or access
radio controls, ALSA, microphone, speaker or RF. The original 70% serial target
is unchanged; sustained and hardware qualification remain separate.

## Worker counts and lifecycle

`--acquisition-workers N` selects 1, 2, 3 or 4 total acquisition participants,
including the calling RADE worker. N adds N-1 helper threads. The separate
FARGAN/output stage also has a thread: N=4 can have five codec threads, with the
GUI and other application work running separately. This does not reserve CPU
cores or assert that four workers use the whole processor efficiently.

The persistent pool is configured before reception; creation is included in
initialization time. Unsupported numeric/control conditions retain the serial
reference path and are counted. A failed pool configuration fails the benchmark
instead of silently accepting fewer workers. Final helper counters are read
after processing, and the pool is closed before output identifies its helper
threads as joined.

Acquisition helper jobs, CPU totals and dispatch waits cover the complete stream,
including warm-up. Existing `a_cpu_ns` is **measured caller-thread CPU only** and
excludes helpers. Per-record helper CPU deltas are separately summed over the
same completed measured population as `acquisition_helper_cpu_ns` and included
with A/B in `total_dsp_cpu_ns`. This sum covers measured A/B intervals and helper
partition computation; it excludes helper dispatch/locking overhead outside those
timers and is not whole-process CPU utilization. Do not mix it with the
warm-up-inclusive pool counters or call lower caller CPU a reduction in total
decoder CPU. `rade_rx_ms` and A service wall time include the blocking acquisition
operation and scheduling.

## Reproducible package

`build_benchmark_package.py --rade-acq-parallel` requires the matching Buildroot
SDK, `--rade-acq-neon` and `--rade-pipeline`, with their existing validation
arguments. The manual workflow has a corresponding default-OFF
`rade_acq_parallel` input. The artifact is
`x6200-freedv-acquisition-parallel-package`; its runtime directory remains
`freedv-benchmark`.

The patch is checksum-pinned against the guarded source and expected resulting
files. The guarded full-search translation unit stays unchanged. Model, SDK and
frozen waveform pins are retained. The pipeline source adds guarded selection
and statistics; every N uses the same executable/fixture, making N=1 the direct
control. Earlier packages are context rather than an identical-executable test.

Existing 47-case acquisition, upstream V1 speech/text/loss, original benchmark
and pipeline gates still execute. Additional SDK gates exercise the same 47
detector cases and 247 receiver calls across 19 stream cases for each worker
count, compare all receiver states and decisions against the serial reference,
and check 130 lifecycle assertions across 11 cases plus 14 disabled-API checks.
Twelve full pipelines compare counters and feature/PCM/metadata hashes with the
matching executable at one worker. Missing workloads or failed equivalence block
the archive. QEMU timings are not radio performance.

## Radio characterization

Extract into a separate runtime directory and preserve prior results. Leave the
GUI/waterfall active and close unrelated digital applications. These file-only
workloads need no active channel, microphone or RF transmission.

```sh
cd /tmp/freedv-acquisition-parallel
chmod +x bin/rade-pipeline-bench
python3 tools/freedv/run_acquisition_benchmarks.py --radio --output /tmp/freedv-acquisition-workers-01
```

The helper verifies package files and runs 12 paced cases: synchronized signal
(10 seconds), silence (10 seconds), and interrupted signal (26 seconds), each
with 1, 2, 3 and 4 acquisition workers. Every case has three seconds of input
warm-up, rounded to whole blocks. Expect several minutes for the sweep. The
interrupted case has two muted intervals, with recovery counters after each
completed interval that has sufficient subsequent input.

Counts run in ascending order per workload. This first sweep is not randomized
and cannot isolate thermal/order effects by itself. Return the complete output,
including failures. Collection continues after failures, never lowers requested
counts, and leaves capacity/hardware qualification INCONCLUSIVE. Nothing is
uploaded automatically; `/tmp` disappears on reboot.

For one case:

```sh
./bin/rade-pipeline-bench parallel fixtures/rade-modem.raw 10 paced 3 continuous --acquisition-workers 2
```

Assess fixed-clock input lateness, queues, output cadence, full-path latency and
GUI responsiveness together. Complete execution or an empty feature queue does
not prove incoming samples can keep up. These statistics do not simulate speaker
buffering or measure hardware underruns. Short runs and small p99 populations
cannot replace the 30-minute tests, native audio routing, PTT or RF qualification.
Choose worker count from measured behavior rather than assuming the largest wins.

For the manual build, Windows transfer and result collection steps, use the
[radio test guide](rade-acquisition-parallel-radio-test.md).
