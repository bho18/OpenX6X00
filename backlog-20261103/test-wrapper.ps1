$ErrorActionPreference = 'Stop'
$Package = Join-Path $PWD 'probe-update-20261103'
$Tokens = $null; $Errors = $null
[System.Management.Automation.Language.Parser]::ParseFile((Join-Path $Package 'Run-BacklogProbe.ps1'), [ref]$Tokens, [ref]$Errors) | Out-Null
if ($Errors.Count -gt 0) { $Errors | Format-List; exit 1 }
$global:Calls = [System.Collections.Generic.List[string]]::new()
function global:ssh {
    $global:Calls.Add(($args -join ' '))
    if (($args -join ' ') -notmatch 'StrictHostKeyChecking=yes') { throw 'Host check missing' }
    $global:LASTEXITCODE = if (($args -join ' ') -match ' probe ') { 1 } else { 0 }
}
function global:scp {
    $global:Calls.Add(($args -join ' '))
    if (($args -join ' ') -notmatch 'StrictHostKeyChecking=yes') { throw 'Host check missing' }
    if ($args -contains '-r') {
        $Destination = $args[-1]
        New-Item -ItemType Directory -Path (Join-Path $Destination 'bundle/probe') -Force | Out-Null
        '{"status":"FAIL","scope":"wrapper rehearsal only"}' | Set-Content (Join-Path $Destination 'bundle/probe/result.json')
    }
    $global:LASTEXITCODE = 0
}
$Out = Join-Path $env:RUNNER_TEMP 'wrapper-rehearsal'
& (Join-Path $Package 'Run-BacklogProbe.ps1') -OutputDir $Out
if (-not (Test-Path "$Out.zip")) { throw 'Failure evidence was not archived' }
if (-not ($global:Calls | Where-Object { $_ -match ' collect ' })) { throw 'Collection skipped after failed probe' }
if (($global:Calls -join "`n") -match 'systemctl|killall|authorized_keys|reboot|/usr/sbin/x6100_gui') { throw 'Unexpected persistent mutation' }
Write-Host 'PASS: PowerShell parsed; failed probe still collects and archives evidence using strict host checks.'
