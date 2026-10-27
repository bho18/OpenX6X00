# Short radio test with RADE stage timers

Use the working X6200 application 0.3.6/baseband 1.0.7 setup, with the GUI and
waterfall active in receive. No station, audio connection, PTT or RF transmission
is required. These executables process prerecorded fixtures in memory. This
procedure copies a separate benchmark package into RAM; it does not install
firmware or replace the earlier package.

## Download and prepare the stage package on the PC

Download [x6200-freedv-stage-timing-package from successful run 40535053954](https://github.com/bho18/OpenX6X00/actions/runs/40535053954/artifacts/12748702112).
Save it in Downloads as `x6200-freedv-stage-timing-package.zip`. The artifact
contains the runtime archive, corresponding sources and validation evidence.
Keep the sources on the PC; copy only the extracted runtime directory to the
radio. The commands below locate the runtime archive even when GitHub's artifact
contains a nested `freedv-package` folder.

In Windows PowerShell:

```powershell
$RadioIp = "192.168.100.64"
$Zip = "$env:USERPROFILE\Downloads\x6200-freedv-stage-timing-package.zip"
$Stage = "$env:USERPROFILE\Downloads\freedv-stage-$(Get-Date -Format 'yyyyMMdd-HHmmss')"
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
ssh "root@$RadioIp" "test ! -e /tmp/freedv-stage-benchmark"
if ($LASTEXITCODE -ne 0) { throw "Cannot use a fresh radio test directory; return the output" }
scp -r "$Stage\freedv-benchmark" "root@${RadioIp}:/tmp/freedv-stage-benchmark"
if ($LASTEXITCODE -ne 0) { throw "Runtime transfer failed" }
ssh "root@$RadioIp"
```

## Run at the radio prompt

```sh
cd /tmp/freedv-stage-benchmark &&
chmod +x bin/codec2-frame-bench bin/rade-frame-bench &&
/lib/ld-linux-armhf.so.3 --list bin/codec2-frame-bench &&
/lib/ld-linux-armhf.so.3 --list bin/rade-frame-bench &&
python3 tools/freedv/run_benchmarks.py --radio --output /tmp/freedv-stage-smoke-01
```

This runs the same seven short workloads as the acquisition comparison, now
with per-stage timing. Allow several minutes. A timing FAIL is
still useful evidence and is distinct from a command/dependency error. If a
command fails, return the exact output and any partial result directory; do not
replace system libraries or change radio settings to make it run. Do not launch
the 30-minute suite yet. The existing acquisition optimization is retained; stage clocks separate RADE
processing, FARGAN speech synthesis and resampling. Timing failures are expected
from the previous result and do not mean the test failed to collect evidence.

## Return results before restarting

At the radio prompt, run `exit`. Back in the same PowerShell window:

```powershell
scp -r "root@${RadioIp}:/tmp/freedv-stage-smoke-01" "$Stage/"
if ($LASTEXITCODE -ne 0) { throw "Result transfer failed" }
$ResultZip = "$env:USERPROFILE\Downloads\freedv-stage-smoke-$(Get-Date -Format 'yyyyMMdd-HHmmss').zip"
Compress-Archive -Path "$Stage\freedv-stage-smoke-01" -DestinationPath $ResultZip -ErrorAction Stop
Write-Output $ResultZip
```

Upload the result ZIP shown by the final command. It includes the package's SDK
validation summary, source/model identities, verified package checksum list and
per-stage timing fields. Report whether the normal GUI
and waterfall remained active and any noticeable slowdown. No recordings or
results are uploaded automatically. The extra timers add overhead. These short measurements would not by themselves
qualify native routing, PTT behavior, production stack usage or sustained RADE
performance.

## Build record

The package was built from `99047e747cf07f0f169970b3edeaba475542250d` on
`experiment/x6200-stage-timing`, using the pinned SDK from baseline run
`40417980169`. Run `40535053954` completed successfully. The acquisition
correctness gates and all four SDK RADE stage-accounting checks were required
before archive creation. This establishes build/execution checks; target timing
comes from the returned radio results.

GitHub reports artifact ZIP SHA256
`d3ff6a02b6784e2441e8f3f6ada08a0544eec0e065c315e045f56e1abc5387c0`.
The archive's `ARCHIVE-SHA256SUMS` gives the separate runtime/source archive
hashes used by the commands above. All source/model and experiment identities
are also recorded in the package manifest.
