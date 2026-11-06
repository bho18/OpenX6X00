# X6200 USB PTT transition and end-to-end RF development test

Use the release's **replacement image and matching helper kit** on a spare SD
card. Stock baseband firmware is unchanged. The original native hand-mic mode
remains blocked. This USB headset/panel-PTT mode first runs an automatic RF-disabled transition
check, then offers a separate local Enable TX action for the SDR campaign.
The earlier successful recording, codec and headset playback results remain
accepted; there is no new basic speech-recording or listening round.
The [qualification strategy](usb-test-strategy.md) defines the software,
lifecycle, radio-transition and RF gates and how failed attempts remain in the
record. This guide is the short operator procedure for the radio stages.

The RF test requests 1 W and retains a 20-second software limit per press.
Requested power is not calibrated output power, and Linux TX clearing is not an
autonomous baseband RF-off guarantee. Settings are temporary and restored on
normal Exit.

## Prepare

1. Verify both release ZIPs against `SHA256SUMS`. Flash the image ZIP with your
   usual image-writing tool, boot the spare card, and retain your working card.
   Do not install a baseband update.
2. Connect the Shokz Loop110 and power/pair the headset. Set a comfortable
   headset listening volume. Disconnect the hand mic and paddles/other keying
   accessories. Use only the radio's on-device panel PTT for the later RF test.
3. Connect the transmitter to a suitable dummy load. Let the SDR receive a weak
   leakage signal, or use a properly attenuated RF coupler. Never connect the
   transmitter output directly to an SDR input. Avoid SDR overload/clipping.
4. Select an appropriate permitted HF test frequency before opening FreeDV.
   Disable ATU for the dummy load, split, TX shift, RIT, XIT and transverter
   offsets. Close other radio apps/recorders and TCI control clients. Tuning is
   locked while USB mode owns the radio. The mode temporarily selects USB-DIG
   and 100..3100 Hz RX filtering.
5. Set the SDR to the same dial frequency in **USB**, with approximately
   100..3100 Hz audio passband. Disable squelch and audio noise processing.
   Prepare to record **demodulated audio**, PCM signed 16-bit mono, preferably
   48 kHz. Keep the saved recording at most 30 seconds. IQ recordings are not
   input to this helper.

## Automatic transition check with RF disabled

1. Open FreeDV and confirm **USB FreeDV / RADE V1 - RF TEST**. Wait for
   preparation to finish. The display must say **RF disabled** and actual radio
   **RX**. Leave every PTT released.
2. Select **Run transition check** locally. Leave the headset connected and
   all PTT released through the initial 20-second idle period and the three
   automatic direction/control-delay turns. **No speaking or listening task is
   required.** This specifically checks
   the control/audio interaction that failed in the last RF attempts. The
   idle/short-turn sequence is a transition screen; it does not establish
   session reopen behavior, physical endpoint suspension or sustained operating
   margin. Those claims need their separately declared campaign results.
3. The same retained codec and four real streams are serviced through simulated
   turns and injected control delays while the real radio remains RX. The real
   MODEM-on/key operation is not called, and the radio adapter's independent TX
   gate remains disabled. Any internal modem loopback checks continuity and
   timing automatically; it is not another voice-quality or RF receive test.
   If output preparation briefly pauses capture delivery, the controller keeps
   RF off and waits for new valid microphone and radio samples before continuing.
   Capture that does not recover within the preparation deadline remains a
   blocker. Active transmission still uses the strict capture-loss guard.
4. Continue only after **Transition check passed**, with actual radio RX and
   panel PTT released. RF remains disabled after a pass. If there is a fault,
   unexpected radio TX or an unconfirmed RX state, select Stop, then Exit when
   possible. Collect evidence without an SDR file and return the ZIP. If radio
   TX persists after Stop, switch the radio off. Do not adjust gains or repeat
   basic audio tests to work around a transition fault.

The check uses the same retained codec, streams and controller as the RF phase.
It must finish normally before Enable TX can promote the session. Failure cannot
be overridden by repeated Enable presses; Exit and a fresh session are required
after a fault.

## One complete SDR transmit campaign

1. After the transition check passed, briefly press/release panel PTT while TX is
   still disabled. The radio must remain RX. If it keys, select Stop and end the
   test; do not enable TX.
2. Select **Enable / disable TX** locally. Start the SDR audio recording. Hold
   panel PTT, wait for **Transmitting**, and speak for about eight seconds:
   "RF test turn one. One two three four five. The quick brown fox jumps over
   the lazy dog. End RF test turn one."
   Microphone input before Transmitting is discarded while audio preparation
   and TX acknowledgement settle; do not begin speaking during that transition.
3. Release PTT. The controller should close its bounded microphone tail,
   finish accepted speech and the modem tail, drain playback, clear software TX
   and wait for a newer RX packet.
   Allow three seconds after release; if TX persists, select Stop. If it still
   persists, switch the radio off. Packet telemetry is not direct physical
   RF-off instrumentation.
4. Stop the SDR recording a few seconds after RF ends. Confirm the SDR was not
   saturated or muted and save the complete 1..30 s PCM16 audio WAV.
5. After normal release worked, make a separate short transmission on
   the dummy load and select Stop while PTT remains held, then release PTT.
   RF should end and the mode must stay stopped without rekeying. Keep this
   interrupted burst out of the WAV used to judge the complete first phrase.
   Record this outcome separately; a normal-release result does not also
   qualify Stop while held.
6. Select **Exit**, release all PTT, and wait for ordinary radio control to
   return. Ownership is retained until asynchronous work has stopped,
   restoration was sent, a newer actual RX packet arrived and panel PTT is
   released. A stopped label alone must not be treated as confirmed RF clear.
   Save evidence before a normal reboot because detailed traces/audio are under
   `/tmp`. Bounded incident metadata is also retained on the SD card. If
   persistent TX required power-off, report that recovery; collect the surviving
   metadata afterward. Missing trace/audio leaves the trial blocked. Do not
   describe it as passed or silently replace it with a retry. Controller
   completion metadata covers audio/controller teardown; confirm settings
   restoration and RF cessation through the hardware-trial observations.

## Collect and decode evidence

Extract the release's matching helper kit. In Windows PowerShell run:

```powershell
powershell.exe -NoProfile -ExecutionPolicy Bypass -File .\Invoke-UsbRfEvidence.ps1 -RadioIp 192.168.4.210 -SdrWav 'C:\path\to\sdr-usb-audio.wav'
```

For an RF-disabled transition check or startup failure, omit `-SdrWav`. This collects
diagnostics only and does not count as RF speech verification. The helper
verifies the installed image components, collects the stopped session, and
optionally decodes your **SDR recording**. It sends no radio/PTT commands.

Listen to the downloaded `results/offair-decode/offair-decoded.wav` when present.
Compare the complete RF phrase and first/last words with the microphone tap.
The microphone evidence can also contain background audio from the preceding
RF-disabled transition check; the phase trace identifies the RF speech turn.
Record intelligibility, dropouts, RF release behavior,
frequency, SDR settings and power/ALC indications. Automated synchronization
alone is not a successful complete-phrase result.

Return the printed `usb-rf-results-*.zip`, including on failure. It contains your
voice and recording. WAV accepts PCM16 mono/stereo at 8, 44.1 or 48 kHz; stereo
is averaged with no normalization. Rejected input WAVs up to 16 MiB are retained
for diagnosis. Larger inputs retain a bounded header and digest. Collection,
GUI/audio state and RF decoding are reported separately so a later no-sync
result cannot hide the first startup fault.

## Scope

Four bound streams are retained: radio RX capture, radio modem playback, USB
microphone capture and USB decoded-speech playback. One 48 kHz codec alternates
directions; explicit resamplers bridge the radio's fixed 44.1 kHz boundary.
Pulse converts the USB microphone from its 16 kHz hardware rate; headset
playback uses 48 kHz. RECORD stays clear and MODEM is the stock RF TX source.
Modem PCM is trimmed to one quarter amplitude before mono sink duplication for
conservative initial drive. An internal modem tap is not RF evidence.

Audio servicing is independent of blocking control/I2C writes and codec
transitions. Audio preparation completes before a key request. Retained panel
release events, bounded queues, asynchronous control completion, priority OFF,
first-fault metadata and per-stream sample/callback counters support diagnosis.
Stop fences future TX admission immediately; a blocked hardware transaction can
still delay delivery of the clear command. The display and trace distinguish a
requested clear from confirmed fresh RX.

The integrated transition check evaluates operation of this Linux audio/controller
implementation on the device. It does not qualify antenna RF, an independent
baseband lease, native hand-mic isolation or simultaneous RF duplex. The SDR
campaign tests the transmit leg. RF receive qualification, longer conversations
and split-frequency operation require a transmitting peer and remain later
steps.
