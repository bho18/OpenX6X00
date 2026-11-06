# Windows PowerShell 5-compatible evidence handling; mocked SSH/SCP, no radio.
$ErrorActionPreference='Stop'
$root=Join-Path ([IO.Path]::GetTempPath()) ('usb rf evidence runner '+[guid]::NewGuid().ToString('N'))
New-Item -ItemType Directory -Path $root | Out-Null
Copy-Item -LiteralPath (Join-Path $PSScriptRoot 'Invoke-UsbRfEvidence.ps1') -Destination $root
@{files=@{}} | ConvertTo-Json | Set-Content -Encoding UTF8 -LiteralPath (Join-Path $root 'rf-test-manifest.json')
$global:usbRfEvidenceCase='success'
$global:usbRfEvidenceCommands=@()
function global:Read-Host {return 'Mock listening notes; no audio heard or radio contacted'}
function global:ssh {
 $global:usbRfEvidenceCommands+=([string]$args[-1])
 Write-Output 'synthetic SSH output: no radio contacted'
 $global:LASTEXITCODE=0
 if($global:usbRfEvidenceCase -eq 'helper_failure' -and $args[-1] -like 'python3 *'){
  $global:LASTEXITCODE=1
 }
}
function global:scp {
 $global:LASTEXITCODE=0
 if($args[-2] -like 'root@*:/tmp/*/results'){
  if($global:usbRfEvidenceCase -eq 'copy_failure'){$global:LASTEXITCODE=1;return}
  $result=Join-Path $args[-1] 'results'
  New-Item -ItemType Directory -Path $result | Out-Null
  $status='COLLECTED_NO_SDR_RECORDING'
  if($global:usbRfEvidenceCase -in @('helper_failure','blocked_zero_exit')){$status='BLOCKED'}
  if($global:usbRfEvidenceCase -in @('qualification','invalid_qualification')){$status='READY_FOR_RF_TEST'}
  $rfTested=($global:usbRfEvidenceCase -eq 'invalid_qualification')
  $guiStatus='STOPPED';if($status -eq 'BLOCKED'){$guiStatus='BLOCKED'}
  @{schema='x6200.usb-rf-evidence.v1';status=$status;rf_key_commands_sent=$false;
    reason='Fixture microphone flush timeout';primary_failure_stage='gui';rf_disabled=$true;rf_tested=$rfTested;
    stages=@{collection=@{status='PASS'};gui=@{status=$guiStatus};qualification=@{status='PASS'}}} |
   ConvertTo-Json | Set-Content -Encoding UTF8 -LiteralPath (Join-Path $result 'rf-evidence.json')
  'partial recording placeholder' | Set-Content -LiteralPath (Join-Path $result 'microphone.raw')
 }
}
try {
 foreach($case in @('success','helper_failure','blocked_zero_exit','copy_failure','qualification','invalid_qualification')){
  $global:usbRfEvidenceCase=$case
  $before=@(Get-ChildItem -LiteralPath $root -Filter '*.zip').Count
  $rejected=$false;$message=''
  try {& (Join-Path $root 'Invoke-UsbRfEvidence.ps1') -RadioIp '192.0.2.1'} catch {$rejected=$true;$message=$_.Exception.Message}
  if($rejected -ne ($case -notin @('success','qualification'))){throw "Wrong result: $case"}
  if($case -in @('helper_failure','blocked_zero_exit')){
   if($message -notlike '*Fixture microphone flush timeout*' -or $message -like '*Unexpected evidence*'){
    throw "Primary blocker lost: $case - $message"
   }
  }
  $archives=@(Get-ChildItem -LiteralPath $root -Filter '*.zip')
  if($archives.Count -ne $before+1){throw "Evidence not preserved: $case"}
  $latest=$archives | Sort-Object LastWriteTimeUtc -Descending | Select-Object -First 1
  $expanded=Join-Path $root ('inspect-'+$case)
  Expand-Archive -LiteralPath $latest.FullName -DestinationPath $expanded
  $collection=Get-Content -Raw -LiteralPath (Join-Path $expanded 'collection.json') | ConvertFrom-Json
  if($collection.collection_completed -ne ($case -ne 'copy_failure')){throw "Collection/test distinction lost: $case"}
  if($collection.test_accepted -ne ($case -in @('success','qualification'))){throw "Acceptance result lost: $case"}
 }
 foreach($command in $global:usbRfEvidenceCommands){
  if($command -notmatch '^(umask 077 && mkdir /tmp/usb-rf-evidence-[a-f0-9]+|python3 -u /tmp/usb-rf-evidence-[a-f0-9]+/usb_rf_evidence.py --output /tmp/usb-rf-evidence-[a-f0-9]+/results)$'){
   throw "Unexpected remote command: $command"
  }
 }
 $global:LASTEXITCODE=0
 Write-Host 'PASS: primary blocker, real/legacy exit mismatch, qualification scope and partial/copy evidence preservation; mocked SSH/SCP only.'
} finally {
 Remove-Item Function:\ssh,Function:\scp,Function:\Read-Host -ErrorAction SilentlyContinue
 Remove-Variable usbRfEvidenceCase,usbRfEvidenceCommands -Scope Global -ErrorAction SilentlyContinue
 Remove-Item -LiteralPath $root -Recurse -Force
}
