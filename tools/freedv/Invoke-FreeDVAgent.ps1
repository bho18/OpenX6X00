param(
 [Parameter(Mandatory=$true)][ValidateSet('Preflight','Stage','Status','Events','Abort','Collect','Assess','Probe','SpeechFixture','CaptureWatch')][string]$Action,
 [string]$SpeechWav=(Join-Path $PSScriptRoot 'numbered-speech.wav'),
 [ValidateRange(1,100)][int]$Turns=4,
 [ValidateRange(100,8000)][int]$DurationMs=8000,
 [switch]$Cycles,
 [ValidateRange(1,600)][int]$DurationSeconds=150,
 [string]$RadioIp='192.168.100.62',
 [string]$CandidateManifest=(Join-Path $PSScriptRoot 'candidate-manifest.json'),
 [ValidateSet('record-hand-zero','record-hand-saved','record-ordinary-zero','record-ordinary-saved','modem-waveform','modem-prekey-gain','record-then-modem','live-speech','receive')][string]$Case='record-ordinary-zero',
 [switch]$RepeatFive,
 [string]$OutputDir=(Join-Path (Get-Location) 'freedv-route-results')
)
$ErrorActionPreference='Stop'
if($Action -eq 'SpeechFixture'){
 Add-Type -AssemblyName System.Speech
 $speaker=New-Object System.Speech.Synthesis.SpeechSynthesizer
 $format=[System.Speech.AudioFormat.SpeechAudioFormatInfo]::new(44100,[System.Speech.AudioFormat.AudioBitsPerSample]::Sixteen,[System.Speech.AudioFormat.AudioChannel]::Mono)
 $speaker.SetOutputToWaveFile($SpeechWav,$format)
 1..10 | ForEach-Object { $speaker.Speak("Phrase $_. Alpha begins. One two three four. Bravo ends.") }
 $speaker.Dispose()
 Get-FileHash -Algorithm SHA256 -LiteralPath $SpeechWav
 return
}
if($RadioIp -notmatch '^[a-zA-Z0-9.:-]+$'){throw 'Invalid radio address'}
$opts=@('-o','BatchMode=yes','-o','StrictHostKeyChecking=yes','-o','ConnectTimeout=5')
$remote='/tmp/freedv-agent-kit'
& ssh @opts "root@$RadioIp" "umask 077 && mkdir -p $remote"
if($LASTEXITCODE){throw 'SSH failed; existing keys and verified host records are required'}
foreach($name in @('route_agent.py','radio_retest.py','capture_source_watch.py')){
 & scp -O @opts (Join-Path $PSScriptRoot $name) "root@${RadioIp}:$remote/$name"
 if($LASTEXITCODE){throw "Copy failed: $name"}
}
& scp -O @opts $CandidateManifest "root@${RadioIp}:$remote/candidate-manifest.json"
if($LASTEXITCODE){throw 'Manifest copy failed'}
$identity=Get-Content -Raw -LiteralPath $CandidateManifest | ConvertFrom-Json
foreach($hash in @($identity.gui_sha256,$identity.pulse_probe_sha256)){
 if($hash -notmatch '^[0-9a-f]{64}$'){throw 'Candidate manifest has an invalid binary hash'}
}
$operation=$Action.ToLowerInvariant()
if($Action -eq 'CaptureWatch'){
 New-Item -ItemType Directory -Force -Path $OutputDir | Out-Null
 $watchLog=Join-Path $OutputDir ('source-watch-'+(Get-Date -Format 'yyyyMMdd-HHmmss')+'.log')
 Write-Host 'Recording the physical source in bounded stereo chunks. Keep this window open; run Stage in a second PowerShell window. Both physical PTT controls remain released.'
 & ssh @opts "root@$RadioIp" "python3 $remote/capture_source_watch.py --gui-sha256 $($identity.gui_sha256) --seconds $DurationSeconds --channels 2 --chunks" | Tee-Object -FilePath $watchLog
}elseif($Action -eq 'Probe'){
 if(!(Test-Path -LiteralPath $SpeechWav)){throw 'Run -Action SpeechFixture first or supply a 44.1kHz mono PCM16 WAV with -SpeechWav'}
 & scp -O @opts $SpeechWav "root@${RadioIp}:$remote/speech.wav"
 if($LASTEXITCODE){throw 'Speech fixture copy failed'}
 $cycleArg=if($Cycles){' --cycles'}else{''}
 $probeOutput='/tmp/freedv-agent-probe-'+(Get-Date -Format 'yyyyMMdd-HHmmss')+'-'+([guid]::NewGuid().ToString('N').Substring(0,8))
 & ssh @opts "root@$RadioIp" "python3 $remote/radio_retest.py probe --duration-ms $DurationMs$cycleArg --speech-wav /tmp/freedv-agent-kit/speech.wav --turns $Turns --output $probeOutput --gui-sha256 $($identity.gui_sha256) --probe-sha256 $($identity.pulse_probe_sha256)"
}else{
 $argsText="--manifest $remote/candidate-manifest.json"
 if($Action -eq 'Stage'){
  $argsText+=" --case $Case";if($RepeatFive){$argsText+=' --repeat-five'}
  if(Test-Path -LiteralPath $SpeechWav){$argsText+=' --fixture-sha256 '+(Get-FileHash -LiteralPath $SpeechWav -Algorithm SHA256).Hash.ToLowerInvariant()}
 }
 if($Action -in @('Collect','Assess')){$argsText+=' --output /tmp/freedv-agent-results'}
 & ssh @opts "root@$RadioIp" "python3 $remote/route_agent.py $operation $argsText"
}
if($LASTEXITCODE){throw "$Action failed; preserve the output for diagnosis"}
if($Action -eq 'Collect'){
 New-Item -ItemType Directory -Force -Path $OutputDir | Out-Null
 & scp -O @opts "root@${RadioIp}:/tmp/freedv-agent-results/transfer-manifest.json" (Join-Path $OutputDir 'transfer-manifest.json')
 if($LASTEXITCODE){throw 'Manifest transfer interrupted; repeat Collect'}
 $manifest=Get-Content -Raw (Join-Path $OutputDir 'transfer-manifest.json') | ConvertFrom-Json
 foreach($entry in $manifest.PSObject.Properties){
  if($entry.Name -notmatch '^[A-Za-z0-9_./-]+$' -or $entry.Name -match '(^/|(^|/)\.\.(/|$))'){throw 'Unsafe transfer entry'}
  $path=Join-Path $OutputDir $entry.Name
  if((Test-Path -LiteralPath $path) -and (Get-FileHash -LiteralPath $path -Algorithm SHA256).Hash.ToLowerInvariant() -eq $entry.Value){continue}
  New-Item -ItemType Directory -Force -Path (Split-Path -Parent $path) | Out-Null
  $partial=$path+'.partial'
  & scp -O @opts "root@${RadioIp}:/tmp/freedv-agent-results/$($entry.Name)" $partial
  if($LASTEXITCODE){throw 'Transfer interrupted; repeat Collect to resume verified chunks'}
  if((Get-FileHash -LiteralPath $partial -Algorithm SHA256).Hash.ToLowerInvariant() -ne $entry.Value){throw "Transfer hash mismatch: $($entry.Name)"}
  Move-Item -LiteralPath $partial -Destination $path -Force
 }
 Write-Host "Verified private evidence: $OutputDir"
}
