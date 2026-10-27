# Acquisition cost and signal gating: follow-up options

The matched radio smoke results put original no-sync processing at 1.505031
seconds per second of input and synchronized reception at 1.252197. With the
acquisition candidate, those values are 1.199747 and 1.260680 respectively.
Both receive paths still exceed real time, and neither meets the agreed p99
limit of 0.70. Reducing search cost alone does not establish a working complete
receiver.

## Existing gating

In pinned `rade_c` revision `c8a3dc156045cae2cd251e1a4be0c304c9ddf2f9`,
`rade_rx.c` runs pilot acquisition in SEARCH/CANDIDATE and neural decoding in
SYNC. The benchmark runs FARGAN only when the API returns speech features.
The initial known-pilot search covers 960 timing positions and 40 frequency
offsets, with two 160-sample correlations per cell: 12,288,000 complex
accumulation steps per 120 ms input block.

After synchronization, the receiver narrows timing/frequency tracking and
updates only 5% of the coarse grid for noise estimation. It also begins neural
and speech processing. This explains why optimizing acquisition can change
which workload costs more. Synchronization is deliberately retained across
brief fades; decoding can continue during that hold period.

There is no separate cheap packet header available before synchronization in
this implementation. The known repeated pilots are the initial recognizable
pattern. Unique-word evidence comes from neural decoding.

## What a preliminary detector could establish

An energy or squelch threshold is cheap, but cannot reliably distinguish absent
signals from weak decodable signals under AGC, receiver noise and interference.
A sliding autocorrelation of the repeated pilots, or a coarse-to-fine search,
could be evaluated as an advisory detector before allowing it to suppress full
searches. FFT correlation is another possible implementation of the full search;
it changes floating-point arithmetic and needs numerical validation.

The current `silence.raw` is literal zero PCM. It exercises repeated acquisition
but is not a recording of an idle RF channel. An exact-zero shortcut could improve
this fixture while doing little for real receiver noise. Add deterministic
nonzero noise and weak-signal/channel fixtures before evaluating any rejection
gate. Required comparisons include acquisition probability/delay, false locks,
missed short transmissions, reacquisition, feature/audio loss and processing
cost across offsets, fading, interference and sample-clock errors.

## Reuse before rejection

A source-derived opportunity is to reuse the previous full search's second
pilot-correlation grid as the next search's first grid. With a receive-buffer
shift of exactly 960 samples, the sample windows are identical:

`current Dt1[t][f] = previous Dt2[t][f]`

This could avoid computing one grid while retaining every searched position,
the existing threshold and the original reduction order. It requires explicit
caller-owned validity: consecutive complete searches, unchanged pilot/config
and floating-point behavior, and the expected sample shift. Reset, synchronized
partial-grid updates, other shifts and configuration changes must invalidate it.
The public standalone detector must retain its full-search behavior unless the
caller establishes this contract. No speedup or correctness is claimed yet.

Stage timing remains the next measurement for the complete receive path. Any
later parallel pipeline must report full per-block latency and queue behavior;
improved throughput alone does not replace the agreed p99 acceptance rule.
