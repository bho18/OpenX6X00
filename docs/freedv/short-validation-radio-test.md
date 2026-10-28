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
& {
$RadioIp = "192.168.100.64"
$RemoteBench = "/tmp/freedv-acquisition-parallel-20261027-165525"
$TestStamp = Get-Date -Format "yyyyMMdd-HHmmss"
$LocalHelper = Join-Path $env:USERPROFILE "Downloads\run_short_validation-$TestStamp.py"
$HelperUrl = "https://raw.githubusercontent.com/bho18/OpenX6X00/bca6eae8b4f4f018a67f3529b0d4886c7708115b/tools/freedv/run_short_validation.py"
$ExpectedHelperSha = "a94e567ba6d7f3cd9e633f74c4931ebf42a0f47195d995fde8a82b9ea27d5bda"
Invoke-WebRequest -UseBasicParsing -Uri $HelperUrl -OutFile $LocalHelper -ErrorAction Stop
if ((Get-FileHash -LiteralPath $LocalHelper -Algorithm SHA256).Hash -ne $ExpectedHelperSha) {
    throw "Helper checksum mismatch. Use the matching guide and source."
}
$RemoteHelper = "/tmp/freedv-short-helper-$TestStamp.py"
$RemoteResults = "/tmp/freedv-short-results-$TestStamp"
ssh "root@$RadioIp" "test -f '$RemoteBench/bin/rade-pipeline-bench' && test -f '$RemoteBench/bin/rade-frame-bench' && test -f '$RemoteBench/bin/codec2-frame-bench' && chmod +x '$RemoteBench/bin/rade-pipeline-bench' '$RemoteBench/bin/rade-frame-bench' '$RemoteBench/bin/codec2-frame-bench' && test -x '$RemoteBench/bin/rade-pipeline-bench' && test -x '$RemoteBench/bin/rade-frame-bench' && test -x '$RemoteBench/bin/codec2-frame-bench' && test ! -e '$RemoteHelper' && test ! -e '$RemoteResults'"
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
}
```

The enclosing script block stops subsequent transfer/test commands if a check
throws, including when pasted interactively. The execute-bit restoration changes
file metadata only; the runner still verifies the original package hashes.

## Retry the four frame cases after an execute-permission failure

Let any running noise case finish first and preserve its original results.
Download the updated helper using the URL/hash above. Restore execute permission
as shown above. In the test command, add:

```text
--cases rade-tx 700e-tx 700e-rx 700e-no-sync
```

Always choose a fresh result directory; do not overwrite the original noise
case. This retry measures about eight minutes plus preparation. Its summary
identifies the selected four cases; it does not claim all-five coverage. Retrieve
both original and retry folders for a combined assessment.

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
