param([string]$RadioIp='192.168.4.210')
$ErrorActionPreference='Stop'
$kitDir=Split-Path -Parent $MyInvocation.MyCommand.Path
if($RadioIp -notmatch '^[a-zA-Z0-9.:-]+$'){throw 'Invalid radio address'}
$manifest=Get-Content -Encoding UTF8 -Raw -LiteralPath (Join-Path $kitDir 'validation-manifest.json') | ConvertFrom-Json
foreach($entry in $manifest.files.PSObject.Properties){
 if($entry.Name -notmatch '^[a-zA-Z0-9_.-]+$'){throw 'Invalid package filename'}
 $actual=(Get-FileHash -Algorithm SHA256 -LiteralPath (Join-Path $kitDir $entry.Name)).Hash.ToLowerInvariant()
 if($actual -ne $entry.Value){throw "Package integrity failure: $($entry.Name)"}
}
$opts=@('-o','BatchMode=yes','-o','StrictHostKeyChecking=yes','-o','ConnectTimeout=5',
        '-o','ServerAliveInterval=15','-o','ServerAliveCountMax=3')
$token=[guid]::NewGuid().ToString('N')
$evidence=Join-Path $kitDir ("usb-headset-validation-results-$token")
New-Item -ItemType Directory -Path $evidence | Out-Null
$log=Join-Path $evidence 'transport.txt'
$remote="/tmp/usb-headset-validation-$token"
$testExit=$copyExit=-1
$complete=$false
$failure=''

function Invoke-UsbValidationCommand([string]$Executable,[string[]]$Arguments) {
 Get-Command $Executable -ErrorAction Stop | Out-Null
 $savedPreference=$ErrorActionPreference
 try {
  $ErrorActionPreference='Continue'
  & $Executable @Arguments 2>&1 | Tee-Object -FilePath $log -Append | Out-Host
  $code=$LASTEXITCODE
 } finally {$ErrorActionPreference=$savedPreference}
 return [int]$code
}

Write-Host 'USB headset audio + recorded actual-codec validation. No radio/PTT control calls.'
Write-Host 'Use the current .2 image. Keep the Loop110 connected and the headset powered and paired.'
Write-Host 'Leave the radio in RX and all PTT buttons released. Start at comfortable headset volume.'
Write-Host 'Speak after GO for 12 seconds; listen for three quiet beeps while speaking.'
Write-Host 'After capture, wait for codec processing, then listen to ORIGINAL and DECODED speech.'
Write-Host 'The evidence ZIP contains your microphone recording. Do not unplug during this test.'
try {
 Read-Host 'Press Enter when ready' | Out-Null
 $code=Invoke-UsbValidationCommand 'ssh' ($opts+@("root@$RadioIp","umask 077 && mkdir $remote"))
 if($code -ne 0){throw 'SSH failed; no audio test started'}
 $files=@('usb_headset_validate.py','usb_headset_inventory.py','usb_headset_pcm','validation-manifest.json') |
  ForEach-Object {Join-Path $kitDir $_}
 $code=Invoke-UsbValidationCommand 'scp' (@('-O')+$opts+$files+@("root@${RadioIp}:$remote/"))
 if($code -ne 0){throw 'Helper transfer failed; no audio test started'}
 $testExit=Invoke-UsbValidationCommand 'ssh' ($opts+@("root@$RadioIp",
  "chmod 700 $remote/usb_headset_pcm && python3 -u $remote/usb_headset_validate.py --output $remote/results"))
 # Retain partial reports/PCM even if a stage fails.
 $copyExit=Invoke-UsbValidationCommand 'scp' (@('-O','-r')+$opts+@("root@${RadioIp}:$remote/results",$evidence))
 if($copyExit -ne 0){throw 'Evidence transfer failed; remote files remain in /tmp'}
 if($testExit -ne 0){throw 'Audio/codec validation stopped; preserve the ZIP for diagnosis'}
 $report=Get-Content -Encoding UTF8 -Raw -LiteralPath (Join-Path $evidence 'results/validation.json') | ConvertFrom-Json
 if($report.schema -ne 'x6200.usb-headset-validation.v1' -or
    $report.status -ne 'COMPLETED_LISTENING_REVIEW_REQUIRED' -or
    $report.rf_tested -ne $false -or $report.live_codec_tested -ne $false){
  throw 'Unexpected validation report; preserve the ZIP for diagnosis'
 }
 $complete=$true
 Write-Host 'Automated stages completed. Listening review is still required.'
} catch {
 $failure=$_.Exception.Message
 Write-Warning $failure
} finally {
 $notes=Read-Host 'Listening notes: three beeps while speaking? Original clear? Decoded words/start/end clear? Any delay/dropouts or sound from radio speaker?'
 $notes | Set-Content -Encoding UTF8 -LiteralPath (Join-Path $evidence 'operator-notes.txt')
 [ordered]@{test_exit=$testExit;copy_exit=$copyExit;collection_completed=$complete;error=$failure;remote_evidence=$remote} |
  ConvertTo-Json | Set-Content -Encoding UTF8 -LiteralPath (Join-Path $evidence 'collection.json')
 $sums=foreach($file in Get-ChildItem -LiteralPath $evidence -File -Recurse){
  $relative=$file.FullName.Substring($evidence.Length+1).Replace('\','/')
  (Get-FileHash -LiteralPath $file.FullName -Algorithm SHA256).Hash.ToLowerInvariant()+'  '+$relative
 }
 $sums | Set-Content -Encoding Ascii -LiteralPath (Join-Path $evidence 'COLLECTION-SHA256SUMS')
 $zip="$evidence.zip"
 Compress-Archive -Path (Join-Path $evidence '*') -DestinationPath $zip
 Write-Host "Send back: $zip"
 Write-Host 'This validates USB audio and a recorded codec round trip; live codec/PTT/RF operation remains untested.'
}
if(!$complete){throw 'USB validation did not complete; use the printed ZIP for diagnosis'}
