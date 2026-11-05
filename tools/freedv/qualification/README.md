# Persistent native FreeDV test kit

Current operating guide: `docs/freedv/operating-guide.md`. Implementation and
remaining qualification: `docs/freedv/implementation-status.md`. This kit never
arms or keys a radio. RF experiments retain existing diagnostic local arming,
Stop/recovery and the 1 W/ten-second limits. Large IQ remains on the computer.

The single workflow is **preflight → run selected cases → collect → assess**.
Use Python 3.11+ on Linux or Windows. Device/SDK builds run on the matching Linux
host. Installed official GUI testing may run on either platform. No automatic
uploads occur. Do not place private recordings or IQ in the firmware repository.
Unpack the helper archive over a copy of the identified firmware checkout;
building executables requires that checkout and its pinned dependencies. The
archive is a source/tool kit, not a firmware image or a portable SDK.

## Setup and reproducible identities

`../sources.json` pins codec/model/SDK/control sources; `reference-sources.json`
pins the complete GUI, backend, RADE, upstream Opus archive and model. Verify
archives before extraction. `reference.py` does this automatically, including
Opus source-file verification. `gui-packages.lock.json` records the Debian 13
GUI development packages. The bundled `host-packages.lock.json` (copied from the original workspace setup)
and SDK recovery instructions describe the original host closure;
they are not portable deployment SDKs. A matching SDK, GCC 12.3/glibc 2.36 and
archive hash from `sources.json` are mandatory for radio binaries. Do not deploy
Debian ARM libraries on the radio.

A workspace-only dependency installer is provided:

```sh
python tools/freedv/qualification/bootstrap.py --lock tools/freedv/qualification/host-packages.lock.json \
  --lock tools/freedv/qualification/gui-packages.lock.json --cache deb-cache --prefix host-prefix --download
```

It verifies package/base-file hashes and extracts into the selected prefix. It
does not mutate system packages; omit `--download` for an offline cached setup.
The GUI/image lock includes e2fsprogs and mke2fs. GUI runtime base dependencies
must match the recorded Debian 13 base. Preserve the SDK separately.

Require Python, CMake, compiler/C++ compiler, pkg-config, libsamplerate, pinned
Opus/FARGAN/LPCNet/RADE, models, SOX, FFmpeg/ffprobe, PulseAudio/ALSA utilities and
image tools **including mke2fs, debugfs and e2fsck**. Full GUI builds additionally
need wxWidgets (core/base/aui/html/net/adv/propgrid), GTK3, libsndfile, speexdsp,
hamlib, ebur128, samplerate, ALSA, PulseAudio, OpenSSL, udev and libusb development
libraries. Record actual dependency/compiler versions and package/source hashes.
On a fresh host, resolve exactly the lock versions and verify every package hash;
record existing base-system package versions too. Missing dependencies are BLOCKED.

Run `python -m unittest discover -s tools/freedv/qualification -p 'test_*.py' -v`
and retain stdout/stderr and a hashed PASS report before trusting kit results.
Known-reference tests cover gaps, delays, clipping, discontinuities and distinct
RF/playback markers. Collector tests reject build/profile mismatch, missing
recordings, incomplete runs and absent listening; a real temporary CTest rehearsal
exercises run/collect/assess. Hardware-specific timing/RF setup validation remains
separate and must be performed on actual equipment.

## Preflight

From the firmware checkout, after building all required test executables:

```sh
python tools/freedv/qualification/kit.py build-identity --build-id candidate-name \
  --binary /absolute/build/freedv_voice_engine_test --out build.json
python tools/freedv/qualification/kit.py preflight --family engine \
  --case engine-synthetic \
  --build build.json --profile profile.json --inventory inventory.json \
  --tools-lock tool-hashes.json --out preflight.json
```

`build.json` includes source commit, complete source-file manifest/tree hash and
actual executable hashes. Include every executable relevant to the family, all
under the selected build directory. A profile JSON must explicitly give `id`,
`local_mode` (`USB` or `USB-DIG`), `upper_sideband: true` and the complete effective
settings/route/mixer/devices/channels/build/hardware/evidence identities. Development
profiles cannot enable production operation.
Non-development cases require explicit hardware/build/baseband/control,
device/channel/rate, route/mixer, `effective_settings` and `evidence_identity`
fields. The profile's `build_id` must match the build manifest. Supply codec/model
archives as verified `inputs`; preflight checks their pinned hashes. Complete
GUI cases also require the pinned GUI/backend archives and separately verified
installed-release equipment. A mode label alone cannot satisfy these identities.

`inventory.json` contains `equipment` entries named by `cases.json`, each with
`verified`, identity, reviewer/date and fixture notes; `inputs` is a list of
`path`/`sha256` source/model/recording-format fixtures. Include
`recording_format_verified`, `minimum_free_bytes`, and `tools_validation` with
`path`/`sha256` pointing to a tool-validation JSON whose `status` is PASS. Equipment
must be verified before using it as a prerequisite. This includes working and
candidate cards, recovery path, dummy load/coupling/attenuation, hand microphone,
continuous-IQ receive SDR and (for RX/conversation) a transmitting peer/RF source.

`tool-hashes.json` maps executable names to verified binary SHA256 from the pinned
setup. Preflight records versions as well as checking hashes, binaries, inputs,
storage and equipment. Never create a lock merely to bless an unexplained binary.
Preflight BLOCKED returns nonzero; do not proceed as though it passed.
Repeat `--case` to select independent cases in a family. Running or assessing a
case outside that selection is rejected. Omitting it checks the entire family,
including its equipment dependencies. FFmpeg/ffprobe and image utilities use
their actual version flags; failed version checks are BLOCKED.

## Run selected cases

```sh
python tools/freedv/qualification/kit.py run --case engine-synthetic \
  --preflight preflight.json --build-dir /absolute/build --out run-001
```

Automated CTest cases retain stdout/stderr and actual exit status. Operator cases
produce an INCONCLUSIVE template with the exact procedure and required evidence.
Complete measurements, attach recordings with `path`, `role`, `sha256`, `bytes`
and actual recording `format`, and record completion. PCM audio artifacts must
be 16-bit WAV with rate/channel/frame metadata; raw/IQ acquisition identities
must be explicit. `external_recordings` entries give `local_path`,
`storage_reference`, `role`, `sha256`; collection verifies them on the recording
computer and carries references, not large IQ. A label alone cannot produce PASS.

Actual-engine runners (output directories must already exist):

```sh
/absolute/build/freedv_voice_engine_test --turns 100 --rate 8000 --output outputs
/absolute/build/freedv_voice_engine_test --turns 1 --cycles 100 --rate 44100
/absolute/build/freedv_voice_runner --speech-list numbered-files.txt \
  --turns 100 --rate 44100 --output speech-output
/absolute/build/freedv_voice_runner --rx known-modem-8k-s16le.raw \
  --seconds 600 --rate 8000 --output rx-output
```

The speech list contains one absolute signed-16-bit mono raw recording path per
turn, at the chosen boundary rate. Preserve the numbered original manifest with
transcript/first/last words and SHA256; SOX conversions must record their exact
command and input/output hashes. The runner retains TX modem output, RX decoded
speech and engine statistics. The default is paced; `--unpaced` is a distinct
throughput experiment, never a timing/RF result. Snapshot executable/source/model
identities **before** running, then hash all outputs. Synthetic tests do not pass
the numbered-speech gate. Record /proc thread/descriptor/RSS samples for full
resource-growth qualification; engine counters alone are not an OS leak check.

The assembled host fixture and detailed integration steps are in
`docs/freedv/integration-cases.md`. It rebuilds the actual application with a
checked result before testing, records binary/source hashes, uses isolated PA
null devices and local FIFOs, and can run 100 turns plus 100 mode lifetimes.
Neither silent host turns nor test-only codec failure hooks pass speech/RF gates.

For explicitly requested live-path measurements, use the read-only collector
on the computer running the application (Linux `/proc` is required):

```sh
python tools/freedv/qualification/telemetry.py --pid APPLICATION_PID \
  --seconds 120 --interval-ms 100 --output telemetry-001
```

It records cached generation statistics, granted PulseAudio buffers, device
latency, bounded underflow events, processing/queue/scheduling counters and
process CPU/RSS/thread/descriptor observations. It also retains the completed
TX generation's input/first-write/underflow/drain summary after returning to RX.
Samples carry their actual observation timestamp; a busy snapshot stays
unavailable. Capture is bounded to 600 seconds/16 MiB and cannot key or stop the
radio. Bind its files to the selected preflight/build/profile and recordings.
This is not an RF timing measurement or a listening assessment. Normal mode
operation creates no such files.

Build the complete pinned GUI with:

```sh
python tools/freedv/qualification/reference.py --archives source-archives \
  --model opus_data-HASH.tar.gz --work new-reference-directory --build
```

This creates disposable source copies, verifies upstream Opus automatically,
records pin adapters and builds `freedv`. Missing system dependencies are BLOCKED;
build failures are FAIL. Source verification does not mean the GUI built. Follow
`docs/freedv/official-gui.md` for the separate installed-release and listening
tracks. The existing backend runner is additional evidence only.

## Collect and assess (Linux or Windows)

```sh
python tools/freedv/qualification/kit.py collect --build build.json \
  --profile profile.json --bundle run-001 --bundle run-002 --out collection
python tools/freedv/qualification/kit.py assess --build build.json \
  --profile profile.json --bundle collection/engine-synthetic --out assessed.json
```

PowerShell uses the same Python commands and paths appropriate to that computer.
The older `Collect-NativeSession.ps1`/`native_session.py` remain available for
radio diagnostic exports; attach their outputs to the matching case rather than
changing their original image identity. The new collector verifies exact
build/profile/preflight identity, artifact hashes/completion and required roles,
then writes `collection.json` and readable `results.md`. Only declared artifacts
are copied; unrelated private files stay outside the collection.

Human assessments identify reviewer/date/findings and explicit PASS/FAIL.
Listening additionally identifies intelligible phrases, first and last words,
and the decoded-speech artifact hash. Until recorded, intelligibility is
INCONCLUSIVE. Read the conservative timing procedure before assessing RF-off;
software timestamps or unvalidated operator intervals do not establish 500 ms.
Do not count a rehearsal or host/mock result as a physical capability pass.
For `engine-numbered-turns`, supply numeric `turn_count`, unique
`turn_identities`, and `turn_id` on every original/modem/decoded artifact.
`turn_assessments` must bind every decoded hash to its intelligible phrase and
first/last words. A single output or an aggregate listening label cannot pass
the 100-turn gate. Collection also checks the separate preflight file agrees
with the run's embedded preflight.
