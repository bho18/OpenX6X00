param([string]$RadioIp='192.168.4.210', [switch]$CheckOnly)
$ErrorActionPreference='Stop'
$kitDir=Split-Path -Parent $MyInvocation.MyCommand.Path
$python=Join-Path $kitDir 'mic_ptt_input_watch.py'
if(!(Test-Path -LiteralPath $python -PathType Leaf)){throw "Missing $python"}
if($RadioIp -notmatch '^[a-zA-Z0-9.:-]+$'){throw 'Invalid radio address'}
$opts=@('-o','BatchMode=yes','-o','StrictHostKeyChecking=yes','-o','ConnectTimeout=5')
$token=[guid]::NewGuid().ToString('N')
$remoteScript="/tmp/mic-ptt-input-watch-$token.py"
& scp -O @opts $python "root@${RadioIp}:$remoteScript"
if($LASTEXITCODE){throw 'Script copy failed'}
& ssh @opts "root@$RadioIp" "python3 -u $remoteScript --check-only"
if($LASTEXITCODE){throw 'Read-only prerequisite check failed; no press test started'}
if($CheckOnly){return}
Write-Host 'This observes ordinary PTT and DOES NOT inhibit RF.'
Write-Host 'Before continuing: suitable dummy load connected, ordinary USB mode, lowest power,'
Write-Host 'no digital/test mode, no transmitting CAT/TCI/modem applications, both PTT controls released.'
$answer=Read-Host 'Type READY when prepared, or press Enter to cancel'
if($answer -cne 'READY'){Write-Host 'Cancelled; no press test started';return}
$evidence=Join-Path $kitDir ("mic-ptt-results-$token")
New-Item -ItemType Directory -Path $evidence | Out-Null
$remoteOut="/tmp/mic-ptt-input-results-$token"
$savedPreference=$ErrorActionPreference
try {
 $ErrorActionPreference='Continue'
 & ssh @opts "root@$RadioIp" "python3 -u $remoteScript --output $remoteOut" 2>&1 |
  Tee-Object -FilePath (Join-Path $evidence 'console.txt')
 $observationExit=$LASTEXITCODE
} finally {$ErrorActionPreference=$savedPreference}
$observationExit | Set-Content -Encoding Ascii -LiteralPath (Join-Path $evidence 'observation-exit-code.txt')
Write-Host 'Release both PTT controls now. Confirm the radio returned to receive.'
$notes=Read-Host 'For panel, mic first, mic second: did the TX indicator follow each press/release? Note missed prompts or unexpected behavior'
$notes | Set-Content -Encoding UTF8 -LiteralPath (Join-Path $evidence 'operator-notes.txt')
& scp -O -r @opts "root@${RadioIp}:$remoteOut" $evidence
$copyExit=$LASTEXITCODE
$copyExit | Set-Content -Encoding Ascii -LiteralPath (Join-Path $evidence 'copy-exit-code.txt')
$zip="$evidence.zip"
Compress-Archive -Path (Join-Path $evidence '*') -DestinationPath $zip
Write-Host "Observation exit: $observationExit; copy exit: $copyExit"
Write-Host "Send back: $zip"
if($observationExit -ne 0 -or $copyExit -ne 0){Write-Warning 'Incomplete run; preserve this ZIP for diagnosis. Remote evidence remains in /tmp.'}
