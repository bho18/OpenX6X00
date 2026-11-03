param(
    [string]$RadioIp = '192.168.100.62',
    [string]$OutputDir
)
$ErrorActionPreference = 'Stop'
if ($RadioIp -notmatch '^[a-zA-Z0-9][a-zA-Z0-9.-]*$') { throw 'Use an IPv4 address or hostname.' }
$Manifest = Get-Content -Raw -LiteralPath (Join-Path $PSScriptRoot 'probe-manifest.json') | ConvertFrom-Json
$Helper = Join-Path $PSScriptRoot 'radio_retest.py'
$Binary = Join-Path $PSScriptRoot 'freedv_voice_pulse_probe'
foreach ($Check in @(@($Helper, $Manifest.helper_sha256), @($Binary, $Manifest.probe_sha256))) {
    if ($Check[1] -notmatch '^[0-9a-f]{64}$' -or (Get-FileHash -LiteralPath $Check[0] -Algorithm SHA256).Hash.ToLowerInvariant() -ne $Check[1]) {
        throw 'Probe package hash mismatch. Nothing has been uploaded or run.'
    }
}
if ($Manifest.expected_gui_sha256 -notmatch '^[0-9a-f]{64}$') { throw 'Invalid target GUI identity.' }
$Stamp = (Get-Date -Format yyyyMMdd-HHmmss) + '-' + [guid]::NewGuid().ToString('N').Substring(0,8)
if (-not $OutputDir) { $OutputDir = Join-Path $PWD ('freedv-backlog-' + $Stamp) }
if (Test-Path -LiteralPath $OutputDir) { throw 'Choose a new OutputDir; existing evidence is preserved.' }
$Remote = '/tmp/freedv-retest-' + $Stamp
& ssh -o BatchMode=yes -o StrictHostKeyChecking=yes -o ConnectTimeout=5 "root@$RadioIp" "umask 077 && mkdir -p '$Remote/bundle'"
if ($LASTEXITCODE -ne 0) { throw 'SSH failed. Use the existing Windows key and verified host record.' }
& scp -O -o BatchMode=yes -o StrictHostKeyChecking=yes -o ConnectTimeout=5 $Helper $Binary "root@${RadioIp}:$Remote/"
if ($LASTEXITCODE -ne 0) { throw 'Probe copy failed; no radio audio operation was requested.' }
& ssh -o BatchMode=yes -o StrictHostKeyChecking=yes "root@$RadioIp" "chmod 700 '$Remote/freedv_voice_pulse_probe' && python3 '$Remote/radio_retest.py' probe --output '$Remote/bundle/probe' --probe-binary '$Remote/freedv_voice_pulse_probe' --gui-sha256 $($Manifest.expected_gui_sha256) --probe-sha256 $($Manifest.probe_sha256)"
$ProbeExit = $LASTEXITCODE
& ssh -o BatchMode=yes -o StrictHostKeyChecking=yes "root@$RadioIp" "python3 '$Remote/radio_retest.py' collect --output '$Remote/bundle/collect'"
$CollectExit = $LASTEXITCODE
New-Item -ItemType Directory -Path $OutputDir | Out-Null
& scp -O -r -o BatchMode=yes -o StrictHostKeyChecking=yes "root@${RadioIp}:$Remote/bundle" $OutputDir
if ($LASTEXITCODE -ne 0) { throw "Copy failed. Evidence remains at ${RadioIp}:$Remote/bundle" }
Copy-Item -LiteralPath (Join-Path $PSScriptRoot 'probe-manifest.json') -Destination $OutputDir
$Archive = "$OutputDir.zip"
Compress-Archive -Path (Join-Path $OutputDir '*') -DestinationPath $Archive
Get-FileHash -LiteralPath $Archive -Algorithm SHA256
Write-Host "Saved: $Archive"
Write-Host 'Stop here and return this ZIP. No manual RF tests are requested by this update.'
if ($ProbeExit -ne 0 -or $CollectExit -ne 0) { Write-Warning 'Probe or collection did not pass. The failure evidence is included.' }
