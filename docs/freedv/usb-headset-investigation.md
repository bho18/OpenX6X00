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

The next kit is `usb-headset-validation-20261105.1.zip`. It runs on the existing
`.2` image; no replacement image is required. Its bounded campaign:

1. Rechecks the exact GUI, installed actual-codec runner/library, USB ancestry,
   physical ALSA ownership, unmuted source/sink and unambiguous Loop110 identity.
2. Captures 12 seconds of microphone speech while playing three quiet markers
   through the headset. Both streams use their full USB names and
   `PA_STREAM_DONT_MOVE`; endpoint loss stops the test with partial evidence.
3. Replays the original capture, then feeds that exact recording through the
   installed speech encoder, RADE modem and decoder. It checks actual nonzero
   speech/modem/synchronized/synthesis work and one end-of-over event.
4. Replays decoded speech and collects operator listening notes. Original,
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
and listen for three beeps while speaking. Then compare original and decoded
playback. Processing can take up to three minutes. Record whether both contain
the first/last words, are intelligible, have dropouts, and whether any sound came
from the radio speaker. Return the printed `usb-headset-validation-results-*.zip`;
it includes your voice recording. Strict known-host SSH is retained from previous
runs. No password or trust-bypass option is added.

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
