# X6200: acquisition worker comparison

This test measures whether parallel acquisition improves RX capacity on the
radio. It runs offline fixtures with the ordinary GUI/waterfall active. It does
not access microphone, speaker, radio controls or RF, and needs no active
channel. Keep other digital applications closed. No firmware installation or
card flashing is involved.

## Build and download the package

Open the fork's [manual build workflow](https://github.com/bho18/OpenX6X00/actions/workflows/main.yml?query=branch%3Aexperiment%2Fx6200-parallel-acquisition).
In **Actions**, choose **Build image / FreeDV acquisition scaling package**, then
**Run workflow**. Select the branch **experiment/x6200-parallel-acquisition** and
run it; there are no additional inputs on this experiment branch.

Wait for the run you started to finish successfully. The job must complete all
existing acquisition/V1/pipeline checks and the parallel acquisition SDK gates
before creating its runtime archive. Do not use an artifact from a failed or
still-running build.

Download **x6200-freedv-acquisition-parallel-package** from the successful run's
**Artifacts** section. It contains a benchmark package, not firmware to flash.
All counts use the same executable and frozen waveform, with one acquisition
participant providing the direct comparison. Acquisition reuse is excluded from
this experiment. Nothing is sent to the original upstream repository.

## Download and transfer from Windows PowerShell

Download the artifact ZIP from the run's **Artifacts** section. The following
commands locate the nested runtime archive, verify its published checksum and
extract it on Windows. The radio's Python installation lacks gzip support. If
your browser changed the ZIP name, adjust `$AcquisitionZip` to its actual path.

```powershell
$RadioIp = "192.168.100.64"
$AcquisitionZip = "$env:USERPROFILE\Downloads\x6200-freedv-acquisition-parallel-package.zip"
if (!(Test-Path -LiteralPath $AcquisitionZip -PathType Leaf)) { throw "Download the artifact ZIP first, or correct AcquisitionZip." }
$TestStamp = Get-Date -Format "yyyyMMdd-HHmmss"
$Unpack = "$env:USERPROFILE\Downloads\freedv-acquisition-parallel-$TestStamp"
Expand-Archive -LiteralPath $AcquisitionZip -DestinationPath $Unpack -ErrorAction Stop
$Archives = @(Get-ChildItem -LiteralPath $Unpack -Recurse -File -Filter freedv-benchmark-package.tar.gz)
if ($Archives.Count -ne 1) { throw "Expected exactly one runtime archive." }
$RuntimeArchive = $Archives[0]
$ChecksumPath = Join-Path $RuntimeArchive.DirectoryName "ARCHIVE-SHA256SUMS"
$ChecksumLines = @(Get-Content -LiteralPath $ChecksumPath | Where-Object { $_ -match '^[0-9a-fA-F]{64}  freedv-benchmark-package\.tar\.gz$' })
if ($ChecksumLines.Count -ne 1) { throw "Missing runtime archive checksum." }
$Expected = ($ChecksumLines[0] -split '  ', 2)[0]
$Actual = (Get-FileHash -LiteralPath $RuntimeArchive.FullName -Algorithm SHA256).Hash
if ($Actual -ne $Expected) { throw "Archive checksum mismatch." }
tar -xzf $RuntimeArchive.FullName -C $Unpack
if ($LASTEXITCODE -ne 0) { throw "Runtime extraction failed." }
$RuntimeDir = Join-Path $Unpack "freedv-benchmark"
$RemoteBench = "/tmp/freedv-acquisition-parallel-$TestStamp"
$RemoteResults = "/tmp/freedv-acquisition-parallel-results-$TestStamp"
ssh "root@$RadioIp" "test ! -e '$RemoteBench' && test ! -e '$RemoteResults'"
if ($LASTEXITCODE -ne 0) { throw "SSH failed or remote test paths already exist." }
scp -r $RuntimeDir "root@${RadioIp}:$RemoteBench"
if ($LASTEXITCODE -ne 0) { throw "Runtime transfer failed." }
```

Only the runtime directory is transferred. Keep source archives and build logs
on the PC. `/tmp` is RAM-backed and disappears when the radio reboots; copy
results back before powering down. No DATA partition setup is needed.

## Run and retrieve

Leave the normal radio GUI/waterfall running. Run from the same PowerShell
window so the path variables remain available:

```powershell
ssh "root@$RadioIp" "cd '$RemoteBench' && chmod +x bin/rade-pipeline-bench && python3 tools/freedv/run_acquisition_benchmarks.py --radio --output '$RemoteResults'"
$BenchmarkExit = $LASTEXITCODE
$ReturnDir = "$env:USERPROFILE\Downloads\freedv-acquisition-parallel-results-$TestStamp"
scp -r "root@${RadioIp}:$RemoteResults" $ReturnDir
if ($LASTEXITCODE -ne 0) { throw "Result transfer failed; keep the radio powered on." }
$ResultZip = "$ReturnDir.zip"
Compress-Archive -LiteralPath $ReturnDir -DestinationPath $ResultZip -ErrorAction Stop
Write-Host "Benchmark exit: $BenchmarkExit; result ZIP: $ResultZip"
```

The helper runs 12 paced cases: synchronized RX (10 seconds), no-sync zero input
(10 seconds), and interrupted reception (26 seconds), each with 1, 2, 3 and 4
acquisition participants. Every case includes three seconds of warm-up, rounded
to complete input blocks. The two-stage receiver stays enabled for every case;
count 1 is the comparison within this same executable. Allow several minutes
for the sweep, and let it finish all counts even if an earlier case fails.

The helper verifies the package files and saves inventory, resource samples,
timings, counters, hashes and failures. A nonzero benchmark exit is useful
evidence: retrieve the complete results even if a case could not keep up or
results differ. If execution fails before the results directory exists, retain
the console error instead.

Return the ZIP and note whether the GUI and waterfall remained responsive and
whether another digital application was running. Record any audio or control
symptoms you notice, but this file-only test does not itself produce speaker
audio or qualify microphone/speaker routing.

The selected count includes the calling RADE thread. The FARGAN/output stage
has a separate thread, so count 4 may involve five codec threads alongside the
GUI. The test measures how those choices behave; it does not assume the largest
count will perform best.

These short tests cannot establish sustained capacity. Input lateness, queue
growth, output cadence, latency and acquisition load are evaluated separately.
Matching hashes and a responsive GUI do not establish the original 70% serial
gate or the later 30-minute hardware qualification.
