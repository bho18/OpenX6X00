# X6200 FreeDV operational Beta

Use the `freedv-operational-beta-20261106.1` GitHub release and its matching
helper kit on a spare SD card. Keep the working `usb-voice-beta-20261106.1`
card as a fallback. Stock baseband firmware is unchanged. The operating path
is **half-duplex RADE V1**, using a USB headset microphone and headphones or a
paired USB microphone/speaker device, plus the radio's own panel PTT.

The earlier recording, codec round trip, headset playback and RF transmit
milestones remain accepted. The next field milestone is a known working RADE
V1 peer received in the headset, followed by alternating replies. This build
does not ask you to repeat those component tests.

## Install and enter FreeDV

1. Download `x6200-usb-rf-windows.zip`, `usb-rf-test-kit.zip` and `SHA256SUMS`
   from the GitHub release. Verify their SHA-256 hashes, extract the image and
   flash a spare card using your usual image-writing tool. Image and kit must
   share the same build ID, image hash, **startup contract 4** and **output
   contract 1** and **capture opening contract 1**. No baseband update is needed.
2. Plug the USB headset or its USB receiver into the radio's **HOST** port.
   Power and pair it, then start at a comfortable low volume. Built-in mic,
   hand mic and radio speaker are unsupported for FreeDV audio in this build.
   Hand-mic PTT and headset call buttons do not start a FreeDV transmission.
3. Open **Apps → Digital modes → FreeDV**. Read the information box and choose
   **Continue**. **Cancel** leaves normal operation in place. Select **Don't
   show this again** only if desired; Continue saves that preference, including
   across restarts. The Mode menu's Help button can show the instructions again.
4. The normal waterfall and tuning controls remain visible. The radio applies
   the required USB data profile and filters. Leave panel PTT released while
   preparation finishes. **Receiving** follows actual stream preparation, buffers,
   fresh capture/RX status and a released PTT; these admit operation. There is
   no Run check or Enable TX step. Startup does not transmit fake qualification
   turns or wait through a fixed 20-second idle.

With multiple USB devices, select the intended paired device under **Audio**.
The saved physical device identity is reused; unplugging it stops the session
and does not move audio or TX to another device. A receive-only frequency can
still receive, but is not eligible for TX. Split, transverter and native-radio
mic/speaker operation are outside this Beta.

## Lower buttons and knobs

| Control | Operation |
| --- | --- |
| **Mode** | RADE V1, TX timeout and Help. Other modes appear when supported. |
| **Mic** | Saved speech gain from −12 to +24 dB and live input/encoder level meters. |
| **Audio** | Select the USB device that provides both capture and playback. |
| **Power** | The radio's usual power setting and knob-binding behavior, using the shared 0.1–10 W control. |
| **Exit** | Gracefully close FreeDV and restore normal operation. A second distinct press while leaving is **Stop now**. |
| **VOL** | Adjust selected USB headphone/speaker volume when its normal Volume function is selected, including in popups. Selecting Power gives the normal power-knob behavior. |
| **Tuning** | Tune normally in RX. TX admission pauses while the latest frequency/profile is applied and fresh RX is reacquired. |

If existing settings are retained, the saved **+16 dB** gain migrates for the
Loop110. On a fresh card, select **+16 dB** for your current headset and confirm
its meter. A new device starts at 0 dB. Aim for normal speech peaks near
−12 to −6 dBFS with room for louder syllables. Avoid CLIP. This is speech gain
before encoding, not modem/RF drive or automatic gain control. Speaking with
PTT released previews the meter without sending speech to RF.

The TX timeout is **180 seconds** by default and can be set from **30 to 600
seconds**. Each transmission keeps the timeout selected at its start. Use
short replies for the initial field test. Power changes use the standard radio
control and can be applied during TX; choose a suitable power for your setup.

## Receive and make a reply

1. Arrange a peer with a known working **RADE V1** station, on the same dial
   frequency and sideband. Another FreeDV mode is not a compatible source. Use
   a permitted frequency and an appropriate antenna. Have the peer send a short
   message with recognizable first and last words.
2. Leave panel PTT released. Listen in the USB headset and set headphone volume
   with VOL. Waterfall activity or Receiving alone does not prove decoder sync
   or intelligible speech. Note whether the first and last words are complete.
3. Wait for Receiving with TX eligible, then hold the **on-radio panel PTT**. Speak normally, then
   release it. Allow the finishing/drain indication to complete; the accepted
   microphone interval and end-of-over modem tail finish before TX clears.
4. Have the peer report speech level, clipping and first/last-word completeness.
   Make several short alternating turns. Tune in RX and reopen the Mic/Audio/
   Mode popups to confirm ordinary controls remain usable. The same warm codec
   stays resident across direction changes, popups, gain, volume and tuning.

## Leave, switch apps or stop

GEN, Apps, other mode/page navigation and Exit all use coordinated teardown.
During a normal exit in TX, the current accepted speech closes at a finite
capture boundary, its modem tail finishes within the existing three-second
drain limit, TX clears, fresh RX is checked, and radio settings are restored.
If panel PTT remains held, release it so the requested destination can open.
A navigation close is recorded separately from an actual physical PTT release.

If an immediate stop is needed, use a **second distinct Exit/Stop now press**.
Protection faults, lost USB/server and shutdown use immediate abort behavior.
A fault does not automatically reconnect, change devices or resume TX. Review
the displayed fault before reopening FreeDV. Shutdown is bounded even if PTT
remains held. A software RX confirmation is telemetry evidence; antenna-level
RF-off timing has not been independently measured.

## Optional evidence collection

After leaving FreeDV, keep the radio running and all PTT released. Extract the
matching `usb-rf-test-kit.zip` on Windows, open PowerShell in that folder, and run:

```powershell
powershell.exe -ExecutionPolicy Bypass -File .\Invoke-UsbRfEvidence.ps1 -RadioIp 192.168.4.210
```

Add `-SdrWav 'C:\path\demodulated-usb-audio.wav'` only if an off-air recording is
useful. PCM16 mono/stereo at 8, 44.1 or 48 kHz, up to 120 seconds, is supported.
IQ files are not decoded by this helper. It sends **no TX/PTT commands**.
Return the printed ZIP if there is a fault, missing speech or an exit problem;
it contains microphone/audio taps, so review it before sharing.

For contract 4, expect **startup: PASS**, **qualification: NOT_REQUESTED**,
**control_completion: CONFIRMED_RX**, **capture_open: PASS**, **output_completion: PASS** and
**session: COMPLETE** after a healthy exit. No SDR file normally gives
`COLLECTED_NO_SDR_RECORDING`; that is not a failure or a claim of RF reception.
A fault, missing readiness, incomplete terminal receipt or mismatched build is
BLOCKED. A decoded file still needs listening review. Detailed test evidence
and the distinction between software gates and radio observations are in
[the test strategy](usb-test-strategy.md).
