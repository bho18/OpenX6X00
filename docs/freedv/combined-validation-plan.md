# Complete codec qualification and diagnostic firmware together

Status: implementation and test plan, not a released test procedure. The current
30-minute synchronized RADE radio result is pending. No speaker, microphone,
routing or physical PTT qualification is claimed here.

The next delivery should be one versioned diagnostic-card release with a guided
hardware session and an unattended codec batch. Avoid another operator download,
flash and upload for each individual experiment. Small development and startup
checks remain automatic gates inside the build or suite, rather than separate
requests to the operator.

All branches, builds, issues and eventual review PRs remain in `bho18/OpenX6X00`.
The custom logo remains fork-only. Do not submit anything upstream in this
milestone.

## 1. Finish codec qualification

### Preserve completed evidence

Keep the completed 30-minute three-participant zero-input acquisition result.
It met the proposed 70% timing screen for that specific workload with the normal
GUI and waterfall active. Zero input does not establish behavior on receiver
noise or interference.

Assess the pending continuous-speech result before selecting the final pipeline
configuration. Retain three acquisition participants as the current candidate;
do not run another worker-count sweep without a measured reason. Reuse the
pending result for its original SDK package and fixture. An integrated audio
test on the new firmware is a different workload and still needs measurement.

### One unattended batch

Package a single runner that verifies the source, model, fixture and executable
hashes, checks free memory/storage and the running GUI, then runs these cases
sequentially. Codec workloads must not compete with one another. Each endurance
case has 30 seconds of warm-up followed by 30 minutes of measured input.

| Case | Work performed | Principal evidence |
| --- | --- | --- |
| RADE TX | Speech feature extraction, encoding and input/output resampling | Sustained output, processing tails, deadlines and resources |
| 700E TX | Speech encoding and resampling | Reference TX timing and resources |
| 700E synchronized RX | Continuous valid modem input, decoding and resampling | Actual synchronization, speech output and deadlines |
| 700E no-sync RX | Pinned no-signal input | Acquisition load and lack of false speech/sync |
| RADE receiver-noise RX | Pinned deterministic, nonzero band-limited noise | Acquisition capacity beyond literal zeros; false detections |
| RADE transition exercise | About 15 minutes of clean speech, controlled dropouts, EOO/new-over boundaries, noise and known reacquisition opportunities | Per-block state transitions, worker/fallback paths, output gaps, queue recovery and reacquisition time |

Allow approximately 2 hours 45 minutes plus startup/fixture preparation. This
does not repeat the pending long synchronized case or the completed zero-input
endurance case. Changed codec/worker implementations invalidate reuse of the
affected timing evidence and require the runner to explain the necessary repeat.

Validate new fixtures against the pinned native reference before publishing
them. A repeated finite over is a transition fixture, not a continuous-sync
fixture. Specify noise seeds, levels and interruption boundaries in the case
manifest. Only demand recovery in segments demonstrated decodable by the
reference; weak-signal segments characterize behavior without inventing an
unsupported sensitivity requirement. Add captured radio noise as a later replay
case when the hardware session supplies it.

The runner preserves every completed or failed case and writes one summary.
A timing failure is recorded and does not discard the remaining independent
cases. Integrity failures stop launch; sustained overload, lost GUI or resource
limits stop the affected run and preserve evidence. No automatic retries,
uploads, RF control or physical audio-device access are added to the benchmark.

### Completion criteria

Report the original p99 processing/interval <= 0.70 screen explicitly. Also
report input backlog, stage capacity, output cadence, queue bounds and full-path
latency separately for the overlapping RX stages. One block taking more than
120 ms through two overlapping stages is not by itself proof of lost throughput.
Conversely, a responsive GUI is not evidence of intact audio delivery.

Do not silently change the original criterion. If sustained throughput passes
but the original criterion fails, retain both findings and present a concrete
buffering/latency acceptance proposal for the product decision. Initialization,
fixture storage, intrinsic codec delay and added buffering remain separate.

## 2. Complete and validate the diagnostic image

### Implement before asking for another hardware deployment

1. **Connect session ownership to the real radio.** The current session
   controller is mock-backed, and Settings -> Info remains read-only. Implement
   the backend under the radio control lock; reject conflicting recording,
   digital/audio, tuning and TX sessions. Guard every competing local/remote
   control path while ownership is held. Preserve SWR and frequency protection.
2. **Complete snapshot and restoration.** Extend the partial cached snapshot to
   the affected radio-owned overrides, microphone/gains, RECORD/MODEM/PTT,
   speaker/monitor, levels, ALSA channel settings, mode/filter, frequency and
   power. Do not restore unrelated shared-register bits wholesale or rewrite
   persistent preferences. Restore only after fresh, post-clear observed RX.
3. **Finish capture and playback instrumentation.** Connect the existing
   pre-decimation capture ring and background WAV writer to the session. Add
   exact accepted-sample counts and explicit errors to diagnostic playback:
   `audio_play_if_room()` currently can write only part of a block and return
   true, so its boolean return is insufficient for continuous-speech accounting.
   Use bounded queues, explicit handling of unwritten samples, and underrun,
   overflow, hole, flush and latency counters. Never write files in callbacks.
4. **Share the codec implementation.** Extract a bounded streaming engine from
   the validated pipeline for use by the benchmark and the in-process diagnostic
   adapter. Verify output/state equivalence against the tested implementation.
   Keep the benchmark free of radio and device access; only the firmware adapter
   owns capture, playback and routing. Stream fixtures through bounded buffers
   instead of bringing the current large whole-file conversion into the GUI.
5. **Add named diagnostic presets and export.** Provide local Arm, Run, Stop,
   commanded/observed TX, capture/playback counters and restoration status. Include
   fixture-to-speaker, live capture observation, route matrix, recovery and a
   gated microphone-to-RADE TX preset. No diagnostic session or arming resumes
   after restart. The bench helper may collect results but cannot arm or key.
6. **Validate and package the whole delivery.** Run host builds with diagnostics
   OFF/ON, host backend fault tests and the shared X6100 compile check. Cover
   partial writes, queue saturation, conflicting writers, stale TX packets,
   physical PTT held, timeout, worker failure and failed restoration. Retain
   actual RADE speech/text/V1 loss checks and traditional FreeDV checks. Build
   baseline and diagnostic images from declared pinned sources with the matching
   X6200 Buildroot SDK, preserving update-signature enforcement and baseband.

The release includes both image checksums, the SDK identity, source/model
manifest, symbols, codec suite, validated fixtures, operator guide and recovery
instructions. Pin any chosen upstream update explicitly; do not build whichever
`main` happens to contain. A failed required build/validation gate prevents
publishing a hardware-ready label.

### One guided hardware session on the spare card

Back up settings and preserve the working card. Use the spare card for the
diagnostic image. Verify ordinary receive, controls, waterfall, speaker and
microphone operation before starting. Keep the GUI/waterfall active throughout.
Use supported storage after a free-space check; do not format the unexplained
data partition or rely on `/tmp` surviving a reboot.

| Phase | Operator and firmware actions | What it establishes |
| --- | --- | --- |
| Baseline and routes | Inventory automatically; exercise all eight RECORD x MODEM x software-PTT combinations with hand mic first, dummy load and SDR. Distinguish spoken mic audio, generated playback and received audio. Capture raw PCM and state/RF transitions. | Which native routes expose microphone/RX audio and carry playback to speaker/RF, whether capture/playback coexist, and whether they contaminate one another |
| Recovery | In the same guided session exercise GUI/front-panel PTT, hand-mic PTT, timeout, injected worker failure, Stop while physical PTT is held, and controlled service restart after ordinary cases are predictable. | Stop latency, held-PTT behavior, first/last-frame analog leakage and restoration |
| Decode and speaker | Once a route is demonstrated safe for unkeyed playback, run 30 minutes of the known continuous RADE modem fixture through live decode, FARGAN, resampling and the radio speaker. Keep raw RX capture running where the measured route permits it. Follow with the transition fixture and a short 700E playback reference. | Actual decoded speech delivery, physical playback continuity, integrated CPU/memory load and whether simultaneous RX capture remains usable |
| Integrated microphone trial | After the relevant hand-mic/generated-RF route and stop tests pass, locally arm short microphone -> RADE encoder -> modem playback -> RF trials into the dummy load. Record RF with the SDR for reference decode on the PC. | A diagnostic end-to-end TX chain and actual RF waveform, subject to usable SDR capture and successful reference decoding |
| Remaining native alternatives | Repeat relevant cases with the built-in mic, including actual keyed operation. Inspect separate ALSA channels and supported mixer alternatives if a required native route remains unresolved. | Built-in behavior and evidence needed before considering external audio |

Budget roughly 1-2 hours of attended routing/recovery work plus a 30-minute
speaker soak and about 15 minutes of transition playback. Alternatives may take
longer. These are planning estimates, not shortened acceptance runs. The image
contains all cases up front; a failed route disables dependent cases and records
why, without preventing unrelated measurements or requiring a fresh build.

All unqualified route combinations are potentially keyed: each requires local
arming and explicit execution, starts at 1 W requested power, and is limited to
10 seconds of automatically controlled TX. No unattended sweep across RF cases.
The long speaker run is unlocked only for a demonstrated unkeyed route, with
continuous observed-TX monitoring and abort on unexpected TX. Hardware-held PTT
still requires the operator to release it; software cannot guarantee RF stop.

The 30-minute codec TX batch is file-only and never transmits RF. For
software-owned hardware TX, require command clearing and observed RF cessation
within 500 ms of abort under the tested conditions. Hold restoration while
physical PTT remains active, display `Release microphone PTT`, and wait for fresh
RX status. Use the service stop/start mechanism for recovery, not merely killing
the supervised GUI process.

### Decode-and-audio evidence limits

Fixture -> decoder -> speaker is a useful integrated test and belongs in this
delivery. It proves neither RF receive interoperability nor simultaneous native
RX capture by itself. Record those as separate results. During a suitable
off-air observation, compare radio capture with the SDR while distinct local
speech plays; inspect contamination and loss of received audio.

Include a live capture -> decoder -> speaker diagnostic preset for use when a
valid FreeDV signal is available. A receive-only SDR cannot supply that signal.
Controlled RF receive interoperability remains blocked on a peer transmitter or
suitable source; a fixture or opportunistic off-air observation must not be
reported as the missing controlled test. Hearing clean speech also does not
replace output counters and captured evidence.

## Consolidated results and decision

Export one folder per large session, with a top-level summary that can be
collected together. Include exact versions, source/model/fixture hashes, case
definitions, timestamped state/path events, per-block timing, resource samples,
queue/drop/underrun counters, before/after restoration comparisons, selected WAV
captures and operator/SDR observations. Keep full counters for endurance runs but
use bounded audio excerpts at startup, middle, end and transitions; preflight
and cap recording storage rather than filling the small root filesystem.
Preserve SDR recordings on the PC and associate them by case/time/hash. No
automatic upload or full configuration-database export.

The final report keeps separate PASS/FAIL/INCONCLUSIVE decisions for codec
workloads, speaker delivery, simultaneous RX capture, hand-mic TX routing,
built-in mic, PTT/recovery and RF interoperability. Missing evidence stays
INCONCLUSIVE. Native-route failures require the documented alternatives before
external audio is considered. The measured pipeline timing may require an
explicit product decision even if audio is usable.

Production FreeDV UI, activity-frequency markers, logging, remote microphones
and upstream submission remain subsequent work. These broader diagnostics
exercise the intended audio chains without claiming a finished voice mode.
