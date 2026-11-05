param([string]$RadioIp='192.168.4.210')
$ErrorActionPreference='Stop'
$kitDir=Split-Path -Parent $MyInvocation.MyCommand.Path
$python=Join-Path $kitDir 'usb_headset_inventory.py'
if(!(Test-Path -LiteralPath $python -PathType Leaf)){throw "Missing $python"}
if($RadioIp -notmatch '^[a-zA-Z0-9.:-]+$'){throw 'Invalid radio address'}
$opts=@('-o','BatchMode=yes','-o','StrictHostKeyChecking=yes','-o','ConnectTimeout=5')
$token=[guid]::NewGuid().ToString('N')
$evidence=Join-Path $kitDir ("usb-headset-results-$token")
New-Item -ItemType Directory -Path $evidence | Out-Null
$log=Join-Path $evidence 'transport.txt'
$remote="/tmp/usb-headset-kit-$token"
$inventoryExit=$copyExit=-1
$complete=$false
$failure=''

function Invoke-InventoryCommand([string]$Executable,[string[]]$Arguments) {
 Get-Command $Executable -ErrorAction Stop | Out-Null
 $savedPreference=$ErrorActionPreference
 try {
  $ErrorActionPreference='Continue'
  & $Executable @Arguments 2>&1 | Tee-Object -FilePath $log -Append | Out-Host
  $code=$LASTEXITCODE
 } finally {$ErrorActionPreference=$savedPreference}
 return [int]$code
}

Write-Host 'Read-only USB headset inventory; no audio playback or radio control commands.'
Write-Host 'Connect one USB audio device directly to HOST. Leave PTT released.'
try {
 $code=Invoke-InventoryCommand 'ssh' ($opts+@("root@$RadioIp","umask 077 && mkdir $remote"))
 if($code -ne 0){throw 'SSH failed; no inventory collected'}
 $code=Invoke-InventoryCommand 'scp' (@('-O')+$opts+@($python,"root@${RadioIp}:$remote/usb_headset_inventory.py"))
 if($code -ne 0){throw 'Inventory helper copy failed'}
 $inventoryExit=Invoke-InventoryCommand 'ssh' ($opts+@("root@$RadioIp","python3 -u $remote/usb_headset_inventory.py --output $remote/inventory.json"))
 # Preserve a report even when it records a prerequisite/collection failure.
 $copyExit=Invoke-InventoryCommand 'scp' (@('-O')+$opts+@("root@${RadioIp}:$remote/inventory.json",(Join-Path $evidence 'inventory.json')))
 if($inventoryExit -ne 0){throw 'Inventory helper reported a failure; preserve the report and logs'}
 if($copyExit -ne 0){throw 'Inventory transfer failed; remote evidence remains in /tmp'}
 $report=Get-Content -Encoding UTF8 -Raw -LiteralPath (Join-Path $evidence 'inventory.json') | ConvertFrom-Json
 if($report.schema -ne 'x6200.usb-headset-inventory.v1' -or
    $report.status -notin @('USB_DUPLEX_CANDIDATE','NO_USB_AUDIO_DEVICE','USB_AUDIO_INCOMPLETE') -or
    $report.hardware_qualified -ne $false){
  throw 'Unexpected or blocked inventory report; preserve it for diagnosis'
 }
 Write-Host "Inventory status: $($report.status)"
 $complete=$true
} catch {
 $failure=$_.Exception.Message
 Write-Warning $failure
} finally {
 [ordered]@{inventory_exit=$inventoryExit;copy_exit=$copyExit;collection_completed=$complete;error=$failure} |
  ConvertTo-Json | Set-Content -Encoding UTF8 -LiteralPath (Join-Path $evidence 'collection.json')
 $sums=foreach($file in Get-ChildItem -LiteralPath $evidence -File){
  (Get-FileHash -LiteralPath $file.FullName -Algorithm SHA256).Hash.ToLowerInvariant()+'  '+$file.Name
 }
 $sums | Set-Content -Encoding Ascii -LiteralPath (Join-Path $evidence 'SHA256SUMS')
 $zip="$evidence.zip"
 Compress-Archive -Path (Join-Path $evidence '*') -DestinationPath $zip
 Write-Host "Send back: $zip"
 Write-Host 'Inventory completion does not qualify audio or RF operation.'
}
if(!$complete){throw 'USB inventory did not complete; use the printed ZIP for diagnosis'}
