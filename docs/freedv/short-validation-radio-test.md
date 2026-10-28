# X6200: remaining codec checks in one short batch

Run once, keep the GUI/waterfall active, and collect one ZIP. Five cases each
measure two minutes after five seconds of warm-up: RADE TX, 700E TX, 700E
synchronized RX, 700E no-sync RX and RADE synthetic-noise acquisition using three
participants. Allow about 11-15 minutes including preparation. Independent case
failures are preserved and the remaining cases continue; a failed integrity or
resource preflight stops launch. Read the console even if SSH exits nonzero.

This uses the unchanged `b9fcea82` matching-SDK package already on the radio.
No active channel, microphone, speaker playback, RF transmission or new card is
involved. It completes the remaining short file-only codec cases; the subsequent
speaker/microphone and switching session needs the diagnostic image, which is
not released yet. The [development record](short-validation-development.md)
describes completed checks and limits.

If the radio rebooted and the runtime disappeared, restore the same downloaded
SDK package using the [earlier transfer guide](rade-acquisition-parallel-radio-test.md),
then adjust `$RemoteBench`. Keep the downloaded package on the PC. Do not replace
its files with host binaries or edit files covered by its checksum list.

Run this block in Windows PowerShell:

```powershell
$RadioIp = "192.168.100.64"
$RemoteBench = "/tmp/freedv-acquisition-parallel-20261027-165525"
$TestStamp = Get-Date -Format "yyyyMMdd-HHmmss"
$LocalHelper = Join-Path $env:USERPROFILE "Downloads\run_short_validation-$TestStamp.py"
$HelperUrl = "https://raw.githubusercontent.com/bho18/OpenX6X00/6a6aeb430d79312a32dec4bdcbd60f39ef456682/tools/freedv/run_short_validation.py"
$ExpectedHelperSha = "b2147d59b813c04bebe5342f5fd8be118e6f50081826c59576861485d88c1f16"
Invoke-WebRequest -UseBasicParsing -Uri $HelperUrl -OutFile $LocalHelper -ErrorAction Stop
if ((Get-FileHash -LiteralPath $LocalHelper -Algorithm SHA256).Hash -ne $ExpectedHelperSha) {
    throw "Helper checksum mismatch. Use the matching guide and source."
}
$RemoteHelper = "/tmp/freedv-short-helper-$TestStamp.py"
$RemoteResults = "/tmp/freedv-short-results-$TestStamp"
ssh "root@$RadioIp" "test -x '$RemoteBench/bin/rade-pipeline-bench' && test -x '$RemoteBench/bin/rade-frame-bench' && test -x '$RemoteBench/bin/codec2-frame-bench' && test ! -e '$RemoteHelper' && test ! -e '$RemoteResults'"
if ($LASTEXITCODE -ne 0) { throw "Check runtime path, execute permissions and SSH; no tests started." }
scp $LocalHelper "root@${RadioIp}:$RemoteHelper"
if ($LASTEXITCODE -ne 0) { throw "Helper transfer failed." }
ssh "root@$RadioIp" "python3 '$RemoteHelper' --package '$RemoteBench' --output '$RemoteResults'"
$SuiteExit = $LASTEXITCODE
$ReturnDir = Join-Path $env:USERPROFILE "Downloads\freedv-short-results-$TestStamp"
scp -r "root@${RadioIp}:$RemoteResults" $ReturnDir
if ($LASTEXITCODE -ne 0) { throw "Results could not be retrieved. Keep console errors and the radio powered on." }
$ResultZip = "$ReturnDir.zip"
Compress-Archive -LiteralPath $ReturnDir -DestinationPath $ResultZip -ErrorAction Stop
Write-Host "Suite exit: $SuiteExit; result ZIP: $ResultZip"
```

Retrieve partial results even if a case fails. If a failure occurs before the
result folder is created, preserve the console error instead. Return the ZIP
with an observation of GUI/waterfall responsiveness and whether any other
digital application ran. Recordings/results are never uploaded automatically.

`summary.json` distinguishes execution completion from timing margin. An
execution-complete result may still fail the original 70% screen or need deadline
review. Resources are sampled once per second; GUI presence is checked between
cases, while continuous responsiveness is an operator observation. Noise is a
deterministic filtered synthetic input, not calibrated RF or a sensitivity test.
Passing this batch does not establish physical audio continuity or PTT behavior.
