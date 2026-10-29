# Windows collection: connection first, run the script once

The October 29 transfer failed at `ssh: connect ... port 22: Connection timed out`.
No radio data transfer had started. Changing ZIP or SCP formats does not fix a
TCP connection timeout. The additional local evidence-path error is separate.
Pasting a `.ps1` file's body line by line also lets later commands run after an
interactive error; use a single file invocation instead.

## Before another test session

On the radio, read its current IP address in **Settings -> Wireless**. A freshly
written card or another Wi-Fi network may have a different address. Ensure the
PC and radio have a reachable connection; guest/client-isolated networks may
block communication. No internet port forwarding is needed for this LAN test.

In Windows PowerShell, run:

```powershell
$RadioIp = Read-Host 'Current IP address shown on the radio'
Test-NetConnection -ComputerName $RadioIp -Port 22
```

Require **TcpTestSucceeded : True** before continuing. Then:

```powershell
ssh -o ConnectTimeout=8 -o ConnectionAttempts=1 "root@$RadioIp" 'echo SSH_OK'
```

Require `SSH_OK`. These commands neither key RF nor change radio settings. If
either fails, stop and resolve the exact error before collecting a new session.

| Error | What to check |
|---|---|
| Connection timed out | Current address, radio Wi-Fi connection, PC network/reachability and client isolation; authentication has not started |
| Connection refused | Address is reachable, but SSH is not accepting connections there |
| Permission denied | Authentication; v0.3.6 uses keys configured in web System -> SSH, not an assumed factory password |
| EvidenceDir does not exist | Existing PC folder containing selected SDR WAVs/notes; a leading `\` points to a drive root, not the current folder |
| No `/tmp/freedv-native-*` results | Export/session availability; `/tmp` is temporary and reboot discards it |

Do not change host-key verification, enable passwords or weaken authentication
to fix a network timeout. The upstream [SSH instructions](../MANUAL.md#ssh)
apply if a later error is actually authentication-related.

## After Export, before reboot

Keep the radio on. In PowerShell, enter the existing test-kit directory:

```powershell
Set-Location "$env:USERPROFILE\Downloads\x6200-freedv-next-tests"
```

Use its existing `Collect-NativeSession.ps1` file; **do not paste its contents**.
Select the actual existing folder containing the PC recordings and notes:

```powershell
$EvidenceDir = (Read-Host 'Full path to the folder containing SDR WAVs and notes').Trim('"')
powershell -NoProfile -ExecutionPolicy Bypass -File .\Collect-NativeSession.ps1 -RadioIp $RadioIp -EvidenceDir $EvidenceDir
```

The helper copies all `/tmp/freedv-native-*` folders with legacy SCP, includes
selected operator files, and creates a new timestamped ZIP only after copying
succeeds. It cannot Acquire, Arm, Run or key the radio. A separate PowerShell
process also avoids reusing stale `$OutputDir` variables from pasted commands.
Do not continue by manually entering the helper's later lines after a failure.
Its partial output is preserved for inspection; failed transfers are not a
completed evidence bundle.

Before rebooting, inspect the new output directory and ZIP. Require a **radio**
directory containing actual case folders and `coordinator.json`, the matching
build manifest, and the expected `capture.wav`/`output.wav` for completed audio
cases. An **operator-only** archive contains SDR evidence but cannot establish
local capture, codec/sample accounting or restoration. Keep large IQ on the PC
and associate it by filename/hash. Copy only selected diagnostic results, not
complete configuration databases.

Today's rebooted session cannot be recovered from an empty failed-transfer
folder. Preserve any separate successful copy if one exists; do not delete old
PC directories while troubleshooting. Restore collection readiness without RF
first, then use a short focused hardware session rather than rerunning the codec
benchmarks. No firmware reflash is required for this collection correction.
