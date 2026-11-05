# Building the half-duplex development candidate

This candidate is Linux application firmware for the existing X6200 image. It
does not update the MCU/baseband or FPGA. A successful software build leaves
physical microphone routing, receive/speaker routing, independent hand PTT,
physical RF-off timing and radio processing margin unqualified. Production
native mode remains gated by its real capabilities and qualification profile.
The X6200 development variant enables `ENABLE_FREEDV_NATIVE_VALIDATION` for
truthful candidate diagnostics. Missing hardware capabilities still prevent
native RX/TX; this option does not set a qualified production profile.

On stock baseband, the native validation entry reports the unsupported contract
and remains blocked. The report-informed development revision adds software
checks around direction-specific stereo audio and applied route/control state;
the report does not establish the complete live contract needed for native TX.
Use the [integrated qualification procedure](native-half-duplex-qualification.md)
for the next receive-only endpoint and PTT mapping stage and its prerequisites.
That procedure does not imply a new receive-only mapping screen exists. The
[baseband research plan](baseband-research/README.md) identifies the missing
lower-layer evidence and support. X6200 builds withdraw the
legacy FreeDV Diagnostics route/gain experiments: the panel and its radio
acquisition/preparation paths refuse those operations. Ordinary radio and
ordinary recording remain available. Native conversation testing stays gated
by its separate audio/PTT contract.

## Inputs

Use the source revision recorded in the final software verification. The LVGL
submodule is pinned by that revision. Host builds require its pinned
`build-host/deps` liquid-dsp and ft8 libraries, Boost headers, CMake, Ninja,
pkg-config, SDL2, PulseAudio, NetworkManager, dbus-glib, ALSA, sndfile, PNG,
JPEG, sqlite, udev and FFTW development files. `host/build.sh build` prepares
the two DSP dependencies from their pinned sources.

ARM builds use the matching Buildroot GCC 12.3 SDK, not a generic Debian ARM
compiler. SDK and source pins remain in `tools/freedv/sources.json` and the
recovery manifest. The native build also consumes the pinned RADE parallel
acquisition implementation, its exact Opus configuration/model and static
samplerate library. Reusing unchanged libraries preserves their original
verification identity; it does not mean a new performance measurement ran.

In the managed development workspace, source `/workspace/setup/env.sh` to use
the prepared host tools/libraries. The helper removes host include and library
search variables from ARM build subprocesses. Its SDK toolchain selects the
target sysroot. Do not point an ARM build at the host dependency sysroot.

## Reproducible variants

Run `tools/freedv/build_native_development.py --help` for explicit paths. Keep
each work directory outside the checkout and use one directory per variant.
The helper configures and builds the complete application, runs registered
FreeDV host tests for the `host` variant, and records command lines, source-file
hashes, compiler/library identities, binary hashes and ARM ELF metadata. It
rejects a build if source files changed while it ran. Final packaging requires
committed source; exploratory builds can record a dirty snapshot.

Example in the prepared workspace:

```sh
source /workspace/setup/env.sh
TASK_SDK=/workspace/build/native-sdk-recovery/recovered-ready/sdk/arm-buildroot-linux-gnueabihf_sdk-buildroot
TASK_CODEC=/workspace/build/migration-codec-build
export PATH="$TASK_SDK/sbin:$PATH" # debugfs/e2fsck/mke2fs for image checks

python3 tools/freedv/build_native_development.py \
  --variant host --work /workspace/build/x6200-native-host \
  --control-headers /workspace/build/migration-host-control-headers

python3 tools/freedv/build_native_development.py \
  --variant host-native-validation \
  --work /workspace/build/x6200-native-host-validation \
  --codec /workspace/build/x6200-native-host-codec \
  --codec-fixture /workspace/build/migration-codec-release/package/freedv-benchmark/fixtures/rade-modem.raw \
  --build-id freedv-native-development-20261105.2

python3 tools/freedv/build_native_development.py \
  --variant x6200-diagnostic --work /workspace/build/x6200-native-arm \
  --sdk "$TASK_SDK" --codec "$TASK_CODEC" \
  --build-id freedv-native-development-20261105.2

python3 tools/freedv/build_native_development.py \
  --variant x6200-normal --work /workspace/build/x6200-native-normal \
  --sdk "$TASK_SDK"

python3 tools/freedv/build_native_development.py \
  --variant x6100 --work /workspace/build/x6200-native-x6100 \
  --sdk "$TASK_SDK" --codec "$TASK_CODEC" \
  --x6100-prefix /workspace/build/x6200-native-x6100-control/install
```

The X6100 compatibility library must be built from the pinned
`1426f56ef6759dc1c90cb76478c6e524e08c5c94` source using the same SDK and installed
under that separate prefix. This compiles the shared application and X6100
conditional code; it does not create an X6100 hardware qualification result.
The compatibility variant enables diagnostics and native voice to compile the
affected conditional paths; it is not an X6100 delivery image.

The `host` variant exercises the coordinator, real radio binding with mocked
hardware, adapter, startup cancellation and route-contract regressions. Its
codecs are disabled. The `host-native-validation` variant compiles the complete
native validation GUI with the virtual host profile, then runs three synthetic
engine turns and the real-codec streaming, fixture and adapter checks. It does
not claim that the GUI entry was driven or that physical audio/RF worked.
Its host codec inputs must be built with `RADE_ACQ_PARALLEL=ON` and
`RADE_ACQ_NEON=OFF`; the expected layout is the pinned host codec build, including
the Opus build under `rade_c/build_opus-prefix/src/build_opus`. ARM native
libraries cannot be used in host tests.

## Processing and image gates

After source stops changing, repeat affected builds and tests. Execute the
ARM `freedv_voice_runner` under QEMU using the matching target loader and
libraries. Require actual modem blocks and zero engine failures; a zero-work
exit is not success. Record this as emulated correctness, never radio timing,
physical audio, speech intelligibility or live RF evidence.

Assemble the candidate with `tools/freedv/build_voice_candidate.py`. Its
verification input must identify the exact committed source, source files,
GUI/runner/probe bytes, host regression results, all three ARM application
builds and the executed ARM runner statistics. Preserve unavailable physical
observations as `BLOCKED`, `UNSUPPORTED` or `UNASSESSED`.

The baseline image must match SHA256
`a11d8fa49156a8a9ada151e2472c1da1fde2115c22563b331270035f6b6285ab`.
It is available inside the pinned `x6200-freedv-baseline.zip` release asset
whose SHA256 is
`0fdfdd2bc9f67cda6137a8adb8e60e829b2055db2506f1184cf8c3b924311b01`.
The matching-SDK-only archive does not contain this image.

Image assembly retains `prepare_native_image.py` checks: complete runtime
overlay, every written file read back, SDK-matched ELF dependency closure,
QEMU loader version checks, preserved signing key, filesystem checks, and
unchanged bytes outside the root partition. Do not bypass these checks to
produce an image. Image creation does not install, flash, arm or key a radio.

Keep the resulting image, source archive/bundle, exact software verification,
dependency provenance, checksums and consolidated device test procedure
together. The image is a development candidate; actual native conversation
testing still depends on the unresolved hardware audio/PTT contract.
The source bundle contains the candidate's full reachable history and is
verified in an empty repository; no earlier local commit is required to import
it. Dependency submodules retain their separately pinned source identities.

Public releases contain a corresponding-source archive and a separate public
Git snapshot. Exclude only `docs/freedv/baseband-research/` from that snapshot;
retain every application, build and test file byte-for-byte. Never publish the
private source bundle or reachable private history. Verify the complete Git
tree after staging, including tracked files matched by ignore patterns, and
preserve the LVGL gitlink. Record the original built commit, public commit and
excluded paths in `PUBLICATION-MANIFEST.json`. The Windows ZIP contains exactly
one full `.img`, the consolidated guide, test/build evidence and checksums.
Give each revision a new release tag so earlier downloadable builds remain
available. Compression or transport reconstruction must preserve the exact
raw image, source bytes and original software verification.
