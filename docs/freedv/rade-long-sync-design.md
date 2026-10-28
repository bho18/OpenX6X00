# Continuous fixture for sustained synchronized RX

Use the already tested `b9fcea82` matching-SDK runtime to measure 30 minutes of
synchronized speech. Its original 9.984-second fixture includes an end-of-over
frame and repeats, which mixes steady speech with reacquisition. This experiment
adds an external continuous fixture without changing the decoder, numerical
guard, worker scheduling, resampler, queue policy or acceptance threshold.

## Generation and provenance

The producer repeats the pinned speech PCM to exactly 1,860 seconds and calls
the pinned native `rade_tx_wav` once. Speech feature extraction and transmitter
state continue across speech repetition boundaries. It does not concatenate
encoded transmissions or cut EOO frames from existing modem PCM.

The generator must report 15,500 normal modem frames. Their 960 samples each
place the first EOO at sample 14,880,000 (1,860 seconds), safely beyond 30 seconds
of warm-up plus 1,800 measured seconds. The terminal EOO contributes 1,152 samples,
making the PCM16 mono 8-kHz fixture exactly 29,762,304 bytes, below the existing
executable's 32-MiB input limit.

The versioned external manifest records source/model/speech pins, generated
speech and PCM hashes, producer and generator identities, native compiler and
flags, library/configuration hashes and the native validation result. TLS and
model checksums remain enforced by the existing reference builder. Generation
is not claimed to be bit-reproducible across hosts; each artifact records the
actual waveform hash used for its tests.

The workflow publishes PCM, manifests, validation JSON, checksums and notices
only after the complete native receiver gate passes. It does not publish native
executables as a radio package. Failed validation JSON and build logs remain in
a separate diagnostic artifact; the unvalidated waveform is not published.

## Radio collection and memory

The standalone collector verifies the exact tested SDK package before importing
its helpers, then verifies the external fixture and native result as data. The
Windows procedure runs a short radio smoke case before the endurance case; both
use three acquisition participants plus the existing speech worker. A successful
completed run must retain synchronization and speech features on every measured
block and encounter no EOO or fixture wrap. Failures and partial results are kept.

Keeping the runtime unchanged retains its whole-fixture conversion at startup.
The waveform's converted float array consumes 328,131,452 bytes (312.93 MiB), and
simultaneous loader arrays total about 398.08 MiB at preparation peak. The raw
tmpfs file is separate RAM use. The collector requires 600 MiB MemAvailable after
transfer and checks it again before launch. It never changes memory or storage
configuration to force a test through. Preparation time, peak RSS, retained
fixture memory and progressively touched timing records must be separated from
codec working memory and steady-state timing.

## Development validation

The final producer and collector passed 27 focused tests and independent review.
Full local native orchestration generated the waveform and decoded all 15,000
measured blocks after warm-up, with synchronization/features throughout and zero
EOO events. The collector accepted that actual artifact and revalidated its
native result and checksums without pin overrides.

The local waveform SHA256 was
`1b0497a4e6b70701fd769202d2dedb439798ab35fe1b54dad48a0b5523a32585`;
the CI-generated artifact may have a different recorded hash. Native elapsed
times do not qualify radio performance, and no radio speech endurance result is
claimed by these checks.

Use the [radio guide](rade-long-sync-radio-test.md) for the manual artifact build,
Windows transfer and result collection. Assess sustained output cadence, input
backlog, queues, resource observations and full-path latency separately. The
original 70% serial assessment and all physical audio/PTT/RF gates remain intact.
The finite-over fixture and a future optional transition trace remain separate
tools for investigating the earlier brief delays.
