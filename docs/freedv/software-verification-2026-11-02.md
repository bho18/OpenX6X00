# Persistent native FreeDV: software verification, 2026-11-02

This is development verification of the working tree based on
`21df019884da3e8cdc1b4521a4b4f8a51ca210e1`. It is not a qualified radio release.
No radio was accessed, armed, keyed or changed. The complete acceptance catalog
is [the 54-case matrix](acceptance-to-tool-matrix.md); its uncompleted gates
remain BLOCKED or INCONCLUSIVE.

Evidence is retained in `/workspace/build/voice-validation-20261102`. Earlier
`/workspace/build/voice-validation` results remain unchanged and retain their
original source/binary identities and limitations. The `reviewed` subdirectory
contains current source, binary, dependency and tool manifests, the changed-file
source archive, and the executable preflight/run/collection/assessment rehearsal.
The helper archive requires the identified full checkout and dependencies; it
is not an image or SDK.

## Completed checks

| Check | Result and scope | Evidence |
| --- | --- | --- |
| Host regression | PASS, 29 CTests; retained engine, actual adapter/binding, ownership, preferences, release/timeout and recovery | `release-host-tests.log` |
| Assembled native application | PASS, 100 real PA TX/RX turns and 100 entry/exit cycles; silent virtual source and simulated PTT only | `release-endurance-ui/result.json` |
| Live telemetry collector rehearsal | PASS, 20 samples in 2 seconds with zero missed replies; collected grants/counters are not RF or listening acceptance | `release-endurance-ui/telemetry/telemetry.json` |
| Qualification tools | PASS, 17 tests; known gaps/delay/clipping/markers, collection identity/completion/listening rejection, source drift and web lease races | `release-kit-tests.log` |
| Image tools | PASS, 5 synthetic image/SDK/ELF tests using mke2fs, debugfs, e2fsck and objcopy | `release-image-tools.log` |
| ARM application builds | PASS; native X6200 with diagnostics OFF and ON, compatibility X6200, full X6100; matching SDK, ARM hard-float ELF observations | `release-sdk-*.log`, `final-*.log`, `*-elf.txt` |
| Complete pinned official GUI build | PASS, complete application build only; installed-release and interoperability remain unassessed | `/workspace/build/voice-official-gui-full/reference-manifest.json` |
| Audio adapter ASan/UBSan | PASS with leak detection disabled; actual pump with mock codec/PA boundaries | `sanitizers/audio-asan-ubsan.log` |
| Session ThreadSanitizer | PASS for coordinator/profile/test; codec and device dependencies are outside its scope | `sanitizers/tsan.log` |

The GUI binary SHA256 is
`3c991240a865a7657252317da0aaf0d8afadd44065aa27d2cd17c894f3c7356a`.
Its manifest records GUI/backend/RADE/Opus/model pins and the disposable GCC
namespace adapters. No codec arithmetic was changed by those adapters. A GUI
build or backend-runner smoke output is not a listening/interoperability pass.

The final assembled host binary SHA256 is
`239078e3014c6759c98822a702b89dc7f69dbc5f6a592033b5ea7fd2846828a0`.
Its playback stream identities stayed fixed across turns. Post-exit counts
returned to 21 threads and 40 descriptors; cycle 10/100 RSS was 51,476/51,512 KiB
(36 KiB growth). The run verifies automatic RX, local tuning/VFO, TX guards,
graceful device drain, Stop while PTT is held, release lockout and asynchronous
mode replacement. Reviewed TX/recovery screenshots keep Stop/Exit visible.
Source/fixture/collector hashes and the checked build log are recorded in its
result. The `reviewed/binary-identities.json` manifest identifies retained
copies of all four ARM applications and both host applications.

## Retained receiver endurance

`rx600-optimized` snapshots its runner, backend, codec/application CMake caches,
relevant source and fixture hashes **before** its paced 600-second run. The
current engine source matches that snapshot. It processed 4,999 modem blocks,
4,734 synchronized blocks, 4,673 synthesis blocks and 61 acquisitions with zero
reported engine failures. Input/output high-water marks were 240/960 samples;
one live RADE, encoder, decoder, four resamplers and two workers were retained.
Decoded output is retained and listening remains UNASSESSED. This is repeated
host-fixture RX, not a controlled RF or intelligibility result.

The original unoptimized host backend and its failed silent-source backlog
runs remain separate. RelWithDebInfo fixes that build configuration; it does
not reverse the historical radio's 70% processing-margin failure.

## Evidence limitations and remaining gates

Silent null-source application turns establish lifecycle behavior, not distinct
speech or physical PTT. Host mixer/PTT simulation cannot attest a hardware route.
The host prefix has no `amixer`; existing ordinary startup commands log that
absence. This run therefore supplies no ALSA mixer/equipment qualification.
PulseAudio latency/underflow counters and software event timestamps cannot prove
RF cessation. LeakSanitizer's earlier ptrace/runtime restriction remains
INCONCLUSIVE; no leak-free claim is made. The original unsnapshotted ten-minute
run, stale-binary follow-ups, UI findings and collector-tag failed attempts keep
their own identities and are not substituted for current checks.

Actual image/rootfs dependency closure and boot validation are BLOCKED because
no candidate image is available. A matching SDK link and hard-float ELF header
do not establish actual-image compatibility. Build/control identifiers such as
`matching-sdk-control` are development labels, not compatibility attestations.

Production availability remains blocked by the absent complete qualified
profile and documented independent hand-mic release while MODEM is active.
Both matched profiles, both physical PTT controls, simultaneous hand-mic/modem
RF, no analog leakage, external RF survival during speaker playback, numbered
speech listening, both complete-GUI interoperability directions, the installed
official release, live conversation, valid physical 500 ms timing and final
bidirectional RF acceptance still require their specified evidence. A
receive-only SDR cannot supply controlled receive RF. No profile is registered
and no finished bidirectional native-mode claim is made.

Continue with [the operating guide](operating-guide.md),
[integration cases](integration-cases.md), [matched profiles](matched-profiles.md)
and [the test-kit workflow](../../tools/freedv/qualification/README.md). Preserve
the working `.2` card and recovery path.
