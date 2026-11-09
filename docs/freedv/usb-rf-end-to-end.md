# X6200 FreeDV operational Beta

Use the `freedv-operational-beta-20261107.3` GitHub release and its matching
helper kit on a spare SD card. Download both from the same
[GitHub release](https://github.com/bho18/OpenX6X00/releases).
Keep the previous working card as a fallback. The Linux/SDK baseline and stock
baseband firmware are unchanged. The operating path
is **half-duplex RADE V1**, using a USB headset microphone and headphones or a
paired USB microphone/speaker device, plus the radio's own panel PTT.

The [2026-11-09 compatibility note](https://github.com/bho18/OpenX6X00/blob/freedv-operational-beta-20261107.3/docs/freedv/compatibility-hardening-2026-11-09.md)
describes the control, audio-handover and shutdown changes in the current source.
Use the release manifest to identify which changes are included in an image.

The earlier recording, codec round trip, headset playback and RF transmit
milestones remain accepted. The next field milestone is a known working RADE
V1 peer received in the headset, followed by alternating replies. This build
does not ask you to repeat those component tests.

## Install and enter FreeDV

1. Download `x6200-usb-rf-windows.zip`, `usb-rf-test-kit.zip` and `SHA256SUMS`
   from the GitHub release. Verify their SHA-256 hashes, extract the image and
   flash a spare card using your usual image-writing tool. Image and kit must
   share the same build ID, image hash, **startup contract 4** and **output
   contract 1** and **capture opening contract 1** and **refinement contract 2**. No baseband update is needed.
2. Plug the USB headset or its USB receiver into the radio's **HOST** port.
   Power and pair it, then start at a comfortable low volume. Built-in mic,
   hand mic and radio speaker are unsupported for FreeDV audio in this build.
   Hand-mic PTT and headset call buttons do not start a FreeDV transmission.
3. Open **APP → Modes → FreeDV**. Read the information box and choose
   **Continue** (**F5**). **Cancel** (**F4**) leaves normal operation in place. Select **Don't
   show this again** only if desired; Continue saves that preference, including
   across restarts. The Mode menu's Help button can show the instructions again.
4. The normal waterfall and tuning controls remain visible. The radio applies
   the required USB data profile and filters. Leave panel PTT released while
   preparation finishes. **Receiving** follows actual stream preparation, buffers,
   fresh capture/RX status and a released PTT; these admit operation. There is
   no Run check or Enable TX step. Startup does not transmit fake qualification
   turns or wait through a fixed 20-second idle.

With multiple USB devices, select the intended paired device under **Audio**.
Confirm a device in the dropdown to apply it; Close dismisses the menu without another confirmation. A change waits for released PTT and confirmed RX. The saved physical device identity is reused; unplugging it stops the session
and does not move audio or TX to another device. A receive-only frequency can
still receive, but is not eligible for TX. Split, transverter and native-radio
mic/speaker operation are outside this Beta.

## Lower buttons and knobs

| Control | Operation |
| --- | --- |
| **Mode** | RADE V1, TX timeout and Help. Other modes appear when supported. Timeout changes apply in RX. |
| **Mic Gain** | Saved speech gain from −12 to +24 dB and live input/encoder level meters. |
| **Audio** | Select the USB device that provides both capture and playback. |
| **Power** | The radio's usual power setting and knob-binding behavior, using the shared 0.1–10 W control. |
| **F5** | **Cancel edit** restores the previous microphone gain or discards an unconfirmed timeout; **Close** returns from a settings popup. On the normal screen, **Exit** runs cleanup and restores normal operation. Further Exit presses are ignored while cleanup finishes. |
| **VOL** | Keep the usual select/edit workflow and bindings, including in popups. With Volume selected, adjust USB **Headphones**; other selected controls, including Power, keep their existing behavior. |
| **MFK** | On the main screen, both rings keep their normal control-selection/edit behavior. In a FreeDV popup, focus the native value row, click to edit, turn to change the whole value, then click to save/confirm. Use **Cancel edit/F5** to discard a draft, then **Close/F5** to return. |
| **Tuning** | Tune normally in RX. TX admission pauses while the latest frequency/profile is applied and fresh RX is reacquired. |

If existing settings are retained, the saved **+16 dB** gain migrates for the
Loop110. On a fresh card, select **+16 dB** for your current headset and confirm
its meter. A new device starts at 0 dB. Aim for normal speech peaks near
−12 to −6 dBFS with room for louder syllables. The live meter warns **Near maximum** at −1 dBFS and **Mic overload—lower gain** for digital saturation or PCM limits; it works with PTT released as well as during TX. These measurements cannot diagnose every form of clipping inside a headset. This is speech gain
before encoding, not modem/RF drive or automatic gain control. Speaking with
PTT released previews the meter without sending speech to RF.

Mic Gain changes in **1 dB** steps and applies immediately while editing: the speech meter reflects every dial adjustment. Click MFK to save the chosen gain. **Cancel edit/F5**, changing popups or leaving without saving restores the previous gain. Timeout changes in **30-second** steps and applies only when confirmed. If TX prevents a timeout change, release PTT and apply it. The Power button closes a popup and follows the usual radio power-control path.

FreeDV uses the selected application theme, including custom colors, fonts and shapes. Theme changes follow the normal coordinated exit and screen rebuild. The dedicated status/meter row reserves its own space and does not cover frequency labels.

Release controls before entering or leaving the mode. Buffered turns and
half-presses from before the ownership change are discarded; entry waits for
the input reset to finish. Remote queries remain available, but competing
settings/TX commands are refused while FreeDV owns the radio. CAT and the local
configurator now report that refusal rather than success for an ignored write.

The TX timeout is **180 seconds** by default and can be set from **30 to 600
seconds**. Each transmission keeps the timeout selected at its start. Use
short replies for the initial field test. Power changes use the standard radio
control and can be applied during TX; choose a suitable power for your setup.

## Headphone volume and idle recovery

The normal **Volume** control becomes **Headphones** in FreeDV, wherever that control is assigned. The display uses the usual plain **0–100** units, for example **Headphones 30**, from confirmed USB readback. Selecting another control still selects and edits that control; FreeDV does not rebind either knob. The radio's speaker setting is preserved and its normal display returns on exit.

Each USB device remembers its own headphone level and mute state. A genuinely new device starts at **30**; saved zero and mute remain valid. Preferences save in the background after volume settles. A save failure does not undo working audio. The headset may have its own local volume or end-stop sound; its internal setting is only observable when the USB device reports it. FreeDV does not infer limits from that sound or impose a minimum of 10.

Audio offers an optional **Volume preview**, enabled by default. A quiet half-second tone follows a confirmed adjustment after a short pause in dial movement, when RX has no decoded speech. Further dial movement changes the playing tone's level without chopping it into snippets. Speech, PTT, tuning, faults, mute/zero and exit cancel it. The tone goes only to the USB headset; startup restoration and selecting Volume do not produce it. Preview and volume-control failures have bounded independent recovery and do not stop otherwise healthy audio.

Normal screen blank/wake keeps the codec and audio streams running. If microphone delivery stops, new TX is inhibited while healthy reception continues and one bounded microphone recovery runs on the same endpoint. If it cannot recover, Audio shows the relevant explanation and **Try again**. Release PTT and make a new press after recovery.

FreeDV temporarily pauses only the native Bluetooth raw-radio RX loopbacks,
then restores their managed routing on exit. This does not enable a native
Bluetooth microphone or reject its future support. The current supported
headset path remains USB, including a headset whose paired receiver presents
USB capture and playback to the radio.

A brief radio-status interruption in RX immediately blocks TX and enters **Recovering radio connection** for up to two seconds, retaining healthy audio and the codec. Recovery requires confirmed RX, stable configuration, healthy capture and released PTT. It never resumes a held transmission. Persistent loss, status loss during TX, unexpected TX, protection or an unsuccessful RF clear stops operation with a specific explanation. Audio provides contextual recovery where it can be performed safely; it never silently selects a different device.

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

Exit presents no additional stop action; cleanup finishes automatically.
Protection faults and lost USB/server retain their internal abort handling.
Hard faults do not automatically reconnect, change devices or resume TX. Review
the displayed fault and use the contextual Audio recovery action when offered. Shutdown is bounded even if PTT
remains held. SIGTERM/SIGINT allow up to five seconds for graceful cleanup,
then request abort and terminate if it cannot complete; expiry does not confirm
RF clear. A software RX confirmation is telemetry evidence; antenna-level
RF-off timing has not been independently measured.

If the ordinary audio stream cannot be restored, the software may request one
GUI restart after USB audio has stopped and fresh RX confirms the restored
radio state. It returns to ordinary operation, without resuming TX or reopening
FreeDV automatically. It does not use a timeout as permission to skip RX.

## Focused field observations after upgrading

Use one normal operating session:

1. Open Mic Gain. Speak with PTT released while editing and verify each dial adjustment changes the meter immediately. Center-click saves; Cancel restores the previous gain. Check the live warning responds to loud speech.
2. Select Volume using each knob's usual workflow, then select Power or another control and verify it retains its normal function. Check the plain headphone number, slower adjustments, fast turns and reversals through the lower range, including zero and back upward. At a comfortable level, pause after a change and listen for a useful half-second preview. The headset's own end-stop sound is separate.
3. Open Audio and Mode. Check there are no missing-glyph rectangles or diagnostic rows; confirming a different connected device applies without Use device. Close returns normally.
4. Allow screen timeout, leave the panel dark for the interval that previously caused trouble, then wake it and check the mic meter, volume control and a fresh PTT press. Exit through GEN or APP and confirm cleanup completes, with normal speaker volume and knob selection intact.
5. When a known working RADE V1 peer is available, check received first/last words and make alternating replies. This remains the outstanding live receive milestone.

The earlier recording, codec and RF-transmit milestones need not be repeated. Software checks simulate endpoint interruptions; actual headset idle behavior remains a field observation. If a failure recurs, preserve its incident before repeatedly exiting/reopening the mode.

## Optional evidence collection

After leaving FreeDV, keep the radio running and all PTT released. Extract the
matching `usb-rf-test-kit.zip` on Windows, open PowerShell in that folder, and run:

```powershell
powershell.exe -ExecutionPolicy Bypass -File .\Invoke-UsbRfEvidence.ps1 -RadioIp 192.168.4.210
```

Add `-SdrWav 'C:\path\demodulated-usb-audio.wav'` only if an off-air recording is
useful. PCM16 mono/stereo at 8, 44.1 or 48 kHz, up to 120 seconds, is supported.
IQ files are not decoded by this helper. It sends **no TX/PTT commands**.
Return the printed ZIP if there is a fault, missing speech or an exit problem.
Detailed capture defaults to off: ordinary operation retains summaries and
fault information without microphone/audio taps or verbose event histories.
For a specific incident, enable **Settings → Info → FREEDV DIAGNOSTICS →
Detailed session capture** before entering FreeDV. The setting takes effect on
the next mode entry and is not required for normal operation. When enabled,
the ZIP can contain recordings, so review it before sharing. The engineering
radio-test panel remains separate from this capture switch.

For startup contract 4 with refinement contract 2, expect **startup: PASS**, **qualification: NOT_REQUESTED**,
**control_completion: CONFIRMED_RX**, **capture_open: PASS**, **refinement: PASS**, **output_completion: PASS** and
**session: COMPLETE** after a healthy exit. No SDR file normally gives
`COLLECTED_NO_SDR_RECORDING`; that is not a failure or a claim of RF reception.
A fault, missing readiness, incomplete terminal receipt or mismatched build is
BLOCKED. A decoded file still needs listening review. Detailed test evidence
and the distinction between software gates and radio observations are in
[the test strategy](usb-test-strategy.md).
