# Native RADE V1 half-duplex qualification

This protocol qualifies a persistent radio conversation through the native
coordinator, radio driver and live audio adapter. It does not turn a diagnostic
pass into production qualification. No physical test results are recorded here.
The current hardware round is receive-only endpoint/PTT availability mapping;
the [operator guide](candidate-test.md) replaces the earlier gain/order/MODEM
sequence. Native TX remains blocked. Targeted stock 1.0.7/1.0.8 comparisons retain
the relevant route, TX-sum and PTT limitations; a software-only repair is not
established.

RF is half-duplex; host capture and playback must operate concurrently **within
each direction**. The product stays in FreeDV until the operator exits:

| State | Concurrent live paths | Boundary that must be demonstrated |
| --- | --- | --- |
| Receive | RF demodulator → Linux decoder; decoded PCM → radio speaker | Speaker playback preserves RF capture; raw modem monitoring and feedback are absent. |
| Transmit | Hand microphone → Linux encoder; modem PCM → RF modulator | Microphone capture survives playback; direct analog microphone modulation is isolated. |
| Release | Stop new microphone admission; finish accepted speech and bounded tail | Physical RF ceases, receive routing returns, and the next live reply is heard without reopening the mode. |
| Exit/fault | Fence admission, clear software TX, restore only when safe | Held/unknown physical PTT is distinguished from software keying and actual RF. |

WAV files are optional evidence taps, never an operational stage. `HW_RECORD`
is a hardware route selection and is not interchangeable with file recording.

## Findings that change the test boundary

Stock TX DSP sums ADC L+R regardless of RECORD. In RX, RECORD replaces both
ordinary demodulated DAC slots with the ADC sum; MODEM TX entry overwrites ADC
routing. Neither is the independent route pair needed by native RADE. Gain zero
is 0 dB, not mute. Line-out zero affects LOL/LOR, not the headphone path, and
monitor zero does not establish RF microphone isolation. The reviewed VOX
command cache does not prove an effective control change.

The physical connector associated with the MCU input must be measured. Admitted
PTT/shared state is not a raw physical release observation, and aggregate TX can
remain true under MODEM after release. Stock command getters and I2C success
provide no host-applied route acknowledgement. Any I2C STOP can refresh stock
host presence; its nominal 200-loop interval is not a hard RF-off deadline.
Expiry retains software-request bytes and CW hang state, so logical TX can
reassert; physical RF rekey has not been established by that static finding.

Keep the host's 44.1 kHz sample contract distinct from MCU codec/DSP scheduling.
The observed 4 ms processing block is not total codec, buffering or turnaround
latency. Neither static disassembly nor Linux slot selection identifies the
board net reaching the host, speaker, headphone or RF modulator.

## Implementation admission and evidence

Resolve the matching baseband/codec/FPGA routing contract before physical TX:
the two concurrent routes above, independently observable panel and hand PTT,
and authority over physical keying and analog modulation. Preserve the existing
RADE engine, bounded queues and retained streams. A Linux encoder cannot recover
microphone samples absent from the host source. Unsupported lower-layer
capabilities remain unavailable; cached control bits and aggregate TX are not
proof of PTT intent, route state or RF-off.

The validation entry uses `fd_session_enter_validation()` with a structurally
valid candidate profile and a hardware-binding `fd_validation_admission`.
Production `complete_pass` and capability fields must not be fabricated to reach
this path. A local `fd_session_validation_arm()` then
`fd_session_validation_run()` authorizes one subsequent fresh physical press.
The grant expires and is consumed once; it does not become a product operating
step. A held control at entry, a stale observation, missing keying authority or
unsupported hand PTT must prevent admission. The stock backend remains unsupported. The ABI-2 acquire/apply/renew/observe/
release functions are abstract contracts for a reviewed MCU binding, not
invented stock commands. Observe must not renew a lease or refresh cached state.
Applied state binds the contract, MCU boot ID, lease token, route generation and
explicit inhibit scope; it still does not prove electrical routing or RF-off.

Use `fd_session_observe()` for state, generation, turn count, validation/grant
state and `grants_consumed`. Use the driver observations for fresh, independent
per-source PTT sequences. `fd_voice_audio_snapshot_get()` supplies adapter,
engine, capture and playback evidence. Generation numbers increase through RX
and TX and need not be contiguous across the ten TX turns. PTT sequence numbers
increase independently for panel and hand sources.

The bounded `native_trace` module exports transition/PTT snapshots, software TX
commands and approximately one audio snapshot per second as JSONL. In validation
RX with the bench grant disarmed and both controls released, the local **Save
trace** action writes a uniquely named `/mnt/freedv/native-session-*.jsonl`.
Disarmed fault/wait-for-release states also allow export, retaining held or
unknown controls in the failure evidence without changing the radio route.
Its header marks simulation explicitly; its footer reports discarded rows and
completeness. The assessor checks both, matches the session/baseband identity,
and requires TX/drain transitions and fresh RX before each live reply. The
`native_control` rows keep requested lease/generation/route separate from a
nullable effective `applied_state`. Header `audio_slots` describes Linux channel
selection; `physical_endpoint_mapping_proven: false` is intentional. No cached
command, aggregate TX or ordinary I2C presence is promoted into physical evidence.
Preserve
the original file and hash it before collection. Export before leaving the
mode, then record exit/restoration observations separately in the bundle.

These APIs are evidence inputs, not a complete conversation-report exporter.
Audio snapshots include `adapter_instance`, `engine_instance`, `capture_instance`
and `playback_instance`, assigned at successful resource opens and retained
through turns. These IDs are process-local; use them with the trace session ID
and recorded creation counters. PTT observations include retained
`release_sequence`, which changes on a physical release even when a re-press
occurs before the next level poll. A lower-layer binding must retain that fact.
Add the missing
transition events and exact high-water instrumentation where necessary. A
periodically sampled queue maximum is not an exact high-water measurement.
Keep timestamped route commands, actual observations and RF measurement separate.
The one-second snapshots cannot establish sample-edge timestamps, exact capture
high-water marks or independent RF timing. Existing engine high-water counters
retain their actual event-driven meaning; `capture_queued` is a sampled depth.
The offline assessor deliberately leaves unavailable fields BLOCKED.

## Current receive-only gate

Collect the read-only ALSA/PulseAudio/input inventory and map individual host
channels to measured board/codec nets using the operator guide. Preserve absent
hand-PTT observations and unknown connector identity. Powered PTT edge or signal
injection work that could key RF requires independently verified inhibition;
otherwise leave it blocked. No gain/order sweep, aggregate-release inference or
isolated operator TX is part of this round.

Exit this gate with a board-net/endpoint map and explicit lower-layer work items,
not a nominally passing profile. If hardware cannot provide the simultaneous
pairs, document that limit; an external interface is a product change.

## Later campaign after candidate interfaces exist

Pin the source, GUI binary, baseband, control library, Buildroot, RADE model and
route-contract identities. Record the audio endpoints, channel mapping, gains,
RF settings, reference FreeDV version/track and operator. Use a repeatable,
locally controlled dummy-load/attenuated test arrangement with a known-good
RADE V1 RF source and an independent SDR feeding official FreeDV. The X6200 uses
its physical microphone and speaker. The PC/SDR observe and supply reference RF;
they are not part of the standalone product audio path.

Choose turnaround and PTT freshness budgets **before** running, and save that
test plan as evidence. Measure independent RF with sufficient resolution, and
document alignment of its clock with the radio's monotonic event timeline. The
assessor adds twice the RF timestamp resolution plus the maximum alignment error
to each cross-clock RF timing interval before comparing it with the budget.

1. Require the measured board mapping and candidate MCU interfaces: explicit
   RX/TX pairs, effective applied acknowledgements, raw PTT/release history and
   an independent native lease with explicit physical/autonomous inhibit scope.
   Independently measured RF shutdown is a prerequisite to isolated operator TX.
   Then establish the route pairs in the same native driver that will run the conversation. Confirm live microphone capture during modem
   injection, analog-mic isolation, RF capture during decoded-speaker playback,
   raw-modem suppression and absence of playback feedback. Record the actual
   method and observation for each; RMS, zero gain, line-out level and monitor
   level cannot establish analog isolation. Preserve requested commands, effective
   applied configuration and physical signal observations as separate evidence.
   Bound the first instrumented TX trials and use local per-turn grants; the
   former record-then-MODEM/gain sequence is withdrawn.
2. Complete three instrumented RX/TX/RX cycles through `voice_session` and the
   physical driver as a checkpoint. Confirm retained resources and correct turn
   boundaries. Do not substitute the timed diagnostic worker or stored modem
   playback for this coordinator.
3. Run the acceptance session with radio WAV taps disabled. Enter once and
   complete ten TX turns over at least five minutes, normally 5–10 minutes:
   five turns from each physical PTT control. Give every TX turn a distinct
   spoken phrase with identifiable first and last words. After every release,
   receive a live reference reply through the radio speaker before the next
   press. Require recognizable speech, both boundary words, no stale speech and
   automatic return to receive. Record a human listener and evidence reference;
   decoder synchronization by itself does not satisfy this step.
4. In that retained session, accumulate at least 120 measured seconds of live
   decoded RF output to the speaker. Record nonoverlapping decoded intervals,
   interrupt the reference signal, then demonstrate audible reacquisition
   without reopening the mode. Exclude interruptions, silence without decoding,
   the radio's own TX periods, warm-up and file-processing time from the total.
5. Account for each accepted microphone sample and every queue. Reject capture
   deletion, stale-turn audio, pre-drain playback underruns, faults, out-of-bound
   high-water marks and resource growth beyond the declared budget. Confirm
   ordinary settings and safe routing restoration after exit.

Use one evidence bundle for the campaign. If it fails, localize the failure to
the first failing boundary: microphone source, encoder admission, modem output,
RF output, receiver input, decoder output, speaker or turn control. Fix that
boundary and rerun the affected campaign, rather than accumulating unrelated
short diagnostic successes. Changes to evidence-only tooling do not require a
radio reflash or a new RF run if the original evidence already answers the gate.

## Separate release gates

The ten-turn campaign is functional qualification, not endurance qualification
or a production release grant. Preserve these separate gates:

- Test startup release, release/repress, short taps, overlapping controls,
  held-at-entry, stale/unknown observations, Stop, timeout, protection, retuning,
  crash/restart and safe restoration. A rejected startup generation must admit
  neither new codec input nor RF output. Exercise delayed capture startup to
  expose the previously identified admission race.
- Complete official-GUI interoperability in both directions and both pinned
  source-built and installed-release tracks. A conversation tested with one
  reference build does not close that matrix.
- Run affected two-minute paced, real-radio performance measurements with the
  normal GUI active. Report p99/max processing time, represented intervals,
  deadline misses, drops, underruns, queue evolution, memory and available
  thermal observations. Preserve the historical p99/interval ≤0.70 margin
  failure until changed-path measurements resolve it. Reuse unchanged codec
  evidence only within its original scope.
- Measure graceful release → final RF and release → usable RX independently
  from emergency abort → RF-off. The 1,500 ms drain deadline is a fault ceiling,
  not a claimed conversational latency. The 500 ms software-owned emergency
  abort target is unproven as an override of held physical PTT; that requires
  actual lower-layer authority and independent RF evidence.

## Offline conversation report

Run from the repository root:

```sh
python3 tools/freedv/assess_native_conversation.py /path/to/bundle/conversation.json
python3 -m unittest discover -s tools/freedv -p test_assess_native_conversation.py
```

Exit codes are 0 for PASS, 1 for FAIL and 2 for BLOCKED. PASS means the supplied
conversation report is internally consistent and its referenced artifact bytes
match their hashes. It does **not** establish that observations are truthful or
automatically interpret RF recordings/listening evidence. The operator/reviewer
must make and substantiate those observations. Every result has
`release_qualification: "NOT_ASSESSED"`; the tool cannot register a profile.

Missing/unknown observations and absent/empty artifacts are BLOCKED. Observed
violations, conflicting values, corrupt artifacts and impossible timelines FAIL.
When both exist, FAIL takes precedence while all BLOCKED findings are retained.
`simulation`, `qemu`, virtual audio and file-processing domains never produce a
physical PASS. Synthetic unit fixtures test the checker only.

The version is `x6200.native-conversation.v2`; v1 lacks the effective-route,
lease and endpoint evidence and is not silently upgraded. All times below are milliseconds
on one aligned monotonic timeline, not wall-clock dates. Numeric values must be
finite; counts are integers and booleans are not numbers. Use JSON `null` for
unknowns, never a guessed zero. Hashes are lowercase hexadecimal.

| Object | Required fields and semantics |
| --- | --- |
| Top level | `schema`, `evidence_domain: "physical_radio"`, `mode: "RADE_V1"`, `operating_path: "native_voice_session"`, plus objects below. |
| `artifacts` | Map of IDs (without dots) to `{path, sha256, kind}`. Paths are relative to the report directory and must remain inside the bundle. Nonempty referenced bytes must match SHA256. Kinds: `native_trace`, `ptt_trace`, `rf_capture`, `test_plan`, `route_observation`, `speaker_observation`, `remote_decode_observation`, `control_command`, `effective_state`, `board_mapping`, `hardware_contract`. Native traces must use the schema-1 JSONL format with physical provenance and a complete, undiscarded footer. Multiple observations can reference the same suitable artifact. |
| `identities` | `expected` and `observed` maps with `source_commit`, `gui_sha256`, `baseband`, `control_commit`, `buildroot_commit`, `rade_model_sha256`, `route_contract_id`; values must match. Commits are full 40-digit hashes, SHA256 values 64 digits. `evidence` references the native trace. Include the full build manifest as a companion artifact; the native header does not contain every hash and the checker cannot independently attest the supplied build-manifest claims. |
| `budgets` | Positive limits `press_to_rf_ms`, `release_to_rf_off_ms`, `release_to_rx_ms`, `reacquisition_ms`, `ptt_max_age_ms`; nonnegative `rss_growth_bytes`; `fixed_before_run: true`; `evidence` referencing the test plan. Release-to-RF budget cannot exceed the 1,500 ms fault ceiling. |
| `session` | Stable strings `session_id`, `engine_id`, `capture_stream_id`, `playback_stream_id`; `engine_create_count`, `capture_open_count`, `playback_open_count` each 1; `start_ms`, `end_ms` spanning ≥300,000 ms; `wav_taps_enabled: false`; `restoration_confirmed`, `local_settings_preserved` true; native-trace `evidence`. |
| `routes` | Entries `tx_mic_survives`, `analog_mic_isolated`, `rx_capture_survives_speaker`, `raw_modem_suppressed`, `feedback_absent`. Each has separate `requested` and `applied` records described below, `observed: true`, `observed_at_ms`, `observation_basis: "physical_signal_measurement"`, `method`, and `route_observation` evidence. An observed signal does not fabricate a missing effective acknowledgement. |
| `endpoints` | `rx_capture`, `rx_playback`, `tx_capture`, `tx_playback`, each with `host_device`, integer `host_channel`, measured `board_net`, `physical_endpoint` and `board_mapping` evidence. Physical endpoints respectively are `rf_demodulated_audio`, `radio_speaker`, `physical_hand_microphone`, `rf_modulator`. |
| `hardware_contract.ptt` | `source: "raw_physical_edges"`, `retained_release_counter: true`, positive measured `minimum_retained_release_ms`, `source_map` for panel/hand, and `board_mapping` evidence. Admitted/shared PTT state cannot satisfy it. |
| `hardware_contract.lease` | `controller: "independent_controller"`, `expiry_clears_pending_requests: true`, `inhibit_ack_source: "effective_hardware_ack"`, `inhibit_scope` covering `software_tx`, `physical_ptt`, `civ`, `vox`, `tune_calibration`, `cw_tail`, and `hardware_contract` evidence. Ordinary I2C presence is insufficient. |
| `hardware_contract.shutdown` | `source: "independent_rf"`, `trigger: "native_lease_expiry"`, `initial_rf_present`, `physical_ptt_held`, `ordinary_i2c_traffic_continued` all true; measured nonnegative `max_rf_off_ms`, `uncertainty_ms`, positive preset `budget_ms`, and `rf_capture` evidence. Measured interval plus uncertainty must meet the budget. This separately controlled lower-layer qualification is a prerequisite to later operator TX, not a result supplied by Linux acknowledgements or the stock presence timer. |
| `rf_observation` | `source: "independent_rf"`, positive `resolution_ms`, nonnegative `clock_alignment_max_error_ms`, `clock_alignment_method`, `evidence` of kind `rf_capture`. Aggregate TX and commanded MODEM are not independent RF evidence. |
| `reference_decoder` | `application: "official_FreeDV"`, exact `version`, and `track` identifying the reference build used in this conversation. |
| `rx` | `source: "live_rf"`, `output: "radio_speaker"`, `live_decoded_ms` ≥120,000 equal to the sum of ordered `live_intervals: [{start_ms, end_ms}]`; native-trace `evidence`; `speech` observation. Intervals must lie inside the session, include the turn replies and not overlap RF TX. |
| `rx.reacquisition` | `lost_ms < restored_ms <= decoded_ms` inside the session, within the declared reacquisition budget, plus a `speech` observation proving audible recovery. |
| Speech observation | `{expected_phrase, heard_phrase, intelligible: true, observer, evidence}`. First/last words must match, ignoring case/punctuation. A listener judges recognizable content; the tool does not score all interior words. RX/reply evidence kind is `speaker_observation`, TX evidence kind `remote_decode_observation`. |
| `queues` | `capture`, `engine_input`, `modem_output`, each `{capacity_samples, high_water_samples, measurement: "event_high_water"}`; positive capacity, nonnegative peak within capacity; native-trace `evidence`. Sampled maxima cannot establish these bounds. |
| `resources` | Positive integer `rss_start_bytes`, `rss_end_bytes`, `rss_peak_bytes`; final growth within the preset budget; native-trace `evidence`. These endpoints do not replace the separate performance/time-series release gate. |
| `turns` | Exactly ten entries in time order; five `panel`, five `hand`. See turn template below. Every entry retains all four session/resource IDs and has a strictly increasing TX generation. |

Each route's `requested` record contains positive integer `lease_token`,
`generation`, `route`, the request `at_ms`, and `control_command` evidence.
`applied` contains the matching three fields plus positive `boot_id`, `sequence`,
the observed `at_ms`, `source: "effective_hardware_ack"` and `effective_state`
evidence. These must agree with a fresh leased `native_control.applied_state`
record, including the contract and all inhibit acknowledgements. The native ABI
route values describe host interfaces, not invented wire/register values. Route
requests alone and stale/null applied states leave the report BLOCKED. An MCU
boot change invalidates the retained-session claim. Applied acknowledgement time
must precede physical observation, which remains separately evidenced.

This report skeleton intentionally has no fabricated observations and evaluates
as BLOCKED. Populate it from collected evidence; do not copy synthetic test
fixture measurements into a radio report.

```json
{
  "schema": "x6200.native-conversation.v2",
  "evidence_domain": "physical_radio",
  "mode": "RADE_V1",
  "operating_path": "native_voice_session",
  "artifacts": {},
  "identities": {"expected": {}, "observed": {}, "evidence": null},
  "budgets": {"fixed_before_run": null, "evidence": null},
  "session": {},
  "routes": {},
  "endpoints": {},
  "hardware_contract": {},
  "rf_observation": {},
  "reference_decoder": {},
  "rx": {"live_intervals": [], "speech": {}, "reacquisition": {}},
  "queues": {},
  "resources": {},
  "turns": []
}
```

Each turn uses this shape. Its grant ID identifies the consumed local Arm → Run
grant, not a reused session-wide permission. PTT sequences are from independent
physical observations and increase per control. The separate retained release
counter advances on raw release and never regresses across turns; timestamps reference the
observed physical edges. `last_mic_sample_ms` is the capture time of the last
accepted microphone sample, not the later time at which encoding completes.
`rx_ready_ms` denotes established usable receive routing; the live reply proves
that reception actually resumed. Expected TX phrases must be distinct.

```json
{
  "session_id": null, "engine_id": null,
  "capture_stream_id": null, "playback_stream_id": null,
  "generation": null, "bench_grant_id": null,
  "ptt": {
    "control": null, "source": "independent_physical", "raw_source_id": null,
    "release_sequence_at_press": null, "release_sequence_at_release": null,
    "max_age_ms": null,
    "press_sequence": null, "release_sequence": null, "evidence": null
  },
  "speech": {
    "expected_phrase": null, "heard_phrase": null,
    "intelligible": null, "observer": null, "evidence": null
  },
  "press_ms": null, "admit_ms": null, "rf_on_ms": null,
  "release_ms": null, "last_mic_sample_ms": null,
  "rf_off_ms": null, "rx_ready_ms": null,
  "reply": {
    "first_audio_ms": null, "last_audio_ms": null,
    "speech": {
      "expected_phrase": null, "heard_phrase": null,
      "intelligible": null, "observer": null, "evidence": null
    }
  },
  "accepted_mic_samples": null, "encoded_mic_samples": null,
  "unaccounted_samples": null, "capture_drops": null,
  "stale_audio_samples": null, "playback_underruns_before_drain": null,
  "fault_count": null, "tx_end_queues_empty": null, "evidence": null
}
```

Accepted and encoded microphone counts must be positive and equal for these
normally completed turns. Unaccounted samples, capture drops, stale samples,
pre-drain underruns and fault counts must be zero; TX queues must be empty at
completion. Rejected-start/fault-injection traces belong in the separate release
tests, not among the ten successful conversation turns. Bind every observation
to original artifacts and preserve failed runs instead of rewriting them as
successful reports after a fix.
