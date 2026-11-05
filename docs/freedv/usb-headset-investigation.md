# USB headset investigation

The candidate uses a USB headset microphone and earphones, the radio's side PTT,
and Linux processing on the radio. It is a separate topology from the original
native hand-microphone/speaker design. No end-to-end USB-headset RF result has
been obtained, and stock baseband compatibility remains unqualified.

The current `freedv-native-development-20261105.2` image includes the
`snd-usb-audio` module and PulseAudio udev discovery. This supports investigating
a class-compliant USB audio device; it does not establish that a particular
headset exposes usable microphone and playback endpoints. Prefer a low-power,
bidirectional USB audio device connected directly to the radio's HOST port.
The manufacturer does not support external USB hubs. Device model and actual
enumeration must be recorded before implementation choices are fixed.

## Proposed audio and control paths

Half-duplex conversation is the primary use case and acceptance target: receive
and decode, then transmit and encode, then return to receive. Only one codec
direction is active at a time. Keeping streams and codec contexts allocated
does not require processing or transmitting both directions concurrently.
The earlier simultaneous USB capture/playback probe tested endpoint capability;
it does not define the operating model or require full-duplex RF.

| Direction | Pinned capture endpoint | Processing | Pinned playback endpoint |
| --- | --- | --- | --- |
| Receive | Physical radio demodulated-audio source | Linux RADE decoder | USB headset earphone sink |
| Transmit | USB headset microphone source | Linux RADE encoder | Physical radio modem-audio sink |

These are four explicit endpoints. Neither PulseAudio defaults nor numeric ALSA
card order identify them reliably. The intended implementation retains a capture
stream for each source, a playback stream for each sink, and one codec engine
throughout the conversation. Direction changes select and fence the active
streams, without reopening devices or admitting old-turn samples. Keep the
existing 44.1 kHz application PCM boundary initially; the engine already converts
to RADE's speech/modem rates, and USB hardware-rate conversion needs measurement.

Qualify sustained operation in each direction separately, then repeated
RX-to-TX-to-RX turns under panel PTT. Acceptance includes first/last words,
bounded release and modem-tail drain, return-to-RX timing, no stale-turn audio,
stable queues and resource counts, playback pacing, and device-loss handling.
Do not use simultaneous encoder/decoder workload as a requirement for this
half-duplex profile. Split-frequency operation is a later variant using separate
RX/TX frequencies with the same exclusive direction ownership and turnaround
rules; it does not by itself require simultaneous RF reception/transmission.

The stock routing candidate keeps RECORD clear and uses ordinary MODEM entry to
select the line-audio TX route. The external microphone bypasses the requirement
to obtain native microphone samples while feeding modem audio to the MCU.
Headset earphones avoid using RECORD for decoded receive playback. This does not
qualify simultaneous native RF capture and playback through the built-in speaker;
that original receive pair remains unresolved.

Panel PTT must be consumed by the Linux voice mode before ordinary analog PTT
handling. Its release closes microphone admission, finishes accepted speech and
the bounded modem tail, then clears MODEM and returns to receive. Define a
USB-headset profile requiring the panel source only. Preserve the original
all-native guard and do not fabricate hand-PTT observations, applied-route ACKs,
MCU leases or native capability bits to admit this different topology.

Raw radio-audio loopbacks, including the existing Bluetooth receive monitor,
must not feed the selected earphones alongside decoder output. Keep modem output
pinned to the physical radio sink. Device loss must fence input/output, latch a
fault and request software unkey; it must not fall back to another source/sink
or automatically resume a transmission after reconnection.

This design retains stock baseband fault-response limitations. Host commands
and aggregate TX telemetry do not establish physical RF-off or an independent
MCU timeout. Qualification must measure normal turnaround and failure behavior;
the proposed USB profile supplies no stronger shutdown guarantee by itself.

## Inventory result and next validation

The returned 2026-11-05 inventory identifies a Shokz Loop110, USB ID
`3511:2b1e`, with a physical USB microphone and playback sink. Its microphone
hardware exposes 16 kHz mono S16_LE; playback exposes 44.1/48 kHz stereo
S24_3LE. PulseAudio has both endpoints in a duplex profile. Idle SUSPENDED
state is normal. These findings establish a duplex candidate, not actual
microphone, listening, latency or RF success. The device's unique serial is
retained only in private inventory/results, not hardcoded in published source.

The next kit is `usb-headset-validation-20261105.2.zip`. It runs on the existing
`.2` image; no replacement image is required. Its bounded campaign:

1. Rechecks the exact GUI, installed actual-codec runner/library, USB ancestry,
   physical ALSA ownership, unmuted source/sink and unambiguous Loop110 identity.
2. Captures 12 seconds of microphone speech while playing three quiet markers
   through the headset. Both streams use their full USB names and
   `PA_STREAM_DONT_MOVE`; endpoint loss stops the test with partial evidence.
3. Feeds that exact recording through the
   installed speech encoder, RADE modem and decoder. It checks actual nonzero
   speech/modem/synchronized/synthesis work and one end-of-over event.
4. Replays original and decoded speech and collects operator listening notes. Original,
   modem, decoded PCM/WAV, buffer/callback statistics and engine counters stay
   together in one evidence ZIP, including diagnostics if a stage fails.

This kit opens USB endpoints only and contains no radio-control/PTT/I2C calls.
It does not change Pulse defaults, card profiles or hardware mixer settings.
Playback streams start at a conservative per-stream volume. All raw/WAV audio
is mono 44.1 kHz S16_LE at the client boundary; Pulse resamples the 16 kHz USB
microphone and converts playback to the USB hardware format. Buffer parameters
and callback intervals are diagnostic observations, not measured acoustic
round-trip latency. Exact words and clipping must be assessed by listening;
nonzero RMS does not establish speech intelligibility.

This is concurrent USB PCM plus a **recorded codec round trip**, not a live
codec pipeline or an RF test. Success supports implementing the USB profile;
it does not qualify direction switching, release/drain, disconnect recovery,
on-air intelligibility or sustained real-time performance. Existing baseband
and original all-native capability guards remain unchanged.

In Windows PowerShell, run the published kit after verifying its download hash:

```powershell
powershell.exe -NoProfile -ExecutionPolicy Bypass -File .\Invoke-UsbHeadsetValidation.ps1 -RadioIp 192.168.4.210
```

Keep the Loop110 connected and its headset powered and paired. Leave the radio
in RX and every PTT released. At GO, speak the displayed phrase for 12 seconds
and listen for three beeps while speaking. Wait for codec processing, then compare
original and decoded playback. Processing can take up to three minutes. Record whether both contain
the first/last words, are intelligible, have dropouts, and whether any sound came
from the radio speaker. Return the printed `usb-headset-validation-results-*.zip`;
it includes your voice recording. Strict known-host SSH is retained from previous
runs. No password or trust-bypass option is added.

### First returned audio campaign and revised helper

The `20261105.1` campaign captured exactly 529,200 mono client samples (12 s)
while submitting the complete marker stream. Its capture/playback phase drained
successfully in 12.063 s with no reported holes, capture overflows or pre-EOF
playback underruns. The operator heard all three markers while speaking.
The longest observed capture callback gap was 61 ms, not an acoustic latency
measurement. The recording contains nonzero audio; listening remains
necessary to characterize intelligibility and dropouts.

The subsequent original-file replay submitted all 1,058,400 bytes but timed out
at 17.003 s without a drain acknowledgement. There was no recorded endpoint
failure or pre-EOF underrun. The operator heard understandable speech with
dropouts. These observations do not establish whether those dropouts were in
capture, playback, or the wireless link, or why the server withheld completion.
The old helper did not retain EOF underrun counts or server timing indexes.

The revised helper uses automatic nonzero prebuffering for finite files, retires
the write callback at EOF before requesting drain (following PulseAudio's file
player), and records the phase, prebuffer, EOF underruns, timing indexes, sink
latency estimate and suspension state. It still requires drain acknowledgement;
neither a timeout nor an estimated playback position becomes a pass. The change
has passed virtual PulseAudio playback-only, short-tail, duplex and endpoint-loss
checks on the host and the ARM helper under emulation. It has not yet been
verified against the reported Loop110 completion stall.

Codec processing now precedes replay, so a playback completion fault preserves
actual-codec results. Separately, the returned capture was processed with the
matching image's ARM runner and RADE library under emulation: 1,200 speech
frames, 202 modem blocks, one EOO, 98 synchronized blocks, 97 synthesis blocks,
and zero engine failures. The decoded file contains 511,064 samples (about
11.589 s). This verifies offline processing of this input, not on-radio CPU
margin, word completeness, headset playback or RF behavior. User recordings
and decoded audio are retained privately, not published in the helper kit.

### Returned revised campaign: recorded path completed

The returned `20261105.2` campaign completed every automated stage on the radio:
12 s simultaneous USB capture/playback, actual recorded codec round trip,
original playback, decoded playback and endpoint recheck. Evidence ZIP and
inner/outer checksums all verified. Processing reported 1,200 speech frames,
202 modem blocks, one EOO, 98 synchronized blocks, 97 synthesis blocks and zero
engine failures. Both replays obtained drain acknowledgement, with equal final
read/write indexes and zero pre-EOF underrun counts. Their one EOF underrun each
occurred at the exact submitted byte count, consistent with reaching file end.

The operator initially reported clear speech with minor dropouts through the
headset, then separately confirmed the downloaded files sound clean without
dropouts. Accept the recorded speech/codec/listening milestone; the remaining
interruption investigation belongs to live headset playback rather than the
saved codec output. These observations do not distinguish Pulse/ALSA buffering,
USB transport, the dongle's wireless behavior or the headset itself.

Playback timing remains unresolved: the 529,200-sample original (12 s at
44.1 kHz) completed in 11.040 s; the 511,064-sample decoded file (11.589 s)
completed in 10.662 s. Both are close to their sample counts divided by 48 kHz
plus 15 ms, but current snapshots still identify the sink as 44.1 kHz. A clock
or rate mismatch is a candidate, not an established cause: drain timing and
hardware latency accounting could also matter. A separate private virtual-sink
check consumed 3 s in 3.030 s with the same helper, so the early completion was
not reproduced on that fixture. Verify active stream, ALSA and USB rates and
sustained playback pacing in the next real-time test. Reported sink latency is
not an acoustic end-to-end latency measurement.

The next milestone is sustained real-time half-duplex processing, one direction
at a time, with explicit USB streams retained across alternating turns, followed
by panel-PTT and integrated RF qualification.
The completed file test does not establish live codec deadlines, queue stability,
direction fencing or RF behavior. Preserve the original native-mode guard;
USB-profile support remains a separate implementation.

### Live half-duplex turn kit

`usb-half-duplex-20261105.1.zip` runs on the existing `.2` image. Start
`Invoke-UsbHalfDuplex.ps1 -RadioIp 192.168.4.210` from the verified GitHub kit.
The test is timed; leave physical PTT released throughout. No radio-control,
I2C or PTT calls are linked into the standalone helper.

One retained actual codec alternates three eight-second microphone TX windows
with paced channel-file RX windows. The microphone feeds the encoder live;
modem samples are retained in a file in place of an RF channel. During RX that
file feeds the decoder at the declared sample rate, and synthesized speech goes
straight to the headset while decoding runs. This is live processing in each
direction, not simultaneous TX/RX and not an on-air test. The same USB source,
sink and codec contexts remain open across all three turns. Inactive microphone
data is discarded and idle playback is silence, preserving the endpoint session
without admitting the microphone during RX or playing modem audio in earphones.

At each countdown, prepare the displayed numbered phrase. At GO, speak for
eight seconds, including "Turn 1/2/3" and "End turn 1/2/3". At RX, listen to that
turn's decoded speech; wait for the next countdown. Compare first/last words,
turn identities, speed/pitch, dropouts and unwanted previous-turn speech.
The campaign normally takes about 70 seconds, with a 180-second process limit.
Return `usb-half-duplex-results-*.zip` even if it stops early. Its microphone,
modem and decoded PCM/WAV files contain your speech. Listen to microphone and
decoded WAVs for comparison; modem WAVs are channel evidence, not speech audio.

The helper requests a 48 kHz mono S16LE client boundary for this USB-only test.
It does not change the installed GUI or its 44.1 kHz radio PCM boundary. Pulse
performs hardware format/rate conversion as needed. Active source/sink rates,
ALSA USB status and server read-index pacing are recorded; a server rate outside
48 kHz +/-1% blocks completion. This investigates the earlier 44.1/48 kHz timing
discrepancy without assuming its cause. Server timing is not an acoustic latency
measurement or proof that the wireless leg has no dropouts.

Capture and render queues are bounded at 500 ms, and TX finish has a 3 s bound.
Per-turn generation fences clear PCM, while codec histories and workers remain
retained. Telemetry includes queue high-water marks, codec and stream creation
counts, one EOO per TX, capture continuity, first decoded-output timing and
mid-payload render-padding events. Startup and expected end-of-channel silence
are distinguished from the middle of the known payload; counts are diagnostic,
not an automatic word-completeness verdict. Reported playback gaps remain a
separate `COMPLETED_WITH_PLAYBACK_GAPS` result, never an unqualified pass.
Device loss exits without fallback or automatic reconnect/resume.

The full local real-codec host fixture completed three eight-second turns in
66.657 s, retained one engine/two streams, emitted three EOOs, and reported no
engine failures, capture holes, PA underruns or mid-payload render gaps. Measured
server pacing was 48,000.687 samples/s on the virtual sink. A separate disconnect
fixture stopped during TX with an alternate sink present. These are software
fixtures, not measurements of the radio/headset. The ARM binary links the same engine archive and
matching RADE library as the installed candidate. An ARM-emulated run encoded
and synthesized speech, then reached the 500 ms input bound after RX returned
to search. That run is not a real-time ARM pass, nor a measurement of radio CPU
margin. Keep the bound intact and use this hardware campaign to establish actual
pacing; partial failure still retains its trace, PCM and first-fault phase.
Panel PTT, RF routing, actual RF-off timing and split-frequency control remain
subsequent GUI-owned integration work.

### Returned live half-duplex campaign: accepted short-turn milestone

The returned `usb-half-duplex-20261105.1` campaign completed all three live
eight-second TX/RX turns on the radio in 66.937 s. ZIP integrity and both checksum
manifests verified. Operator notes: "Yes, all sounded excellent, no dropouts
observed." Accept this short-turn live Linux/USB codec milestone for the tested
Loop110, retaining the original machine reports' unassessed listening fields as
immutable evidence; human review is recorded here rather than rewriting them.

One engine/encoder/decoder and two USB streams were retained. Three EOOs, 2,400
speech frames and 192 synthesis blocks completed, with zero engine failures,
capture holes, capture overflows, PA underruns or mid-payload render gaps.
The maximum input queue was 9,600 samples (200 ms at 48 kHz), microphone queue
528 samples (11 ms), and render queue 19,236 samples (400.75 ms), all within the
500 ms bounds. This is observed queue behavior over three short turns, not a
worst-case CPU-margin or long-duration stability measurement.

Active ALSA USB status confirms 48,000 Hz stereo packed-24-bit playback and
16,000 Hz mono S16_LE capture. Pulse lists the headset sink at 48 kHz with copy
resampling for the 48 kHz client; the microphone is converted from 16 to 48 kHz.
Measured server read pacing was 47,998.535 samples/s (about -30.5 ppm from
48 kHz). No earlier 44.1/48 kHz discrepancy appeared with retained streams and
48 kHz playback. Both choices changed together, so this result does not isolate
the original cause; preserve the successful configuration for integration.

TX finish-to-channel-file processing took 78..84 ms. This excludes actual modem
playback/RF drain and is not a physical PTT-release-to-RF-off result. First
decoded samples were submitted toward playback 1,020..1,037 ms after paced RX
began; this includes test buffering and codec acquisition and does not measure
acoustic end-to-end latency. The operator's general listening pass is not an
independent word-by-word or acoustic-latency measurement.

Proceed to the GUI-owned USB profile: retain explicit radio RX/modem endpoints
and USB microphone/headphone endpoints; choose the correct rate/conversion per
endpoint; own panel PTT before legacy analog handling; admit one codec direction
at a time; fence old-turn PCM and finish/drain TX before returning to RX. Keep
the original all-native guard intact. Then qualify one integrated RF conversation
campaign, including first/last words, press/release behavior, actual RF turnaround,
disconnect and recovery, and longer RX/search/turn operation. No PTT/RF operation
was tested in this upload. Any new Linux image must be delivered through GitHub.

### Earlier inventory procedure

The first step is read-only USB, ALSA and PulseAudio inventory with the headset
connected directly to HOST. Download the matching helper package from GitHub and
run in Windows PowerShell:

```powershell
.\Invoke-UsbHeadsetInventory.ps1 -RadioIp 192.168.4.210
```

The helper only inventories devices and configuration. It does not open PCM
streams, change routes or mixers, stage FreeDV, or issue radio-control commands.
No new Linux image is needed for this inventory. Return its evidence package
and the USB audio device model; successful enumeration grants no RF capability.

After USB PCM and recorded codec/listening validation, implement the explicit endpoints and
panel-only control in the GUI-owned mode, and qualify one integrated RF
conversation campaign covering both directions, boundary speech, release/drain,
disconnect and recovery. Do not key RF from a separate helper while the GUI owns
the radio. Later Linux changes and any resulting image package will be delivered
through GitHub.
