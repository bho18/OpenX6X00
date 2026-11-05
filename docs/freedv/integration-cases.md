# Native engine and application procedures

These procedures produce development evidence on a host and candidate evidence
on identified hardware. A silent virtual source does not pass numbered speech,
microphone route, RF, listening or physical PTT gates. Keep each run's binary,
source, codec/model, profile and equipment manifests. Snapshot them before running.

## Build and host fixture

Use the pinned host dependencies and `tools/freedv/validate_native_host_codec.py
--work NEW_DIRECTORY --jobs 2` to prepare matching codecs. Host RADE builds must
set `CMAKE_BUILD_TYPE=RelWithDebInfo`; unoptimized acquisition can exceed real
time. Keep the original unoptimized result separately.

For a separate simulator build, configure `HOST_BUILD=ON`,
`ENABLE_FREEDV_NATIVE_CODEC=ON`, `ENABLE_FREEDV_VOICE=ON`,
`ENABLE_FREEDV_DIAGNOSTICS=OFF`, and `FREEDV_HOST_TEST_PROFILE=ON`, supplying the
same pinned `FREEDV_RADE_SOURCE`, `FREEDV_RADE_LIBRARY`, `FREEDV_OPUS_BUILD`,
`FREEDV_OPUS_LIBRARY`, `FREEDV_SAMPLERATE_LIBRARY` and include directory. Set an
unused `HOST_DATA_DIR`. This fixture is forbidden in physical builds by CMake.
Normal radio builds keep it OFF; `fd_profile_production()` currently returns
NULL because no complete profile is qualified.

```sh
cmake --build HOST_BUILD --target x6100_gui
python tools/freedv/qualification/host_mode.py --build HOST_BUILD \
  --output NEW_EVIDENCE_DIRECTORY --turns 100 --cycles 100
```

The harness refuses another GUI's sockets/data directory and creates its own
silent PulseAudio null sink/source. It uses local evdev-format FIFOs, never a
physical device. It checks automatic RX, local frequency/VFO changes, rejected
TX tuning/band/VFO changes, real PA drain, explicit Stop/release lockout, and
destination opening only after held-PTT exit completes. Stream identities stay
constant across turns. Post-exit thread/descriptor/RSS counts accompany every
cycle. Screenshots support a separate UI review. No intelligibility assessment
is inferred.

Run `ctest --test-dir BUILD --output-on-failure`. The ownership tests share a
CTest resource lock because their external web/database lease is real. The
`freedv_voice_radio` test exercises the actual production binding, cached hardware
stub and control owner. `freedv_voice_writer` checks cross-process exclusion in
both lock orders. `freedv_voice_faults` compiles test-only injection hooks into
the actual engine; these hooks are absent from application binaries. It covers
encoder/decoder/synthesis faults, saturated output cancellation and the 500 ms
input limit. `freedv_voice_session` includes an independently stalled 1.5 s
finish operation; `freedv_voice_timeout` waits the real 30 seconds and checks
both-source release lockout. Audio tests cover partial writes, callback/drain/
flush failures, capture errors and Stop racing rearm.

## Speech and receive survival

Create a manifest of at least 100 **original, distinguishable** numbered phrases,
their transcript/first/last words, WAV identities and boundary rates. Convert
copies to mono signed-16 raw with recorded SOX commands/hashes. Do not replace
missing speech with tones and label it accepted speech. Run
`freedv_voice_runner --speech-list LIST --turns 100 --rate RATE --output NEW`.
The list has one absolute path per turn. Retain every original, modem waveform,
decoded turn and engine counters. Collect WAV artifacts with `turn_id` matching
`measurements.turn_identities`. A listening reviewer supplies one
`turn_assessments` entry per turn containing `turn_id`, `decoded_sha256`,
`intelligible_phrase`, `first_words` and `last_words`, plus reviewer/date/findings.
No single nonzero output or aggregate listening label passes this gate.

For ten-minute RX, run `freedv_voice_runner --rx MODEM_RAW --rate 8000 --seconds
600 --output NEW`. Record the exact input's repetition/EOO/outage schedule.
Retain synchronized blocks, synthesis blocks, acquisitions, decoded output and
bounded timing statistics. Processing/synthesis spans and output-queue wait are
separate from idle-worker notification-to-resume latency; the latter is not an
OS scheduler trace. Compare accounting against the known fixture. Do not infer
speech intelligibility from synchronization.

For fading, prepare identified copies with 0.2, 1 and 5 second interruptions,
record their positions and hashes, and repeat playback off/on. Require loss and
reacquisition without additional receiver creation. Hardware survival requires
controlled external RF; a receive-only SDR cannot provide it.

## Local control, restoration and recovery

1. Save deliberately different USB/USB-DIG filter, AGC, processing, microphone,
   line/monitor/power preferences and distinct A/B frequencies. Record DB and
   cached settings before entry. Verify entry changes neither frequency nor VFO.
2. In RX, tune manually, recall a type-6 bookmark and memory, change band/VFO and
   volume. Verify the selected profile is reapplied after each transaction and
   synchronization resets to search. Bookmarks and memories cannot select an
   unqualified mode. Add/edit/delete bookmarks; duplicate rejection is atomic.
3. Repeat frequency/band/VFO/memory requests in TX, drainage, Fault and EXITING,
   and through CAT/TCI/browser plus the web settings endpoints. Verify rejection
   **before** preference or hardware mutation. Attempt competing audio apps and
   independent/remote TX. Capture response, DB and commanded settings evidence.
4. Exercise 20/90/150/230 ms speech taps, mid-frame release, overlapping sources,
   repress during drain, cold entry, subsequent turns, and a turn after 60 s RX
   idle. Check accepted input, final padding, one EOO, 200 ms silence and actual
   device completion. Listening must identify beginning/end content.
5. Stop during preparation, backpressure, TX and drainage. Try a 30 s timeout,
   holding each PTT alone and both together. No fresh turn until all required
   independent releases and a fresh RX observation. Repeat app replacement and
   generic close while held or source health is missing. Destination construction
   and analog routing restoration remain deferred.
6. Exit at the **current** frequency/VFO and confirm ordinary preferences and
   permitted volume changes. Restart with PTT held/released; startup clears
   software TX and waits for fresh RX/release health before restoring analog
   routing. Do not use an old application-memory frequency as restoration.

The adapter overrides hardware only; ordinary per-band/VFO/SSB/DIGI preferences
remain the restoration source. The bounded context history is telemetry and is
never needed to restore settings. Supported filters and AGC timing are reapplied explicitly. Native RF uses
zero IF offset; exit restores the current ordinary offset and its VFO frequency
commands without reloading an entry frequency. X6200 has no second-bank or IF
command in this backend; its fixed behavior must be explicitly qualified. Physical X6200/X6100 independent hand-mic release is
currently unsupported by the pinned documented control interface; it returns
invalid, keeps native availability blocked, and must not be inferred from TX
or radio-side PTT.
