param(
    [ValidateSet('Collect','Probe')][string]$Action = 'Collect',
    [string]$RadioIp = '192.168.100.62',
    [string]$CandidateManifest,
    [string]$RecordingDir,
    [string]$OutputDir
)
$ErrorActionPreference = 'Stop'
if ($RadioIp -notmatch '^[a-zA-Z0-9][a-zA-Z0-9.-]*$') { throw 'Use an IPv4 address or hostname.' }
$Helper = Join-Path $PSScriptRoot 'radio_retest.py'
if (-not (Test-Path -LiteralPath $Helper)) { throw 'Keep radio_retest.py next to this script.' }
$Stamp = (Get-Date -Format yyyyMMdd-HHmmss) + '-' + [guid]::NewGuid().ToString('N').Substring(0,8)
if (-not $OutputDir) { $OutputDir = Join-Path $PWD ('freedv-' + $Action.ToLower() + '-' + $Stamp) }
if (Test-Path -LiteralPath $OutputDir) { throw 'Choose a new OutputDir; existing evidence is preserved.' }
$Options = ''
if ($Action -eq 'Probe') {
    if (-not $CandidateManifest) { throw 'Probe requires -CandidateManifest pointing to the downloaded candidate-manifest.json.' }
    $Manifest = Get-Content -Raw -LiteralPath $CandidateManifest | ConvertFrom-Json
    if ($Manifest.gui_sha256 -notmatch '^[0-9a-f]{64}$' -or $Manifest.pulse_probe_sha256 -notmatch '^[0-9a-f]{64}$') {
        throw 'Manifest does not identify both the GUI and isolated probe.'
    }
    $Options = " --gui-sha256 $($Manifest.gui_sha256) --probe-sha256 $($Manifest.pulse_probe_sha256)"
}
$Remote = '/tmp/freedv-retest-' + $Stamp
& ssh -o BatchMode=yes -o ConnectTimeout=5 "root@$RadioIp" "umask 077 && mkdir '$Remote'"
if ($LASTEXITCODE -ne 0) { throw 'SSH connection failed. Use the existing Windows SSH key; no radio changes were made.' }
& scp -O -o BatchMode=yes -o ConnectTimeout=5 $Helper "root@${RadioIp}:$Remote/radio_retest.py"
if ($LASTEXITCODE -ne 0) { throw 'Helper copy failed.' }
$Operation = $Action.ToLower()
& ssh -o BatchMode=yes -o ConnectTimeout=5 "root@$RadioIp" "python3 '$Remote/radio_retest.py' $Operation --output '$Remote/results'$Options"
$RunExit = $LASTEXITCODE
New-Item -ItemType Directory -Path $OutputDir | Out-Null
& scp -O -r -o BatchMode=yes -o ConnectTimeout=5 "root@${RadioIp}:$Remote/results" $OutputDir
if ($LASTEXITCODE -ne 0) { throw "Collection failed. Results remain at ${RadioIp}:$Remote/results" }
if ($RecordingDir) {
    if (-not (Test-Path -LiteralPath $RecordingDir -PathType Container)) { throw 'RecordingDir does not exist; copied radio results are preserved.' }
    $OperatorDir = Join-Path $OutputDir 'operator'
    New-Item -ItemType Directory -Path $OperatorDir | Out-Null
    Get-ChildItem -LiteralPath $RecordingDir -File | Where-Object { $_.Extension -in '.wav','.json','.txt' } |
        ForEach-Object { Copy-Item -LiteralPath $_.FullName -Destination $OperatorDir }
}
$Archive = "$OutputDir.zip"
Compress-Archive -Path (Join-Path $OutputDir '*') -DestinationPath $Archive
Get-FileHash -LiteralPath $Archive -Algorithm SHA256
Write-Host "Saved: $Archive"
if ($RunExit -ne 0) { Write-Warning 'Probe/collection did not pass. The result and logs are included; do not treat it as PASS.' }
