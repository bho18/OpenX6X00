# Native diagnostic image: implementation and qualification status

Updated 2026-10-28. The implementation is present; image readiness requires the
mandatory automated host, matching-SDK and actual-image checks below. Physical
routing/PTT/audio/RF gates remain **INCONCLUSIVE** until the operator returns a
result bundle. Follow [the short radio procedure](native-radio-test.md) only with
a diagnostic candidate image that has passed those automated gates.

**Hardware continuation:** the published image at `2083e3e7` has now returned
audible fixture, hand-mic, routing and recovery evidence. See the
[case-specific results](native-radio-results-2026-10-28.md). Use
[Session 1](native-session-1.md) for the next RF proof and short turns, with
[Session 2](native-session-2.md) for unresolved native routing/recovery. The
operator selected functional Stop qualification before the short TX proof;
precisely measured external RF cutoff remains a separate gate. Do not repeat
the completed unchanged codec or two-minute fixture cases.

## Fixed baseline

The integration branch is `experiment/x6200-native-diagnostics` in
`bho18/OpenX6X00`, based explicitly on v0.3.6 commit
`91f3b5e03f8cf7bffc58a1c44eaef6221be21631`. The fork logo is retained; no upstream
mutation or submission is authorized. `tools/freedv/sources.json` retains codec,
model, Buildroot and control-library pins. Generated image manifests record the
actual integration commit and effective compiler/library/fixture identities.

The recovered baseline SDK archive SHA256 is
`bc253744032e1acfbb219fb22d07d3ec8024269da84dc02404048fc365a0b528`.
Its GCC 12.3.0, glibc 2.36 loader and SDK NE10 identity match the measured package.
The older SDK-manifest hash identifies the Buildroot commit text, not a unique
SDK archive. Both outer artifact and inner archive checksums were verified;
normalizations of three canonical SDK symlink aliases are recorded explicitly.

Previously completed radio measurements still describe their original package.
They establish file-processing capacity, not the new physical audio path. The
original synchronized-RX 70% margin remains **FAIL**; sustained throughput passed.

## Implemented

- Real in-process radio backend with exclusive writer reservation, fresh packet
  status, exact cached snapshots, low-power guarded experiments and transient-only
  restoration. Snapshot/readback includes microphone/gains, VOX, route commands,
  levels, speaker/monitor, power and unchanged tuning/filter/protected bits.
- Competing app/audio/control start checks, guarded parameter/queued writes and
  worker-safe busy observations. SWR/baseband protections and keepalive remain
  operational. Diagnostic startup defers analog restoration while TX/status blocks it.
- Explicit case capabilities and cancellable preparation. Local Arm/Run are
  distinct; RF cases remain bounded to ten seconds. Failed clear retries run
  independently of GUI/disk joins. Flush acknowledgement and fresh post-clear RX
  precede mixer/radio restoration. Failed release/readback retains ownership and
  re-enters restoration. The first stop cause is retained and exported.
- Real MFK/front-panel UI beside the normal waterfall. A serialized remote-input
  fence drains old injected/held events and admits fresh local controls. Close
  waits for safe release; no arming or test resumes after restart.
- Bounded hardware-free RADE engine with stateful resampling, receiver plus two
  acquisition helpers, separate FARGAN output, generation fencing and graceful
  end-of-over inside the outer RF deadline. Fixture input is streamed.
- Capture fan-out, exact partial-write handling, explicit cross-thread playback
  cancellation, selected stereo channels/supported mixer alternatives and exact
  restoration. Callbacks do not write files. Excerpts are bounded; full counters,
  cancelled tails and deliberate excerpt truncation remain distinct.
- Clean 120-second fixture playback, interrupted short fixture playback, live RX,
  live microphone TX, the eight route probes and stop/timeout/failure cases.
  Per-configuration confirmations gate dependent tests; qualification expires
  when the session releases.
- Private consolidated exports with selected manifests, cached commands versus
  packet observations, before/after radio/ALSA state, WAV excerpts, codec/audio
  metrics and resource samples. No automatic uploads or configuration databases.

## Checks and release path

Local diagnostic CTests (20 tests without the optional native-codec targets) pass.
The real backend, mocked audio adapter, stereo/mixer, input provenance, release,
preparation, failed-clear retry and stop-cause paths are exercised. The production
coordinator is tested through local Arm/Run, completion, fresh-RX restoration and
unkeyed-route confirmation. A deterministic watchdog-tail regression prevents a
new case being advertised ready before the previous timer callback has retired.
Native codec
checks cover speech-kernel equality, streaming/EOO, synchronized and interrupted
fixtures, and codec output accepted by mocked playback. These are development
checks; they do not qualify radio throughput, acoustics or RF.

The image workflow requires fresh source-matching reports for diagnostics ON/OFF,
actual simulator protocol/screen and local MFK panel entry/Close checks, X6100 backend compilation, native codec
reference checks, matching-SDK/QEMU correctness and actual-image ELF closure.
It builds paired normal/diagnostic images with current runtime assets, symbols,
checksums and manifests in disposable verified image copies. Baseband and updater
signature enforcement remain unchanged. See [build instructions](native-image-build.md).
No hardware-ready claim is implied merely by the source being committed.

The remaining operator qualification is one guided session with the hand mic and
speaker first, a two-minute audible decode, brief routing/recovery/RF cases and
5–10 minutes of mixed turns. Complete relevant built-in/stereo/mixer alternatives;
missing observations stay INCONCLUSIVE. Controlled RF receive interoperability
still needs a peer transmitter/source. No routine 30-minute audio test is required.

## Cloud environment publication

The user's Publish operation reported a generic saved-settings error. No backend
publication logs are exposed; its root cause and successful snapshot publication
remain unconfirmed. GitHub API and artifact-storage access now work after runtime
reconnection, and the matching SDK has been recovered. A publication error is no
longer the SDK network blocker. Never describe a saved draft as proof of publication.
