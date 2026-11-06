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

## What the returned failures changed

| Observation | Required test-process response |
| --- | --- |
| Working speech files did not establish reliable playback completion | Check completion acknowledgments and retain submitted/rendered counts; keep listening review separate. |
| A decoded SDR file could obscure an earlier GUI failure | Preserve the earliest runtime fault and report collection, controller, qualification and RF decode independently. |
| A successful 743 ms uncork left captures stale; delivery resumed about 30 ms later | Test operation acknowledgment and subsequent fresh valid samples as separate events, including missing recovery and release/Stop during recovery. |
| The failed device run followed about 20 seconds in RX | Exercise idle histories as well as rapid turns. An injected pause models a failure; it is not proof of real device suspension/resume. |
| Application PCM was queued while the negotiated Pulse playback reserve was only 50 ms | Require a measured buffer contract on all four streams, then test controlled servicing pauses at both actual output sinks. |
| A second full-pass RX/TX case exceeded the render queue during its final modem tail | Reserve current output-frame capacity for every resampler call, including its final flush; test tail backpressure with complete payload delivery. |
| A host run underrun was followed by a clean replay | Retain every attempt. A replay cannot remove the failure or establish scheduling margin. |
| Polling refreshed the fake packet clock | Advance fake telemetry independently of observation so stale-status tests exercise real loss of freshness. |
| An operation injection could fire during the wrong phase | Require the intended phase/state and observed injected operation; startup coverage cannot substitute for TX or RX-return coverage. |
| A 650 ms injected delay measured as 649 ms because issue and wait timers began at different points | Anchor the injected hold to the controller's first operation poll and retain separate issue, wait and acknowledgment timestamps. |
| A stale/incomplete trace or ignored fault could look complete | Bind current-session identity, immutable first fault and terminal receipt; reconcile them before accepting a result. |
| One global decode could hide a missing or stale later turn | Check the complete raw modem waveform and actual decoder output for each turn against its own reference. |
| TX loss during drain could be counted as a completed turn | Keep checking fresh TX status during drain and reject the turn before completed-turn accounting can qualify it. |
| A delayed MIC callback crossed release and valid speech was discarded in DRAIN | Close a finite indexed capture interval, drain it before EOO, and check its exact PCM against an independent input observer. |
| Unbounded fixture processes and latest-PASS selection hid failed attempts | Bound commands and child cleanup; retain immutable attempt history and block promotion after failed or unfinished attempts. |
| All deliveries paused together on a host at its memory limit, without per-case resource counters | Screen host resources before qualification, retain end counters, and keep a resource failure separate from an RF or codec claim. |
| A qualification-loop assertion bypassed bounded unexpected-fault cleanup | Exercise failed-child evidence preservation during startup and qualification, and use the same bounded cleanup path for unexpected runtime faults. |

## Contracts checked across every phase

The test oracle checks the following throughout startup, preparation, key
admission, active TX, drain, unkey, RX recovery, Stop and Exit:

* RF-disabled evaluation makes no real key request. Unexpected actual TX blocks
  the session. Enable requires completed qualification, fresh actual RX and a
  released panel PTT.
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
request a 450 ms target and one-second capacity; admission checks the actual
grant, rather than trusting the request. Capture capacity must cover 150 ms plus
two negotiated fragments. The one-second transport capacity does not expand the
existing application backlog or capture-loss limits. This buffering adds startup
and tail latency; hardware conversation latency remains unmeasured.

Controlled TX and RX servicing pauses must preserve the complete modem and
headset waveforms respectively. A below-budget grant must block keying. A short
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

## One qualification chain

1. **Software contracts.** Run two complete repetitions of the registered
   functional matrix against the shared production controller, real codec and four retained Pulse streams.
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
   recording at a common 8 kHz rate. Calibrated against 30 actual retained
   host-fixture turns, it allows at most one second of alignment lag, requires full payload
   coverage except 5 ms at resampler edges, correlation of at least 0.95,
   fitted gain from 0.24 to 0.26, and 100 ms-window normalized RMS error no
   greater than 0.35. Each turn must also decode, with at most one 120 ms block
   plus 20 ms of duration difference. Truncated, swapped and altered payload
   negatives must fail. These are continuity/content checks; human speech
   intelligibility remains unassessed by them.
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
scheduling. Its null sinks do not reproduce the physical ALSA driver, USB bus,
Loop110 wireless link, baseband MODEM routing or RF. RF-disabled evaluation
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
