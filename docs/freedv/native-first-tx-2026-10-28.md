# First live hand-mic RADE RF results

Evidence: `freedv-complete-20261028-223700.zip`, SHA256
`b60dd7d9bbd59e9b4c1d4945cf4a38dee2e8754e0e61353c399c9514aa5690c5`.
The checked archive contains 61 files, 25,202,985 expanded bytes, 14 radio cases,
seven SDR++ audio WAVs and a read-only audio topology inventory. Image source is
`2083e3e7f819e2b3fc4feaf27e5c9a007c996308`. Raw recordings remain private.

## Outcome

Valid RADE was acquired from two late SDR recordings and the pinned reference
decoder produced 5.59 and 5.95 seconds of non-silent speech. The operator reports
both are badly distorted; only the word "test" is recognizable at the end of the
second, and an independent FreeDV playback check is also mostly unintelligible.
**Transmitted intelligible speech: FAIL for this build.** Valid modem acquisition
and non-silent synthesis do not establish a successful voice mode.

The operator confirms that RF cuts off when transmissions end. The recorded
software clear/flush/fresh-RX/restoration results support functional recovery.
The numerical external 500 ms RF guarantee remains INCONCLUSIVE without correlated
external timing. No IQ recording or timing video is in this archive. The operator
case/WAV metadata templates are unfilled, so filenames are retained as evidence
labels rather than asserting associations from unsynchronized clocks.

## Three live TX cases

All used R1 M1 P0 / Hand mic / Existing mono / Keep mixer and observed packet TX.

| Case | Duration | Stop reason | Codec failures | Playback underflows | Result |
|---|---:|---|---:|---:|---|
| 12 | 0.743 s | Audio failure | 1 | 2 | Aborted and restored |
| 13 | 7.624 s | Natural completion | 0 | 58 | Completed EOO, restored; continuity not qualified |
| 14 | 7.605 s | Natural completion | 0 | 56 | Completed EOO, restored; continuity not qualified |

Cases 13/14 each encoded 59 blocks representing 7.08 seconds and delivered exactly
318,578 modem-output samples to playback, with zero unexplained output cancellation,
capture drops/holes/errors or playback write errors. They completed EOO before the
outer limit, which is why their durations are shorter than eight seconds. Numerous
underflows cannot all be dismissed as a single startup event or hidden by balanced
accepted-sample counts; the old image does not timestamp the gaps.

Commands cleared in 43/42/42 ms for cases 12/13/14. All 14 selected radio/ALSA
before/after comparisons match, with restoration complete. The earlier manual
case 5 expired instead of being manually stopped; later manual cases completed
with the intended cause. A 20-second interrupted fixture case also ran. This
bundle does not contain the ten completed mixed turns.

## Processing capacity and first-run fault

TX is a single ordered speech-feature/modem/resampling worker. The earlier
three-participant acquisition changes are RX search work, not parallel TX.

For cases 13/14, measured TX processing totals were 4.660/4.699 seconds for 7.08
seconds represented audio: about 65.8/66.4%. Slowest blocks were 88.6/96.2 ms per
120 ms interval, with no codec deadlines missed. These narrow runs show average
TX capacity but do not pass the original 70% p99-margin criterion.

Case 12 had a 264.9 ms block and one missed deadline. Input backlog peaked at
11,224 samples (about 255 ms), below the 500 ms guard. A cold/scheduled stall and
subsequent catch-up are consistent with the counters. The image records only a
generic codec failure, so its exact failing branch is not proven.

A separate controlled host reproduction supplies 300 ms of input while withholding
the sink briefly. The previous engine fails after one encoded block with 5,182
output samples queued. The corrected engine retains that same bounded queue and
waits for its consumer, with zero failures. Regression checks cover both recovery
with graceful EOO and abort/join while waiting without draining output. This fixes
a reproducible TX backpressure defect; it does not establish that all radio
startup or speech-quality problems are solved. Queue-wait counters are exported
separately; existing service timing still includes any blocking wait.

## Microphone and playback evidence

The first saved four seconds of case 13 contain essentially DC plus about one
count of AC variation; case 14 contains substantially more activity. The clips
are bounded excerpts, not the complete seven-second microphone input. Their
content/levels require listening and comparison before assigning a physical-source
or gain failure. Do not infer the operator's full speech timing from the excerpts.

The local modem WAVs from before playback/RF independently decode to 3.55 seconds
for each completed case; listening comparison against the SDR decodes is pending.
This separates codec/input defects from playback/RF impairments without another
radio test. Originals are preserved; a separately labeled microphone listening
aid removes DC and applies gain only for playback, never as codec evidence.

The topology confirms a hardware ALSA capture source rather than the PulseAudio
monitor source. No PulseAudio loopback module is listed. Those facts narrow the
RX contamination investigation but do not prove electrical independence. The
inventory was taken outside the running diagnostic stream, so its sink latency
cannot be assigned to the live TX cases.

## Next evaluation

Do not repeat the unchanged radio batch. First compare local and RF-decoded
speech, microphone excerpts and waveform continuity. Retain the current native
receive/built-in-microphone uncertainties. Profile TX stages before adding cores:
average encode capacity passed these runs, while startup backpressure, weak/missing
captured speech and playback underflows are concrete problems to resolve.
