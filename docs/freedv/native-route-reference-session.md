# Recover the working microphone route: reference session

Use the existing `.2` diagnostic card. Keep the current gains, volume, channels
and mixer settings unchanged. Keep the dummy load connected, USB, split OFF and
TX shift zero. Physical hand-mic PTT stays **released throughout** this session.
We already have held-PTT evidence and a clear ordinary analog baseline; do not
repeat those. This session has two short normal recordings and three eight-second
route probes, only two of which should key RF. There is no RADE encoding test.

The earlier .1 build contains both silent TX13 and intelligible TX14 on the same
selected R1 M1 P0 profile. Earlier route probes also captured speech on R1 M0 P1.
The radio-route application, control backend and raw PulseAudio capture tap did
not change between .1 and .2. Added level measurements do not intentionally
modify PCM samples. That source comparison does not rule out a timing or software
issue, but a new .2-only routing change is not established. We need a working
reference and before/after state rather than assuming native capture is absent.

## 1. Prepare a fresh PC evidence folder and a read-only snapshot

Close diagnostics and wait for restored RX first. Do not reboot. SSH is already
working. In the same Windows PowerShell window, paste the entire block:

```powershell
. {
    $ErrorActionPreference = 'Stop'
    $KitRoot = Join-Path $env:USERPROFILE 'Downloads\x6200-freedv-next-tests'
    Set-Location -LiteralPath $KitRoot
    $RadioIp = Read-Host 'Current working radio IP'
    $RouteStamp = Get-Date -Format yyyyMMdd-HHmmss
    $EvidenceDir = Join-Path $KitRoot ('route-evidence-' + $RouteStamp)
    New-Item -ItemType Directory -Path $EvidenceDir | Out-Null
    'Label | Mic selection | Radio case/file | Speech heard | RF/other observations' |
        Set-Content -LiteralPath (Join-Path $EvidenceDir 'notes.txt')
    $RemoteReader = "/tmp/freedv-route-inventory-$RouteStamp.py"
    $BeforeJson = "/tmp/freedv-route-before-$RouteStamp.json"
    scp -O .\inventory.py "root@${RadioIp}:$RemoteReader"
    if ($LASTEXITCODE -ne 0) { throw 'Helper copy failed. Stop here.' }
    ssh -o ConnectTimeout=8 "root@$RadioIp" "python3 $RemoteReader --output $BeforeJson"
    if ($LASTEXITCODE -ne 0) { throw 'Inventory failed. Stop here.' }
    scp -O "root@${RadioIp}:$BeforeJson" (Join-Path $EvidenceDir 'before-recorder.json')
    if ($LASTEXITCODE -ne 0) { throw 'Inventory download failed. Stop here.' }
    Write-Host "Save this session's SDR WAVs and notes in: $EvidenceDir"
}
```

The existing `inventory.py` reads OS/ALSA/PulseAudio information. It does not
write baseband registers, open a physical PCM stream, Acquire, Arm or key RF.
Keep this PowerShell window open so its variables remain defined. If copying
fails, resolve it before tests and keep the radio on.

## 2. Normal voice-recorder reference with Auto, then Hand

Write down the original normal microphone selection and both gain values.
The latest export had Auto, hand gain20 and internal gain30. Preserve the gain
values; we change only microphone selection for these two recordings.

For **V-AUTO**:

1. Outside diagnostics, Settings -> Audio -> MICROPHONE: select **Auto**.
2. Return to the normal screen in USB and press **MSG**. In a voice mode this
   opens the Voice Keyer/voice-message window.
3. On **(MSG 1:2)** press **Rec**. Speak into the hand mic for about six seconds:
   **"Auto recording, one two three, microphone reference."** Do not press PTT.
4. Press the same button, now **Stop**. The radio should remain RX. Record any
   **"No audio: recording uses the hand mic"** warning; it is useful evidence.
5. Select the new message and press **Listen**. Expect local speaker playback,
   not RF. Note whether the words are clear, faint, absent or distorted.
6. Stop playback. Press the first page button to reach **(MSG 2:2)**, then
   **Rename** the new recording **FDVAUTO**. If that name exists, use FDVAUTO2;
   do not overwrite or rename an unrelated existing message. Then **Close**.

For **V-HAND**, select **Hand** in Settings -> Audio -> MICROPHONE and repeat
the same six-second recording/listening procedure. Say **"Hand recording, four
five six, microphone reference."** Rename this new message **FDVHAND** (or an
unused FDVHAND2). Stop playback and Close the voice recorder.

Use **Rec/Stop** and **Listen**, not **TX** or **Auto**, in this window. Its title
can contain "VOICE KEYER (TX)"; that is not an instruction to transmit. Require
the actual radio to remain RX. If RF starts unexpectedly, stop the operation.

Restore the original normal microphone selection (Auto in the last export)
before the diagnostic probes. Do not change the microphone gains. The standard
recorder transiently changes its route/gains and restores them when stopped;
this is the supported reference behavior being evaluated.

Copy just these newly named test messages to the PC before RF:

```powershell
. {
    $ErrorActionPreference = 'Stop'
    scp -O "root@${RadioIp}:/mnt/msg/FDVAUTO*.mp3" "$EvidenceDir"
    if ($LASTEXITCODE -ne 0) { throw 'Auto reference copy failed. Keep the radio on.' }
    scp -O "root@${RadioIp}:/mnt/msg/FDVHAND*.mp3" "$EvidenceDir"
    if ($LASTEXITCODE -ne 0) { throw 'Hand reference copy failed. Keep the radio on.' }
}
```

These files are ordinary MP3 speech recordings; play them in a normal media
player, not through FreeDV. Do not delete existing voice messages or format a
partition if recording/copying fails. Note the exact error instead.

## 3. Three named route probes, same profile and released PTT

Open Settings -> Info -> Open FreeDV Diagnostics and **Acquire**. Keep:
**Hand mic / Existing mono / Keep mixer**. Do not change gains or volume.

Use each exact **top dropdown** case below, also setting the smaller R/M/P
dropdown to match for clarity. These named route probes can be Arm/Run directly
after Acquire; this round does not require microphone confirmations or repeating
the three Stop-check cases. Do not select a RADE TX case.

| Label | Top dropdown | Expected radio state and generated audio |
|---|---|---|
| D0 | **Route R1 M0 P0** | RX; generated tone may play at the radio speaker; no RF expected |
| D1 | **Route R1 M0 P1** | Software-PTT TX; generated tone should reach the SDR |
| D2 | **Route R1 M1 P0** | MODEM TX; generated tone should reach the SDR |

For each row:

1. Start SDR audio recording before the keyed cases D1/D2. Keep the same SDR
   frequency, USB passband and recording settings. Save audio in `$EvidenceDir`.
   Keep large IQ on the PC separately.
2. Select the named route, **Arm -> Run**. Even D0 requires local Arm because
   the firmware treats route probes conservatively as potentially keyed.
3. Keep physical PTT released. Say **"Route zero, one two three"** for D0,
   **"Route one, four five six"** for D1, and **"Route two, seven eight nine"**
   for D2. Speak during the first four seconds, then remain quiet.
4. Let the eight-second case finish. Require Ready, fresh RX and restoration
   before the next case. Record the actual radio case number and SDR filename.
5. Note any ordinary analog speech heard on RF in D1/D2, alongside or instead
   of the generated tone. That may show capture works but analog isolation does
   not. It is not a FreeDV voice success and must not unlock a mic TX trial.

D0 and D1 differ only in software PTT. D0 and D2 differ only in MODEM. D2 is
deliberately repeated **after** the standard recorder has exercised its route,
to check for an initialization/history dependency; we already have its failing
before-reference evidence. This is not an instruction to keep repeating D2
until a good recording happens.

Use the saved files to judge capture, not the obscured AC readout. The popup fix
is in source PR36 but is not installed in .2. D0 may capture the generated beep
as well as speech; note what you hear and return the file rather than assuming
the beep means no microphone audio. We can separate the tone during analysis.

On unexpected RF in D0, stale status, failed restoration or another fault,
press Stop with physical PTT released. End the sequence for stuck RF. All new
tests keep physical PTT released; no held-button recovery test is requested.

## 4. Export, collect after-state and return one verified ZIP

After the final case returns to Ready, press **Export**. Keep the radio on and
the diagnostic panel acquired while copying. In the same PowerShell window:

```powershell
. {
    $ErrorActionPreference = 'Stop'
    $AfterStamp = Get-Date -Format yyyyMMdd-HHmmss
    $AfterJson = "/tmp/freedv-route-after-$AfterStamp.json"
    ssh -o ConnectTimeout=8 "root@$RadioIp" "python3 $RemoteReader --output $AfterJson"
    if ($LASTEXITCODE -ne 0) { throw 'After-state inventory failed. Keep the radio on.' }
    scp -O "root@${RadioIp}:$AfterJson" (Join-Path $EvidenceDir 'after-probes.json')
    if ($LASTEXITCODE -ne 0) { throw 'After-state copy failed. Keep the radio on.' }
    $FinalDir = Join-Path $env:USERPROFILE ('Downloads\freedv-route-check-' + $AfterStamp)
    powershell -NoProfile -ExecutionPolicy Bypass -File .\Collect-NativeSession.ps1 -RadioIp $RadioIp -EvidenceDir $EvidenceDir -OutputDir $FinalDir
    if ($LASTEXITCODE -ne 0) { throw 'Collection failed. Keep the radio on; do not enter later script lines.' }
    $Cases = @(Get-ChildItem -LiteralPath (Join-Path $FinalDir 'radio') -Filter coordinator.json -Recurse)
    if ($Cases.Count -eq 0 -or -not (Test-Path -LiteralPath "$FinalDir.zip")) {
        throw 'Radio cases or ZIP missing. Do not reboot.'
    }
    Write-Host "Verify and return: $FinalDir.zip"
    Invoke-Item $FinalDir
}
```

Require **operator** to contain FDVAUTO/FDVHAND MP3s, before/after inventory JSON,
notes and the new SDR WAVs. Require **radio** to contain the three new route
cases with `coordinator.json`, `capture.wav`, `output.wav` and the build manifest.
Older cases may also be copied; keep them rather than deleting temporary evidence.
The notes must identify which actual case number is D0, D1 and D2.

Only after verifying the copies may you Close diagnostics or reboot. Return
that one ZIP. Do not proceed to encoding or confirm microphone routing in this
round, even if one file sounds promising: first compare the reference recorder,
raw captured channels and RF isolation together. No new image, gain boost,
baseband modification or external audio is part of this session.
