$ErrorActionPreference='Stop'
$root=Join-Path $PWD 'capture-fix-20261103'
Get-ChildItem (Join-Path $root 'tools') -Filter *.ps1 | ForEach-Object {
 $tokens=$null;$errors=$null
 [System.Management.Automation.Language.Parser]::ParseFile($_.FullName,[ref]$tokens,[ref]$errors)|Out-Null
 if($errors.Count){throw ($errors|Out-String)}
}
$global:FreeDVWatchCalls=@();$global:FreeDVWatchReject=$true
function global:scp {$global:LASTEXITCODE=0}
function global:ssh {
 $global:FreeDVWatchCalls+=,($args -join ' ')
 if(($args -join ' ') -notmatch 'StrictHostKeyChecking=yes'){throw 'Missing strict host checking'}
 $global:LASTEXITCODE=if($global:FreeDVWatchReject){1}else{0}
}
$failed=$false
try {& (Join-Path $root 'tools/Start-FreeDVCaptureWatch.ps1') -CandidateManifest (Join-Path $root 'candidate-manifest.json')}catch{
 if($_ -notmatch 'Installed GUI does not match'){throw};$failed=$true
}
if(-not $failed -or $global:FreeDVWatchCalls.Count -ne 1 -or $global:FreeDVWatchCalls[0] -notmatch '--check-only'){throw 'Mismatch did not stop before launch'}
$global:FreeDVWatchReject=$false;$global:FreeDVWatchCalls=@()
& (Join-Path $root 'tools/Start-FreeDVCaptureWatch.ps1') -CandidateManifest (Join-Path $root 'candidate-manifest.json')
if($global:FreeDVWatchCalls.Count -ne 2 -or $global:FreeDVWatchCalls[1] -notmatch 'nohup python3.*--seconds 120'){throw 'Unexpected remote operations'}
Write-Host 'PASS: Windows watcher parses, rejects a mismatched candidate before launch, and uses strict key-based SSH.'
