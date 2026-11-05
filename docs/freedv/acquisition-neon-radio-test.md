# Short radio test of the acquisition candidate

Use the working X6200 application 0.3.6/baseband 1.0.7 setup, with the GUI and
waterfall active in receive. No station, audio connection, PTT or RF transmission
is required. These executables process prerecorded fixtures in memory. This
procedure copies a separate benchmark package into RAM; it does not install
firmware or replace the earlier package.

## Save the existing signal-input profile

At the radio prompt, run `exit` to return to Windows PowerShell. Then:

```powershell
$RadioIp = "192.168.100.64"
scp "root@${RadioIp}:/tmp/rade-rx-sync-profile.data" "$env:USERPROFILE\Downloads\"
if ($LASTEXITCODE -ne 0) { throw "Profile copy failed" }
```

Return that small `.data` file in the chat. Its matching original binaries were
already supplied. The profile disappears from `/tmp` if the radio restarts.

## Download and prepare the candidate on the PC

Download [x6200-freedv-acquisition-neon-package from successful run 40522638111](https://github.com/bho18/OpenX6X00/actions/runs/40522638111/artifacts/12744035005).
Save it in Downloads as `x6200-freedv-acquisition-neon-package.zip`. The artifact
contains the runtime archive, corresponding sources and validation evidence.
Keep the sources on the PC; copy only the extracted runtime directory to the
radio. The commands below locate the runtime archive even when GitHub's artifact
contains a nested `freedv-package` folder.

In Windows PowerShell:

```powershell
$RadioIp = "192.168.100.64"
$Zip = "$env:USERPROFILE\Downloads\x6200-freedv-acquisition-neon-package.zip"
$Stage = "$env:USERPROFILE\Downloads\freedv-neon-$(Get-Date -Format 'yyyyMMdd-HHmmss')"
Expand-Archive -Path $Zip -DestinationPath $Stage -ErrorAction Stop
$Archives = @(Get-ChildItem -Path $Stage -Recurse -File -Filter "freedv-benchmark-package.tar.gz")
if ($Archives.Count -ne 1) { throw "Expected exactly one runtime archive" }
$Archive = $Archives[0]
$SumLines = @(Get-Content (Join-Path $Archive.DirectoryName "ARCHIVE-SHA256SUMS") -ErrorAction Stop |
    Where-Object { $_ -match '^[0-9a-fA-F]{64}\s+freedv-benchmark-package\.tar\.gz$' })
if ($SumLines.Count -ne 1) { throw "Runtime checksum entry missing or ambiguous" }
$Expected = ($SumLines[0] -split '\s+')[0]
$Actual = (Get-FileHash $Archive.FullName -Algorithm SHA256 -ErrorAction Stop).Hash
if ($Actual -ne $Expected) { throw "Runtime archive checksum mismatch" }
tar -xzf $Archive.FullName -C $Stage
if ($LASTEXITCODE -ne 0) { throw "Runtime extraction failed" }
ssh "root@$RadioIp" "df -h /tmp"
```

Require at least 150 MiB available on `/tmp` before continuing. Keep this
PowerShell window open so `$Stage` remains available. With sufficient space:

```powershell
ssh "root@$RadioIp" "test ! -e /tmp/freedv-neon-benchmark"
if ($LASTEXITCODE -ne 0) { throw "Cannot use a fresh radio test directory; return the output" }
scp -r "$Stage\freedv-benchmark" "root@${RadioIp}:/tmp/freedv-neon-benchmark"
if ($LASTEXITCODE -ne 0) { throw "Runtime transfer failed" }
ssh "root@$RadioIp"
```

## Run at the radio prompt

```sh
cd /tmp/freedv-neon-benchmark &&
chmod +x bin/codec2-frame-bench bin/rade-frame-bench &&
/lib/ld-linux-armhf.so.3 --list bin/codec2-frame-bench &&
/lib/ld-linux-armhf.so.3 --list bin/rade-frame-bench &&
python3 tools/freedv/run_benchmarks.py --radio --output /tmp/freedv-neon-smoke-01
```

This runs the seven short workloads. Allow several minutes. A timing FAIL is
still useful evidence and is distinct from a command/dependency error. If a
command fails, return the exact output and any partial result directory; do not
replace system libraries or change radio settings to make it run. Do not launch
the 30-minute suite yet. The acquisition-only change may leave other workloads
above the performance target.

## Return results before restarting

At the radio prompt, run `exit`. Back in the same PowerShell window:

```powershell
scp -r "root@${RadioIp}:/tmp/freedv-neon-smoke-01" "$Stage/"
if ($LASTEXITCODE -ne 0) { throw "Result transfer failed" }
$ResultZip = "$env:USERPROFILE\Downloads\freedv-neon-smoke-$(Get-Date -Format 'yyyyMMdd-HHmmss').zip"
Compress-Archive -Path "$Stage\freedv-neon-smoke-01" -DestinationPath $ResultZip -ErrorAction Stop
Write-Output $ResultZip
```

Upload the result ZIP shown by the final command. It includes the package's SDK
validation summary and source/model identities. Report whether the normal GUI
and waterfall remained active and any noticeable slowdown. No recordings or
results are uploaded automatically. A short-run speedup would not by itself
qualify native routing, PTT behavior, production stack usage or sustained RADE
performance.
