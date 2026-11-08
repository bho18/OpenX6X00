# Updating and building the operational FreeDV beta

Development and publication live in `bho18/OpenX6X00`. `Guru-RF/OpenX6X00`
provides upstream application changes; it is a read-only source for this
workflow. Do not send upstream a branch, tag, pull request or release.

## Source integration

The integration branch reconciles the public FreeDV history with its original
upstream baseline. Future updates use ordinary merges; an unrelated-history
merge is not part of the regular procedure. Keep working beta tags available.

In a clean checkout of the development branch, inspect the remotes first. Set
`origin` to `https://github.com/bho18/OpenX6X00.git` and `upstream` to
`https://github.com/Guru-RF/OpenX6X00.git`, then protect the push destination:

```sh
git remote set-url --push upstream DISABLED
git config remote.pushDefault origin
git fetch upstream main
git switch -c integration/freedv-upstream-YYYYMMDD
git merge upstream/main
git submodule update --init --recursive
```

Record the fetched upstream SHA and review each overlapping change. Pay
particular attention to input handling, radio/PTT ownership, audio servicing,
screensaver/wake, settings migrations and shutdown. Retain FreeDV's named
migration ledger instead of adding FreeDV steps to upstream's numeric migration
sequence. Commit the reviewed source before qualification; never modify that
source while its build or audio campaign is running.

The current integration deliberately retains the verified X6200 Linux/SDK
baseline. Its upstream Buildroot revision and Linux kernel did not change; the
newer FT8 library and application-owned runtime programs/scripts are installed
as reviewed updates. Record those identities separately from the baseline.

## Build inputs

Use a Linux host with Bash and the matching pinned Buildroot GCC 12.3 SDK. A generic
Debian ARM compiler is not the deployment toolchain. Populate the source's
pinned LVGL submodule and host DSP dependencies. Dependencies and model pins
are recorded in `tools/freedv/sources.json`; the build helpers validate actual
inputs and record their hashes.

Prepare these paths outside the source checkout:

- The verified baseline image and its matching SDK.
- Patched ARM and host RADE/Opus/libsamplerate builds, including the pinned model.
- The pinned modem/continuous fixtures, X6200 control headers and SDK-built
  X6100 compatibility-library prefix.
- The current runtime helpers/library outputs required by the image packager.
- Fresh work and output directories, with enough disk space for all builds,
  retained campaign evidence and image assembly.

Keep dependency caches separate from source and evidence. Preserve prior
failed attempts. Reclaim only verified regenerable intermediates; `/tmp` may be
memory-backed and is not suitable for large qualification recordings. The
existing qualification memory, CPU and deadline limits remain in force.

## Reproducible build and checks

The release pipeline has three explicit stages: `builds`, `verify` and
`package`. They share the same clean committed source, build ID, dependency
paths and work directory. Running the build stage alone is not a complete
release qualification. The default `all` mode is intended for callers that
already prepared the required runtime and CI inputs; use the staged sequence
for a new integration.

First build the reviewed FT8 library and XARA input with the matching SDK:

```sh
python3 tools/freedv/build_runtime_package.py inputs \
  --sdk "$TASK_SDK" --ft8-source-archive "$TASK_FT8_ARCHIVE" \
  --work "$TASK_RUNTIME_INPUT_WORK"
```

The input manifest is `$TASK_RUNTIME_INPUT_WORK/runtime-inputs.json`. It
records the exact compiled library, headers, helper, source and notices. Then
build the five application variants and run the full repeated qualification:

```bash
TASK_SOURCE_COMMIT=$(git rev-parse HEAD)
TASK_COMMON=(
  --expected-commit "$TASK_SOURCE_COMMIT" --build-id "$TASK_BUILD_ID"
  --sdk "$TASK_SDK" --codec "$TASK_ARM_CODEC" --host-codec "$TASK_HOST_CODEC"
  --fixtures "$TASK_FIXTURES" --baseline "$TASK_BASELINE"
  --control-headers "$TASK_CONTROL_HEADERS" --x6100-prefix "$TASK_X6100_PREFIX"
  --work "$TASK_WORK" --output "$TASK_OUTPUT"
)
python3 tools/freedv/build_operational_release.py --stage builds \
  "${TASK_COMMON[@]}" \
  --runtime-inputs "$TASK_RUNTIME_INPUT_WORK/runtime-inputs.json" \
  --observer-provenance "$TASK_OBSERVER_PROVENANCE"
```

The observer-provenance record identifies the pinned host-only NumPy 2.3.5
wheel and its installed numerical observer. It is not a target-image dependency.
The pipeline archives linked binaries, then retires regenerable CMake objects
before checks, retaining a retirement receipt and all recorded binaries/logs.
Direct callers of the lower-level build helper can request the same behavior
with `--retire-intermediates-before-checks`. Qualification evidence is retained.

All compilation completes before the timed controller campaign. The pipeline
retains all five sealed build records, host tests, real-codec checks, every
controller case twice and independent scheduling/resource observations.

Collect the runtime package from that source-bound ARM build, and build a
fresh web wheel from the merged source:

```sh
python3 tools/freedv/build_runtime_package.py package \
  --sdk "$TASK_SDK" \
  --inputs "$TASK_RUNTIME_INPUT_WORK/runtime-inputs.json" \
  --gui-build "$TASK_WORK/arm/build" --output "$TASK_RUNTIME_PACKAGE"
python3 -m pip wheel --no-deps --wheel-dir "$TASK_WEB_WHEELS" ./webserver
```

Set `TASK_WEB_WHEEL` to the single newly built `x6100_webserver-*.whl` in that
fresh directory. Do not reuse a wheel made from different application sources.

Push the reviewed source explicitly to an `integration/freedv-upstream-*`
branch in `bho18/OpenX6X00`. That narrow fork-only trigger starts **FreeDV
operational source checks** for the full pushed commit; no change to the fork's
`main` branch is required. Once GitHub registers the workflow, an explicit
Actions run can also supply `$TASK_SOURCE_COMMIT` as the exact 40-character
product commit. This checks Windows collection, Linux
inventory and the complete controller regression. Every job retains canonical
source receipts; the controller job also records its pinned model acquisition.
The workflow sends no radio/PTT commands and publishes no release.

After the actual run succeeds, download and validate its evidence:

```sh
python3 tools/freedv/collect_operational_ci.py \
  --run-id "$TASK_CI_RUN_ID" --expected-commit "$TASK_SOURCE_COMMIT" \
  --output "$TASK_CI_EVIDENCE"
python3 tools/freedv/build_operational_release.py --stage verify \
  "${TASK_COMMON[@]}" \
  --ci-result "$TASK_CI_EVIDENCE/ci-result.json" \
  --dependency-provenance "$TASK_DEPENDENCY_PROVENANCE" \
  --observer-provenance "$TASK_OBSERVER_PROVENANCE" \
  --runtime-package "$TASK_RUNTIME_PACKAGE"
python3 tools/freedv/build_operational_release.py --stage package \
  "${TASK_COMMON[@]}" \
  --runtime-package "$TASK_RUNTIME_PACKAGE" --web-wheel "$TASK_WEB_WHEEL"
```

The dependency-provenance input is the retained verified SDK/codec provenance,
not an old application's passing test result. Verify rechecks dependency
identities, actual CI/source receipts, ARM processing under QEMU, GUI operation
and production collector/CLI behavior. Package requires that verification and
the exact reviewed runtime/wheel. A build without passing CI remains blocked
for publication.

The separate **Build base image (manual)** workflow maintains full Linux
images. It does not automatically produce the operational FreeDV candidate and
is not required for an application-only integration.

## Publication and verification

Validate the nine completed release assets before publishing:

```sh
python3 tools/freedv/publish_operational_release.py \
  --assets "$TASK_OUTPUT" --tag "$TASK_BUILD_ID" \
  --expected-commit "$TASK_SOURCE_COMMIT"
```

Publication is explicit and accepts only `bho18/OpenX6X00`:

```sh
python3 tools/freedv/publish_operational_release.py \
  --assets "$TASK_OUTPUT" --tag "$TASK_BUILD_ID" \
  --expected-commit "$TASK_SOURCE_COMMIT" --publish \
  --notes "$TASK_RELEASE_NOTES" --download "$TASK_FRESH_DOWNLOAD"
```

Use a new date-based `freedv-operational-beta-YYYYMMDD.N` build ID/tag and a
fresh download directory. The publisher refuses an existing release, targets
the tested product commit, downloads all published assets and independently
checks their identities. Keep older working releases available.

The release contains the image ZIP, matching helper kit, START-HERE guide,
source archive/bundle, candidate manifest, software-verification record, image
ABI evidence and checksums. Never mix an old helper kit with a new image.
Download every published asset afresh and verify its checksums, raw image
identity, source/tag identity and kit bindings before declaring delivery done.

The accepted recording, codec and RF-transmit milestones remain historical
hardware evidence. They are not claimed as new measurements by software tests.
The field check for this integration covers normal controls, screen wake,
graceful exit and alternating half-duplex turns. Reception from a known working
RADE V1 peer remains the outstanding live receive milestone. Keep the previous
working SD card as the rollback path; a Linux image update does not update the
separate MCU/baseband firmware.
