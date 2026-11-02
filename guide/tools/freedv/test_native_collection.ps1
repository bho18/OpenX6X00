# Exercise evidence preservation on Windows without SSH or a radio.
$ErrorActionPreference = 'Stop'
$Root = Join-Path ([IO.Path]::GetTempPath()) ([Guid]::NewGuid().ToString())
New-Item -ItemType Directory -Path $Root | Out-Null
$Evidence = Join-Path $Root 'evidence'
New-Item -ItemType Directory -Path $Evidence | Out-Null
Set-Content -LiteralPath (Join-Path $Evidence 'notes.txt') -Value 'synthetic operator notes'
$global:NativeCopyFailure = $false
function global:scp {
    $Destination = $args[-1]
    Set-Content -LiteralPath (Join-Path $Destination 'synthetic-radio-evidence.txt') -Value 'test only'
    if ($global:NativeCopyFailure) { $global:LASTEXITCODE = 1 } else { $global:LASTEXITCODE = 0 }
}
try {
    $Script = Join-Path $PSScriptRoot 'Collect-NativeSession.ps1'
    $Tokens = $null; $Errors = $null
    [System.Management.Automation.Language.Parser]::ParseFile($Script, [ref]$Tokens, [ref]$Errors) | Out-Null
    if ($Errors.Count) { throw 'Collection script parse failed.' }
    $Good = Join-Path $Root 'good'
    & $Script -RadioIp '192.0.2.1' -EvidenceDir $Evidence -OutputDir $Good
    if (-not (Test-Path -LiteralPath "$Good.zip")) { throw 'Successful copy did not create ZIP.' }
    if (-not (Test-Path -LiteralPath (Join-Path $Good 'operator\notes.txt'))) { throw 'Operator notes missing.' }
    $global:NativeCopyFailure = $true
    $Failed = Join-Path $Root 'failed'
    $Rejected = $false
    try { & $Script -RadioIp '192.0.2.1' -EvidenceDir $Evidence -OutputDir $Failed } catch { $Rejected = $true }
    if (-not $Rejected -or (Test-Path -LiteralPath "$Failed.zip")) { throw 'Failed copy was packaged as successful.' }
    if (-not (Test-Path -LiteralPath (Join-Path $Failed 'radio\synthetic-radio-evidence.txt'))) { throw 'Partial evidence was deleted.' }
    # GitHub's PowerShell runner forwards LASTEXITCODE; clear the deliberately
    # injected scp failure after asserting it was handled and evidence survived.
    $global:LASTEXITCODE = 0
    Write-Host 'PASS: Windows parsing, consolidated collection, failed-copy preservation; no radio contacted.'
} finally {
    Remove-Item Function:\scp -ErrorAction SilentlyContinue
    Remove-Item -LiteralPath $Root -Recurse -Force
}
