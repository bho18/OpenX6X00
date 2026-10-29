# Session 1: hand-mic RADE RF proof and ten short turns

Use the diagnostic card already installed: release
`freedv-native-diagnostics-20261028.1`, image source `2083e3e7`.
**No reflash, standalone codec batch or two-minute fixture repeat is needed.**

The operator has selected proof of concept first. The three functional recovery
checks passed previously; the exact external 500 ms RF-stop measurement remains
a separate qualification. The local Stop confirmation below records functional
observations; it does not assert a measured RF time.

## Before touching the radio controls

1. Keep the dummy load connected, hand mic plugged in, physical PTT released.
   Use USB, split off, TX shift zero. Cases request 1 W and limit software TX to
   ten seconds. Preserve the working card, settings, baseband and data partition.
2. Keep the ordinary GUI/waterfall active. Close other radio digital/audio apps
   and remote-control sessions. Do not start a second physical audio recorder.
3. On Windows, extract the next-test package. In PowerShell, in that folder:

   ```powershell
   python .\native_session.py prepare --output .\evidence
   ```

   Put short SDR USB WAVs and optional timing video in `evidence`. Enter device,
   application and observations in `evidence\session.json` and `notes.txt`.
   Associate each WAV with its displayed radio case number in `recordings.json`;
   use relative paths such as `turn-01.wav`, not the example text or a PC path.
4. Set the SDR to the same RF frequency in USB. Disable squelch, audio noise
   reduction and notch filters. Choose a passband that contains the full modem
   signal; do not trim it to the tone used in routing tests. Check for overload.
5. Verify that a short USB WAV and IQ recording actually contain samples. For
   USB WAV prefer 48 kHz mono PCM16; other formats can be converted during analysis.
   Record IQ at a supported rate covering the entire signal and adjacent noise.
   Note its format, sample rate and center frequency. Keep large IQ on the PC:

   ```powershell
   Get-FileHash 'C:\path\to\your-recording' -Algorithm SHA256
   ```

   Enter its actual path/hash in the matching recording entry. A continuous IQ
   recording for the session is acceptable if you note each case's time range.
6. Start recordings about one second before Run and finish about one second
   after RF ceases. If tone/digital audio is absent, overloaded or clipped, fix
   the SDR capture before proceeding; do not infer a radio encoding failure.

## Acquire and qualify two profiles

Open **Settings -> Info -> Open FreeDV Diagnostics**, Acquire, then wait for Ready.
Use the local MFK turn/press controls. Leave **Hand mic / Existing mono / Keep mixer**
selected throughout. Keep the panel acquired until all ten turns finish.

The top dropdown selects a case. The smaller route dropdown controls the generic
codec/Stop cases. A named Route case uses the bits in its own name, so set both
consistently. Selection never transmits; every potentially keyed case needs its
own local Arm -> Run.

1. Top **Route R1 M0 P0**, small route **R1 M0 P0**, Arm -> Run; let its eight
   seconds finish. Expect a speaker beep, packet RX, no RF. After Ready, immediately
   select **Confirm unkeyed RX route**, then Run without Arm. This qualifies
   demonstrated speaker playback; independent live RF capture is still unresolved.
2. Top **Route R1 M1 P0**, small route **R1 M1 P0**, record SDR, Arm -> Run.
   Say "Hand microphone route test" immediately. Expect a generated tone on RF,
   silence from the speaker and microphone speech in Linux capture. Let it finish.
   If SDR output contains ordinary analog speech, or the route differs from prior
   behavior, stop dependent live-TX tests and preserve this evidence. Otherwise,
   immediately select **Confirm independent mic/TX route**, then Run without Arm.
3. Same small TX route and settings: **Manual Stop test**, Arm -> Run -> Stop
   after about two seconds. Verify RF stops and Ready/restoration returns.
4. **Watchdog timeout test**, Arm -> Run; let it stop at about three seconds.
5. **Audio failure test**, Arm -> Run; let the intentional failure at about 1.5
   seconds stop it. One injected audio failure is expected in this case.
6. If all three recovered normally, select **Confirm Stop/timeout/failure
   observations**, Run without Arm, same small TX route and settings.

Steps 3-5 repeat only because this image clears qualifications on Close/restart.
Do not claim the separate 500 ms RF requirement from their command timestamps.
Record the displayed case numbers, especially the future live TX cases.

## Ten turns: approximately 5-10 minutes

For each turn, with Hand mic / Existing mono / Keep mixer unchanged:

1. Set small route **R1 M0 P0**. Select **Interrupted RADE fixture 20s**, Run
   without Arm. Listen to decoded speech from the speaker. The known waveform
   has an intentional interruption around seconds 10-12; note recovery afterward.
   This feeds a file to the decoder, not a signal received through RF.
2. Once Ready, set small route **R1 M1 P0**, select **Hand/built-in mic RADE TX 8s**.
3. Start/mark SDR WAV and IQ. Press Arm -> Run locally. Immediately say a short
   phrase: "Turn one, X6200 hand microphone test," changing the number each turn.
   Speak during roughly the first four seconds; the current radio WAV excerpt
   ends at four seconds, although SDR captures the complete RF transmission.
4. Let TX finish normally, except **turn 3: press Stop after about three seconds**.
   A normal TX reserves its final second for codec end-of-over processing; do not
   keep speaking through the end. Abort must not wait for that tail.
5. Confirm packet RX, RF off and restoration. Wait about five seconds in Ready.
   Record the displayed TX case number, WAV filename and any issue in notes.

The SDR should hear digital modem sound, not intelligible analog voice. Successful
RADE TX is established by reference-decoding the returned recording and recognizing
your phrase. Hearing digital noise or seeing a waterfall is not sufficient.

If a qualification is refused, verify exact route/mic/channel/mixer selections.
Do not bypass the guard. If physical PTT is held, release it; software cannot
override it. Stop dependent cases for stuck RF, stale status, capture faults,
failed restoration or sustained audio problems; retain independent evidence.

## RF timing evidence without delaying the proof

If available, use one camera to show the local Stop action and live SDR RF-power
indication together. Focus the radio control on Stop before measuring so navigation
time is excluded. Continuous IQ must confirm that disappearance is RF-off, not
an audio gate, display pause or recording gap. Use the earliest visible button
action and a conservative later RF-off indication; record frame uncertainty.

An upper bound <=500 ms supports PASS. A longer observed display interval with
unknown positive SDR/display delay is INCONCLUSIVE, not an automatic RF failure.
Never compare unsynchronized PC and radio wall clocks to claim this measurement.
The `timing` helper records a manually measured interval and uncertainty against a
real evidence file; it does not extract timing from video automatically.

## Read-only audio inventory and export

Press Export after restoration. Collect topology while the ordinary GUI remains
running; this helper only lists devices/controls/streams:

```powershell
$RadioIp = '192.168.100.64'
scp -O .\inventory.py "root@${RadioIp}:/tmp/freedv-topology.py"
ssh "root@$RadioIp" 'python3 /tmp/freedv-topology.py --output /tmp/freedv-topology-session1.json'
scp -O "root@${RadioIp}:/tmp/freedv-topology-session1.json" .\evidence\
```

If inventory reports an existing output file, copy the existing result or choose
a new filename. Do not overwrite or delete radio evidence. No gzip module is needed.

Complete `recordings.json` associations. Unknown fields may stay null. WAV files
go in `evidence`; large IQ stays on the PC with its path/hash/format and case times.
Then run:

```powershell
powershell -ExecutionPolicy Bypass -File .\Collect-NativeSession.ps1 -RadioIp $RadioIp -EvidenceDir .\evidence
```

This copies all `/tmp/freedv-native-*` folders and your selected evidence into a
new timestamped Downloads folder, creates one ZIP, and prints its path/hash.
It cannot Arm or Run. Upload that ZIP. Do not reboot before copying it.

Reference decoding and analysis happen after return, using pinned tools; the
operator is not required to install a decoder. No automatic upload occurs.
