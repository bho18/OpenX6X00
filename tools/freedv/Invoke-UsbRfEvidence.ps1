param([string]$RadioIp='192.168.4.210',[string]$SdrWav='')
$ErrorActionPreference='Stop'
$kitDir=Split-Path -Parent $MyInvocation.MyCommand.Path
if($RadioIp -notmatch '^[a-zA-Z0-9.:-]+$'){throw 'Invalid radio address'}
$manifest=Get-Content -Encoding UTF8 -Raw -LiteralPath (Join-Path $kitDir 'rf-test-manifest.json') | ConvertFrom-Json
foreach($entry in $manifest.files.PSObject.Properties){
 if($entry.Name -notmatch '^[a-zA-Z0-9_.-]+$'){throw 'Invalid helper filename'}
 if((Get-FileHash -Algorithm SHA256 -LiteralPath (Join-Path $kitDir $entry.Name)).Hash.ToLowerInvariant() -ne $entry.Value){throw "Helper integrity failure: $($entry.Name)"}
}
if($SdrWav -and !(Test-Path -LiteralPath $SdrWav -PathType Leaf)){throw 'SDR WAV not found'}
$opts=@('-o','BatchMode=yes','-o','StrictHostKeyChecking=yes','-o','ConnectTimeout=5','-o','ServerAliveInterval=15','-o','ServerAliveCountMax=3')
$token=[guid]::NewGuid().ToString('N')
$evidence=Join-Path $kitDir ("usb-rf-results-$token")
New-Item -ItemType Directory -Path $evidence | Out-Null
$log=Join-Path $evidence 'transport.txt'
$remote="/tmp/usb-rf-evidence-$token"
$testExit=$copyExit=-1;$complete=$false;$failure=''
function Invoke-UsbRfCommand([string]$Executable,[string[]]$Arguments){
 Get-Command $Executable -ErrorAction Stop | Out-Null
 $saved=$ErrorActionPreference
 try {$ErrorActionPreference='Continue';& $Executable @Arguments 2>&1 | Tee-Object -FilePath $log -Append | Out-Host;$code=$LASTEXITCODE}
 finally {$ErrorActionPreference=$saved}
 return [int]$code
}
Write-Host 'Read-only RF evidence collection and optional SDR audio decoding. No TX/PTT commands.'
Write-Host 'Exit USB FreeDV mode on the radio first. Leave all PTT released.'
try {
 $code=Invoke-UsbRfCommand 'ssh' ($opts+@("root@$RadioIp","umask 077 && mkdir $remote"));if($code){throw 'SSH failed'}
 $files=@('usb_rf_evidence.py','usb_half_duplex.py','usb_headset_validate.py','usb_headset_inventory.py','rf-test-manifest.json') | ForEach-Object {Join-Path $kitDir $_}
 $code=Invoke-UsbRfCommand 'scp' (@('-O')+$opts+$files+@("root@${RadioIp}:$remote/"));if($code){throw 'Helper upload failed'}
 $extra=''
 if($SdrWav){
  $code=Invoke-UsbRfCommand 'scp' (@('-O')+$opts+@($SdrWav,"root@${RadioIp}:$remote/sdr.wav"));if($code){throw 'SDR recording upload failed'}
  $extra=" --sdr-wav $remote/sdr.wav"
 }
 $testExit=Invoke-UsbRfCommand 'ssh' ($opts+@("root@$RadioIp","python3 -u $remote/usb_rf_evidence.py --output $remote/results$extra"))
 $copyExit=Invoke-UsbRfCommand 'scp' (@('-O','-r')+$opts+@("root@${RadioIp}:$remote/results",$evidence))
 if($copyExit){throw 'Evidence download failed; files remain in /tmp'}
 if($testExit){throw 'Collection/decode reported a blocker; return the ZIP for diagnosis'}
 $report=Get-Content -Encoding UTF8 -Raw -LiteralPath (Join-Path $evidence 'results/rf-evidence.json') | ConvertFrom-Json
 if($report.schema -ne 'x6200.usb-rf-evidence.v1' -or $report.rf_key_commands_sent -ne $false -or
    $report.status -notin @('COLLECTED_NO_SDR_RECORDING','RF_RECORDING_DECODED_LISTENING_REQUIRED')){throw 'Unexpected evidence report'}
 $complete=$true;Write-Host "Evidence status: $($report.status)"
} catch {$failure=$_.Exception.Message;Write-Warning $failure}
finally {
 $notes=Read-Host 'Notes: frequency, PTT release/RX return, SDR level/ALC, decoded first/last words, disconnect/Stop behavior?'
 $notes | Set-Content -Encoding UTF8 -LiteralPath (Join-Path $evidence 'operator-notes.txt')
 @{test_exit=$testExit;copy_exit=$copyExit;collection_completed=$complete;error=$failure;remote_evidence=$remote} | ConvertTo-Json | Set-Content -Encoding UTF8 -LiteralPath (Join-Path $evidence 'collection.json')
 $sums=foreach($file in Get-ChildItem -LiteralPath $evidence -File -Recurse){(Get-FileHash -Algorithm SHA256 -LiteralPath $file.FullName).Hash.ToLowerInvariant()+'  '+$file.FullName.Substring($evidence.Length+1).Replace('\','/')}
 $sums | Set-Content -Encoding Ascii -LiteralPath (Join-Path $evidence 'COLLECTION-SHA256SUMS')
 Compress-Archive -Path (Join-Path $evidence '*') -DestinationPath "$evidence.zip"
 Write-Host "Return: $evidence.zip"
 Write-Host 'Listen to results/offair-decode/offair-decoded.wav if present; automated sync does not establish word completeness.'
}
if(!$complete){throw 'RF evidence needs review; use the printed ZIP'}
