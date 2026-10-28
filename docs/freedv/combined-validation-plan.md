# Complete remaining validation with short native voice tests

Status: implementation and test plan, not a released test procedure. The
[sustained synchronized RADE result](rade-long-sync-radio-2026-10-27.md) confirms
real-time offline speech throughput; the original 70% margin remains unmet.
No speaker, microphone, routing or physical PTT qualification is claimed here.

The user's direction is to finish the remaining validation promptly and show
usable native voice, while shortening audio runs. Keep the remaining codec,
routing and recovery coverage; do not defer that work wholesale. This supersedes
the earlier 30-minute audio durations, 2-hour-45-minute batch and speaker soak.
Use short cases and repeated half-duplex turnarounds. Longer runs are follow-up
tools when a measured problem justifies them, not an automatic requirement.

The next delivery should be one versioned diagnostic-card release with short
codec cases, a guided hardware session and consolidated results. Avoid another
operator download, flash and upload for each individual experiment. Keep build
and startup checks automatic. Order the hardware cases to demonstrate audible
voice as soon as its route is established, then finish the remaining coverage in
the same package and session.

All branches, builds, issues and eventual review PRs remain in `bho18/OpenX6X00`.
The custom logo remains fork-only. Do not submit anything upstream in this
milestone.

## 1. Finish the remaining codec checks in a short batch

### Preserve completed evidence

Keep the completed 30-minute three-participant zero-input acquisition result.
It met the proposed 70% timing screen for that specific workload with the normal
GUI and waterfall active. Zero input does not establish behavior on receiver
noise or interference.

Retain the completed continuous-speech result: all 15,000 measured blocks stayed
synchronized and produced speech, with no dropped blocks or queue overflow.
Proceed with audio integration without another long run. Retain three
acquisition participants as the current candidate; do not run another worker-count
sweep without a measured reason. Reuse existing results for their original SDK
package and fixtures. Measure the new integrated audio path directly in the
short demonstration.

### Retain coverage and shorten durations

Verify source, model, fixture and executable hashes, free memory/storage and the
running GUI automatically. Run the five remaining file-only workloads below for
two measured minutes each after five seconds of warm-up. Collect them together,
sequentially, with no competing codec tests. The batch represents about ten
minutes plus warm-up and preparation; it measures short-run correctness and
capacity, not endurance.

| Case | Measured duration | Purpose |
| --- | --- | --- |
| RADE TX with speech processing and resampling | 2 minutes | Encoder capacity and output continuity |
| 700E TX with resampling | 2 minutes | Reference encoding |
| 700E synchronized RX with resampling | 2 minutes | Reference decoding and actual synchronization |
| 700E no-sync RX | 2 minutes | Reference acquisition load |
| RADE RX with deterministic nonzero noise | 2 minutes | Acquisition beyond literal zeros and false detections |

Reuse the completed synchronized RADE result and directly check the new
integrated decoder during the two-minute speaker case. Cover interruptions,
EOO/new-over behavior and reacquisition in the 5-10-minute conversation exercise
below. There is no need for a separate long transition benchmark before that
exercise. Keep 700E available for short speaker comparison if it helps diagnose
a routing versus codec problem.

Changed codec/worker implementations require relevant correctness and short
timing checks, and the report must distinguish the new build from earlier
endurance evidence. They do not automatically trigger another long run.

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

### Criteria and measurement limits

Report the original p99 processing/interval <= 0.70 screen explicitly. Also
report input backlog, stage capacity, output cadence, queue bounds and full-path
latency separately for the overlapping RX stages. One block taking more than
120 ms through two overlapping stages is not by itself proof of lost throughput.
Conversely, a responsive GUI is not evidence of intact audio delivery.

The original 70% margin is a reported engineering measurement, not a blocker for
attempting this short proof of concept. A successful demonstration requires
intelligible decoded speech, no unaccounted dropped audio or post-startup
playback underruns during the measured speech intervals, bounded queues and
repeatable TX/RX recovery. Do not call a failed original margin a pass. Explain
any output gaps caused deliberately by loss of signal separately from delivery
faults. Initialization, intrinsic codec delay and added buffering remain separate.

Retain all short-run timing, synchronization, output-integrity, queue and resource
checks. Report sample counts and exact measured durations with percentiles. The
remaining routing and recovery checklist is still in scope. Select longer tests
only when observations justify them. Long idle listening still exercises
acquisition even when nobody speaks; the shorter protocol does not claim thermal
or long-duration leak qualification, and records that limit explicitly.

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
   fixture-to-speaker, live capture observation, all route matrix cases,
   recovery and a gated microphone-to-RADE TX preset. No diagnostic session or arming resumes
   after restart. The bench helper may collect results but cannot arm or key.
6. **Validate and package the whole delivery.** Run host builds with diagnostics
   OFF/ON, host backend fault tests and the shared X6100 compile check. Cover
   partial writes, queue saturation, conflicting writers, stale TX packets,
   physical PTT held, timeout, worker failure and failed restoration. Retain
   actual RADE speech/text/V1 loss checks and traditional FreeDV checks. Build
   baseline and diagnostic images from declared pinned sources with the matching
   X6200 Buildroot SDK, preserving update-signature enforcement and baseband.

The release includes both image checksums, the SDK identity, source/model
manifest, symbols, short codec checks, validated fixtures, operator guide and recovery
instructions. Pin any chosen upstream update explicitly; do not build whichever
`main` happens to contain. A failed required build/validation gate prevents
publishing a hardware-ready label.

### One guided hardware session, with proof of concept early

Back up settings and preserve the working card. Use the spare card for the
diagnostic image. Verify ordinary receive, controls, waterfall, speaker and
microphone operation before starting. Keep the GUI/waterfall active throughout.
Use supported storage after a free-space check; do not format the unexplained
data partition or rely on `/tmp` surviving a reboot.

| Phase | Operator and firmware actions | What it establishes |
| --- | --- | --- |
| Baseline and candidate routes | Inventory automatically; test the candidate speaker and hand-mic/generated-RF routes first, with dummy load and SDR. Distinguish spoken mic audio, playback and received audio. Capture raw PCM and state/RF transitions. Do not wait to finish all eight combinations before exercising a working route. | Whether the required native paths exist, coexist and avoid contamination |
| First audible decode | Once unkeyed playback is established, decode two measured minutes of a known RADE modem recording through FARGAN and resampling to the radio speaker. Speech should be audible within the first seconds after acquisition. Keep RX capture running where the measured route permits it. | Recognizable decoded speech on the actual speaker, accounted output and short-run capacity |
| Hand-mic capture and generated RF | Capture 10-20 seconds of spoken hand-mic audio. In a separately armed case, send a distinguishable generated waveform for at most 10 seconds while checking that microphone capture survives and analog speech does not leak to RF. | The independent input/output route needed for live encoding |
| Stop and restore | Before live-mic RF trials, exercise Stop, timeout and injected worker failure; verify fresh observed RX and saved-state restoration. Check held physical PTT separately, without claiming software can override it. | Software stop behavior and repeatable recovery |
| Integrated microphone trial | After the relevant route and stop checks pass, locally arm a 5-10-second microphone -> RADE encoder -> modem playback -> RF trial into the dummy load. Record RF with the SDR and attempt reference decoding on the PC. | A short end-to-end TX demonstration, subject to usable SDR capture and successful reference decoding |
| Repeated conversation turns | Run ten guided cycles of about 20 seconds of fixture RX/decode-to-speaker, 5-10 seconds of locally armed live-mic TX, then about 5 seconds of idle/acquisition. Vary a few received segments with known signal interruptions. Keep the same session/worker lifecycle intended for the integrated mode and record every transition. | First/last speech delivery, TX/RX turnaround, reacquisition, stale audio, queue reset and resource behavior across mode changes |
| Finish remaining coverage | Complete the remaining RECORD x MODEM x software-PTT combinations, relevant built-in-mic cases including actual keyed operation, GUI/front-panel and hand-mic PTT behavior, and controlled service restart after ordinary cases behave predictably. Inspect separate channels and supported mixer alternatives where routing remains unresolved. | Complete the routing and recovery checklist with brief cases and explicit unresolved results |

The conversation exercise represents about 5-6 minutes of scheduled input;
allow roughly 5-10 minutes with local controls. Setup and initial route diagnosis
are additional and may take longer. Combine the proof-of-concept cases in one
image and export, with visible progress toward audible RX and encoded TX. A
failed route disables its dependent cases and records why, while unrelated
measurements remain available.

Finish the remaining route matrix, relevant built-in-mic cases, restart
characterization and necessary channel/mixer alternatives in this validation
delivery, with short observations. Do not declare native routing unusable before
the supported alternatives have been tested. Preserve any unresolved result
explicitly and direct follow-up at that problem rather than automatically asking
for a longer run of every workload.

All unqualified route combinations are potentially keyed: each requires local
arming and explicit execution, starts at 1 W requested power, and is limited to
10 seconds of automatically controlled TX. No unattended sweep across RF cases.
Speaker playback is unlocked only for a demonstrated unkeyed route, with
continuous observed-TX monitoring and abort on unexpected TX. Hardware-held PTT
still requires the operator to release it; software cannot guarantee RF stop.

Any file-only codec TX check never transmits RF. For
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
captures and operator/SDR observations. Keep counters for the complete short
session and bounded audio excerpts around speech and transitions; preflight
and cap recording storage rather than filling the small root filesystem.
Preserve SDR recordings on the PC and associate them by case/time/hash. No
automatic upload or full configuration-database export.

The validation report keeps separate PASS/FAIL/INCONCLUSIVE decisions for codec
workloads, speaker delivery, simultaneous RX capture, hand-mic TX routing,
built-in mic, PTT/recovery and RF interoperability. Missing evidence stays
INCONCLUSIVE. Native-route failures require the documented alternatives before
external audio is considered. The measured pipeline timing may require an
explicit product decision even if audio is usable. Mark untested cases as not
run. Results apply to the stated shorter protocol; do not claim 30-minute
endurance, thermal stability or controlled RF receive interoperability from it.

Production FreeDV UI, activity-frequency markers, logging, remote microphones
and upstream submission remain subsequent work. These broader diagnostics
exercise the intended audio chains without claiming a finished voice mode.
