# X6200 persistent FreeDV candidate: end-to-end operator tests

Use this one procedure from first boot through returning the results. It replaces
the Session 1, Session 2 and TX-delivery instructions for the **2026-11-02
persistent test candidate**. Those documents remain historical references.
This documentation update does not change the image; no reflash is needed if
you have already installed that candidate.

The delivered source is `f8d624b3a7ffdb1992715d184e4e090b6d250cab`; the GUI build
identifier is `native-voice-test-0da34f37b9ed`. The compressed SD image SHA256 is
`9f1cf21ddc48e62aa94dd439fcc8d5f84db8d277dcadc19a0ce6b7ed25287aa9`.

The available entry is **APP → Modes → FreeDV test**. It opens locally armed
diagnostic cases, including the actual persistent engine/audio adapter. Normal
automatic PTT operation is still gated. The tests below exercise what this image
actually exposes; the last section identifies the gates it cannot complete.

## 1. Equipment, recovery and first boot

For the first session, have the X6200, connected hand microphone, candidate card,
preserved working `.2` card, dummy load, suitable RF coupling/attenuation,
receive SDR, SDR++, and a computer with SSH/SCP. A phone can record your spoken
phrases and film Stop observations. Never connect transmitter output directly
to the SDR input.

A transmitting peer or controlled RF source is needed only for section 9.
A receive-only SDR cannot supply that signal. An installed official FreeDV GUI
with **RADE V1 / RADEV1** is useful for immediate listening in section 6;
without it you can collect the short recordings and return them for decoding.

If the image is not installed yet, download **x6200-persistent-voice-test.zip**
from the **freedv-persistent-test-image-20261102** GitHub prerelease and extract
it. The extracted folder contains one complete **x6200-persistent-voice-test.img**
file. Write that `.img` to a separate spare card with your usual image writer
and enable write verification. No image assembly or parts are needed. The
separate `.img.xz` release asset is an optional compressed copy for writers
that support xz. Preserve the working card and settings backup. Do not update
the baseband or change the update signing key.

Boot with **both physical PTT controls released and the dummy load connected**.
Check ordinary receive, speaker, frequency/VFO, controls and waterfall. Record
the Info/application and baseband versions. Stop here if ordinary operation is
broken, the candidate entry is missing, or the build identity is wrong.

Keep these recovery rules throughout:

- After every case, require **Ready**, fresh packet **RX**, RF off on the SDR,
  and completed restoration before starting the next case.
- Press the radio panel's **Stop** for an unexpected fault, sustained stuttering,
  stale/unknown status, protection warning, stuck RF or failed restoration.
  Release all physical PTT controls. Do not press confirmation buttons to get
  past a failed observation.
- **Close** requests cancellation and waits for safe restoration. If RX cannot
  be established, stop testing, power down and boot the preserved working card.
  Copy results before a planned reboot whenever recovery permits it.
- The software cases request **1 W** and bound software-controlled RF to
  **10 seconds**. A physically held PTT can keep RF active independently; release
  it promptly. Treat every named Route probe as potentially transmitting.

## 2. Make one results folder on the computer

The Windows commands below run in PowerShell from the **extracted image ZIP
folder**, which contains `candidate-manifest.json` and `guide`.
Replace the example address with the radio's actual address.

```powershell
$RadioIp = '192.168.100.64'
$Evidence = Join-Path $PWD ('evidence-' + (Get-Date -Format yyyyMMdd-HHmmss))
New-Item -ItemType Directory -Path $Evidence | Out-Null
Copy-Item .\candidate-manifest.json $Evidence
$Notes = Join-Path $Evidence 'notes.txt'
New-Item -ItemType File -Path $Notes | Out-Null
scp -O "root@${RadioIp}:/usr/share/x6100/freedv/build-manifest.json" (Join-Path $Evidence 'radio-build-manifest.json')
Write-Host "Save short recordings and notes here: $Evidence"
notepad $Notes
```

Resolve any connection/copy failure before starting RF tests, so you know you
can save the radio's temporary results. The SSH/SCP commands in this guide
collect files or perform the explicitly described service restart; they do not
Arm or Run a case. No Python installation is needed for collection.

Start `notes.txt` with:

```text
Operator/date:
Radio/application/baseband versions:
Image: persistent test candidate 2026-11-02
Frequency / active VFO / split / TX shift:
Profile: USB or USB-DIG; RF orientation: upper sideband
RX/TX filter settings / AGC / compression / NR / notch / EQ:
Hand-mic / line / monitor / speaker gains:
SDR model / SDR++ version / gain / attenuation / AGC:
SDR Audio rate/channels/format; IQ rate/format/center frequency:
Official GUI version, if used:

For EVERY case:
Test label / profile / radio result folder / displayed case number:
Route / microphone / capture channel / mixer:
Spoken phrase, first word and last word, if applicable:
SDR Audio filename / IQ filename and path / phone recording:
Speaker observation / analog speech on RF? / gaps or stuttering?:
Ready + fresh RX + RF off + restoration after case?:
Result: PASS / FAIL / BLOCKED / INCONCLUSIVE; reason:
```

Use the displayed case numbers, not guessed timestamps. Confirmations do not
create a new numbered audio case. Do not use the older `native_session.py
prepare` template for this candidate: it carries an earlier image identity.

## 3. Set up SDR++ recordings once

1. Select **USB** on the SDR and tune its receiver/VFO to exactly the X6200's
   displayed frequency. Use an approximately **3 kHz** passband containing the
   full modem signal. USB-DIG on the X6200 still uses upper-sideband RF here.
2. Disable SDR squelch, noise reduction and notch. Set RF gain/attenuation to
   avoid overload. Record AGC and gain settings; keep them fixed during a case.
3. In **Module Manager**, add two `recorder` instances, named **FreeDV Audio**
   and **FreeDV IQ**, or use an existing instance for one of them.
4. Set FreeDV Audio to **Audio**, the selected USB receiver stream, **WAV /
   Int16 / mono**, and **Ignore silence OFF**. Save short WAVs in `$Evidence`.
   Keep the stream's actual sample rate, normally 44.1 or 48 kHz; note it.
5. Set FreeDV IQ to **Baseband**, preferably **WAV / Int16**. Choose a supported
   hardware rate covering the signal and adjacent noise. Save IQ separately
   on the PC and note its rate, center frequency and format. IQ has I/Q channels;
   it is not the mono WAV used for speech decoding. At 1 MS/s it uses about
   4 MB/s. Record per case to avoid filling the disk or a WAV's size limit.

For every RF case: start **both** recorders about one second before Run, check
their duration counters advance, and stop about one second after RF ends.
After stopping, name the files with profile and test label, for example
`USB-F1-audio.wav` and `USB-F1-iq.wav`, and write the actual case number in notes.
Keep pre/post silence. Check the first tone recording contains the tone, not
the PC microphone or a different VFO. Correct clipping, missing data or wrong
recording source before continuing.

## 4. Select the USB comparison profile and learn the controls

Before opening diagnostics, choose your permitted HF test frequency and VFO,
**USB**, split **OFF**, TX shift **zero**, approximately 3 kHz filtering and a
low ordinary TX power setting. Use explicit processing settings: turn off
compression, NR, notch and EQ where available, and record any control you cannot
disable or read. Choose usable hand-mic gain and a comfortable speaker level;
record all gains and effective filter/AGC settings. Do not assume a mode label
disables processing automatically.

Close competing digital/audio applications and remote CAT/TCI/web control
sessions. Keep the normal GUI/waterfall running. Do not start a second recorder
on the radio's audio device.

Open **APP → Modes → FreeDV test**. Use the **MFK** knob: turn to focus a
control, press to edit/select, then turn/press to choose. The panel contains:

- Top dropdown: **case name**.
- Smaller dropdown: **route** (`R… M… P…`). R/M/P are commanded RECORD, MODEM
  and software PTT bits, not independent physical measurements.
- Select **Hand mic / Existing mono / Keep mixer** for the initial comparison.
- **Acquire** once; wait for **Ready** and fresh packet RX.
- Each Route/TX/recovery case needs **Arm → Run** for the exact same selections.
  Every new RF case needs a new Arm. Receive cases and confirmations use
  **Run without Arm**.
- **Stop**, **Export** and **Close** are separate buttons. Export does not
  upload or copy results to the computer.

Keep the session acquired through the short sequence below. Do not tune, change
VFO/band, change ordinary gains or switch USB/USB-DIG while acquired. To change
those settings, wait for restoration, Export, Close, change normally and
Acquire again. Re-establish the confirmations after reacquiring. Changing the
persistent capture channel or mixer also requires Close/reacquire.

## 5. Run this short sequence on USB

Record every numbered case in notes. Use SDR Audio + IQ for all potentially
keyed cases. Keep **physical PTT released** throughout this section.

### A. Capture and unkeyed playback

1. **A1 — `Capture 10s`: Run without Arm.** Say "Capture check, hand microphone"
   immediately. Expect no software-keyed RF. Microphone silence in this passive
   RX case alone does not establish failure of capture while keyed.
2. **A2 — `Route R1 M0 P0`: Arm → Run.** Set the smaller route dropdown to
   **R1 M0 P0** as well. Let the eight-second probe finish. Check the generated
   tone reaches the speaker and that packet status and SDR remain unkeyed.
3. Immediately select **`Confirm unkeyed RX route`**, same smaller route and
   mic/channel/mixer, then **Run without Arm**. Require **"Observation confirmed
   locally"**. Confirm only if A2 was actually unkeyed and playback worked.
   This unlocks unkeyed playback; it does not prove independent RF receive.
4. **A3 — `Interrupted RADE fixture 20s`: Run without Arm**, smaller route
   R1 M0 P0. Listen for decoded speech, the intentional gap around seconds
   10–12, and recovery afterward. Record stutters and whether the GUI remains
   responsive. This is a file-to-speaker control, not RF reception.

### B. Hand-microphone route

1. **B1 — `Route R1 M1 P0`: Arm → Run**, smaller route **R1 M1 P0**, still
   Hand mic / Existing mono / Keep mixer. Say "Microphone route, alpha to omega"
   immediately. Let the probe finish.
2. Expect generated tone on RF, usable microphone speech in `capture.wav`,
   and **no intelligible direct analog microphone speech on RF**, including
   the start and end. Watch the capture AC/peak activity, but do not treat a
   moving meter as proof of microphone identity or usable speech.
3. Check the saved microphone WAV before confirming if you cannot establish
   usable capture. With the panel Ready, copy that case's file and listen on
   the PC. Use the actual displayed folder and case number, for example:

   ```powershell
   $RemoteCase = '/tmp/freedv-native-REPLACE-WITH-DISPLAYED-FOLDER/case-REPLACE-WITH-NUMBER'
   scp -O "root@${RadioIp}:${RemoteCase}/capture.wav" (Join-Path $Evidence 'USB-B1-microphone.wav')
   ```

4. If B1 meets those observations, **immediately**, before running another
   audio case, select **`Confirm independent mic/TX route`**, same smaller route
   and mic/channel/mixer, **Run without Arm**. Require the confirmation message.
   If capture is missing, wrong or unintelligible, or analog speech leaks to RF,
   do not confirm. Record FAIL/INCONCLUSIVE and skip the microphone cases below.
   The recovery and known-waveform tests remain useful if recovery is normal.

### C. Functional Stop, timeout and audio-failure recovery

Keep the smaller route **R1 M1 P0** and the same mic/channel/mixer:

| Label | Exact case and action | Required observation |
|---|---|---|
| C1 | `Manual Stop test`: Arm → Run; press the radio panel's Stop after about two seconds | RF ceases, fresh packet RX returns, restoration completes |
| C2 | `Watchdog timeout test`: Arm → Run; let its approximately three seconds expire | Same recovery, with timeout as the cause |
| C3 | `Audio failure test`: Arm → Run; let its intentional failure at about 1.5 seconds stop it | Same recovery; one intentional audio failure is expected |

Wait for Ready/restoration between cases. An unexpected additional failure, a
manual case that merely times out, or incomplete restoration does not qualify.
If all three behave correctly, select **`Confirm Stop/timeout/failure
observations`**, same route/mic/channel/mixer, **Run without Arm**; require the
confirmation message. If refused, keep the evidence and stop dependent TX trials.
These are functional observations, not a measured 500 ms RF-off result.

### D. Known modem waveform, then the persistent microphone engine

1. **F1 — `Known RADE waveform TX 8s`: Arm → Run.** Stay quiet. Record the full
   SDR Audio and IQ. This bypasses microphone encoding and tests delivery of a
   known modem waveform to RF. Its bounded cutoff is deliberate; F1 is not an
   end-of-over/drain test. It needs C's confirmation, but not B's microphone
   confirmation.
2. If B and C passed, run **P1 — `Persistent hand-mic TX 8s + finish`: Arm → Run**.
   When Running/TX appears, immediately say:
   **"Alpha, USB turn one, one two three four, omega."** Finish speaking within
   about five seconds, then stay quiet. Do not press microphone PTT. Use a phone
   or independent PC microphone recorder to retain the original spoken phrase,
   if available, and associate it with P1.
3. Let P1 finish naturally. Microphone admission closes automatically after
   eight seconds; accepted speech, EOO, 200 ms silence tail and device drainage
   should complete before unkeying. Allow up to roughly 9.5 seconds of normal
   finish; the independent RF watchdog remains ten seconds. A drain deadline
   failure or watchdog cutoff is a failed normal finish, not a successful EOO.
4. **P2 — same persistent case, new Arm → Run**, without Close. Say
   **"Bravo, USB turn two, five six seven eight, zulu."** Let it finish normally.
   The distinct words test retained-context turns and stale speech.
5. **P3 — same persistent case, new Arm → Run**, speak a new numbered phrase,
   then press the panel's **Stop after about three seconds**. This is an abort:
   a cut-off speech tail/absent EOO is expected. Require prompt RF-off and safe
   restoration. It must not wait for graceful finish.
6. **P4 — same persistent case: Arm, then Stop before Run.** If Preparing is
   visible, Stop then; otherwise Stop once Armed. Require cancellation and
   restoration without RF ever starting. Record which state you actually
   interrupted; an Armed cancellation does not establish a Preparing test.

Listen to F1 and the complete P1/P2 SDR WAVs using section 6 before committing to
ten turns. If no decoder is available, finish the other short profile in section
7, collect using section 11 and return the evidence for review. Do not spend
time on ten distorted or unassessed turns.

## 6. Decode and listen on the official FreeDV GUI

This can be done after copying recordings; it does not need another radio or a
virtual audio cable. Keep the reference GUI disconnected from X6200 CAT/PTT.
Use headphones while testing speech to avoid feedback into the hand microphone.

1. Record the installed official GUI version, platform, installer/executable
   hashes when available, audio devices and rates. Explicitly select
   **RADE V1 / RADEV1**. A RADE V2-only release cannot do this V1 test.
2. Configure its normal receive/speaker device and start the modem. Before each
   playback, where available, use the GUI's **Record** control, select
   **Decoded / WAV**, and start saving to `$Evidence`, so the first word is kept.
3. Use **Tools → Start Play File - From Radio…** to select the **mono SDR Audio
   WAV**, with looping off. Do not select the I/Q WAV or ordinary microphone
   speech. Listen to F1, then P1 and P2, saving a separate decoded output for
   each. Stop recording after the file and decoder tail have finished. Labels
   can differ by installed release; record any missing playback/record capability.
4. Write the words you actually recognize, especially **Alpha/omega** and
   **Bravo/zulu**, the turn number, distortion, missing starts/ends, gaps and any
   previous-turn phrase. Record listener name/date and decoded filename/hash.
   Sync lights, digital noise and nonzero audio do not pass intelligibility.

F1 clear but P1/P2 bad points to a microphone/encoding/live-delivery issue. Bad
F1 means the known-waveform RF/recording path also needs investigation. Keep the
originals in either case. Leave listening INCONCLUSIVE if no listening assessment
is made. This installed-GUI check is one reference track; it does not substitute
for the separately pinned complete source-built GUI and full rate matrix.

## 7. Repeat the same short sequence on USB-DIG

After USB restoration, **Export**, note the folder, **Close**, and verify ordinary
RX. Switch normally to **USB-DIG**, keeping frequency/VFO, upper-sideband RF,
waveform, bandwidth, gains, power and SDR setup matched. Read and record effective
settings **after** switching: SSB and DIGI saved groups may differ. Explicitly
reapply matching filters/processing settings. Do not assume the previous values
carried over.

Acquire again and repeat **all of section 5**, including its local confirmations,
on USB-DIG. Use `USB-DIG-…` recording labels and say "USB digital" in P1/P2.
Do not transfer confirmations or a successful route/capture result from USB.
Listen as in section 6. Preserve failed and inconclusive recordings from both.

If a baseline route fails, record that exact local mode/route/mic/channel/mixer.
Do not guess register values or call every native route failed. Supported route
or mixer alternatives are separate experiments requiring their own probes and
confirmations. Built-in microphone exploration is outside this hand-mic session.

For the longer trial, use a profile that passed the short speech/delivery/recovery
checks; use USB-DIG if both did. This is a provisional test choice. A production
profile can be selected only after every required capability passes: both pass
→ prefer USB-DIG; only one passes → that one; neither fully passes → release
blocked. Do not combine successful halves of different profiles.

## 8. Ten retained-engine turns, about 5–10 minutes

Run this only after the short recordings have intelligible listening acceptance.
Keep one local mode, route mapping, microphone/channel/mixer and acquired session
through all ten cycles. If Close/reacquire was necessary, repeat the section 5
route probes and C confirmations for that exact setup first.

For each turn 1–10:

1. Smaller route **R1 M0 P0**, case **`Persistent live receive 120s`**, **Run
   without Arm**. Receive for about 20 seconds, then Stop and wait for Ready/RX.
   With a peer, have it send a numbered RADE V1 phrase and pause/restart its
   signal during turns 4 and 8. Without a peer, keep the dummy load connected:
   this tests live no-sync capture/acquisition and direction changes only;
   valid RF decode/reacquisition remains BLOCKED.
2. Smaller route **R1 M1 P0**, case **`Persistent hand-mic TX 8s + finish`**,
   new **Arm → Run**. Say **"Alpha, turn [number], persistent FreeDV test,
   omega"** within the first five seconds. Record SDR Audio and IQ with that
   number. Except turn 3, let it finish naturally.
3. On **turn 3**, Stop after about three seconds. Expect explicit truncation,
   no delayed previous-turn tail, and safe recovery. It is an abort case, not
   a normal-finish listening pass.
4. Wait about five seconds in Ready before the next cycle; before turn 7 wait
   about 30 seconds to exercise a turn after idle. Note GUI responsiveness,
   activity, stutters, unexpected faults and any speech from an earlier turn.

If a peer/source is connected, use the fixture's approved switching/isolation
arrangement before each X6200 TX. Otherwise reconnect the dummy load while both
transmitters are unkeyed. Never Arm into an unprotected receiver/source connection.

Listen to and retain each normal TX decode, with first/last words and turn
identity. The exported counters must show stable creation counts through this
acquired session: one live RADE/encoder/decoder set and four resamplers; turns
and EOO counters are cumulative, so compare deltas. Unexpected growth, duplicate
EOO on normal finishes, unexplained sample loss, repeated in-speech underflows
or sustained backlog needs investigation. The exported counters support that
review; silence from the speaker alone does not prove it.

This is a mixed diagnostic exercise. Without a transmitting peer it is not a
bidirectional conversation, even if every transmitted phrase decodes on the PC.

## 9. Controlled RF receive and speaker independence — with a peer/source

If you have only the receive SDR, mark this section **BLOCKED: no controlled RF
source**, then continue to section 10. Playing the internal fixture is not a
substitute. Do not connect computer playback to the X6200 mic as an RF-RX test.

Use a peer transmitter driven by the official GUI in **RADE V1**, upper-sideband
orientation, through suitable isolated/coupled/attenuated RF test equipment.
Start with a known clean, non-overloading signal. Keep the X6200 unkeyed during
receive measurements; independently monitor the peer with the SDR. Save the peer's
original numbered speech/modem output and record its complete build/audio setup.

For **each USB and USB-DIG local profile**, with matched effective settings:

1. With the X6200 speaker at its normal test level, Acquire and confirm the
   unkeyed R1 M0 P0 playback route as in A2/A3. Run **`Capture 10s`** while the
   peer supplies an identifiable RF signal. This is the capture reference with
   no generated local playback; note the peer's distinct phrase/marker.
2. Run **`Local tone during receive 20s`**, smaller route R1 M0 P0, with the
   same external RF signal. The local tone is 735 Hz; the peer must be clearly
   distinguishable. This case saves the full short capture for comparing RF
   survival and local playback contamination.
3. Run **`Persistent live receive 120s`**, same confirmed smaller route, and
   let it complete the full two minutes. Have the peer send numbered phrases,
   introduce a brief known interruption around 40–45 seconds, then resume.
   Record speaker audio externally for the full run, SDR IQ/Audio of the peer,
   observed acquisition/reacquisition and intelligible first/last words. Do not
   call an early Stop a completed two-minute run.
4. Export/Close, set the speaker level to zero normally, Acquire/confirm again
   and repeat steps 1–3 with the matched peer waveform. A quiet speaker is
   expected; the saved decoder output and counters must establish decoding.
   For the muted route probe, verify RF remains off and the saved local output
   is present; do not claim you heard the muted speaker. Then restore the normal
   level and recheck it. Volume dependence alone does not distinguish acoustic
   from electrical feedback.
5. To retain both raw capture channels, perform a separate repeat with
   **Left capture / Keep mixer**; this additionally saves `capture-stereo.wav`.
   Reconfirm the exact setup after Close/reacquire. Compare both channels before
   trying a supported **Sum Mono** or other exposed mixer alternative. Do not
   infer independence from a channel swap. Each changed setup is a separate
   profile experiment; do not borrow its result for the baseline setup.

RX case WAVs are bounded to the first four seconds; the counters cover the full
120 seconds. That is why the full external speaker/peer recordings and the
20-second marker case are needed. Retain raw recordings, not just a listening aid.
Identify external RF separately from local playback in the assessment.

Run the peer's audio boundary at 44.1 and 48 kHz, with recorded settings, when
supported. The native 8 kHz reference and separately built official-GUI track
are engineering qualification cases; do not relabel one installed-GUI RF run as
the complete three-rate/two-track interoperability matrix.

## 10. Physical controls, Stop evidence and restart

Do this only after ordinary Stop/recovery is predictable. Use the dummy load
and SDR. Collect section 11 before restarting any process. A physically held
PTT observation is separate from software-owned abort timing.

1. After Export/Close, test ordinary **radio-side PTT** press/release, then
   **hand-mic PTT** press/hold/release, separately at low power. Record RF start,
   RF-off after release and fresh RX. Ordinary analog speech here is expected;
   these are hardware baselines, not native FreeDV transmissions.
2. Reacquire the demonstrated profile. On `Manual Stop test`, Arm → Run, briefly
   press/hold hand-mic PTT, then press panel Stop. Release the physical control
   about one second later. Observe whether restoration stays blocked while TX
   is physically held and completes only after release/fresh RX. Keep it brief.
   Stop does not promise to override physical PTT. Note any analog leakage at
   transitions. Do not infer independent hand-mic release under active MODEM
   from aggregate packet TX; that interface remains unsupported in this image.
3. For software-owned Stop with **all physical PTT released**, film the actual
   Stop action and live SDR RF indication together while retaining continuous
   IQ for that case. Record frame rate, the event definition and known display/
   recording delays. This is useful correlation evidence, not an automatic
   500 ms pass. A valid upper bound must include all alignment/delay uncertainty
   and be at most 500 ms. If the setup cannot establish that bound, leave the
   gate INCONCLUSIVE or use synchronized event-trigger/RF-envelope instruments.
   Do not use software command times or disappearance of audio as RF-off proof.
4. Export/Close and **copy all results with section 11**. With both physical PTT
   released, verify the GUI service exists and deliberately stop/start it:

   ```powershell
   ssh "root@$RadioIp" 'ls -l /etc/init.d/S95gui'
   ssh "root@$RadioIp" '/etc/init.d/S95gui stop'
   ssh "root@$RadioIp" '/etc/init.d/S95gui start'
   ```

   If the service is absent or stop fails, record the limitation and do not
   substitute a process kill. Verify startup returns to ordinary RX, never
   resumes a case/arming/TX, and retains frequency/VFO and ordinary preferences.
5. A held-PTT restart is a separate optional advanced observation: only after
   the released restart succeeds, use the dummy load/SDR, hold the hand-mic PTT
   briefly during the same deliberate stop/start with an observer ready to
   release it after about one second, even if the restart has not finished.
   Observe RF/analog audio during the states actually covered and recovery after
   release. Do not prolong the hold to force completion. Record any startup
   interval not covered, or NOT RUN if omitted. A released restart does not
   cover held-PTT behavior or arbitrary crashes.

Physical-source overlap, independent hand-mic release while MODEM remains active,
and PTT-driven graceful native turns cannot be qualified by these bounded
diagnostic cases. Record the observations without claiming those gates passed.

## 11. Export, copy and return one bundle

Do this after **each local profile**, before every restart/reboot, and whenever
the panel reports a storage reserve/budget limit. Wait for restoration, press
**Export**, note the displayed `/tmp/freedv-native-…` folder, then Close when you
are finished with that acquired session. You may Export/copy while Ready without
Close if retaining the engine for more turns. Do not delete radio evidence to
make room or continue after a storage fault.

Radio capture is explicitly bounded. TX excerpts allow 9.5 seconds; persistent
RX excerpts are four seconds even for a full two-minute run. Selected stereo
cases also save `capture-stereo.wav`. The `/tmp` folder is temporary and must be
copied before reboot. Close/reacquire clears confirmations but does not itself
start a new results directory or reset its 64 MiB recording budget. If the budget
is reached, first copy/verify the evidence, Close safely and use the released-PTT
service restart in section 10 to obtain a new process/results directory.

Copy short SDR Audio WAVs, decoded WAVs, phone speech/video clips small enough
to include, and notes into `$Evidence`. Keep large IQ/video on the PC. Hash each
referenced large file and paste its path, hash, format and matching case into notes:

```powershell
Get-FileHash -LiteralPath 'C:\actual\path\USB-F1-iq.wav' -Algorithm SHA256
```

From the same extracted GitHub folder/PowerShell session as section 2:

```powershell
powershell -ExecutionPolicy Bypass -File .\guide\Collect-NativeSession.ps1 -RadioIp $RadioIp -EvidenceDir $Evidence
```

It copies **all** `/tmp/freedv-native-*` folders and your selected evidence into a
new timestamped folder in Downloads, creates one ZIP and prints its path/hash.
It never keys RF or uploads. If it reports a copy failure, keep the radio running,
fix the connection and retry; do not reboot. Open the completed ZIP and check it
contains `radio`, `operator`, the expected cases and notes before ending the session.
Return that ZIP. Send the short decoded listening files/assessments with it;
retain large IQ at the referenced paths for follow-up.

Linux/macOS collection, from the extracted download folder:

```sh
radio_ip='192.168.100.64' # replace with the actual address
results="freedv-results-$(date -u +%Y%m%d-%H%M%S)"
mkdir -p "$results/radio" "$results/operator"
scp -O -r "root@${radio_ip}:/tmp/freedv-native-*" "$results/radio/"
```

Stop if SCP failed; keep the radio running and fix the copy before packaging.
Copy your chosen notes, manifest and short WAVs into `$results/operator/`, then:

```sh
tar -czf "$results.tar.gz" "$results"
shasum -a 256 "$results.tar.gz"
```

Use the same case associations and recording settings on either OS. Large IQ
stays outside the archive. No toolchain, SDK, image build or old codec batch is
required from the operator.

## 12. What the results establish, and what remains

Use **PASS** only with the stated recordings and observations; **FAIL** for an
observed failure, **BLOCKED** for a missing prerequisite, and **INCONCLUSIVE**
for inadequate or unassessed evidence. A failed route applies to that exact
build/local mode/route/mic/channel/mixer, not automatically to every USB route
or to USB-DIG.

The returned bundle should let us assess, separately: known-waveform RF
delivery; captured and RF-recovered microphone speech; normal persistent finish
and abort; turn identity/retained resources; two-minute live RX where supplied;
speaker contamination; functional physical-control/restart behavior; and the
validity of RF timing evidence. The historical 70% processing-margin failure
remains attached to its original evidence; intelligible speech alone does not
reverse it.

This image cannot complete the normal-mode tests for physical-PTT-driven turns,
short-tap/mid-frame PTT release, multi-source overlap/release lockout, the
180-second default/30–600-second timeout, local RX tuning/bookmark editing,
mode replacement during TX, or a continuous two-minute native TX. Its diagnostic
timeout test is three seconds and every software-controlled RF case is at most
ten seconds. Record these normal-mode gates **BLOCKED by this candidate's test
interface**, not PASS or a hardware failure. Independent hand-mic release under
MODEM remains a documented release blocker. They require the remaining hardware
interface/qualified-mode work and a later qualified candidate.

The full 100-turn/100-lifecycle automated checks and complete official-GUI
reference/rate matrix remain engineering qualification work, with their own
identities. A successful short operator session is useful test evidence and is
not a finished bidirectional native-mode release claim.
