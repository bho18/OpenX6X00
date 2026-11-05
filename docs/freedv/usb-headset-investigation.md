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

## Current step and subsequent evidence

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

After endpoint review, scope a headset-only live capture/encode/decode/listening
check with radio commands absent. Then implement the explicit endpoints and
panel-only control in the GUI-owned mode, and qualify one integrated RF
conversation campaign covering both directions, boundary speech, release/drain,
disconnect and recovery. Do not key RF from a separate helper while the GUI owns
the radio. Later Linux changes and any resulting image package will be delivered
through GitHub.
