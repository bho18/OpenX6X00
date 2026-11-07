# USB half-duplex qualification strategy

The target is a conversation using the USB headset microphone and earphones,
Linux RADE processing, the radio audio endpoints and the on-device panel PTT.
One direction owns processing at a time. A successful component experiment is
retained with its original scope; promotion requires evidence for the complete
controller and lifecycle being shipped.

The completed USB recording, recorded codec round trip and short live headset
turns remain accepted. Do not ask the operator to repeat them after an unrelated
controller change. They establish the tested hardware/codec milestones, not the
new GUI's RF routing, release behavior or durability. Stock baseband limitations
and the blocked native hand-mic contract remain separate.

## Operational startup and normal UI

The operational Beta uses startup contract **4** and output contract **1**.
Its initial readiness receipt describes actual prepared streams, negotiated
buffers, fresh capture/RX, configuration revision and a released panel PTT.
Readiness is separate from TX eligibility: out-of-band RX is supported with
TX disarmed. It does not run the legacy fixed idle and three synthetic turns.
Diagnostic qualification remains `NOT_REQUESTED`, target 0 and complete false.
The initial receipt is retained verbatim in the trace and immutable readiness
sidecar, bound to the same session/build/source as terminal completion.

The original **61 diagnostic controller cases** retain startup contract 3;
eight operational cases add automatic readiness, held startup PTT, RX retuning,
receive-only admission, graceful navigation during TX, explicit abort, USB
loss and a held 30-second TX timeout (including continued TX beyond the old
20-second diagnostic timeout). They use the production controller, four actual Pulse streams and codec,
with simulated radio control. Navigation closes a finite speech frontier while
the actual PTT can remain held; it must not fabricate a release counter.
Graceful navigation has done/fault0 and completion reason `navigation`;
physical release, explicit Stop and faults retain distinct evidence. Existing
capture and playback-tail timing bounds remain unchanged.

The operational retune case starts a known public modem signal only after the
new RX configuration has been admitted. It requires actual synthesis, complete
decoded speech at the private-Pulse USB headset monitor, and unchanged waveform
thresholds against the retained decoded tap. The artifacts are regraded at
promotion. This checks the new route/reset boundary in software; it does not
claim a live over-the-air reception on the radio.

UI regressions cover the acknowledgement notice and durable dismissal, normal
waterfall and tuning, lower Mode/Mic/Audio/standard Power/Exit buttons, automatic
PTT admission, and navigation from GEN/Apps/other page/mode paths. Codec creation
counts remain unchanged across tuning/popups/direction changes, and reopening
engines checks that successful process warm-up is reused. Device pairing is
catalogued as one physical USB device with both input/output; reconnect cannot
silently select another device.

Before freezing a full campaign, run the collector/kit, campaign gate and all
synthetic image tests. Their fixtures use the current complete case registry,
including operational records. A narrow debug run is useful for a new case,
but release still requires **every case twice**, unchanged source/fixture/build
identities, complete immutable history and all application builds. The
operator's next task is a known RADE peer receive and alternating replies,
including normal exits; accepted recording/codec/SDR TX component work is not
repeated as a startup ritual.

## What the returned failures changed

| Observation | Required test-process response |
| --- | --- |
| Working speech files did not establish reliable playback completion | Check completion acknowledgments and retain submitted/rendered counts; keep listening review separate. |
| A decoded SDR file could obscure an earlier GUI failure | Preserve the earliest runtime fault and report collection, controller, qualification and RF decode independently. |
| A successful 743 ms uncork left captures stale; delivery resumed about 30 ms later | Test operation acknowledgment and subsequent fresh valid samples as separate events, including missing recovery and release/Stop during recovery. |
| The failed device run followed about 20 seconds in RX | Exercise idle histories as well as rapid turns. An injected pause models a failure; it is not proof of real device suspension/resume. |
| Application PCM was queued while the negotiated Pulse playback reserve was only 50 ms | Require a measured buffer contract on all four streams, then test controlled servicing pauses at both actual output sinks. |
| The `usb-qualification-20261106.1` device run stopped before audio or TX: its playback grant provided only 225 ms against the 300 ms requirement | Correct the Pulse request domain. With `PA_STREAM_ADJUST_LATENCY`, the requested 450 ms covered both device and client latency; the prior host fixture's 50 ms device cap hid the insufficient client reserve. Reproduce the unchanged binary's exact grant with ordinary uncapped sinks, and require the corrected request to pass that profile. Keep the 300 ms admission guard. |
| Correcting the client-buffer domain alone exposed an underrun on a default sink with large backend requests | Keep the 450 ms client reserve and request the documented 20 ms backend service cadence with playback `PA_STREAM_EARLY_REQUESTS`. Preserve the failed focused run and require the complete modem/headset output in the retained servicing-pause screens. A healthy callback clock alone cannot excuse a payload underrun. |
| A complete recorded modem waveform continued leaving a paced backend after the modeled RF gate cleared | Treat protocol drain acknowledgment and backend consumption separately. Obtain fresh timing after drain, wait conservatively for reported queued output, and require the independently paced FIFO's complete signal to remain inside the modeled RF-on interval. A waveform recorded after RF clears cannot qualify the turn. |
| A second full-pass RX/TX case exceeded the render queue during its final modem tail | Reserve current output-frame capacity for every resampler call, including its final flush; test tail backpressure with complete payload delivery. |
| A host run underrun was followed by a clean replay | Retain every attempt. A replay cannot remove the failure or establish scheduling margin. |
| Polling refreshed the fake packet clock | Advance fake telemetry independently of observation so stale-status tests exercise real loss of freshness. |
| An operation injection could fire during the wrong phase | Require the intended phase/state and observed injected operation; startup coverage cannot substitute for TX or RX-return coverage. |
| A release-interleaving fixture held the Pulse lock while waiting for a coordinator that needed it | Keep synchronization hooks nonblocking under the audio lock, bound fixture execution and retain the deadlock as a test-infrastructure failure. |
| Unequal 48 kHz/full-scale and 44.1 kHz/quarter-scale decoder inputs produced different synthesis counts despite a preserved waveform | Require full-waveform preservation first, then compare against an independently generated control with matched transport, leading context and decoder phase; retain the original unequal-domain baseline as diagnostic evidence. |
| A 650 ms injected delay measured as 649 ms because issue and wait timers began at different points | Anchor the injected hold to the controller's first operation poll and retain separate issue, wait and acknowledgment timestamps. |
| A stale/incomplete trace or ignored fault could look complete | Bind current-session identity, immutable first fault and terminal receipt; reconcile them before accepting a result. |
| One global decode could hide a missing or stale later turn | Check the complete raw modem waveform and actual decoder output for each turn against its own reference. |
| TX loss during drain could be counted as a completed turn | Keep checking fresh TX status during drain and reject the turn before completed-turn accounting can qualify it. |
| A delayed MIC callback crossed release and valid speech was discarded in DRAIN | Close a finite indexed capture interval, drain it before EOO, and check its exact PCM against an independent input observer. |
| A release-window upper bound counted a verified capture-pipeline tail as excess active-window input | Keep the lower bound on total accepted frames, apply the unchanged upper bound only before the verified tail, and retain exact source-frontier/cutoff conservation. |
| Unbounded fixture processes and latest-PASS selection hid failed attempts | Bound commands and child cleanup; retain immutable attempt history and block promotion after failed or unfinished attempts. |
| All deliveries paused together on a host at its memory limit, without per-case resource counters | Screen host resources before qualification, retain end counters, and keep a resource failure separate from an RF or codec claim. |
| A qualification-loop assertion bypassed bounded unexpected-fault cleanup | Exercise failed-child evidence preservation during startup and qualification, and use the same bounded cleanup path for unexpected runtime faults. |

## Contracts checked across every phase

The test oracle checks the following throughout startup, preparation, key
admission, active TX, drain, unkey, RX recovery, Stop and Exit:

* RF-disabled evaluation makes no real key request. Unexpected actual TX blocks
  the diagnostic session. Legacy diagnostic Enable requires completed
  qualification, fresh actual RX and released panel PTT. Operational contract 4
  uses actual startup/configuration readiness and TX eligibility instead.
* A key request requires the current valid capture generation, new valid MIC
  and radio RX delivery after preparation, unchanged endpoints, fresh status,
  no protection fault and the same retained panel-release sequence. A release
  followed by a press must still cancel the old turn.
* Stop fences future admission immediately. An in-flight key completion must
  receive compensating OFF; it cannot silently become a new turn.
* Only active-direction samples enter the codec. Each accepted TX turn has one
  end-of-over sequence, a complete ordered modem payload and bounded drain.
  Old-turn samples cannot enter the next turn or the earphones.
  Normal release first closes a finite microphone capture interval; Stop still
  fences processing and RF admission immediately.
* Normal release keeps TX asserted through protocol drain and a fresh,
  asynchronously obtained playback-backend timing interval. Invalid or missing
  timing blocks turn completion. Stop still requests OFF immediately; waiting
  for a complete tail must never delay Stop or allow a later callback to rekey.
* Return/restoration requires completed control work and a newer actual RX
  packet. Software clearing, cached control values and a stopped display are
  different observations. Aggregate TX is not raw hand-mic PTT or an independent
  measurement of antenna RF cessation.
* Endpoint/server loss, missing acknowledgments and failed recovery latch a
  specific fault. Reconnection must not select another device or resume TX.
* A session has a unique identity, its exact source/binary/fixture identities,
  and an explicit terminal result. Missing, truncated or mismatched evidence
  cannot qualify it.

Current controller timing boundaries are 1,000 ms per preparation operation,
1,500 ms for post-preparation capture recovery, 750 ms for required active
capture loss and 1,500 ms for the fresh RX acknowledgment after recovery.
These are software policy bounds, not measured RF-off guarantees. Exercise
both sides of each bound. Do not extend them to make a failing test pass.
The screened I/O servicing pause is 150 ms. Each playback stream must provide
at least 300 ms of usable negotiated reserve (`tlength - minreq`) and prebuffer:
150 ms of servicing delay, a 120 ms codec block and 30 ms of margin. Both outputs
request a 450 ms **client-stream** target and one-second client capacity, without
`PA_STREAM_ADJUST_LATENCY` on playback. Playback uses
`PA_STREAM_EARLY_REQUESTS` with a 20 ms minimum request to ask for a shorter
backend service cadence while retaining the client buffer. This avoids tying
backend bursts to the large client target when the codec produces 120 ms
blocks. The device's own queue is a separate latency domain; a fixed-latency
sink can grant a different cadence or latency. Admission checks the actual
client grant, rather than trusting the request, and completion uses fresh
reported backend timing. Capture flags remain unchanged. Capture capacity must
cover 150 ms plus two negotiated fragments.
The one-second transport capacity does not expand the
existing application backlog or capture-loss limits. This buffering adds startup
and tail latency; hardware conversation latency remains unmeasured.

Pulse protocol drain acknowledgment does not establish that a DAC or device
FIFO is empty. After acknowledgment, the controller requests fresh modem-stream
timing and conservatively waits for the reported sink latency, transport latency
and any positive write-minus-read remainder. The wait uses a monotonic clock,
remains within the existing three-second total drain deadline, and continues
checking fresh TX/protection status. A missing timing reply retains the existing
one-second operation bound. Invalid timing, a deadline overrun or lost TX status
faults the turn. The typed `x6200.usb.playback-tail.v1` receipt describes this
**Pulse-reported backend interval**, not independently measured antenna RF or a
physical DAC acknowledgment.

Controlled TX and RX servicing pauses must preserve the complete modem and
headset waveforms respectively. The fixed-pipe backend also checks output
through a 150 ms TX servicing pause. A below-budget grant must block keying. A short
200 ms accepted turn checks underfilled prebuffer, EOO, drain and RX return;
it does not count as a complete intelligible speech test. Explicit acknowledged
prebuffer/trigger operations finish such short payloads without relaxing the
drain deadline or suppressing an underrun.

Queue accounting must use the rate at each boundary. Available 44.1 kHz output
frames are not interchangeable with 48 kHz codec input frames, and a resampler
can release retained history at its final flush. Recheck capacity after each
producer step and reserve its bounded output before taking more codec data.
Backpressure must preserve order and the complete tail without enlarging queue
limits or discarding samples.

Rate accounting excludes only declared RF-disabled preparation intervals and
matching frames; prior or active-delivery gaps remain measured. At least three
seconds of accumulated steady measurement and the existing ±5% rate gate are
required; an acceptable average cannot excuse a payload underrun.

A separate source-accounting guard applies to active TX windows of at least
750 ms, before emitting EOO or counting success. Accepted microphone input
must be at least 99% of the nominal 48 kHz frames for that window, minus two
actual negotiated microphone fragments for callback boundaries. This detects
gross silent deletion before encoding; it does not prove delivery of every
sample. It is independent of the global ±5% rate gate.

Release also requires an indexed capture-close contract. Fence the transition
against the Pulse callback, obtain fresh capture timing asynchronously, and
freeze a finite raw capture-production frontier. That frontier includes the
reported source/in-flight and transport pipeline, without the playback-latency
correction used for monitor sources. It defines a bounded capture tail, not a
sample-perfect physical PTT timestamp or a measured wireless-headset delay.
Hold closing data in the existing bounded queue until the cutoff is known,
split its final chunk, and require contiguous source indices and exact interval
conservation before EOO. Samples beyond the frozen cutoff cannot replace missing
earlier samples. Invalid timing, index gaps or incomplete closure block the turn.
Closure stays within the existing 750 ms capture bound and the same total
three-second drain deadline; the wall-time source screen remains independent.
This candidate also conservatively rejects a reported frontier more than
24,000 frames (500 ms) ahead of the current MIC read cursor. That admission
horizon is separate from queue occupancy and is not a measured hardware limit.

Two deterministic checks release during a callback and during a 150 ms delivery
hold. Compare the exact accepted microphone interval with an independent raw
input observer, exclude post-cutoff sentinel data, and check the complete modem
payload, one EOO and fresh RX. The 300 ms valid-data-deletion negative must still
fail even if later microphone samples are available.

The host `capture-budget.v2` oracle keeps the unchanged 99%-minus-two-fragments
lower bound on total accepted frames. Its unchanged 101%-plus-two-fragments
upper bound applies to `pre_fence = total_accepted - verified_pipeline_tail`.
Held pre-fence data can arrive through the valid capture pipeline, so the lower
bound must remain on the total. The tail must come from the validated raw
source/transport frontier; a duration estimate alone cannot exempt frames.
Exact source indices, cutoff/fence conservation, no gaps and all existing
deadlines remain required. This corrects the test comparison window, not the
runtime guard, tolerance or deadline.

The deterministic `capture-close-tail120` check demonstrates the distinction:
87,392 accepted frames exceed the old 84,530 active-window ceiling, while
81,630 pre-fence frames remain below that same ceiling. The 5,762-frame tail
comes from the reported 120,000 µs source interval plus 28 µs transport. The
independent source observer matches the entire accepted interval, 47,119
post-cutoff sentinel frames are excluded, both matched decoders produce 13
synthesis blocks, and one EOO returns to fresh RX. Diagnostic regrading of 149
historical raw closures leaves their original attempt records/status unchanged
and supplies no promotion credit to the new source.

## One qualification chain

1. **Software contracts.** Run two complete repetitions of the registered
   functional matrix against the shared production controller, real codec and four retained Pulse streams.
   First prove that the release injector can post a retained release while the
   modeled coordinator is blocked on the audio lock. Separately force real
   controller failures at startup and during qualification, and require both
   children to exit failed with their original fault and terminal evidence.
   These infrastructure checks do not count as passing functional cases.
   Inject delayed/rejected/missing acknowledgments, capture holes and starvation,
   device/server loss, retained releases, blocked control and Stop at phase
   boundaries. Expected faults pass only when their reason and admission/OFF
   consequences match the oracle. A process exiting successfully is insufficient.
2. **Lifecycle and durability.** Run the declared durability profile in fresh
   processes, with its repetition and turn counts pinned before execution.
   The current screen requires 20 turns with the same retained session (the first lasting eight
   seconds), ten
   close/reopen cycles in one process, and a 20-second idle case. Include warm
   turns and failed recovery. The private Pulse idle fixture must observe
   suspension and resume, rather than infer them from elapsed time. Track
   per-turn source generation, EOO,
   actual modem-sink output/decode and RX return, rather than checking only the
   final cumulative counter. Check stream/codec creations, worker threads,
   descriptors, bounded queues and memory trends after teardown. A fixed count
   is a regression screen, not an endurance or thermal rating. The raw modem
   waveform oracle compares each 48 kHz reference with its actual 44.1 kHz sink
   recording at a common 8 kHz rate. Derive its alignment bound from actual
   negotiated client `tlength`, the greater of reported or configured backend
   latency, and the existing 500 ms servicing/codec/crop allowance. The
   450 ms client/50 ms backend profile retains its one-second bound. The gate
   recomputes that bound from raw receipts; an arbitrary larger horizon cannot
   qualify a recording. Require full payload
   coverage except 5 ms at resampler edges, correlation of at least 0.95,
   fitted gain from 0.24 to 0.26, and 100 ms-window normalized RMS error no
   greater than 0.35. This full-waveform gate is primary. Each turn must also
   decode against a matched-transport control independently generated from its
   48 kHz reference through the production 44.1 kHz resampler and quarter-gain
   operation, preserving leading context and decoder phase. Do not use the
   observed sink payload itself as the control. The same one-120 ms-block plus
   20 ms decoded-duration allowance applies; no tolerance is widened.

   Retain the original unequal-domain 48 kHz decoder comparison in the
   investigation record. Each new turn retains its 48 kHz source, matched
   control, actual sink input, transport/library identities, alignment/gain
   and decoder results.
   A retained CI turn demonstrated why: the complete actual waveform matched
   the production transport control with zero RMS error, and both matched
   44.1 kHz decoders produced six synthesis blocks, while the unequal-domain
   48 kHz baseline produced eight. That observation establishes no missing PCM
   in that fixture; it does not establish physical first-word acquisition.
   The 240 ms erasure negative still fails the unchanged waveform threshold.
   Save each turn's partial waveform/source proof before downstream decoder
   checks so a later failure does not erase it. Truncated, swapped and altered
   payload negatives must fail. These are transport/content checks; human
   speech intelligibility and physical first/last-word completeness remain
   unassessed by them.
   Retain complete output checks on the capped sinks and the fixed-pipe
   backend. For the independently paced FIFO, every nonzero transported sample,
   including EOO, must be consumed while the modeled RF gate is on, and the full
   finite zero tail's deadline must precede RF clear. Keep the flags-only
   negative that records a complete waveform after modeled RF clear.
   Missing or invalid backend timing and Stop before the reply or during the
   wait must retain their expected fault, clear/RX confirmation and no-rekey
   outcome. These are software/backend-model checks, not physical USB or RF
   measurements. Rewindable Pulse monitor recordings remain diagnostic evidence;
   do not waive missing prefixes or infer full sink/DAC delivery from them.
3. **Radio transition check, RF disabled.** Run the same retained controller and
   real endpoints on the radio. Keep the independent RF gate disabled. Record
   Pulse/server/module configuration, effective negotiated buffers and endpoint
   identities, scheduling/capture gaps and preparation/recovery timings. An idle
   case covers the observed approximately 20-second RX history before the
   first turn, followed by short warm turns. Report observed suspension separately from an injected-delay test. No speaking or
   listening round is needed.
4. **Integrated RF trial.** Only after the preceding gates pass, use the spare
   card, dummy load, SDR and panel PTT. Record one complete phrase, normal
   release/RX return, and a separate Stop-while-held turn. Assess the decoded
   first/last words and RF release behavior. Internal modem audio and decoder
   synchronization alone do not establish this result.

The host fixture models audio rates, state/control ordering and adversarial
scheduling. Its null sinks and paced FIFO do not reproduce the physical ALSA
driver, USB bus, Loop110 wireless link, baseband MODEM routing or RF. RF-disabled evaluation
also does not exercise real MODEM-on admission. RF receive qualification needs
a transmitting peer; an SDR receiver alone supplies initial TX evidence.

## Reproduce the software qualification

Run from the repository root with the pinned host dependencies, LVGL submodule
and host codec build prepared. Commit the source first; `git status --porcelain`
must print nothing. Record the full commit ID. Replace the example paths and
build ID below with the retained release inputs; `--work` must be outside the
source checkout.

```sh
git status --porcelain
git rev-parse HEAD
python3 tools/freedv/build_native_development.py \
  --variant host-native-validation \
  --usb-test \
  --work /path/to/retained-usb-validation \
  --codec /path/to/pinned-host-codec-build \
  --codec-fixture /path/to/pinned-fixtures/rade-modem.raw \
  --usb-resource-reclaim-root /path/to/task-owned-build-cache \
  --build-id usb-rf-development-example
```

This builds the host GUI/controller and actual codec integration, then runs the
complete `qualification` profile automatically, including the declared full
repetitions and lifecycle cases. It does not access or qualify radio hardware.
The optional reclaim root names only this task's cache files; omit it when
headroom is already sufficient. It never deletes evidence or changes file data.
CI/debug `regression`, `durability` or selected-case runs are useful diagnostics;
they do not replace the full qualifying campaign and cannot promote an image.

CI dependency setup is bounded separately from controller qualification. Both
Linux jobs run the shared Ubuntu-source helper's six fixture tests before APT.
The helper changes only explicit Azure Ubuntu mirror URI tokens to the canonical
HTTPS archive; repository fields and signature verification remain unchanged.
HTTP and HTTPS acquisition timeouts are 30 seconds, with two retries. A mirror
or installation failure remains an infrastructure failure, not a controller
pass or permission to omit the campaign.

Keep the same persistent work/history location across attempts. Preserve its
`attempts/`, `build-campaigns/` and `usb-campaigns/` directories, including failed
and interrupted attempts. `build-record.json` is a derived latest record, not a
replacement for that history. Do not remove failed attempts, reset the history
root or choose a new label to obtain a green result. Resolve the recorded cause
through a reviewed source/configuration change and retain the original failure.

## Durable results and promotion

The full software campaign also has a host resource contract. It requires a
known bounded cgroup, at least 4 GiB of starting memory headroom, at least 1 GiB
remaining at completion, and no new memory-limit, OOM or direct-reclaim events
after the preflight baseline. Before the baseline, explicitly named task-owned
files may receive clean-cache eviction hints; their bytes, hashes and attempt
history remain intact. Record the roots, limits, counters and deltas. Unknown or
pressured environments cannot qualify the image. This is a host test profile,
not an X6200 RAM requirement or a real-time scheduling guarantee. It cannot
override an actual controller failure. CI regression remains nonpromotable.

Declare profiles, cases, repetitions, fixtures and identities before a campaign
starts. Store each attempt and its evidence immutably. The matrix records
`PASS`, `FAIL`, `TIMEOUT` or `INTERRUPTED`; a debug subset never qualifies a
release. The campaign records `RUNNING`, `BLOCKED` or `PASS` separately from
individual attempts. Failed or unfinished same-identity attempts, missing
required repetitions or altered evidence prevent promotion. A resolution must
reference the original failure and a reviewed source/configuration change;
launching another run is not a resolution.

Keep per-phase maxima, actual negotiated buffers and failure timing alongside
averages. Preserve a host underrun even if a later run is clean, and keep
software correctness, device timing and hardware RF claims separate. Build and
image checks must bind the qualifying campaign to the exact shipped binaries,
source tree, helper kit and fixtures. Verify the GitHub downloads before asking
for the next radio trial.

The replacement image and matching kit require
`usb_output_contract_version: 1` in addition to startup contract version 4
for operational mode (version 3 remains supported for diagnostic mode).
Session identity, raw trace/completion hashes and backend-tail receipts must
agree before promotion. Older version-3 sessions without this output marker
remain diagnostic evidence; they do not qualify the corrected output contract.
The `usb-qualification-20261106.1` release remains on HOLD for its reproduced
startup regression. Use its verified replacement and matching kit for the next
integrated radio transition check.

For a radio session, retain the first fault plus an explicit terminal summary,
including session identity, turns, clear/RX confirmation, dropped/truncated
records and artifact completeness. Bounded trace/audio storage must declare
its limit; it must not silently lose a late fault or the terminal result. A
longer campaign needs per-turn artifacts or a bounded incident window rather
than the first 30 seconds of PCM. Do not turn an incomplete recording into a
full-path pass.

The controller terminal receipt covers audio/controller teardown and its RX
observations. It does not certify that the GUI restored every radio setting
or that antenna RF ceased. Settings restoration and RF cessation remain
hardware-trial observations.

Detailed traces and PCM remain under `/tmp`; collect them before a normal
reboot. Startup, first-fault and completion metadata are also written outside
the audio/control threads to `/mnt/freedv-usb-incidents`. A rotating ring retains
the last 16 committed sessions, with immutable records of at most 16 KiB and
explicit prior-session interruption records. Atomic writes and filesystem sync
reduce partial-record loss; they do not guarantee survival of an arbitrary
power cut or faulty storage. Storage failure blocks readiness.

If persistent TX requires power-off, recovery takes priority. The collector
can retain the persistent incident metadata afterward, but missing original
trace/audio evidence leaves the trial blocked. Persistent metadata cannot
qualify the lost session. Longer or unattended radio campaigns still need
per-turn payload preservation beyond the bounded short-trial taps. Do not add
a live RF soak automatically to this short operator procedure.

The operator should receive one image-bound guide and one result ZIP containing
all attempted stages, including failures. Previously accepted component results
stay in the evidence history. The next operator task is the integrated
controller/RF scenario, not another round of basic audio experiments.
