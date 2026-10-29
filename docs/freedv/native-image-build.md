# Building the native diagnostic spare-card images

This workflow produces a normal v0.3.6 image and a diagnostic image from the same
integration commit and matching Buildroot SDK. A successful build means the
artifacts are ready for the guided hardware tests; it does not establish that
microphone, speaker, routing, physical PTT or RF interoperability passed.

Run **X6200 native FreeDV diagnostic images** manually in the fork's Actions page,
selecting `experiment/x6200-native-diagnostics`. The workflow uses read-only
repository/Actions permissions. It never publishes a GitHub release, modifies
upstream repositories, flashes hardware or keys a radio.

## Source and toolchain identity

- Application baseline: `91f3b5e03f8cf7bffc58a1c44eaef6221be21631` (v0.3.6), plus
  the exact integration commit reported in each image manifest.
- Buildroot: `8b7ca9d6d4e82ade0fc7654c46b9f8688689a4a3`, submodule
  `cc34b5b2abf2588f0ef55e46a4fe85a60c19ba1f`.
- Verified baseline Actions run: `40417980169`, artifact `12701486870`, ZIP SHA256
  `0fdfdd2bc9f67cda6137a8adb8e60e829b2055db2506f1184cf8c3b924311b01`.
- The recovered SDK must identify as Buildroot GCC 12.3.0 from `gcc34b5b2`.
  Absolute SDK aliases are relocated only within the recorded SDK prefix or
  target sysroot and recorded in the recovery manifest; original archive bytes
  remain verified. Archive checksums, compiler/sysroot identity and actual runtime dependency code/data
  are verified (allowing Buildroot to strip debug sections). The Buildroot commit-text hash alone is not an SDK archive hash.
- Codec/model/patch pins remain in `tools/freedv/sources.json`. SDK builds retain
  the validated guarded NEON implementation and three acquisition participants.

The continuous speaker fixture is a pinned 132-second prefix of the earlier
validated long transmission. It contains no end-of-over frame and is streamed
through bounded buffers. Its hash and source-artifact identity are in the package.
No additional 30-minute operator run is required by this build.

## Required gates

The image job stops if a selected host/backend/audio test is absent or fails.
It builds diagnostics ON/OFF, exercises the live host simulator's protocol and
screen output, and compiles the shared X6100 backend against pinned headers.
A native codec-enabled adapter test and shared-reference comparisons exercise
the real speech path on the host. The matching SDK then executes existing RADE speech, text, V1 correctness/loss,
pipeline and acquisition-worker correctness checks under QEMU. A separate
unpaced streaming-engine test checks lifecycle and bounded queues without using
emulator timing as a radio-performance estimate.

Both GUIs are compiled with that SDK. The normal image has diagnostics and native
codec integration OFF. The diagnostic image requires both options ON and explicit
SDK-built RADE/Opus/resampler dependencies. Images are withheld unless the edited
root filesystem's loader and complete ELF dependency closure match SDK code/data or exact package bytes
and pass QEMU loader version checks.

## Runtime assets and preservation

The offline image tool applies all current v0.3.6 `rootfs/` files, icons, app data,
web configurator module and version information, rather than swapping only a GUI
into an old application filesystem. It verifies every written file by readback.
A new X6200 kernel/package/image-level requirement stops this derivation and
requires an explicitly pinned full Buildroot rebuild.

The source image is never modified. Only its root partition is replaced; the
bootloader, FAT boot partition, partition table and bytes outside root remain
identical. The fork's boot logo is included. The baseband is not updated and the
baseline update-verification public key is preserved byte-for-byte. No unsigned
update exception is installed.

## Artifacts and operator handoff

`x6200-native-diagnostic-images` contains both `.img` files, checksums, debug
symbols, source/SDK/model manifests, effective CMake configurations, ABI reports
and completed software-gate records. `x6200-native-codec-package-and-sources`
contains the file-only benchmark package and corresponding sources. The verified
matching SDK archive is retained separately as `x6200-native-matching-sdk`. Failure logs
are separate artifacts explicitly labeled **not for flashing**.

Use the spare card, preserve the working card and settings backup, and follow
`RADIO-TEST.md` in the release artifact. The hardware session remains
short: two minutes of decoded speaker audio, brief locally armed route/PTT cases,
and a five-to-ten-minute mixed-turn exercise with one consolidated export.
Do not format the unexplained partition on the existing working card. The bench
helper collects files; it cannot arm or key the diagnostic tests.

A local build can invoke the same tools in order: `fetch_native_baseline.py`,
`validate_native_host.py`, `validate_native_host_codec.py`,
`build_benchmark_package.py`, `fetch_native_fixture.py`,
`validate_native_codec_sdk.py`, then `build_native_images.py`. Each accepts explicit
paths and records its evidence. Keep work directories outside the checkout and
commit source changes before building reproducible packages.
