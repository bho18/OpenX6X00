# X6200 USB headset RF development test

Use the GitHub release's USB RF image on a spare SD card. It includes a separate
GUI-owned USB FreeDV / RADE V1 test mode; the original native hand-mic mode stays
blocked. Stock baseband firmware is unchanged. This is an unqualified RF test
image, with a requested 1 W output setting and a 20-second per-press software
limit. Neither that setting nor Linux software clearing is a physical RF-off
guarantee. Settings are temporary and restored on normal Exit.

## Prepare

1. Verify the image ZIP and helper kit SHA256 values from the release. Flash the
   image ZIP with your usual image-writing tool, boot the spare card, and retain
   your working card. Do not install a baseband update for this test.
2. Connect the Shokz Loop110 and power/ pair the headset. Disconnect the hand
   microphone and paddles/other keying accessories. Use the radio's panel PTT.
3. Connect the transmitter to a suitable dummy load. Let the SDR receive a weak
   leakage signal, or use a properly attenuated RF coupler. Never connect the
   transmitter output directly to an SDR input. Avoid SDR overload/clipping.
4. Select an appropriate permitted HF test frequency before opening FreeDV.
   Disable ATU for the dummy load, split, TX shift, RIT, XIT and transverter offsets. Close other radio
   apps/recorders and TCI control clients. Tuning is locked while USB mode owns
   the radio. The mode temporarily selects USB-DIG and 100..3100 Hz RX filtering.
5. Set the SDR to the same dial frequency in **USB** with approximately
   100..3100 Hz audio passband. Disable squelch and audio noise processing. Set
   it to record **demodulated audio**, PCM signed 16-bit mono, preferably 48 kHz.
   Do not record IQ for this helper. Keep the recording at most 30 seconds.

## One initial RF campaign

1. On the radio select the FreeDV app/mode. Confirm the title
   **USB FreeDV / RADE V1 - RF TEST**. Wait for **Receiving** and fresh **radio RX**.
   Silence while searching for a RADE signal is normal. TX initially is disabled.
2. With TX disabled, briefly press/release panel PTT. It must stay in RX. If it
   keys, select Stop and end the test; do not enable TX.
3. Select **Enable / disable TX** locally. Start the SDR audio recording. Press
   panel PTT, wait for **Transmitting**, and speak for about eight seconds:
   "RF test turn one. One two three four five. The quick brown fox jumps over
   the lazy dog. End RF test turn one."
4. Release PTT. The display should pass through finishing the modem tail and
   return to **Receiving**, with radio TX ending. Allow three seconds after
   release; if TX persists, select Stop. If it still persists, switch the radio
   off. Do not treat a software request as proof that antenna RF stopped.
5. Stop the SDR recording a few seconds after RF ends. Check that the SDR was
   neither saturated nor muted and save a 1..30 s PCM16 audio WAV.
6. Optional on the dummy load, after the normal release worked: make a second
   short transmission and select Stop while PTT remains held, then release PTT.
   RF should end and the mode must stay stopped, without rekeying. This is a
   stop/fault test, not a speech-completeness test. Do not include this separate
   interrupted burst in the WAV used to judge the complete first phrase.
7. Select **Exit**, release all PTT, and wait for ordinary radio control to
   return. Exit retains ownership until the worker has stopped, restoration was
   sent, a newer RX packet arrived, and the panel PTT is released. Reconnect does
   not automatically restart a faulted session. Save evidence before rebooting:
   development traces are under `/tmp`.

## Collect and decode the SDR recording

Extract the release's RF helper kit. In Windows PowerShell run:

```powershell
powershell.exe -NoProfile -ExecutionPolicy Bypass -File .\Invoke-UsbRfEvidence.ps1 -RadioIp 192.168.4.210 -SdrWav 'C:\path\to\sdr-usb-audio.wav'
```

The helper verifies the installed GUI/decoder, collects the most recent USB
session, and decodes your **SDR recording**, not the internally generated modem
tap. It sends no radio/PTT commands. Listen to the downloaded
`results/offair-decode/offair-decoded.wav` and compare with `results/mic.wav`.
Record first/last words, intelligibility, any dropouts, RF release behavior,
frequency, SDR configuration and the radio's power/ALC indications. Automated
synchronization alone does not establish a successful complete phrase.

Return the printed `usb-rf-results-*.zip`, including on failure. It contains your
voice and off-air recording. Without an SDR recording, omit `-SdrWav` to collect
diagnostics only; that does not count as RF speech verification. If the GUI
faults before TX, collect its stopped trace rather than rerunning or changing
gains blindly. WAV accepts PCM16 mono/stereo at 8, 44.1 or 48 kHz; stereo is
averaged to mono with no normalization. The original WAV stays in the bundle.

## Scope and implementation

The mode retains four explicitly bound streams: radio demodulated RX capture,
radio modem playback, USB microphone capture and USB decoded-speech playback.
One 48 kHz codec alternates directions. Two retained SRC instances explicitly
convert the radio streams to/from a fixed 44.1 kHz client boundary. Pulse converts
the USB microphone from 16 kHz; USB earphones use 48 kHz. Ordinary GUI playback is corked/flushed during
ownership, RECORD remains clear, and MODEM is the stock TX source. Modem PCM is
trimmed to one quarter amplitude before mono sink duplication for conservative
initial drive. The `modem.wav` tap is before that trim, not an RF measurement.

TX modem playback and RX headphone playback each start with a 250 ms software
prefill to tolerate codec-frame bursts; a fully finished short TX can start with
less. Queued accepted speech is submitted before requesting encoder finish.
Panel PTT uses a separate evdev event queue plus current-level readback, retaining
releases even if the GUI stalls. Release-and-repress during drain cannot reuse
the previous turn. Admission waits for TX telemetry, accepted microphone samples
finish before EOO, modem playback must drain before unkey, and a newer RX packet
is required before restarting RX. Stop/device loss/protection fence new input
and clear software TX before audio cleanup. A separate watchdog clears on a
stalled TX worker or overlong press. Stock baseband independent physical/tune
sources and host-loss behavior remain outside this Linux guarantee.

The trace and first 30 seconds per tap are bounded. Runtime queue and resource
counts, RF packet observations and faults support review. Trace RF observations
are packet telemetry, not direct RF instrumentation. SDR recording supplies the
first external RF evidence. Your SDR supports transmit-leg testing; receive-leg
RF qualification, longer conversations and split-frequency operation need a
transmitting peer and remain later steps.
