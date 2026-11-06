# X6200 USB Digital Voice beta

Use the `usb-voice-beta-20261106.1` image and its matching helper kit on a spare
SD card. Keep the working `usb-qualification-20261106.2` card as a fallback.
Stock baseband firmware is unchanged. This beta uses a USB headset with microphone
and the radio's on-device panel PTT for **half-duplex RADE V1**.

The earlier recording, encoding, decoding, headset playback and initial RF
transmit results are accepted. The next operating milestone is a live incoming
RADE V1 signal decoded into the headset, followed by alternating short replies.
You do not need another basic audio-recording or SDR transmit campaign first.

## What changed

* **Mic gain**: saved manual gain from -12 to +24 dB, initially 0 dB, before the
  speech encoder. The live RMS/peak meter previews this level even in RX; it
  does not key the radio. CLIP indicates saturation during accepted TX speech.
* **Headphones**: the physical VOL knob controls the selected USB headset sink.
  Its actual volume and mute state are displayed. The extra -18 dB speech-stream
  attenuation is removed. Start with a low headset volume before opening the mode.
* The codec, resamplers and four audio streams remain resident through RX/TX
  turns. RF admission, tail completion and the existing transition check remain.

The +20 dB control used on the receiving PC was FreeDV's **Speaker / decoded
speech output** gain. It does not establish that X6200 mic gain needs +20 dB.
Measured microphone peaks already reached about -5 dBFS; that much input boost
would clip. Adjust speech gain using the meter and a peer's listening report.
Mic gain changes speech, not RF/modem drive. There is no automatic gain control
in this beta.

## Install and prepare

1. Download `x6200-usb-rf-windows.zip`, `usb-rf-test-kit.zip` and `SHA256SUMS` from
   the beta release. Verify their SHA-256 hashes. Extract and flash
   `x6200-usb-rf.img` using your usual image-writing tool, then boot that card.
   Keep image and kit together; their manifests must have the same build ID and
   image hash, startup contract 3 and output contract 1. No baseband update is needed.
2. Connect the Shokz Loop110, then power and pair the headset. Start at low
   listening volume. Disconnect the hand mic, paddles and other keying accessories.
   Use **panel PTT**, not the hand mic or a headset call button.
3. Select the dial frequency before opening FreeDV. Disable split, TX shift,
   RIT, XIT and transverter offsets. Close other radio apps, recorders and TCI
   control clients. Tuning stays locked in USB voice mode. It temporarily selects
   USB-DIG, 100..3100 Hz RX filtering and **1 W requested** TX power. The software
   limit is **20 seconds per press**; use short beta replies. Radio settings are
   restored on normal Exit. Saved mic gain and USB headset volume remain user
   preferences.
4. For live reception, arrange a peer using **compatible RADE V1**, on the same
   dial frequency and sideband, without squelch or speech noise processing in
   the modem path. Another FreeDV mode is not a compatible source. The unvalidated
   second transmitter can wait; a known working operator is a suitable peer.
   Use an appropriate antenna and permitted frequency for that exchange.

## Receive and set the audio controls

1. Open FreeDV and confirm **USB Digital Voice (Beta)**. Wait for **Receiving**,
   actual radio **RX**, and **TX disabled**. Leave PTT released. Reception and
   decoding work before TX is enabled; Run check is not needed just to listen.
2. Turn **VOL** to set headset loudness. The displayed headphone percentage must
   follow it; clockwise also unmutes. This adjusts USB playback, not radio RF
   gain or modem drive. On Exit the knob resumes its ordinary radio behavior.
3. Have the peer send a short RADE V1 transmission. Listen for intelligible
   decoded speech, including its first and last words. A Receiving label means
   the controller is in RX; it is not a decoder-sync or intelligibility indicator.
4. Speak normally with PTT released and use the **Mic gain - / +** buttons only
   as needed. Aim for ordinary speech peaks around **-12 to -6 dBFS**, with room
   for louder syllables. Keep peaks below 0 dBFS. The meter displays the proposed
   encoder input; speaking in RX does not transmit it. Leave gain at 0 dB if
   your normal speech already reaches that range. Do not add 20 dB by default.

## Enable short half-duplex replies

1. With PTT released and actual RX confirmed, select **Run check**. Leave PTT
   released during its 20-second idle and three automatic direction-change turns.
   No speaking or listening exercise is required. Real RF TX remains disabled.
   This is the new session's retained admission check, not a repeat of basic
   codec qualification.
2. Continue only after the check passes and RX is confirmed. Select **Enable TX**
   locally. Hold panel PTT, wait for **Transmitting**, then speak a short reply
   (about 5-10 seconds). Audio spoken during preparation is discarded. Ask the
   peer whether your speech is clear, comfortably loud, and complete at both ends.
3. Release PTT and allow up to three seconds for the accepted speech/modem tail
   to finish and fresh RX to be confirmed. Then listen for the peer's reply.
   Make a few alternating turns rather than transmitting and receiving together.
   If CLIP appears, reduce mic gain before the next turn. Set listening volume
   separately with VOL.
4. **Stop** immediately fences further TX admission and requests software TX
   clear. If TX persists after Stop, switch the radio off. A Linux clear or a
   telemetry RX flag is not an independent physical RF-off guarantee. Use
   **Exit**, release PTT and wait for ordinary controls to return when finished.

A transition fault, missing endpoint or unconfirmed RX must be resolved before
continuing; do not work around one by changing gains or repeating Enable TX.
The [qualification strategy](usb-test-strategy.md) describes the software gates
and retained failure history. This remains a beta: incoming RF reception and
longer conversations need operator confirmation; split-frequency operation is
outside this release's qualification.

## Save one session report

After Exit and before rebooting, extract the matching helper kit and run in
Windows PowerShell:

```powershell
powershell.exe -NoProfile -ExecutionPolicy Bypass -File .\Invoke-UsbRfEvidence.ps1 -RadioIp 192.168.4.210
```

This is read-only collection; it sends no TX/PTT commands. Note the peer/mode,
frequency, RX first/last-word clarity, mic gain, headphone setting, any clipping,
and PTT release/return to RX. Return the printed `usb-rf-results-*.zip`, including
on failure. The ZIP contains microphone audio. Preserve it before reboot because
detailed traces and audio live under `/tmp`; bounded incident metadata is also
retained on the card. Report any power-off recovery separately.

An SDR recording is optional, not required for the accepted initial TX result
or the next live RX step. To decode one, add `-SdrWav 'C:\path\to\sdr-usb-audio.wav'`.
The helper accepts complete **demodulated USB audio**, PCM16 mono/stereo at 8,
44.1 or 48 kHz, up to **120 seconds and 32 MiB**, with no trimming. Stereo is
averaged without normalization. It does not accept IQ data. Larger files retain
only a bounded diagnostic header and digest. Automated sync alone does not
establish intelligibility or complete first/last words.

## Implementation boundaries

The USB microphone supplies speech independently of the stock baseband's coupled
mic/line inputs. Radio RX capture and modem playback use the stock 44.1 kHz host
audio boundary; explicit resamplers bridge the resident 48 kHz codec. RECORD
stays clear, MODEM is the stock RF TX source, and conservative quarter-amplitude
modem drive is unchanged. This is half-duplex RF operation.

Normal release completes the modem tail, acknowledges protocol drain, waits for
a fresh Pulse-reported backend interval within the existing deadline, clears TX
and waits for a newer RX packet. That receipt is not a physical DAC-empty or
RF-off measurement. Stop bypasses the normal tail wait and prioritizes clear.
No raw independent hand-mic PTT interface or baseband watchdog guarantee has
been added. Native hand-mic isolation remains blocked by the stock routing.
