param([string]$RadioIp='192.168.100.62',[string]$CandidateManifest=(Join-Path $PWD 'candidate-manifest.json'))
$ErrorActionPreference='Stop'
if($RadioIp -notmatch '^[a-zA-Z0-9][a-zA-Z0-9.-]*$'){throw 'Use an IPv4 address or hostname.'}
$m=Get-Content -Raw -LiteralPath $CandidateManifest | ConvertFrom-Json
if($m.gui_sha256 -notmatch '^[0-9a-f]{64}$'){throw 'Candidate GUI hash missing.'}
$remote='/tmp/freedv-source-watch-'+[guid]::NewGuid().ToString('N')+'.py'
$sshOptions=@('-o','BatchMode=yes','-o','StrictHostKeyChecking=yes','-o','ConnectTimeout=5')
& scp -O @sshOptions (Join-Path $PSScriptRoot 'capture_source_watch.py') "root@${RadioIp}:$remote"
if($LASTEXITCODE -ne 0){throw 'Could not copy read-only watcher.'}
& ssh @sshOptions "root@$RadioIp" "python3 '$remote' --gui-sha256 '$($m.gui_sha256)' --check-only"
if($LASTEXITCODE -ne 0){throw 'Capture preflight failed. Read the BLOCKED reason above; no recording started.'}
& ssh @sshOptions "root@$RadioIp" "nohup python3 '$remote' --gui-sha256 '$($m.gui_sha256)' --seconds 60 >/tmp/freedv-capture-source-watch-start.log 2>&1 </dev/null &"
if($LASTEXITCODE -ne 0){throw 'Watcher launch failed.'}
Write-Host 'Independent input recording started for 60 seconds. Run this profile's two locally armed turns now. Keep physical PTT released except during the timed middle phase in START-HERE.md; collect after both profile recordings finish.'
