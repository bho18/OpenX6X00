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
  if($global:usbRfEvidenceCase -eq 'helper_failure'){$status='BLOCKED'}
  @{schema='x6200.usb-rf-evidence.v1';status=$status;rf_key_commands_sent=$false} |
   ConvertTo-Json | Set-Content -Encoding UTF8 -LiteralPath (Join-Path $result 'rf-evidence.json')
  'partial recording placeholder' | Set-Content -LiteralPath (Join-Path $result 'microphone.raw')
 }
}
try {
 foreach($case in @('success','helper_failure','copy_failure')){
  $global:usbRfEvidenceCase=$case
  $before=@(Get-ChildItem -LiteralPath $root -Filter '*.zip').Count
  $rejected=$false
  try {& (Join-Path $root 'Invoke-UsbRfEvidence.ps1') -RadioIp '192.0.2.1'} catch {$rejected=$true}
  if($rejected -ne ($case -ne 'success')){throw "Wrong result: $case"}
  $archives=@(Get-ChildItem -LiteralPath $root -Filter '*.zip')
  if($archives.Count -ne $before+1){throw "Evidence not preserved: $case"}
 }
 foreach($command in $global:usbRfEvidenceCommands){
  if($command -notmatch '^(umask 077 && mkdir /tmp/usb-rf-evidence-[a-f0-9]+|python3 -u /tmp/usb-rf-evidence-[a-f0-9]+/usb_rf_evidence.py --output /tmp/usb-rf-evidence-[a-f0-9]+/results)$'){
   throw "Unexpected remote command: $command"
  }
 }
 $global:LASTEXITCODE=0
 Write-Host 'PASS: success and partial/copy failure evidence preservation; mocked SSH/SCP only.'
} finally {
 Remove-Item Function:\ssh,Function:\scp,Function:\Read-Host -ErrorAction SilentlyContinue
 Remove-Variable usbRfEvidenceCase,usbRfEvidenceCommands -Scope Global -ErrorAction SilentlyContinue
 Remove-Item -LiteralPath $root -Recurse -Force
}
