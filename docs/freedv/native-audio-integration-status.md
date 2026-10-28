# Native audio integration: development status

Updated 2026-10-28. This is source development, **not a diagnostic-card release**.
Do not use the read-only Info page as a routing test interface. Hardware gates
remain INCONCLUSIVE. The accepted procedure remains the
[combined validation plan](combined-validation-plan.md).

## Baseline and evidence

The integration branch is `experiment/x6200-native-diagnostics`, with fork
v0.3.6 commit `91f3b5e03f8cf7bffc58a1c44eaef6221be21631` merged explicitly.
`tools/freedv/sources.json` retains the measured Buildroot, model and codec pins,
and records the previous SDK manifest hash and compiler identity. Generated
release manifests must additionally record the actual integration commit,
working-tree status, compiler/sysroot identity and packaged-file hashes.
The existing radio measurements still describe their original package; they do
not qualify this extraction, the new GUI baseline or physical audio adapters.
The 70% synchronized-RX margin finding remains FAIL.

All branches and pushes remain in `bho18/OpenX6X00`. The working fork logo is
preserved. No upstream issue, PR or maintainer contact is part of this work.

## Implemented foundations

- Explicit passive, routing, receive-playback and microphone-TX case types.
  Every routing/TX case still requires local arming and has a ten-second limit.
  Receive playback can run for two minutes only after a completed unkeyed route
  probe and explicit local confirmation. Qualification expires with ownership.
- A controller monitor independent of LVGL and file workers rejects stale status
  and aborts unexpected TX in receive cases. Restoration requires both playback
  cancellation acknowledgement and a fresh post-command-clear RX packet.
  Timer cancellation after an automatic abort permits subsequent tests.
- A writer reservation rejects acquisition during in-flight admitted mutations.
  Nested mutations retain their admission. Persistent/computed parameter writes,
  manager mode/band changes, legacy preference setters, radio setters and setting
  observers, ordinary playback/TX players, recorder starts and screen-record
  toggles use the gate. SWR protection retains an explicit unkey path.
  This is not yet a complete hardware session: active applications, direct
  ingress paths and queued work still require the backend's complete audit.
- Explicit PulseAudio playback handles support cancellation from another thread,
  provide a lock-free stop fence, expose flush acknowledgement and preserve partial-write
  counts. Requested buffering is capped at 200 ms; granted attributes are
  reported. RF stop uses the lock-free request before clearing commands; server
  flush runs afterward outside that critical path. Handles must outlive all users; joins/destruction are outside RF Stop.
- One capture dispatcher reads physical capture and fans out to separate codec
  and recording queues. Backlog limits are 500 ms and one second respectively.
  Source holes and stalled consumers fail the affected measurement. The WAV
  writer can consume a supplied queue without acquiring physical capture.
- Both RADE benchmark implementations use the shared allocation-free speech
  kernels. Encoder features and FARGAN continuation sequencing are shared;
  resampling, receiver workers and device/session adapters still need extraction
  into the complete streaming engine. The benchmark remains device-free.

## Verification completed

- v0.3.6 host GUI builds with diagnostics enabled and disabled.
- All 13 diagnostic CTest cases pass, including the real mocked audio callback,
  cross-thread playback cancellation, acknowledged flush, full/partial writes,
  capture fan-out, stalled consumers, source holes, WAV queue input, reservation
  races, rejected preference changes, timeout and held-TX restoration.
- The X6100 backend compiles against its pinned control-library headers. The
  complete X6100 host link remains unverified because the previous native
  control-library installation is absent.
- Nine native reference/shared benchmark comparisons pass. Pipeline comparisons
  include exact feature, native-float, PCM and metadata hashes for synchronized,
  silence and interrupted input with three acquisition participants.
- A separate kernel check verifies exact TX-feature equality against the original
  encoder loop and exact RX output across arbitrary feature chunking, empty
  sync-gap calls and rejected output capacity (37,600 output samples).

Reproduce speech checks with `tools/freedv/validate_shared_speech.py`, providing
`--rade-source`, `--rade-library`, `--opus-build`, `--fixtures` and `--output`.
These inputs must be the pinned parallel-acquisition RADE and Opus build trees.
The default frozen benchmark reference is `4f3be47`; the summary records its
resolved commit, current source identity and library/core hashes. These native
checks establish correctness, not ARM performance or SDK compatibility.

## Remaining implementation and release gates

1. Implement the real backend under the radio lock, complete transient/mixer
   snapshot/readback/restoration, busy-session checks, full ingress exclusion,
   protection integration and diagnostic startup recovery.
2. Complete bounded streaming RX/TX/resampling and lifecycle adapters; connect
   capture, codec, recorder and explicit playback handle with generation checks.
   Add diagnostics-only stereo/channel and supported mixer alternatives.
3. Connect local Info-page controls, named presets, operator qualifications,
   timing/resource/event collection and bounded consolidated export. No RF
   controls are exposed before the backend and fault tests are complete.
4. Build with the matching SDK, run SDK correctness and ABI checks, then package
   normal/diagnostic images with current runtime assets, symbols and checksums.
5. Deliver one guided session: two-minute speaker case, brief routing/PTT cases,
   live-mic RF trials and a five-to-ten-minute mixed exercise, then one export.
   No routine repeat of the unchanged codec/endurance batch is required.

The matching SDK is not present in this workspace. GitHub API requests currently
fail at proxy CONNECT with HTTP 403, before repository permissions are evaluated;
Git transport works. This blocks SDK-artifact retrieval, workflow dispatch and
PR creation here. It does not account for the unfinished software work above.
No target image, hardware-ready label or new radio qualification is claimed.
