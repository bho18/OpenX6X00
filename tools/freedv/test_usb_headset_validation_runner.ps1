# Windows PowerShell 5-compatible evidence handling; mocked SSH/SCP, no radio.
$ErrorActionPreference='Stop'
$root=Join-Path ([IO.Path]::GetTempPath()) ('usb validation runner '+[guid]::NewGuid().ToString('N'))
New-Item -ItemType Directory -Path $root | Out-Null
Copy-Item -LiteralPath (Join-Path $PSScriptRoot 'Invoke-UsbHeadsetValidation.ps1') -Destination $root
@{files=@{}} | ConvertTo-Json | Set-Content -Encoding UTF8 -LiteralPath (Join-Path $root 'validation-manifest.json')
$global:usbValidationCase='success'
$global:usbValidationCommands=@()
function global:Read-Host {return 'Mock listening notes; no audio heard or radio contacted'}
function global:ssh {
 $global:usbValidationCommands+=([string]$args[-1])
 Write-Output 'synthetic SSH output: no radio contacted'
 $global:LASTEXITCODE=0
 if($global:usbValidationCase -eq 'helper_failure' -and $args[-1] -like 'chmod *'){
  $global:LASTEXITCODE=1
 }
}
function global:scp {
 $global:LASTEXITCODE=0
 if($args[-2] -like 'root@*:/tmp/*/results'){
  if($global:usbValidationCase -eq 'copy_failure'){$global:LASTEXITCODE=1;return}
  $result=Join-Path $args[-1] 'results'
  New-Item -ItemType Directory -Path $result | Out-Null
  $status='COMPLETED_LISTENING_REVIEW_REQUIRED'
  if($global:usbValidationCase -eq 'helper_failure'){$status='BLOCKED'}
  @{schema='x6200.usb-headset-validation.v1';status=$status;rf_tested=$false;live_codec_tested=$false} |
   ConvertTo-Json | Set-Content -Encoding UTF8 -LiteralPath (Join-Path $result 'validation.json')
  'partial recording placeholder' | Set-Content -LiteralPath (Join-Path $result 'microphone.raw')
 }
}
try {
 foreach($case in @('success','helper_failure','copy_failure')){
  $global:usbValidationCase=$case
  $before=@(Get-ChildItem -LiteralPath $root -Filter '*.zip').Count
  $rejected=$false
  try {& (Join-Path $root 'Invoke-UsbHeadsetValidation.ps1') -RadioIp '192.0.2.1'} catch {$rejected=$true}
  if($rejected -ne ($case -ne 'success')){throw "Wrong result: $case"}
  $archives=@(Get-ChildItem -LiteralPath $root -Filter '*.zip')
  if($archives.Count -ne $before+1){throw "Evidence not preserved: $case"}
 }
 foreach($command in $global:usbValidationCommands){
  if($command -notmatch '^(umask 077 && mkdir /tmp/usb-headset-validation-[a-f0-9]+|chmod 700 /tmp/usb-headset-validation-[a-f0-9]+/usb_headset_pcm && python3 -u /tmp/usb-headset-validation-[a-f0-9]+/usb_headset_validate.py --output /tmp/usb-headset-validation-[a-f0-9]+/results)$'){
   throw "Unexpected remote command: $command"
  }
 }
 $global:LASTEXITCODE=0
 Write-Host 'PASS: success and partial/copy failure evidence preservation; mocked SSH/SCP only.'
} finally {
 Remove-Item Function:\ssh,Function:\scp,Function:\Read-Host -ErrorAction SilentlyContinue
 Remove-Variable usbValidationCase,usbValidationCommands -Scope Global -ErrorAction SilentlyContinue
 Remove-Item -LiteralPath $root -Recurse -Force
}
