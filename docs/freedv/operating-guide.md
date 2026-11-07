# Native FreeDV / RADE V1: current operating and qualification guide

For the current **USB headset FreeDV Beta**, use the
[USB operating guide](usb-rf-end-to-end.md). This document covers the separate
native radio-microphone/speaker path and its remaining qualification work.

**Native release is blocked. No qualified native production profile is
registered and the radio-microphone/speaker workflow is not exposed as a
finished APP → Modes entry.** Do not turn on native voice through an unreviewed
profile or interpret a host test as radio acceptance.

The current spare-card round is receive-only endpoint/PTT availability mapping
under [the candidate instructions](candidate-test.md). Legacy recorder/gain/MODEM
trials are withdrawn. Host simulation exercises the retained coordinator, but
the stock X6200 backend cannot admit native TX without the measured endpoint map,
raw PTT, effective route acknowledgement and independent native lease described
in [the qualification protocol](native-half-duplex-qualification.md).

The working `.2` card and recovery path must remain intact. Hardware work is
paused. Software tools neither discover nor control a radio automatically.

The current software work starts at `21df019884da3e8cdc1b4521a4b4f8a51ca210e1`.
The supplied checkout did not contain the later persistent-engine/preferences/
reference-harness workspace described in the plan. The new retained engine,
asynchronous coordinator, reusable audio adapter, preferences and bookmarks are
built alongside the existing diagnostic implementation. See
[implementation status](implementation-status.md) for the production binding,
application integration, [current software verification](software-verification-2026-11-02.md)
and remaining physical gates. Existing evidence stays attached to its original image,
route, binary and procedure.

Use the current workflow: **read-only inventory → receive-only endpoint/input
mapping → resolve the lower-layer contract → later bounded native validation**.
The legacy case matrix below is retained as coverage history, not a grant to
transmit or repeat gain/order trials.
[The test kit](../../tools/freedv/qualification/README.md) gives executable
commands. The [acceptance matrix](acceptance-to-tool-matrix.md) lists 54 cases,
required equipment, recordings, measurements and assessment criteria. All case
results bind the complete build and profile manifests, not only a mode label.
Missing prerequisites are BLOCKED. Missing or inadequate evidence is
INCONCLUSIVE. Neither is PASS. Synthetic engine checks do not pass speech gates.

USB and USB-DIG are both eligible local profiles. [Matched comparisons](matched-profiles.md)
must select one *complete* passing profile: prefer USB-DIG if both pass; select
the sole complete passing profile if only one passes; otherwise keep release
blocked. Record upper-sideband orientation separately. The peer's menu label
need not match. Never combine successful pieces of different profiles or switch
profiles during normal turns.

## Mode operation after qualification

A qualified build exposes **APP → Modes → FreeDV / RADE V1** on compatible
hardware. Entry preserves the current frequency/VFO, prepares once, then starts
receiving automatically. The panel displays Preparing, Receiving/Synchronized,
Transmitting, Finishing, Fault and held/unknown PTT recovery, microphone activity,
frequency and Stop/Exit. There is no production Acquire/Arm/Run sequence.

Use the main tuning knob and local band/VFO controls in RX. Recall is explicit:
choose a bookmark and Recall, or edit its Hz/label and Save; New starts with the
current frequency and Delete removes the selected entry. The 17 GUI-derived HF
seeds are one-time defaults. Manual tuning never snaps back. The selected
qualified profile governs mode regardless of stored bookmark USB/USB-DIG labels.
Frequency/band/VFO/memory changes are rejected during TX, finishing and recovery.
Volume is a permitted local RX control. Remote TX/microphone and competing audio
applications are excluded.

Press either independently qualified physical PTT to transmit hand-mic speech;
release finishes accepted speech, EOO and the silence tail, drains and returns
to RX. Timeout defaults to **180 seconds**, adjustable in RX to **30–600 seconds**.
Timeout requires every source to release before another turn. Stop cancels
immediately and clears software TX; physically held PTT may still hold RF and
is shown separately. Release all controls and establish fresh RX before analog
routing restoration or exit. Selecting another app requests asynchronous exit;
its opening waits for shutdown. Current frequency, VFO, ordinary microphone
preferences and permitted volume changes survive exit. Persisted preferences
never resume TX. 700E, RADE V2, full duplex, remote microphone/PTT and unqualified
built-in microphones are outside this mode.

## Current qualification operation

For now, physical experiments use the existing diagnostic image validation and
[local Session 1](native-session-1.md)/[Session 2](native-session-2.md) recovery
controls. Acquisition/Arm/Run belong to diagnostic testing. They are not the
intended production user flow. Existing diagnostic cases remain bounded to ten
seconds and begin at 1 W into a dummy load with suitable attenuation.

Before operating any candidate, inventory the X6200, hand microphone, working
and candidate cards, recovery path, dummy load/coupling/attenuation, continuous
IQ/demodulating receive SDR, reference computer, official GUI, controlled RF
transmitter/source and event/RF timing setup. A receive-only SDR can validate TX
but cannot supply the controlled signal needed to qualify RX with speaker on.

Stop must independently clear software TX and cancel/flush audio. Held or
unknown physical PTT remains visible and retains ownership. Release all physical
controls and require fresh observed RX before restoring analog routing or
leaving. If recovery cannot establish RX, preserve the evidence and use the
working card/recovery procedure; never bypass the ownership guard. The existing
software startup guard does not establish independent hand-mic release while
MODEM is active; that remains a release blocker.

[Official GUI interoperability](official-gui.md) requires both directions,
8/44.1/48 kHz boundaries, cold/warm turns and listening assessment. [Independent
RF timing](timing.md) must bound initiating event to actual RF cessation,
including uncertainty, to at most 500 ms. Software timestamps alone cannot pass.
There is no finished bidirectional native-mode claim until every required gate
passes on the identified candidate.
