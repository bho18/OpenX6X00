# Persistent native FreeDV implementation status

This is **unreleased development software** based on
`21df019884da3e8cdc1b4521a4b4f8a51ca210e1`. No physical radio was accessed,
armed, keyed or changed. Production availability remains blocked: there is no
registered complete qualified profile, and the pinned hardware/control API does
not establish independent hand-mic release while MODEM is active.

The user-test delivery is the [persistent spare-card candidate](candidate-test.md).
Its diagnostic build exposes **APP → Modes → FreeDV test** without fabricating a
qualified profile. Persistent TX/RX cases retain the actual production engine
and audio adapter across turns, record bounded input/output/raw-channel excerpts,
and export codec/resampler/worker lifetimes. Existing local arming, ten-second RF
limit, Stop and fresh-RX restoration still apply. The image assembly workflow
validates the actual edited rootfs/ELF closure before producing its output.

The completed delivery is `/workspace/deliverables/x6200-persistent-test-20261102.zip`
(135,741,954 bytes), source commit `f8d624b3a7ffdb1992715d184e4e090b6d250cab`.
ZIP SHA256: `3eb980544c7d5caef4cc401845cddcc93eee446d51aa6d974462d45f6df0d424`.
Its `START-HERE.md`, `TEST-RESULTS.md`, source bundle, manifests and checksums are
the current handoff. Documentation here was updated after freezing that build;
the packaged source retains its exact build identity.

The supplied checkout contained the diagnostic per-turn implementation, rather
than the newer persistent workspace described in the plan. Existing repository
artifacts retain their original identities and limitations. Missing later
artifacts have not been invented or relabeled.

## Implemented software

- `voice_engine.c` retains one live RADE V1 context, LPCNet encoder, FARGAN
  decoder, four direction-specific resamplers, acquisition helpers, processor and
  separate synthesis worker. Disposable warm-up never reaches the live receiver.
  Half-duplex workers suspend on condition notifications. RX transitions reset
  to search and discard old audio without recreating the receiver.
- TX extracts 10 ms features after 200 ms of microphone input admitted at the
  actual post-key capture boundary. Short releases complete accepted input;
  only final partial frames are padded. One EOO and a 200 ms silence tail precede
  engine/device drain. Output is bounded to three maximum modem/EOO blocks and
  input backlog to 500 ms. Bounded telemetry separates processing/synthesis,
  output-queue wait and idle-worker notification-to-resume delay.
- `voice_audio.c` uses the actual PulseAudio adapter with retained streams,
  capture flush acknowledgements, cork/flush/rearm generations, partial-write
  offsets and actual device drain. A cancellation epoch prevents Stop racing
  rearm from reopening admission. Ordinary playback is flushed/corked at entry
  and safely restored at exit. No routine per-turn WAV recording exists.
  Runtime capture/playback failures clear software TX independently of
  coordinator progress; a lost PulseAudio context clears TX before restart.
  The read-only, bounded telemetry collector preserves device grants, latency,
  underflow generations and process resource observations.
- `voice_session.c` implements enter/PTT/retune/abort/leave/observe, all required
  states, separately sequenced/fresh PTT sources, release lockout and drain
  repress. A retained independent watchdog enforces the 30–600 s timeout and
  1.5 s graceful-drain deadline even if a coordinator driver hook stalls.
  Held/unknown controls retain ownership; a new unsafe observation during exit
  remutes routing and starts another fresh-RX fence.
- `voice_radio.inc` binds the production adapter to actual control ownership,
  key/clear/protection, tuning and restoration. Entry claims/mutes before codec
  preparation. Supported filters, zero native IF offset, AGC timing, microphone, routing, processing,
  gains, calibrated ALSA playback/capture levels, RF gain, squelch, preamp,
  attenuation and power are explicit. Readback is labeled as cached commands, not
  independent hardware measurement. Unavailable second-filter-bank/IF/TX-filter/EQ controls require
  explicit fixed-characteristic qualification attestations.
- Mode-neutral immutable descriptors bind hardware/build/baseband/control,
  devices/channels, routes/mixer, effective settings and evidence. Selection
  prefers USB-DIG only when its entire profile passes; USB remains eligible.
  `fd_profile_production()` deliberately returns NULL. Physical backends return
  invalid for unsupported independent PTT health; no bits or releases are guessed.
- `mode.c` provides the qualification-gated APP → Modes entry with automatic RX,
  state, synchronization, microphone activity, frequency/VFO/profile, independent
  held/unknown PTT, Stop, Exit, explicit editable bookmarks and timeout preference.
  It preserves entry tuning and never recalls a preset automatically. Host-only
  simulation is visibly labeled and forbidden in physical builds by CMake.
- Local RX frequency/band/VFO/memory/volume changes pass through coordinated
  transactions. TX/drain/recovery reject before mutation. Settings and low-level
  hardware writes, competing applications and remote TX remain fenced. A real
  cross-process file lease closes the web server's direct SQLite mutation race.
  App replacement and generic destruction defer until shutdown/restoration.
- Profile overrides touch hardware only. Ordinary VFO/band/SSB/DIGI preferences
  remain the restoration source across visited contexts; current tuning/VFO and
  permitted volume changes survive exit. Application-memory frequency restore
  is not used. Action 126 and type 6 remain stable; 17 pinned GUI HF seeds,
  edits/deletions and legacy USB/USB-DIG storage values are preserved. Bookmarks
  and memory recall cannot override the qualified mode. Timeout defaults to 180 s.
- The test kit includes pinned dependencies/models, full GUI source verification
  including Opus, real engine/device/app runners, failure injection, preflight,
  case selection, collection, analysis validation and conservative RF timing.
  Missing prerequisites become BLOCKED; absent evidence/listening is INCONCLUSIVE.
  The hundred-speech-turn gate requires every original/modem/decoded turn and
  its listening assessment/hash. See the [54-case matrix](acceptance-to-tool-matrix.md).

## Verification and its scope

Current artifacts are indexed under
`/workspace/build/voice-validation-20261102`; original development reports under
`/workspace/build/voice-validation` remain unchanged.
The [current verification report](software-verification-2026-11-02.md) identifies
the final application run, build variants, manifests and exact limitations.

- Host regression, session timeout, engine fault, actual radio binding, bookmark,
  input/writer and reusable-device checks pass. The expanded suite has 30 CTests,
  including 100 alternating diagnostic turns through the actual retained engine
  and production adapter with recorded-sample accounting.
  Tool/collector tests include source/model preflight, wrong-case/build/profile,
  missing/incomplete recordings, per-turn listening, cross-process web leases,
  known gaps/delays/clipping and a complete rehearsal collection.
- An assembled host application passed 100 real PA TX/RX turns and 100 entry/
  exit cycles. Playback stream IDs stayed fixed across turns; post-exit counts
  returned to 21 threads and 40 descriptors. In the final `release-endurance-ui`
  run, RSS grew 36 KiB from cycle 10 to cycle 100. It verifies explicit Stop,
  held-PTT lockout, asynchronous app replacement and unobscured native controls
  during TX/recovery. It uses a silent virtual source and simulated physical
  PTT, establishing neither intelligibility nor hardware behavior. Earlier
  runs with layout findings and collector failures keep their own identities.
- Identified ten-minute host RX retains the executable, optimized backend,
  relevant source copies and pre-run hashes. It exercises synchronization,
  synthesis and repeated acquisition beyond the old statistics limit. Output
  remains listening-unassessed and is not RF acceptance. Earlier unoptimized
  and unsnapshotted RX runs remain separate development evidence.
- Full X6200 native builds with diagnostics both OFF and ON, compatibility
  builds, and the full X6100 application are checked with the matching ARM SDK.
  The complete pinned official GUI builds with recorded GCC namespace adapters,
  source/package/model hashes and build configuration. A build is not a GUI
  interoperability/listening or installed-release pass.
- Image-tool unit tests use mke2fs/debugfs/e2fsck. The actual candidate image now
  passes filesystem checks, all 45 dependency checks and both ARM loader checks;
  signing-key and root-partition preservation and compressed-image readback are
  verified. The corrected release ARM runner executes 24 modem and 20 synthesis
  blocks under QEMU. Emulator timing is not radio performance. Physical boot is
  untested. Sanitizer results remain scoped: wrapper ASan/UBSan and session
  TSan checks do not instrument all codec dependencies; LeakSanitizer runtime
  limitations remain INCONCLUSIVE, never an absence-of-leaks claim.

## Outstanding qualification

Stage 1 software/tool readiness and stages 2–3 implementation are available for
review. Stage 4 now has an identified, installable spare-card candidate. Hardware
setup/profile qualification and stage 5 remain pending the paused physical setup
and required equipment.

Required remaining evidence includes one complete matched USB/USB-DIG profile,
independently valid radio-side and hand-mic press/hold/release, no analog speech
leakage, simultaneous microphone/modem RF, RF reception during decoded speaker
playback, physical candidate boot/operation, numbered-speech listening, both complete-GUI
interoperability directions and the separate installed-release track, live-path
measurements/conversation, conservative physical RF cessation timing and final
bidirectional RF acceptance. A receive-only SDR cannot supply controlled RF RX.

No qualified profile is registered and no finished bidirectional native-mode
claim is made. Keep the working `.2` card/recovery path. Preserve the historical
70% processing-margin failure as FAIL and analog-leakage failures against their
exact tested USB routes. USB-DIG eligibility adds no hardware measurements and
transfers none of those results. See [the current guide](operating-guide.md),
[integration cases](integration-cases.md), [matched profiles](matched-profiles.md)
and [historical radio results](native-radio-results-2026-10-28.md).
