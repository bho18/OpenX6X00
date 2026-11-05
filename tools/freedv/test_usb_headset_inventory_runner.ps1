# Exercise Windows evidence handling with mocked SSH/SCP; no radio contacted.
$ErrorActionPreference='Stop'
$root=Join-Path ([IO.Path]::GetTempPath()) ('usb headset runner '+[guid]::NewGuid().ToString('N'))
New-Item -ItemType Directory -Path $root | Out-Null
Copy-Item -LiteralPath (Join-Path $PSScriptRoot 'Invoke-UsbHeadsetInventory.ps1') -Destination $root
'# Synthetic file; never executed' | Set-Content -LiteralPath (Join-Path $root 'usb_headset_inventory.py')
$global:usbMockCase='success'
$global:usbMockCommands=@()
function global:ssh {
 $global:usbMockCommands+=([string]$args[-1])
 Write-Output 'synthetic SSH output: no radio contacted'
 $global:LASTEXITCODE=0
 if($global:usbMockCase -eq 'helper_failure' -and $args[-1] -like 'python3 *'){
  $global:LASTEXITCODE=1
 }
}
function global:scp {
 $global:LASTEXITCODE=0
 if($args[-2] -like 'root@*:/tmp/*/inventory.json'){
  if($global:usbMockCase -eq 'copy_failure'){$global:LASTEXITCODE=1;return}
  $status='USB_DUPLEX_CANDIDATE'
  if($global:usbMockCase -eq 'no_usb'){$status='NO_USB_AUDIO_DEVICE'}
  if($global:usbMockCase -eq 'helper_failure'){$status='BLOCKED'}
  @{schema='x6200.usb-headset-inventory.v1';status=$status;hardware_qualified=$false} | ConvertTo-Json |
   Set-Content -Encoding UTF8 -LiteralPath $args[-1]
 }
}
try {
 $scriptPath=Join-Path $root 'Invoke-UsbHeadsetInventory.ps1'
 foreach($case in @('success','no_usb','helper_failure','copy_failure')){
  $global:usbMockCase=$case
  $before=@(Get-ChildItem -LiteralPath $root -Filter '*.zip').Count
  $rejected=$false
  try {& $scriptPath -RadioIp '192.0.2.1'} catch {$rejected=$true}
  if($rejected -ne ($case -in @('helper_failure','copy_failure'))){throw "Wrong result: $case"}
  $archives=@(Get-ChildItem -LiteralPath $root -Filter '*.zip')
  if($archives.Count -ne $before+1){throw "Evidence not preserved: $case"}
 }
 foreach($command in $global:usbMockCommands){
  if($command -notmatch '^(umask 077 && mkdir /tmp/usb-headset-kit-[a-f0-9]+|python3 -u /tmp/usb-headset-kit-[a-f0-9]+/usb_headset_inventory.py --output /tmp/usb-headset-kit-[a-f0-9]+/inventory.json)$'){
   throw "Unexpected remote command: $command"
  }
 }
 $global:LASTEXITCODE=0
 Write-Host 'PASS: success, no-device result, helper failure and transfer failure preserve evidence; mocked SSH/SCP only.'
} finally {
 Remove-Item Function:\ssh,Function:\scp -ErrorAction SilentlyContinue
 Remove-Variable usbMockCase,usbMockCommands -Scope Global -ErrorAction SilentlyContinue
 Remove-Item -LiteralPath $root -Recurse -Force
}
