# X6200 persistent FreeDV test candidate

Ready for the guided spare-card hardware tests. Source: `f8d624b3a7ffdb1992715d184e4e090b6d250cab`.
The installed application identifies as `native-voice-test-0da34f37b9ed`.
The later source revision fixes the standalone ARM runner; application source
is unchanged. Both component identities and all binary hashes are recorded.

The installable image contains **APP → Modes → FreeDV test**, locally armed
persistent hand-mic TX and live RX cases, bounded recordings, export, Stop and
recovery. These cases use the actual production engine/audio adapter and retain
their codec/resampler/stream resources across turns until Close.

Completed software/image checks:

- 30 host CTests, including 100 alternating turns through the actual persistent
  engine and adapter, exact accepted-output recording accounting and EOO deltas.
- APP/Modes candidate entry, persistent RX selection and safe Close exercised
  with local host FIFOs; no Acquire, Arm, Run or physical RF action was performed.
- 17 qualification-kit tests and 5 image-tool tests passed.
- X6200 native/diagnostic/compatibility and full X6100 application builds passed.
- The actual edited rootfs passed complete dependency checks for 45 libraries,
  QEMU loader checks for the GUI and runner, and filesystem checks. The signing
  key and bytes outside the root partition are preserved.
- The ARM runner executed 24 modem blocks and
  20 synthesis blocks under QEMU with zero engine
  failures. Emulator timing is not a radio-performance measurement.
- The compressed image was decompressed and its full SHA256 and byte count
  verified against the validated image.

Physical radio boot, RF, intelligibility and independent hand-mic release under
MODEM have not been qualified. The pinned control API still reports that source
as unsupported; the normal automatic-PTT mode stays gated. The bounded test cases
provide usable hardware test access without inventing a passing profile.
Peak values in persistent diagnostics measure the selected capture channel;
they do not by themselves identify a microphone route or RF/speaker isolation.

Earlier zero-work ARM-runner output and other superseded attempts are retained
under the workspace build directory and are not counted as current evidence.
Historical USB route leakage and processing-margin failures keep their original
identities. Read START-HERE.md, then the candidate test guide.
