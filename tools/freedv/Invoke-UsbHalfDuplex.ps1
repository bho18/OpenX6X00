param([string]$RadioIp='192.168.4.210')
$ErrorActionPreference='Stop'
$kitDir=Split-Path -Parent $MyInvocation.MyCommand.Path
if($RadioIp -notmatch '^[a-zA-Z0-9.:-]+$'){throw 'Invalid radio address'}
$manifest=Get-Content -Encoding UTF8 -Raw -LiteralPath (Join-Path $kitDir 'half-duplex-manifest.json') | ConvertFrom-Json
foreach($entry in $manifest.files.PSObject.Properties){
 if($entry.Name -notmatch '^[a-zA-Z0-9_.-]+$'){throw 'Invalid package filename'}
 $actual=(Get-FileHash -Algorithm SHA256 -LiteralPath (Join-Path $kitDir $entry.Name)).Hash.ToLowerInvariant()
 if($actual -ne $entry.Value){throw "Package integrity failure: $($entry.Name)"}
}
$opts=@('-o','BatchMode=yes','-o','StrictHostKeyChecking=yes','-o','ConnectTimeout=5',
        '-o','ServerAliveInterval=15','-o','ServerAliveCountMax=3')
$token=[guid]::NewGuid().ToString('N')
$evidence=Join-Path $kitDir ("usb-half-duplex-results-$token")
New-Item -ItemType Directory -Path $evidence | Out-Null
$log=Join-Path $evidence 'transport.txt'
$remote="/tmp/usb-half-duplex-$token"
$testExit=$copyExit=-1
$complete=$false
$failure=''

function Invoke-UsbHalfDuplexCommand([string]$Executable,[string[]]$Arguments) {
 Get-Command $Executable -ErrorAction Stop | Out-Null
 $savedPreference=$ErrorActionPreference
 try {
  $ErrorActionPreference='Continue'
  & $Executable @Arguments 2>&1 | Tee-Object -FilePath $log -Append | Out-Host
  $code=$LASTEXITCODE
 } finally {$ErrorActionPreference=$savedPreference}
 return [int]$code
}

Write-Host 'Live half-duplex USB codec test: 3 timed TX/RX turns. No radio/PTT control calls.'
Write-Host 'Use the current .2 image. Keep the Loop110 connected and the headset powered and paired.'
Write-Host 'Leave the radio in RX and all PTT buttons released. Start at comfortable headset volume.'
Write-Host 'TX/RX labels refer to codec phases; the test never requests radio transmission.'
Write-Host 'At each GO, speak the displayed numbered phrase for 8 seconds.'
Write-Host 'At RX, listen to that turn being decoded live. Wait for the next countdown.'
Write-Host 'The evidence ZIP contains your microphone recording. Do not unplug during this test.'
try {
 Read-Host 'Press Enter when ready' | Out-Null
 $code=Invoke-UsbHalfDuplexCommand 'ssh' ($opts+@("root@$RadioIp","umask 077 && mkdir $remote"))
 if($code -ne 0){throw 'SSH failed; no audio test started'}
 $files=@('usb_half_duplex.py','usb_headset_validate.py','usb_headset_inventory.py','usb_half_duplex','half-duplex-manifest.json') |
  ForEach-Object {Join-Path $kitDir $_}
 $code=Invoke-UsbHalfDuplexCommand 'scp' (@('-O')+$opts+$files+@("root@${RadioIp}:$remote/"))
 if($code -ne 0){throw 'Helper transfer failed; no audio test started'}
 $testExit=Invoke-UsbHalfDuplexCommand 'ssh' ($opts+@("root@$RadioIp",
  "chmod 700 $remote/usb_half_duplex && python3 -u $remote/usb_half_duplex.py --output $remote/results"))
 # Retain partial reports/PCM even if a stage fails.
 $copyExit=Invoke-UsbHalfDuplexCommand 'scp' (@('-O','-r')+$opts+@("root@${RadioIp}:$remote/results",$evidence))
 if($copyExit -ne 0){throw 'Evidence transfer failed; remote files remain in /tmp'}
 if($testExit -ne 0){throw 'Audio/codec validation stopped; preserve the ZIP for diagnosis'}
 $report=Get-Content -Encoding UTF8 -Raw -LiteralPath (Join-Path $evidence 'results/validation.json') | ConvertFrom-Json
 if($report.schema -ne 'x6200.usb-half-duplex-validation.v1' -or
    $report.status -notin @('COMPLETED_LISTENING_REVIEW_REQUIRED','COMPLETED_WITH_PLAYBACK_GAPS') -or
    $report.rf_tested -ne $false -or $report.live_codec_tested -ne $true){
  throw 'Unexpected validation report; preserve the ZIP for diagnosis'
 }
 $complete=$true
 Write-Host "Session status: $($report.status). Listening review is still required."
} catch {
 $failure=$_.Exception.Message
 Write-Warning $failure
} finally {
 $notes=Read-Host 'Listening notes: did turns 1/2/3 replay correctly? First/last words, delay, pitch/speed, dropouts or old-turn speech?'
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
 Write-Host 'This exercises live USB encoding and paced channel-file decoding, one direction at a time. PTT/RF remain untested.'
}
if(!$complete){throw 'USB half-duplex session did not complete; use the printed ZIP for diagnosis'}
