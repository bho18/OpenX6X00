# FreeDV feasibility milestone

The objective is to determine whether an X6200 can simultaneously capture the
microphone while feeding a generated signal to RF, and capture received audio
while playing decoded speech on its speaker. It also requires measuring 700E
and RADE V1 on the actual radio with the GUI and waterfall active.

When the diagnostic option is enabled, Settings → Info contains a read-only
snapshot of cached RECORD/MODEM/PTT commands and the last radio state. It is
not independent hardware readback and does not provide or authorize routing
experiments. The required guarded diagnostics, matched Buildroot SDK, image and
on-radio measurements remain to be implemented and verified. In particular,
the X6200's generic `hw_get_i()` does not return a valid RECORD/MODEM/PTT
snapshot; the read-only view uses a separate locked cached-command accessor.

## Evidence and scope

The reference GUI defaults to RADE V1; 700E is a traditional waveform useful for
streaming bring-up. The Codec2 FreeDV API provides the speech and modem stages.
RADE additionally needs the pinned Opus/FARGAN feature extractor and synthesizer.
These are different over-air waveforms; one does not validate the other.

X6200Control describes RECORD as switching the audio stream between mic/speaker
and RX/TX audio. RECORD and MODEM are separate bits, but their combined behavior
is unknown. The existing X6200 packet stream contains magnitude-only spectrum
bins, not IQ samples suitable for a substitute SSB receiver. The upstream manual
states that the built-in microphone is off in receive; this does not establish
its behavior during keyed digital transmission. Hand-mic PTT can key the baseband
directly, so a held physical PTT may not be overridden by the GUI.

Start by testing the hand mic and speaker, followed by the built-in microphone.
Native routing, hardware PTT and RADE timing are independent gates. Do not infer
radio behavior from host or QEMU codec tests.

## Required hardware experiments

With a spare microSD and the actual baseband version, inventory the software,
audio endpoints, mixer settings and CPU. Use the dummy load and a receive-only
SDR for keyed experiments, starting at 1 W requested power and limiting
software-controlled transmissions to 10 seconds. Use GUI/front-panel PTT before
testing hand-mic PTT. Selecting a case must never key RF.

Measure the RECORD × MODEM × software-PTT matrix, with distinguishable microphone,
received RF and generated playback signals. Record commanded state, hardware TX
status, Linux capture, speaker observations and SDR recordings independently.
Establish whether mic capture survives generated RF playback, and whether received
audio remains available while local speech is played. Inspect left/right capture
channels and supported mixer alternatives before ruling out a native route.

Abort/timeout must stop software-generated audio and keying. Wait for actual RX
before restoring analog routes. If a physical PTT remains held, the operator must
release it; a software stop cannot be assumed to override the baseband. Measure
initial/final analog leakage and supervisor-restart behavior separately.

The SDR can monitor the radio's transmission but cannot produce the controlled
RF input needed for full receive interoperability. Off-air routing observations
and software playback do not establish that test.

## Codec measurement

`tools/freedv/reference_benchmark.py` runs pinned upstream whole-file transmitter
and receiver tools and writes elapsed-time results. It is useful for validating
the reference pipeline but does not implement the planned paced streaming
benchmark, report audio-block deadlines, or measure target-radio performance.
It never controls radio hardware.

The separate `codec2_frame_bench.c` prototype exercises real 700E codec frames
with paced or unpaced input and reports per-frame timing ratios. An optional
host-tested path includes stateful 44.1/8 kHz resampling and warm-up exclusion;
the original codec-only path was also exercised on ARM/QEMU. The prototype does
not measure the live audio path or RADE and cannot establish the radio gate.

The required target benchmark must measure RADE V1 and 700E TX, synced RX and
acquisition/no-sync on the actual radio, using the matching SDK. With normal GUI
and waterfall load, run each workload for 30 minutes after warm-up and record
p99/max block time, queue growth, underruns, CPU frequency/throttling, RSS and
available temperature. The proposed p99 processing target is no more than 70%
of the represented audio interval, with no sustained underruns or queue growth.

On the cloud host, the pinned RADE V1 text test passed six cases and a speech
roundtrip produced non-silent decoded audio. The upstream clean-channel
`rade_c_v1_wav` feature-loss check passed at 0.113 against its 0.119 threshold.
The source/model versions and repeatable commands are recorded under
`tools/freedv`. These host results do not establish noise/fading-channel results
or real-time performance on the radio.

## Decision record

Report PASS, FAIL or INCONCLUSIVE for each separate audio route, microphone,
PTT/recovery case and codec workload. An untested route, unavailable hardware,
or missing model is INCONCLUSIVE. External audio is a scope decision only after
the supported native routes have been tested and documented as unusable.

See `tools/freedv/sources.json` for the source revisions and fixture hashes.
Preserve individual component notices and inspect the Opus model archive's
notices before distributing a firmware image containing it.

`.github/workflows/freedv-baseline-image.yml` is a manually triggered X6200
baseline image definition pinned to the inspected Buildroot revision. It is
unverified and does not create a diagnostic image. GitHub only exposes a new
workflow-dispatch definition after the workflow exists on the default branch;
do not merge this draft solely to run it. Validate it when the branch is ready
for review, then add the diagnostic image and benchmark packaging separately.
