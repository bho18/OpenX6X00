# X6200: three-worker follow-up tests

Reuse the acquisition package already tested from source `b9fcea82`. There is no
SDK rebuild, firmware installation or card flashing. These file-only tests use
no radio control or physical audio device and need no active channel. Keep the
normal GUI and waterfall active, with no other digital application running.

The standalone helper runs two selected cases, both with three acquisition
participants and the separate speech worker:

- `steady-sync`: five seconds after three seconds of warm-up, ending before the
  finite fixture's end-of-over marker and wrap. This is a short steady-input
  control, not a sustained receive qualification.
- `no-sync-endurance`: 30 minutes of zero input after 30 seconds of warm-up.
  This checks sustained no-sync acquisition. It does not represent off-air noise,
  sustained synchronized speech, or physical audio output.

The helper verifies the exact previously tested package's checksum list and
files before execution. It records its own SHA256 separately and does not modify
the package. There is no worker-count fallback or automatic upload.

## Transfer the small helper from Windows PowerShell

The runtime path below is from the completed October 27 sweep. If the radio has
rebooted, `/tmp` is empty: transfer the same SDK runtime again using the
[earlier guide](rade-acquisition-parallel-radio-test.md), then set `$RemoteBench`
to its new path. Keep the original test results on the PC.

```powershell
$RadioIp = "192.168.100.64"
$RemoteBench = "/tmp/freedv-acquisition-parallel-20261027-165525"
$TestStamp = Get-Date -Format "yyyyMMdd-HHmmss"
$LocalHelper = "$env:USERPROFILE\Downloads\run_acquisition_endurance-$TestStamp.py"
$HelperUrl = "https://raw.githubusercontent.com/bho18/OpenX6X00/c7a9f41a4a8206b4c0f7953de3e77ca2690203b2/tools/freedv/run_acquisition_endurance.py"
$ExpectedHelperSha = "37ab64011732a5db446b8d1d2a7714c02366d1268f580a7f23d9c1bd4e69ffda"
Invoke-WebRequest -UseBasicParsing -Uri $HelperUrl -OutFile $LocalHelper -ErrorAction Stop
if ((Get-FileHash -LiteralPath $LocalHelper -Algorithm SHA256).Hash -ne $ExpectedHelperSha) { throw "Helper checksum differs; use the current guide and helper together." }
$RemoteHelper = "/tmp/freedv-acq-followup-$TestStamp.py"
$RemoteResults = "/tmp/freedv-acq-followup-results-$TestStamp"
ssh "root@$RadioIp" "test -x '$RemoteBench/bin/rade-pipeline-bench' && test ! -e '$RemoteHelper' && test ! -e '$RemoteResults' && mkdir '$RemoteResults'"
if ($LASTEXITCODE -ne 0) { throw "Check the runtime path, benchmark execute permission and SSH connection; no results were replaced." }
scp $LocalHelper "root@${RadioIp}:$RemoteHelper"
if ($LASTEXITCODE -ne 0) { throw "Helper transfer failed." }
```

## Run the short control, then the endurance case

Use the same PowerShell window so the variables remain available. The first
case takes about ten seconds. The second takes about 31 minutes; leave SSH and
the radio powered on until it returns.

```powershell
ssh "root@$RadioIp" "python3 '$RemoteHelper' --package '$RemoteBench' --case steady-sync --output '$RemoteResults/steady-sync'"
$SteadyExit = $LASTEXITCODE
ssh "root@$RadioIp" "python3 '$RemoteHelper' --package '$RemoteBench' --case no-sync-endurance --output '$RemoteResults/no-sync-endurance'"
$EnduranceExit = $LASTEXITCODE
$ReturnDir = "$env:USERPROFILE\Downloads\freedv-acq-followup-results-$TestStamp"
scp -r "root@${RadioIp}:$RemoteResults" $ReturnDir
if ($LASTEXITCODE -ne 0) { throw "Result transfer failed; keep the radio powered on." }
$ResultZip = "$ReturnDir.zip"
Compress-Archive -LiteralPath $ReturnDir -DestinationPath $ResultZip -ErrorAction Stop
Write-Host "Short control exit: $SteadyExit; endurance exit: $EnduranceExit; result ZIP: $ResultZip"
```

Retrieve the results even if either test returns a nonzero exit code. If a case
fails before producing results, retain its console error too. Return the ZIP
and note any GUI/waterfall slowdown, another digital application running, or
other observed symptoms.

The endurance summary separates completed execution from a narrow timing
observation: p99 whole-path processing at or below 70% of its represented input
interval, with zero reported deadline misses, drops or queue-full events. It
does not change the original serial result or declare overall feasibility.
The short control records timing without applying that endurance screen.
A zero exit status means execution completed and its evidence passed validation;
the timing observation and resource assessment remain separate.

Resource samples need review. The benchmark preallocates timing records and
touches their pages as processing continues; some RSS growth can therefore be
record retention rather than a codec memory leak. Earlier inventory exposed no
temperature sensors, so frequency/cooling samples are not a direct temperature
measurement. Neither case measures physical speaker underruns or qualifies
native audio routing, PTT or RF interoperability.
