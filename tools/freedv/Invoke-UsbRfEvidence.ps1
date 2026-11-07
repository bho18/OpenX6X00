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
$testExit=$copyExit=-1;$complete=$false;$collected=$false;$failure='';$report=$null
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
 $files=@($manifest.files.PSObject.Properties | ForEach-Object {Join-Path $kitDir $_.Name})
 $files+=Join-Path $kitDir 'rf-test-manifest.json'
 $code=Invoke-UsbRfCommand 'scp' (@('-O')+$opts+$files+@("root@${RadioIp}:$remote/"));if($code){throw 'Helper upload failed'}
 $extra=''
 if($SdrWav){
  $code=Invoke-UsbRfCommand 'scp' (@('-O')+$opts+@($SdrWav,"root@${RadioIp}:$remote/sdr.wav"));if($code){throw 'SDR recording upload failed'}
  $extra=" --sdr-wav $remote/sdr.wav"
 }
 $testExit=Invoke-UsbRfCommand 'ssh' ($opts+@("root@$RadioIp","python3 -u $remote/usb_rf_evidence.py --output $remote/results$extra"))
 $copyExit=Invoke-UsbRfCommand 'scp' (@('-O','-r')+$opts+@("root@${RadioIp}:$remote/results",$evidence))
 if($copyExit){throw 'Evidence download failed; files remain in /tmp'}
 $report=Get-Content -Encoding UTF8 -Raw -LiteralPath (Join-Path $evidence 'results/rf-evidence.json') | ConvertFrom-Json
 if($report.schema -ne 'x6200.usb-rf-evidence.v1' -or $report.rf_key_commands_sent -ne $false -or
    $report.status -notin @('BLOCKED','COLLECTED_NO_SDR_RECORDING','RF_RECORDING_DECODED_LISTENING_REQUIRED','READY_FOR_RF_TEST')){throw 'Invalid evidence report schema/status'}
 $collected=$true
 Write-Host "Evidence status: $($report.status)"
 if($report.stages){foreach($stage in $report.stages.PSObject.Properties){Write-Host "$($stage.Name): $($stage.Value.status)"}}
 if($report.status -eq 'BLOCKED'){
  if(!$testExit){Write-Warning 'Helper returned exit 0 for BLOCKED; the report still prevents acceptance.'}
  $reason=[string]$report.reason;if(!$reason){$reason='The GUI/audio/decode test reported a blocker.'}
  throw "Test blocked: $reason"
 }
 if($report.stages){foreach($stage in $report.stages.PSObject.Properties){
  if($stage.Value.status -eq 'BLOCKED'){throw "Report status conflicts with blocked stage $($stage.Name); return the ZIP for diagnosis"}
 }}
 if($testExit){throw "Helper exited $testExit despite a non-blocked report; return the ZIP for diagnosis"}
 if($manifest.usb_startup_contract_version -in @(3,4)){
  if($report.usb_startup_contract_version -ne $manifest.usb_startup_contract_version -or $report.stages.session.status -ne 'COMPLETE'){
   throw 'Current kit requires a completed runtime session matching its startup version; legacy or partial evidence cannot be accepted.'
  }
  $identity=$report.session_identity;$completion=$report.runtime_completion
  if(!$identity -or !$completion -or $identity.session_id -notmatch '^freedv-usb-[a-zA-Z0-9_-]{1,160}$' -or
     $identity.build_id -ne $manifest.build_id -or $identity.source_commit -ne $manifest.firmware_commit -or
     $completion.session_id -ne $identity.session_id -or $completion.build_id -ne $identity.build_id -or
     $completion.source_commit -ne $identity.source_commit -or
     $completion.terminal -isnot [bool] -or $completion.terminal -ne $true -or
     $completion.trace_complete -isnot [bool] -or $completion.trace_complete -ne $true -or
     $completion.trace_history_truncated -isnot [bool] -or $completion.trace_history_truncated -ne $false -or
     ($completion.storage_errno -isnot [int] -and $completion.storage_errno -isnot [long]) -or
     $completion.storage_errno -ne 0){
   throw 'Runtime session/completion identity or durability evidence conflicts with this image/kit.'
  }
 }
 if($manifest.usb_startup_contract_version -eq 4){
  $ready=$report.runtime_startup_readiness
  if($report.stages.startup.status -ne 'PASS' -or $report.stages.qualification.status -ne 'NOT_REQUESTED' -or
     !$ready -or $ready.session_id -ne $identity.session_id -or $ready.build_id -ne $identity.build_id -or
     $ready.source_commit -ne $identity.source_commit -or $ready.status -ne 'PASS' -or
     $completion.operational -ne $true -or $completion.qualification_target -ne 0 -or
     $completion.qualification_complete -ne $false){
   throw 'Operational startup readiness or diagnostic qualification state conflicts with this image/kit.'
  }
 }
 if($report.status -eq 'READY_FOR_RF_TEST'){
  if($report.rf_disabled -ne $true -or $report.rf_tested -ne $false -or $report.stages.qualification.status -ne 'PASS'){
   throw 'Invalid RF-disabled qualification claim'
  }
  Write-Host 'RF-disabled qualification passed. No RF transmission was qualified by this collection.'
 }
 $complete=$true
} catch {$failure=$_.Exception.Message;Write-Warning $failure}
finally {
 $notes=Read-Host 'Notes: frequency, PTT release/RX return, SDR level/ALC, decoded first/last words, disconnect/Stop behavior?'
 $notes | Set-Content -Encoding UTF8 -LiteralPath (Join-Path $evidence 'operator-notes.txt')
 @{test_exit=$testExit;copy_exit=$copyExit;collection_completed=$collected;test_accepted=$complete;report_status=$report.status;primary_failure_stage=$report.primary_failure_stage;error=$failure;remote_evidence=$remote} | ConvertTo-Json | Set-Content -Encoding UTF8 -LiteralPath (Join-Path $evidence 'collection.json')
 $sums=foreach($file in Get-ChildItem -LiteralPath $evidence -File -Recurse){(Get-FileHash -Algorithm SHA256 -LiteralPath $file.FullName).Hash.ToLowerInvariant()+'  '+$file.FullName.Substring($evidence.Length+1).Replace('\','/')}
 $sums | Set-Content -Encoding Ascii -LiteralPath (Join-Path $evidence 'COLLECTION-SHA256SUMS')
 Compress-Archive -Path (Join-Path $evidence '*') -DestinationPath "$evidence.zip"
 Write-Host "Return: $evidence.zip"
 Write-Host 'Listen to results/offair-decode/offair-decoded.wav if present; automated sync does not establish word completeness.'
}
if(!$complete){throw "RF evidence needs review: $failure Use the printed ZIP."}
