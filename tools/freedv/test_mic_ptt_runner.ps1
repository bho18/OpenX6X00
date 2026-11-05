# Exercise the operator runner using mocked SSH/SCP; no radio contacted.
$ErrorActionPreference='Stop'
$root=Join-Path ([IO.Path]::GetTempPath()) ('ptt runner '+[guid]::NewGuid().ToString('N'))
New-Item -ItemType Directory -Path $root | Out-Null
Copy-Item (Join-Path $PSScriptRoot 'Invoke-MicPttInputWatch.ps1') $root
Copy-Item (Join-Path $PSScriptRoot 'mic_ptt_input_watch.py') $root
Copy-Item (Join-Path $PSScriptRoot 'mic_status_snapshot.py') $root
Copy-Item (Join-Path $PSScriptRoot 'mic_ptt_r16_pio.py') $root
$global:mockCalls=@()
$global:mockPrompts=0
function global:ssh { $global:mockCalls+=($args -join ' '); Write-Output 'synthetic read-only observation'; $global:LASTEXITCODE=0 }
function global:scp {
 if($args -contains '-r') {
  $target=Join-Path $args[-1] 'synthetic-radio-output'
  New-Item -ItemType Directory -Path $target | Out-Null
  '{"status":"OBSERVED"}' | Set-Content (Join-Path $target 'result.json')
 }
 $global:LASTEXITCODE=0
}
function global:Read-Host { $global:mockPrompts++; if($global:mockPrompts -eq 1){'READY'}else{'synthetic notes: no radio contacted'} }
try {
 & (Join-Path $root 'Invoke-MicPttInputWatch.ps1') -CheckOnly
 if($global:mockPrompts -ne 0){throw 'Check-only prompted for a press test'}
 if(@(Get-ChildItem $root -Filter '*.zip').Count){throw 'Check-only ran the test'}
 & (Join-Path $root 'Invoke-MicPttInputWatch.ps1')
 $zips=@(Get-ChildItem $root -Filter '*.zip')
 if($zips.Count -ne 1){throw 'Evidence ZIP was not created'}
 $out=Join-Path $root 'verified extraction'
 Expand-Archive -LiteralPath $zips[0].FullName -DestinationPath $out
 if(!(Test-Path (Join-Path $out 'operator-notes.txt'))){throw 'Notes missing'}
 if(!(Test-Path (Join-Path $out 'synthetic-radio-output/result.json'))){throw 'Radio evidence missing'}
 if($global:mockCalls.Count -ne 3){throw 'Unexpected SSH command count'}
 foreach($c in $global:mockCalls){if($c -notmatch 'python3 -u /tmp/mic-ptt-input-watch-[a-f0-9]+.py --(check-only|output)'){throw 'Unexpected remote operation'}}
 $global:mockCalls=@()
 & (Join-Path $root 'Invoke-MicPttInputWatch.ps1') -Wide -CheckOnly
 if($global:mockCalls.Count -ne 2 -or $global:mockCalls[-1] -notmatch '--check-only --wide'){
  throw 'Wide prerequisite check was not used'
 }
 $global:mockPrompts=0
 & (Join-Path $root 'Invoke-MicPttInputWatch.ps1') -Wide
 if($global:mockCalls[-1] -notmatch '--output /tmp/mic-ptt-input-results-[a-f0-9]+ --wide'){
  throw 'Wide status sampling was omitted'
 }
 if(@(Get-ChildItem $root -Filter '*.zip').Count -ne 2){throw 'Wide evidence ZIP missing'}
 $global:mockPrompts=0
 & (Join-Path $root 'Invoke-MicPttInputWatch.ps1') -PttPins
 if($global:mockCalls[-1] -notmatch '--output /tmp/mic-ptt-input-results-[a-f0-9]+ --wide --pio'){
  throw 'PTT pin sampling was omitted'
 }
 if(@(Get-ChildItem $root -Filter '*.zip').Count -ne 3){throw 'PTT pin evidence ZIP missing'}
 'PASS: prerequisite-only and full runner flow, paths with spaces, notes, evidence ZIP; mocked SSH only'
} finally {
 Remove-Item Function:\ssh,Function:\scp,Function:\Read-Host
 Remove-Item -LiteralPath $root -Recurse -Force
}
