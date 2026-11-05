# Windows PowerShell 5-compatible evidence handling; mocked SSH/SCP, no radio.
$ErrorActionPreference='Stop'
$root=Join-Path ([IO.Path]::GetTempPath()) ('usb half duplex runner '+[guid]::NewGuid().ToString('N'))
New-Item -ItemType Directory -Path $root | Out-Null
Copy-Item -LiteralPath (Join-Path $PSScriptRoot 'Invoke-UsbHalfDuplex.ps1') -Destination $root
@{files=@{}} | ConvertTo-Json | Set-Content -Encoding UTF8 -LiteralPath (Join-Path $root 'half-duplex-manifest.json')
$global:usbHalfDuplexCase='success'
$global:usbHalfDuplexCommands=@()
function global:Read-Host {return 'Mock listening notes; no audio heard or radio contacted'}
function global:ssh {
 $global:usbHalfDuplexCommands+=([string]$args[-1])
 Write-Output 'synthetic SSH output: no radio contacted'
 $global:LASTEXITCODE=0
 if($global:usbHalfDuplexCase -eq 'helper_failure' -and $args[-1] -like 'chmod *'){
  $global:LASTEXITCODE=1
 }
}
function global:scp {
 $global:LASTEXITCODE=0
 if($args[-2] -like 'root@*:/tmp/*/results'){
  if($global:usbHalfDuplexCase -eq 'copy_failure'){$global:LASTEXITCODE=1;return}
  $result=Join-Path $args[-1] 'results'
  New-Item -ItemType Directory -Path $result | Out-Null
  $status='COMPLETED_LISTENING_REVIEW_REQUIRED'
  if($global:usbHalfDuplexCase -eq 'helper_failure'){$status='BLOCKED'}
  @{schema='x6200.usb-half-duplex-validation.v1';status=$status;rf_tested=$false;live_codec_tested=$true} |
   ConvertTo-Json | Set-Content -Encoding UTF8 -LiteralPath (Join-Path $result 'validation.json')
  'partial recording placeholder' | Set-Content -LiteralPath (Join-Path $result 'microphone.raw')
 }
}
try {
 foreach($case in @('success','helper_failure','copy_failure')){
  $global:usbHalfDuplexCase=$case
  $before=@(Get-ChildItem -LiteralPath $root -Filter '*.zip').Count
  $rejected=$false
  try {& (Join-Path $root 'Invoke-UsbHalfDuplex.ps1') -RadioIp '192.0.2.1'} catch {$rejected=$true}
  if($rejected -ne ($case -ne 'success')){throw "Wrong result: $case"}
  $archives=@(Get-ChildItem -LiteralPath $root -Filter '*.zip')
  if($archives.Count -ne $before+1){throw "Evidence not preserved: $case"}
 }
 foreach($command in $global:usbHalfDuplexCommands){
  if($command -notmatch '^(umask 077 && mkdir /tmp/usb-half-duplex-[a-f0-9]+|chmod 700 /tmp/usb-half-duplex-[a-f0-9]+/usb_half_duplex && python3 -u /tmp/usb-half-duplex-[a-f0-9]+/usb_half_duplex.py --output /tmp/usb-half-duplex-[a-f0-9]+/results)$'){
   throw "Unexpected remote command: $command"
  }
 }
 $global:LASTEXITCODE=0
 Write-Host 'PASS: success and partial/copy failure evidence preservation; mocked SSH/SCP only.'
} finally {
 Remove-Item Function:\ssh,Function:\scp,Function:\Read-Host -ErrorAction SilentlyContinue
 Remove-Variable usbHalfDuplexCase,usbHalfDuplexCommands -Scope Global -ErrorAction SilentlyContinue
 Remove-Item -LiteralPath $root -Recurse -Force
}
