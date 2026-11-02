param(
    [Parameter(Mandatory=$true)][string]$RadioIp,
    [Parameter(Mandatory=$true)][string]$EvidenceDir,
    [string]$OutputDir
)
$ErrorActionPreference = 'Stop'
# This helper copies evidence only. It cannot Acquire, Arm, Run or key RF.
if ($RadioIp -notmatch '^[a-zA-Z0-9][a-zA-Z0-9.:-]*$') { throw 'Use an IP address or hostname, without shell options.' }
if (-not (Test-Path -LiteralPath $EvidenceDir -PathType Container)) { throw 'EvidenceDir does not exist.' }
if (-not $OutputDir) {
    $OutputDir = Join-Path $env:USERPROFILE ('Downloads\freedv-complete-' + (Get-Date -Format yyyyMMdd-HHmmss))
}
if (Test-Path -LiteralPath $OutputDir) { throw 'OutputDir exists; choose a new directory.' }
New-Item -ItemType Directory -Path $OutputDir | Out-Null
$RadioResults = Join-Path $OutputDir 'radio'
New-Item -ItemType Directory -Path $RadioResults | Out-Null
& scp -O -r "root@${RadioIp}:/tmp/freedv-native-*" "$RadioResults"
if ($LASTEXITCODE -ne 0) { throw "Radio copy failed. Files already copied remain in $OutputDir; do not reboot." }
Copy-Item -LiteralPath $EvidenceDir -Destination (Join-Path $OutputDir 'operator') -Recurse
# Only explicitly selected evidence is copied. Large IQ remains on the PC;
# record its path/format/sample rate/hash in recordings.json.
$Archive = "$OutputDir.zip"
Compress-Archive -Path (Join-Path $OutputDir '*') -DestinationPath $Archive
Get-FileHash -LiteralPath $Archive -Algorithm SHA256
Write-Host "Upload: $Archive"
