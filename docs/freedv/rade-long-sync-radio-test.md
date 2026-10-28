# X6200: sustained synchronized RX with a continuous fixture

This test reuses the previously tested `b9fcea82` SDK benchmark package. It adds
a separate continuous waveform and a small standalone collector. No new radio
binary, firmware image or card flashing is involved.

The waveform contains one continuous RADE V1 transmission lasting 31 minutes,
followed by its end-of-over marker. Both tests finish before that ending. The
producer uses pinned source speech, models and encoder code, and publishes the
fixture only after a complete native-host 30-minute receive check. Host timing
does not qualify radio performance; the first radio case is therefore a short
smoke test before the long run.

Keep the normal GUI and waterfall active and close other digital applications.
The benchmark reads files, produces no speaker audio, does not access radio
controls and does not transmit. It needs no active channel.

## Build and download the waveform artifact

Open the fork's [manual workflow](https://github.com/bho18/OpenX6X00/actions/workflows/main.yml).
Choose **Run workflow**, select **experiment/x6200-long-sync-fixture**, then run
it. Wait for the manually started **build-fixture** job to finish successfully.
The short registration run does not create the waveform.

Open that successful run's **Summary**, scroll to **Artifacts**, and download
**x6200-freedv-long-sync-fixture**. The separate build-record artifact contains
logs, not the radio input package. The waveform artifact contains PCM, JSON,
checksums and notices; its host build tools are not deployed to the radio.

## Verify and transfer from Windows PowerShell

The runtime path below is from the previous sweep. If the radio rebooted,
transfer the same cached SDK runtime again using the
[earlier transfer guide](rade-acquisition-parallel-radio-test.md), including its
execute-permission step, then update `$RemoteBench` below. Do not replace the
runtime with a native-host build.

```powershell
$RadioIp = "192.168.100.64"
$RemoteBench = "/tmp/freedv-acquisition-parallel-20261027-165525"
$FixtureZip = "$env:USERPROFILE\Downloads\x6200-freedv-long-sync-fixture.zip"
if (!(Test-Path -LiteralPath $FixtureZip -PathType Leaf)) { throw "Download the waveform artifact ZIP first, or correct FixtureZip." }
$TestStamp = Get-Date -Format "yyyyMMdd-HHmmss"
$Unpack = "$env:USERPROFILE\Downloads\freedv-long-sync-$TestStamp"
Expand-Archive -LiteralPath $FixtureZip -DestinationPath $Unpack -ErrorAction Stop
$Manifests = @(Get-ChildItem -LiteralPath $Unpack -Recurse -File -Filter fixture-manifest.json)
if ($Manifests.Count -ne 1) { throw "Expected exactly one waveform manifest." }
$FixtureDir = $Manifests[0].DirectoryName
$Names = @("fixture.raw", "fixture-manifest.json", "native-validation.json")
$ChecksumLines = @(Get-Content -LiteralPath (Join-Path $FixtureDir "SHA256SUMS"))
foreach ($Name in $Names) {
    $Pattern = '^[0-9a-fA-F]{64}  ' + [regex]::Escape($Name) + '$'
    $MatchesForFile = @($ChecksumLines | Where-Object { $_ -match $Pattern })
    if ($MatchesForFile.Count -ne 1) { throw "Missing or duplicate checksum for $Name." }
    $Expected = ($MatchesForFile[0] -split '  ', 2)[0]
    $Actual = (Get-FileHash -LiteralPath (Join-Path $FixtureDir $Name) -Algorithm SHA256).Hash
    if ($Actual -ne $Expected) { throw "Checksum mismatch for $Name." }
}
$LocalHelper = Join-Path $Unpack "run_long_sync_benchmark.py"
$HelperUrl = "https://raw.githubusercontent.com/bho18/OpenX6X00/topic/rade-sustained-speech/tools/freedv/run_long_sync_benchmark.py"
$ExpectedHelperSha = "79872e8098007830c33aa425a3bd275adf0abd93dd5049092f1ab0cd2ea11923"
Invoke-WebRequest -UseBasicParsing -Uri $HelperUrl -OutFile $LocalHelper -ErrorAction Stop
if ((Get-FileHash -LiteralPath $LocalHelper -Algorithm SHA256).Hash -ne $ExpectedHelperSha) { throw "Helper checksum differs; use the current guide and helper together." }
$RemoteFixture = "/tmp/freedv-long-sync-fixture-$TestStamp"
$RemoteHelper = "/tmp/freedv-long-sync-helper-$TestStamp.py"
$RemoteResults = "/tmp/freedv-long-sync-results-$TestStamp"
ssh "root@$RadioIp" "test -x '$RemoteBench/bin/rade-pipeline-bench' && test ! -e '$RemoteFixture' && test ! -e '$RemoteHelper' && test ! -e '$RemoteResults' && mkdir '$RemoteFixture' '$RemoteResults'"
if ($LASTEXITCODE -ne 0) { throw "Check the runtime path, execute permission and SSH connection; existing files were not replaced." }
$Raw = Join-Path $FixtureDir "fixture.raw"
$Manifest = Join-Path $FixtureDir "fixture-manifest.json"
$NativeResult = Join-Path $FixtureDir "native-validation.json"
scp "$Raw" "$Manifest" "$NativeResult" "root@${RadioIp}:$RemoteFixture/"
if ($LASTEXITCODE -ne 0) { throw "Waveform transfer failed." }
scp $LocalHelper "root@${RadioIp}:$RemoteHelper"
if ($LASTEXITCODE -ne 0) { throw "Collector transfer failed." }
```

Only the three data files and the checked collector are transferred. The helper
also verifies the exact existing SDK package and the external fixture's source,
model, waveform and native-validation records. No artifact code is imported or
executed. Keep the ZIP and build records on the PC.

## Run the smoke test, then the 30-minute case

Use the same PowerShell window. The smoke case measures five seconds after
three seconds of warm-up. If it completes and validates, the following commands
start the endurance case: 30 minutes after 30 seconds of warm-up. Both select
three acquisition participants plus the existing speech worker.

The unchanged benchmark first converts the entire long waveform in memory.
That preparation can take a few minutes **for each case**, even the short smoke
test, and is reported separately from steady-state codec timing. Allow roughly
35–40 minutes for the endurance process and keep SSH and the radio powered on.

```powershell
ssh "root@$RadioIp" "python3 '$RemoteHelper' --package '$RemoteBench' --fixture '$RemoteFixture/fixture.raw' --fixture-manifest '$RemoteFixture/fixture-manifest.json' --case smoke --output '$RemoteResults/smoke'"
$SmokeExit = $LASTEXITCODE
$EnduranceExit = $null
if ($SmokeExit -eq 0) {
    ssh "root@$RadioIp" "python3 '$RemoteHelper' --package '$RemoteBench' --fixture '$RemoteFixture/fixture.raw' --fixture-manifest '$RemoteFixture/fixture-manifest.json' --case endurance --output '$RemoteResults/endurance'"
    $EnduranceExit = $LASTEXITCODE
} else {
    Write-Host "Smoke test did not validate. The endurance case was not started; retrieve the results and console error."
}
$ReturnDir = "$env:USERPROFILE\Downloads\freedv-long-sync-results-$TestStamp"
scp -r "root@${RadioIp}:$RemoteResults" $ReturnDir
if ($LASTEXITCODE -ne 0) { throw "Result transfer failed; keep the radio powered on." }
$ResultZip = "$ReturnDir.zip"
Compress-Archive -LiteralPath $ReturnDir -DestinationPath $ResultZip -ErrorAction Stop
Write-Host "Smoke exit: $SmokeExit; endurance exit: $EnduranceExit; result ZIP: $ResultZip"
```

Retrieve results even if a case fails. If rejection occurs before a case output
directory exists, retain its console error as well. Return the ZIP and note
whether the GUI and waterfall remained active and responsive, whether another
digital application ran, and any other observed symptoms. The result bundle
contains the fixture manifest and validation record, not another copy of the
large waveform. Nothing is automatically uploaded.

## What this test establishes

The helper requires at least **600 MiB MemAvailable after transfer**, checks it
again immediately before launch, and records both observations. If it refuses
for lack of memory, return that result; it does not change radio configuration.
This headroom check is not a guarantee against unrelated memory use during the
run.

The fixture is about 28.4 MiB of raw PCM. The existing loader retains about
313 MiB of converted float samples and uses additional copies during preparation;
`/tmp` waveform storage consumes RAM separately. Timing records also become
resident as processing continues. Peak RSS and memory growth therefore include
test infrastructure and must be separated from codec memory behavior.

Successful validation requires no end-of-over event and synchronized decoded
features on every measured block. A zero exit code establishes complete,
consistent execution of this workload. It does not automatically qualify speed,
thermal stability, the whole voice mode or hardware audio. Input backlog,
output cadence, queue behavior and total latency are reported separately; the
original 70% serial assessment is unchanged. Native microphone/speaker routes,
PTT recovery and RF interoperability remain separate qualification tasks.
