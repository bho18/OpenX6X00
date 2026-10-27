# X6200: paired serial / parallel receiver smoke test

This test checks whether two workers improve RX completion rate on the radio.
It runs offline fixtures with the ordinary GUI/waterfall active. It does not
access microphone, speaker, radio controls or RF, and needs no active channel.
Keep other digital applications closed. No firmware installation is involved.

Use the successful `x6200-freedv-pipeline-package` artifact from the
[SDK experiment run](https://github.com/bho18/OpenX6X00/actions/runs/40555263608).
Do not use an artifact from a failed or still-running build. The run must finish
all existing acquisition/V1 checks and the additional pipeline SDK gates before
creating a runtime archive. This package introduces a frozen RX waveform; its
serial and parallel results are paired with each other, not assumed to use the
same waveform as the previous stage-timing package.

## Download and transfer from Windows PowerShell

Download the artifact ZIP from the run's **Artifacts** section. The following
commands locate the nested runtime archive, verify its published checksum and
extract it on Windows. The radio's Python installation lacks gzip support.

```powershell
$RadioIp = "192.168.100.64"
$PipelineZip = "$env:USERPROFILE\Downloads\x6200-freedv-pipeline-package.zip"
if (!(Test-Path -LiteralPath $PipelineZip -PathType Leaf)) { throw "Download the artifact ZIP first, or correct PipelineZip." }
$TestStamp = Get-Date -Format "yyyyMMdd-HHmmss"
$Unpack = "$env:USERPROFILE\Downloads\freedv-pipeline-$TestStamp"
Expand-Archive -LiteralPath $PipelineZip -DestinationPath $Unpack
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
$RemoteBench = "/tmp/freedv-pipeline-$TestStamp"
$RemoteResults = "/tmp/freedv-pipeline-results-$TestStamp"
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
ssh "root@$RadioIp" "cd '$RemoteBench' && chmod +x bin/rade-pipeline-bench && python3 tools/freedv/run_pipeline_benchmarks.py --radio --output '$RemoteResults'"
$BenchmarkExit = $LASTEXITCODE
$ReturnDir = "$env:USERPROFILE\Downloads\freedv-pipeline-results-$TestStamp"
scp -r "root@${RadioIp}:$RemoteResults" $ReturnDir
if ($LASTEXITCODE -ne 0) { throw "Result transfer failed; keep the radio powered on." }
$ResultZip = "$ReturnDir.zip"
Compress-Archive -LiteralPath $ReturnDir -DestinationPath $ResultZip
Write-Host "Benchmark exit: $BenchmarkExit; result ZIP: $ResultZip"
```

The helper runs six short cases: serial/parallel pairs for synchronized RX,
no-sync silence and interrupted reception. It verifies the package files and
saves inventory, resource samples, timings, counters, hashes and failures. A
nonzero benchmark exit is useful evidence: retrieve the complete results even
if a case could not keep up or the pair differs. If execution fails before the
results directory exists, retain the console error instead.

Return the ZIP and note whether the GUI and waterfall remained responsive and
whether another digital application was running. Record any audio or control
symptoms you notice, but this file-only test does not itself produce speaker
audio or qualify microphone/speaker routing.

These short tests cannot establish sustained capacity. Parallel completion
rate, per-block latency and acquisition load are evaluated separately; neither
matching hashes nor a responsive GUI changes the original 70% serial result.
