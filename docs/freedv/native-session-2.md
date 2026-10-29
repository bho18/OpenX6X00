# Session 2: native receive routing, microphone source and recovery

This is the follow-up evaluation after Session 1. Select the exact route/control
experiments from the returned read-only topology before deployment. Do not guess
baseband registers or manipulate them from SSH. Use the existing image where it
can answer the question; group any required diagnostic changes into one update.

## Receive independence

Keep an identifiable, steady off-air signal under simultaneous SDR observation.
Its program/tone must differ from the local 735 Hz tone. Capture the full short
case interval where the updated image supports it; retain raw left/right channels.

| Experiment | Duration | Purpose |
|---|---:|---|
| R0 M0 P0, no generated audio | 10 s | Identify the actual RF input and baseline noise |
| R1 M0 P0, local tone, normal speaker level | 20 s | Determine whether RF audio survives beneath local playback |
| Same route with zero speaker level | 20 s | Measure volume dependence; this alone does not identify acoustic versus electrical coupling |
| Supported Sum Mono alternative | 8 s | Complete the untested summing path and compare raw channels |
| Supported Reverse Stereo | 8 s, only if inventory shows behavior beyond swapping the recorded channels | Avoid repeating an already observed channel swap |

Keep mixer is already Mix Mono in the returned snapshots, and Stereo has been
tested. Do not rerun them merely to get another dummy-load beep capture. A new
identifiable RF input or changed speaker condition is a distinct experiment.

Before attaching an antenna, establish the exact speaker profile is unkeyed into
the dummy load and confirm it locally. After restoration, attach an antenna only
for that qualified receive case. Reconnect the dummy load before any RF Arm.
If speaker level must change, Close, change it normally, then Acquire and qualify
the exact profile again; restore the user's original setting afterward.

Compare RF program preservation, playback leakage relative to the no-playback
reference, raw channels, continuity and all errors. Quiet capture without an RF
reference does not pass independence. Neither does a speaker that sounds right
while the decoder's capture contains its own output.

Review the real PulseAudio source/sink/channel mapping and loopback modules,
ALSA input selections, and documented baseband RECORD/MODEM/speaker behavior.
Only expose another routing control if the topology supports it and the existing
radio process can save/restore its exact transient state under ownership. Unsupported
or undocumented paths remain explicit limitations. Do not infer all native routes
have failed from these two mixer results or silently switch to external audio.

## Deliberate built-in microphone isolation

Use the dummy load. Save current ordinary settings, unplug the hand mic and keep
software control local. First verify ordinary built-in speech on RF with explicit
built-in selection, USB and low power for approximately three seconds. Release
PTT and verify RF-off.

Then acquire diagnostics, select Built-in mic / Existing mono / Keep mixer and
run Route R1 M1 P0 for eight seconds with local Arm -> Run. Speak "Radio mic,
handset disconnected" immediately near the radio. Save capture and SDR recordings.
If unresolved, repeat the alternative R1 M0 P1 route. Reconnect the handset and
restore original ordinary selections after safe release.

This is a new connection condition, unlike the completed tests with the handset
connected. If ordinary built-in RF speech works but Linux capture does not,
record a diagnostic capture-route limitation. If ordinary built-in operation
also fails, investigate normal configuration/hardware separately. Live built-in
RADE TX is only attempted after its own exact-profile capture and functional Stop
checks pass; unresolved built-in support does not prevent hand-mic-first planning.

## Physical PTT and controlled restart

After predictable software recovery, use the dummy load and SDR:

1. Outside diagnostic ownership, observe ordinary GUI/front-panel and hand-mic
   PTT press/release at low power. Note analog speech at first/last transitions.
2. On the demonstrated diagnostic TX profile, run a short Manual Stop test.
   Briefly hold physical hand-mic PTT, request Stop and confirm restoration stays
   blocked. Release promptly (about one second after Stop); fresh RX and exact
   restoration must follow. The software time limit cannot override held PTT.
3. Export and copy all evidence before restart testing. Verify the service script
   exists, then perform stop/start with physical PTT released:

   ```sh
   ls -l /etc/init.d/S95gui
   /etc/init.d/S95gui stop
   /etc/init.d/S95gui start
   ```

4. Verify no case or arming resumes and ordinary RX recovers. Only after that
   behavior is predictable, perform a separate brief held-PTT restart observation.
   Observe RF/analog audio during process absence and startup; release promptly.
   Startup should wait for fresh RX before ordinary route restoration.

Use the service, not killing only x6100_gui: its supervisor restarts it. This
characterizes deliberate service stop/start and hardware-held PTT; it does not
establish arbitrary crash behavior or a software guarantee against physical PTT.

## Close the evaluation

Export a consolidated bundle containing the exact image/build manifest, case
definitions, inventory, counters, selected state comparisons, private WAVs,
operator observations and SDR references. Evaluate these gates separately:

- Hand-mic RADE TX, intelligible reference-decoded speech and analog leakage.
- Speaker output plus independent RF capture during playback.
- Built-in capture and any qualified encoding route.
- Software recovery, physical PTT, restart and numerical external RF timing.
- Short-turn processing, audio integrity, bounded queues and resource trends.

Use PASS / FAIL / INCONCLUSIVE with the actual evidence and tested scope. Keep
the original 70% margin finding separate. Controlled RF receive interoperability
still requires a peer transmitter or suitable signal source; the SDR is receive-only.

Commit sanitized findings to bho18/OpenX6X00. Recordings/IQ stay out of Git; no
automatic uploads, upstream submissions, production UI or frequency markers.
