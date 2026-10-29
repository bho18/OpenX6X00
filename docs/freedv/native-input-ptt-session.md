# Next session: microphone input and PTT gating

Use the existing `.2` diagnostic card, source
`21df019884da3e8cdc1b4521a4b4f8a51ca210e1`. No reflash or codec benchmark is
needed. We want to hear what actually reaches Linux capture with hand-mic PTT
released and held, then reference-decode one matched local/RF transmission.
This does not implement or qualify pressing hand-mic PTT to start FreeDV.

There is one controlled physical-button comparison below. All other cases keep
physical PTT released unless the conditional live-mic instructions explicitly
say otherwise. Physical PTT can key the baseband independently; software cannot
enforce its timeout against a held button. Release immediately on unexpected
behavior, then press Stop. If RF will not stop, end the test and power off rather
than keeping it keyed to save files.

Keep the dummy load connected, requested power at 1 W, USB, split OFF and TX
shift zero. Keep normal GUI/waterfall active and other digital/audio apps closed.
Keep the original working card. Use the existing SDR receiving arrangement;
never connect transmitter output directly to an SDR input. Do not change gains,
channel or mixer midway through the comparison.

## 1. Prove file copying before RF

SSH is now fixed. Use that current working address. In Windows PowerShell paste
this whole block, including `. {` and the final `}`, as one unit. It copies the
installed build manifest to prove SCP and the PC destination before RF. Use this
same PowerShell window for the rest of the session.

```powershell
. {
    $ErrorActionPreference = 'Stop'
    $KitRoot = Join-Path $env:USERPROFILE 'Downloads\x6200-freedv-next-tests'
    if (-not (Test-Path -LiteralPath (Join-Path $KitRoot 'Collect-NativeSession.ps1'))) {
        throw 'The test-kit script is missing at this path. Stop here and locate the extracted kit.'
    }
    Set-Location -LiteralPath $KitRoot
    $RadioIp = Read-Host 'Current IP address shown on the radio'
    $EvidenceDir = Join-Path $KitRoot ('evidence-' + (Get-Date -Format yyyyMMdd-HHmmss))
    New-Item -ItemType Directory -Path $EvidenceDir | Out-Null
    $BuildFile = Join-Path $EvidenceDir 'radio-build-manifest.json'
    scp -O "root@${RadioIp}:/usr/share/x6100/freedv/build-manifest.json" "$BuildFile"
    if ($LASTEXITCODE -ne 0) { throw 'File copy failed. Stop here; do not start RF tests.' }
    $Build = Get-Content -LiteralPath $BuildFile -Raw | ConvertFrom-Json
    if ($Build.firmware_commit -ne '21df019884da3e8cdc1b4521a4b4f8a51ca210e1') {
        throw 'This is not the expected .2 source. Stop here and report the manifest.'
    }
    'Label | Radio case | SDR filename | Physical PTT | AC/input | RF/recovery notes' |
        Set-Content -LiteralPath (Join-Path $EvidenceDir 'notes.txt')
    Write-Host 'PREFLIGHT PASSED'
    Write-Host "Put this session's SDR audio WAVs and notes in: $EvidenceDir"
}
```

Proceed only after **PREFLIGHT PASSED**. If copying fails, stop and keep the radio
on; do not start cases or continue with script internals after an error. The
[collection recovery guide](native-collection-recovery.md) is available if the
connection fails again; no network troubleshooting is otherwise requested.

## 2. Prepare SDR++ and an ordinary microphone baseline

Use a mono Int16 audio WAV from the correct USB receiver stream, approximately
3 kHz passband. Disable Ignore silence, squelch, noise reduction and notch
processing. Use the same frequency as the radio; keep levels/tuning fixed.
Record IQ with the separate Baseband recorder if available, keeping large IQ on
the PC outside `$EvidenceDir`. See [SDR++ setup](sdrpp-recording.md).

Set the audio recorder's destination to the fresh `$EvidenceDir` printed above.
For each case, start recording about one second before keying/Run and stop about
one second after RF ends. After stopping a recording, label it or note the actual
filename. Never rename a file while it is being recorded. Use labels A0, S1-S3,
P1 and M1 below, and record the radio's actual case numbers separately.

Before opening diagnostics:

If the diagnostic panel is already open, use Close and wait for restored RX
before this ordinary-radio baseline. Do not try it while diagnostics owns the
radio.

1. Select **Hand** under Settings -> Audio -> MICROPHONE. Write down the hand-mic
   gain and built-in gain; keep these values unchanged for this session.
2. Set normal TX power to **1 W** and use **USB** into the dummy load.
3. Record **A0**: hold hand-mic PTT for about three seconds and say
   **"Analog baseline, one two three."** Release it promptly.
4. Expect ordinary intelligible speech on the SDR and RF-off after release.
   If ordinary speech is absent or distorted, stop here and report A0 plus the
   gain/settings. We need a working normal microphone baseline first.

This is ordinary analog operation outside diagnostics, not a FreeDV PTT test.

## 3. Acquire and complete the three required software Stop checks

Open Settings -> Info -> Open FreeDV Diagnostics. Press **Acquire** and require
**Ready**. Set the smaller controls to **R1 M1 P0 / Hand mic / Existing mono /
Keep mixer**. Do not Close, reacquire or change that profile afterward.

The top dropdown selects the named test. The smaller R/M/P dropdown supplies the
route for generic Stop and microphone tests. Merely selecting a test does nothing.

The image resets confirmations on Acquire; these three brief cases are required
to unlock the later microphone case. Keep hand-mic PTT released and do not speak.
Each RF case needs a new local Arm then Run. Wait for **Ready/RX and restoration**
before the next case.

| Label | Top dropdown | Exact action | Expected result |
|---|---|---|---|
| S1 | Manual Stop test | Arm -> Run; press Stop after about two seconds | Beep on RF, then prompt RF-off and Ready |
| S2 | Watchdog timeout test | Arm -> Run; let it finish | Beep stops automatically at about three seconds; Ready |
| S3 | Audio failure test | Arm -> Run; let the injected failure occur | Intentional audio-fault message after about 1.5 seconds, RF-off and restoration |

S3's injected fault is expected. A fault in another case, stale status, stuck RF
or failed restoration is not: stop the sequence and preserve its evidence.

Select **Confirm Stop/timeout/failure observations**, then **Run without Arm**.
Require **"Observation confirmed locally"**. Selection alone does not confirm.

## 4. P1: one capture comparison, released -> held -> released

Keep the same smaller controls. Top dropdown: **Route R1 M1 P0**. Start SDR
recording P1, then **Arm -> Run**. The software keys RF and generates a continuous
735 Hz tone. Only proceed with the physical-button portion after the panel shows
Running/observed TX and the SDR receives that tone. Never hold physical PTT before
Run, while preparing, or after the case ends.

Count from the start of the tone; keep the hand mic at the same distance:

| Approximate interval | Hand-mic button | Say |
|---|---|---|
| First two seconds | Released | "Released, one two" |
| Next two seconds | Hold PTT | "Held, three four" |
| At about four seconds | **Release PTT** | "Released again, five six" |
| Final two seconds | Released, remain quiet | Nothing; let the eight-second case finish |

Physical PTT must be released well before expiry, by about four seconds. If the
case stops/faults early, release immediately and press Stop. Do not hold the
button waiting for the software timeout. This case is a controlled input/routing
qualification, not ordinary FreeDV button operation or a held-PTT Stop test.

Observe and note:

- **AC** capture level: does it rise with speech only while held, in both states,
  or neither? It is an averaged PCM activity number, not an instantaneous speech
  meter; do not apply an invented pass threshold.
- **SDR**: expect the generated tone; check whether ordinary analog speech is
  added or replaces it when the button is pressed. That is a routing failure,
  not a successful encoded voice test. Release, Stop and do not advance to M1.
- **Recovery**: software RF ends at expiry, with physical PTT already released,
  and the panel returns to Ready with restoration complete.

Record the actual radio case number, SDR filename, button intervals and AC
observations. Do not confirm independent microphone routing yet.

## 5. Checkpoint: copy and listen before more microphone TX

With physical PTT released and the panel Ready, press **Export**. Keep the panel
open/acquired and keep the radio on. In the same PowerShell window:

```powershell
. {
    $ErrorActionPreference = 'Stop'
    $Checkpoint = Join-Path $env:USERPROFILE ('Downloads\freedv-input-check-' + (Get-Date -Format yyyyMMdd-HHmmss))
    powershell -NoProfile -ExecutionPolicy Bypass -File .\Collect-NativeSession.ps1 -RadioIp $RadioIp -EvidenceDir $EvidenceDir -OutputDir $Checkpoint
    if ($LASTEXITCODE -ne 0) { throw 'Collection failed. Stop testing; keep the radio on.' }
    $Cases = @(Get-ChildItem -LiteralPath (Join-Path $Checkpoint 'radio') -Filter coordinator.json -Recurse)
    if ($Cases.Count -eq 0) { throw 'No radio cases copied. Do not continue or reboot.' }
    foreach ($File in $Cases) {
        $Case = Get-Content -LiteralPath $File.FullName -Raw | ConvertFrom-Json
        if ($Case.name -eq 'Route R1 M1 P0') {
            Write-Host "Route case $($Case.case_number): $(Join-Path $File.DirectoryName 'capture.wav')"
        }
    }
    Write-Host "Checkpoint ZIP: $Checkpoint.zip"
}
```

Open the printed `capture.wav` matching P1's radio case number. It is raw
microphone-path audio, **not** a RADE modem recording. Listen in a WAV player,
not by asking FreeDV to demodulate it. Use the SDR P1 WAV to check analog leakage.

| Raw capture / RF observation | Action |
|---|---|
| Released phrase clear; SDR has only the generated tone | Advance to M1 with physical PTT released |
| Only held phrase clear; SDR has only the generated tone | Advance only to the bounded held-window M1 variant below; mark released capture unresolved |
| Both states clear; SDR has only tone | Advance to released M1; record both-state capture success |
| No recognizable speech, ambiguous/too-quiet capture, analog voice at RF, or any fault | Stop here and return the checkpoint ZIP; do not encode another noise-only trial or falsely confirm a usable route |

This checkpoint prevents another RF microphone trial with unverified input and
keeps the first evidence safe on the PC. It is not a second session: stay acquired.
If stopped here, the checkpoint is the bundle to return. No new image is needed
just to analyze it.

## 6. M1: one matched live-microphone encoding trial

Only if P1 established clear raw microphone speech **and** no analog RF bypass:
select **Confirm independent mic/TX route**, then **Run without Arm**. Require
**"Observation confirmed locally"**. The preceding completed case must still
be P1 with the same profile. Export/copy does not change that last-case result.
This confirms the observed button condition, not untested released-button support.

Select **Hand/built-in mic RADE TX 8s**, keeping the smaller profile unchanged.
Start SDR recording M1, then **Arm -> Run**.

- If released capture passed: leave PTT released throughout. Immediately say
  **"Digital microphone test, one two three four"** in the first four seconds,
  then remain quiet and let the case finish.
- If only held capture passed: wait until software has started the case and
  the panel shows Running/observed TX, with modem audio on SDR. After about two
  seconds hold PTT, say **"Held digital test, three four"**, and release at about
  four seconds. Remain released until completion. Do not hold before Run or
  through expiry; release immediately on any early fault. Skip this variant if
  P1 did not demonstrate analog-free RF while held.

Expect digital modem sound on the SDR, not directly audible analog speech.
Normal EOO/drain may finish a little before eight seconds. Wait for RF-off,
Ready and restoration. Record the case number, actual button state and SDR file.
Do not adjust gains or run more trials to chase a clearer decode during this
session. The exported capture and local `output.wav` let us locate the loss.

This still uses Arm/Run to start the session. It does not prove independent
physical PTT sensing, automatic FreeDV key-up, or fault-time RF cessation while
a hardware button remains held. Those production PTT gates remain separate.

## 7. Final export, verified copy and one final upload

Press **Export** again after Ready. Keep the radio on. Add your notes and ensure
the SDR audio WAVs are in `$EvidenceDir`; keep large IQ separately with filename,
sample-rate and hash references. Run the collector once more into a **new** folder:

```powershell
. {
    $ErrorActionPreference = 'Stop'
    $FinalDir = Join-Path $env:USERPROFILE ('Downloads\freedv-complete-' + (Get-Date -Format yyyyMMdd-HHmmss))
    powershell -NoProfile -ExecutionPolicy Bypass -File .\Collect-NativeSession.ps1 -RadioIp $RadioIp -EvidenceDir $EvidenceDir -OutputDir $FinalDir
    if ($LASTEXITCODE -ne 0) { throw 'Collection failed. Keep the radio on; do not run later script lines.' }
    $Cases = @(Get-ChildItem -LiteralPath (Join-Path $FinalDir 'radio') -Filter coordinator.json -Recurse)
    if ($Cases.Count -eq 0 -or -not (Test-Path -LiteralPath "$FinalDir.zip")) {
        throw 'Radio cases or ZIP missing. Do not reboot.'
    }
    foreach ($File in $Cases) {
        $Case = Get-Content -LiteralPath $File.FullName -Raw | ConvertFrom-Json
        Write-Host "$($Case.case_number): $($Case.name)"
    }
    Write-Host "READY TO RETURN: $FinalDir.zip"
}
```

Require the copied case list to include **S1/S2/S3's named tests, P1's route probe
and M1 if run**, using your recorded case numbers. Inspect the ZIP in Explorer:
it must contain **radio** with case folders, `coordinator.json`, build manifest
and capture/output WAVs, plus **operator** with the selected SDR files/notes.
An operator-only ZIP is not complete. Only after verified copying may you Close
diagnostics or reboot. Return the final ZIP; the earlier checkpoint can stay on
the PC unless it was the stopping point.

The full sequence uses roughly half a minute of RF plus setup and inspection,
not a sustained audio run. It answers ordinary mic health, button-dependent
capture, analog isolation, raw-input intelligibility, local encoding and RF
delivery in one guided session. Do not infer a pass where the required evidence
is missing. No external audio fallback, baseband change or upstream submission
is part of this procedure.
