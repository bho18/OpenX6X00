# Microphone muting comparison on the existing diagnostic card

Use the existing `.2` image. No reflash is needed. This round is **two eight-second
tone transmissions and one six-second unkeyed normal recording**. Speak into the
hand microphone during every test, including the zero-gain tests. Physical
hand-mic PTT stays released throughout.

We already have the H20/I30 baseline: microphone capture and offline RADE speech
work, but unencoded microphone speech accompanies the RF tone. Do not repeat
that baseline, the codec batch, or the three Stop qualification cases. Do not
select mic-to-RADE TX or confirm independent routing in this round.

## Saved settings and ownership

The current diagnostic page has no transient gain override. For this operator
comparison, use the supported Settings -> Audio -> MICROPHONE gain sliders
**with diagnostics closed**. These sliders change normal saved preferences.
Write down the original values first and restore them manually at the end.
The last exports show Input Auto, Hand mic 20, Built-in 30.

Each Acquire snapshots the gain values currently set. Diagnostic restoration
returns to those values, not automatically to the original 20/30. Close the
diagnostic session before changing a gain, then Acquire again for the next
profile. Closing releases ownership and retains the case files in `/tmp`;
rebooting loses them. No separate program writes radio registers.

## 1. Prepare

Keep the dummy load connected, USB, split OFF, TX shift zero and VOX OFF. Keep
frequency, filters, speaker/monitor levels and other gains unchanged. Diagnostics
requests 1 W temporarily regardless of the normal displayed power setting.
No other digital application should run; keep the GUI and waterfall active.

Create a new PC evidence folder. In PowerShell, paste this entire block:

```powershell
. {
    $ErrorActionPreference = 'Stop'
    $KitRoot = Join-Path $env:USERPROFILE 'Downloads\x6200-freedv-next-tests'
    Set-Location -LiteralPath $KitRoot
    $RadioIp = Read-Host 'Current working radio IP'
    ssh -o ConnectTimeout=8 "root@$RadioIp" 'uname -m'
    if ($LASTEXITCODE -ne 0) { throw 'SSH failed. Stop before testing.' }
    $MuteStamp = Get-Date -Format yyyyMMdd-HHmmss
    $EvidenceDir = Join-Path $KitRoot ('mute-evidence-' + $MuteStamp)
    New-Item -ItemType Directory -Path $EvidenceDir | Out-Null
    @(
        'Original normal Input/gains: Auto / Hand 20 / Built-in 30 (correct if different)',
        'G1: H20 I0 | actual radio case: | SDR tone: | SDR voice: | GUI/restoration:',
        'G2: H0 I0 | actual radio case: | SDR tone: | SDR voice: | GUI/restoration:',
        'ZERO: normal MSG recording filename: | words audible:',
        'Physical hand-mic PTT released in all tests; record any deviation.',
        'Original settings manually restored: yes/no'
    ) | Set-Content -LiteralPath (Join-Path $EvidenceDir 'notes.txt')
    Write-Host "Copy this round's SDR WAVs into: $EvidenceDir"
    Invoke-Item $EvidenceDir
}
```

Keep this PowerShell window open. Edit `notes.txt` with the actual case numbers
and observations. Do not assume the radio case numbers start at one.

## 2. G1: hand gain 20, built-in gain 0

1. If diagnostics is open, wait for Ready/restored RX, press Export, then Close.
   Require ordinary RX before changing settings.
2. Settings -> Audio -> MICROPHONE: leave normal Input at its original selection
   (Auto in the last export). Set **Hand mic 20**, **Built-in 0**. Close Settings.
3. Settings -> Info -> Open FreeDV Diagnostics -> **Acquire**.
4. Set the top case to **Route R1 M1 P0**. Also set the small route to
   **R1 M1 P0**, microphone **Hand mic**, channel **Existing mono**, mixer
   **Keep mixer**. This is a named tone route test, not the RADE TX case.
5. Start SDR++ audio recording about two seconds before Run. Use the same
   frequency, USB filter, audio/AGC and recording settings as before.
6. On the radio, press **Arm -> Run** locally. Do not touch physical PTT.
7. When the tone starts, wait about one second, then say at normal volume:
   **"X6200 hand microphone, one two three, testing microphone isolation."**
   Keep the same mouth-to-mic distance for G2. Remain quiet after the phrase.
8. Let the eight-second case end. Stop the SDR recording about two seconds after
   RF ends. Require Ready, RX and completed restoration. Note the actual case ID,
   whether the SDR received the tone, and whether voice was audible underneath.
9. Copy that SDR WAV into `$EvidenceDir` as **G1-H20-I0-SDR.wav**. Keep the
   original recording separately. Press Export, then Close diagnostics.

The intended RF output is the generated tone. Whether analog voice remains is
what we are measuring; do not assume muting succeeded. The radio's own speaker
may be silent in TX. Use the SDR and exported captures, not the obscured AC meter.

## 3. G2: both microphone gains zero

With diagnostics closed and ordinary RX restored:

1. Settings -> Audio -> MICROPHONE: set **Hand mic 0**, keep **Built-in 0** and
   normal Input unchanged. Close Settings.
2. Open diagnostics and **Acquire again**. Recheck the top **Route R1 M1 P0**
   case, small **R1 M1 P0**, **Hand mic / Existing mono / Keep mixer**.
3. Repeat G1's recording and **Arm -> Run** procedure. **Speak the same phrase
   into the hand mic even though both gains are zero.** Keep physical PTT released.
4. Allow the eight-second test to finish; require Ready/restored RX. Save the
   SDR copy into `$EvidenceDir` as **G2-H0-I0-SDR.wav** and note the case ID.
5. Press Export, then Close diagnostics. Leave both gains zero briefly for the
   unkeyed reference below.

No new microphone/Stop confirmation is needed for these named route cases.
If Run refuses, record its message rather than selecting an attestation to
unlock another case. On a fault, Stop and wait for fresh RX/restoration. Do not
continue changing settings if ownership/restoration is unresolved. If the SDR
tone is absent, retain the completed trial as evidence; do not repeatedly key
the same case trying to obtain a preferred result.

## 4. ZERO: normal recording, no RF, then restore preferences

This separates loss caused by zero gain from a TX-specific routing effect.
Keep Hand 0 / Built-in 0 and physical PTT released:

1. Outside diagnostics, on the normal USB screen, press **MSG**.
2. On **MSG 1:2**, press **Rec**, speak the same phrase for about six seconds,
   then press **Stop**. The radio must remain RX. Note any no-audio warning.
3. Select the new message and press **Listen**. Note whether words are clear,
   faint or absent. Stop playback. Use neither TX nor Auto in this window.
4. Press the page button to **MSG 2:2**, then **Rename** the new recording
   **FDVZERO**. Use FDVZERO2 if that name already exists; do not overwrite or
   rename an unrelated message. Close the voice recorder.
5. **Restore the original normal microphone selection and both gain sliders
   now:** Auto / Hand 20 / Built-in 30 according to the last export. Use the
   values you wrote down if different. Mark restoration in `notes.txt`.

Diagnostic automatic restoration cannot perform step 5 because it saved the
zero-gain settings at Acquire. Do not leave the radio configured with muted
normal microphone gains accidentally.

## 5. Collect one complete bundle

Before collection, the evidence folder must contain the two SDR WAV copies and
completed notes. Copy the saved normal recording and run the existing collector
as a file; do not paste its script body:

```powershell
. {
    $ErrorActionPreference = 'Stop'
    foreach ($Name in @('G1-H20-I0-SDR.wav', 'G2-H0-I0-SDR.wav')) {
        if (-not (Test-Path -LiteralPath (Join-Path $EvidenceDir $Name))) {
            throw "Missing $Name. Copy the SDR recording into the evidence folder first."
        }
    }
    scp -O "root@${RadioIp}:/mnt/msg/FDVZERO*.mp3" "$EvidenceDir"
    if ($LASTEXITCODE -ne 0) { throw 'Normal recording copy failed. Keep the radio on.' }
    $FinalDir = Join-Path $env:USERPROFILE ('Downloads\freedv-mute-check-' + (Get-Date -Format yyyyMMdd-HHmmss))
    powershell -NoProfile -ExecutionPolicy Bypass -File (Join-Path $KitRoot 'Collect-NativeSession.ps1') -RadioIp $RadioIp -EvidenceDir $EvidenceDir -OutputDir $FinalDir
    if ($LASTEXITCODE -ne 0) { throw 'Collection failed. Keep the radio on; preserve partial copies.' }
    if (-not (Test-Path -LiteralPath "$FinalDir.zip")) { throw 'ZIP missing. Do not reboot.' }
    Write-Host "Verify and upload: $FinalDir.zip"
    Invoke-Item $FinalDir
}
```

Verify `operator` contains both SDR WAVs, FDVZERO MP3 and notes. Verify `radio`
contains both new tone cases with `coordinator.json`, `capture.wav`, `output.wav`
and the build manifest. Older cases may also be present; leave them intact.
Do not reboot until these files are copied and checked. If transfer fails,
settings should already be restored; keep the radio on to preserve `/tmp`.
Return the single ZIP even if zero gain records silence.

## What the comparison answers

Tone-only RF plus intelligible captured microphone speech identifies a candidate
isolated route. Loss of voice from both RF and Linux capture does not qualify it.
If zero-gain normal MSG recording works but zero-gain TX capture does not, the
difference points toward TX routing. If both become silent, zero gain likely
affects the capture path too. We will compare samples and SDR residual speech
before drawing conclusions; absence of faint voice by ear alone is insufficient.

No live RADE TX, PTT-held case, unchanged benchmark batch or long-duration run is
part of this comparison. Do not confirm independent microphone/TX routing yet.
