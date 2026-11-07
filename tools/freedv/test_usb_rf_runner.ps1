# Windows PowerShell 5-compatible evidence handling; mocked SSH/SCP, no radio.
$ErrorActionPreference='Stop'
$workspace=Join-Path ([IO.Path]::GetTempPath()) ('usb rf evidence runner '+[guid]::NewGuid().ToString('N'))
$root=Join-Path $workspace 'kit'
New-Item -ItemType Directory -Path $root | Out-Null
$global:usbRfPython=(Get-Command python -ErrorAction Stop).Source
$fixture=@{usb_rf_test_ready=$true;software_gates_passed=$true;usb_startup_contract_version=3;
 on_device_readiness_required=$true;usb_output_contract_version=1;usb_capture_open_contract_version=1;build_id='fixture-build';firmware_commit=('e'*40);
 gui_sha256=('a'*64);runner_sha256=('b'*64);rade_sha256=('c'*64);image_sha256=('d'*64)}
$fixturePath=Join-Path $workspace 'image-manifest.json'
[IO.File]::WriteAllText($fixturePath,($fixture | ConvertTo-Json),[Text.UTF8Encoding]::new($false))
$kit=Join-Path $workspace 'kit.zip'
& $global:usbRfPython (Join-Path $PSScriptRoot 'build_usb_rf_kit.py') --image-manifest $fixturePath --output $kit
if($LASTEXITCODE){throw 'Fixture kit packaging failed'}
Expand-Archive -LiteralPath $kit -DestinationPath $root
$global:usbRfImportProbe=Join-Path $workspace 'import-probe.py'
$probe=@'
import importlib, json, pathlib, sys
root = pathlib.Path(sys.argv[1]).resolve()
sys.path.insert(0, str(root))
names = json.loads((root / 'rf-test-manifest.json').read_text(encoding='utf-8-sig'))['files']
modules = [importlib.import_module(pathlib.Path(name).stem) for name in names if name.endswith('.py')]
assert sys.flags.isolated and modules
assert all(pathlib.Path(module.__file__).resolve().parent == root for module in modules)
'@
[IO.File]::WriteAllText($global:usbRfImportProbe,$probe,[Text.UTF8Encoding]::new($false))
$global:usbRfRemote=''
$global:usbRfImportFailed=$false
$global:usbRfImportChecks=0
$global:usbRfEvidenceCase='success'
$global:usbRfEvidenceCommands=@()
function global:Read-Host {return 'Mock listening notes; no audio heard or radio contacted'}
function global:ssh {
 $global:usbRfEvidenceCommands+=([string]$args[-1])
 Write-Output 'synthetic SSH output: no radio contacted'
 $global:LASTEXITCODE=0
 $command=[string]$args[-1]
 if($command -match '^umask 077 && mkdir /tmp/(usb-rf-evidence-[a-f0-9]+)$'){
  $global:usbRfRemote=Join-Path $workspace $Matches[1]
  New-Item -ItemType Directory -Path $global:usbRfRemote | Out-Null
  $global:usbRfImportFailed=$false
 }
 if($command -like 'python3 *'){
  # Import the actual uploaded helpers from an isolated fake radio directory.
  # This executes no collector main, device access, audio or control calls.
  & $global:usbRfPython -I $global:usbRfImportProbe $global:usbRfRemote
  $global:usbRfImportChecks++
  if($LASTEXITCODE){$global:usbRfImportFailed=$true;$global:LASTEXITCODE=1;return}
  if($global:usbRfEvidenceCase -eq 'helper_failure'){$global:LASTEXITCODE=1}
 }
}
function global:scp {
 $global:LASTEXITCODE=0
 if($args[-1] -like 'root@*:/tmp/*/'){
  foreach($item in $args[0..($args.Count-2)]){
   if(Test-Path -LiteralPath $item -PathType Leaf){
    if($global:usbRfEvidenceCase -eq 'missing_upload_dependency' -and [IO.Path]::GetFileName($item) -eq 'usb_output_contract.py'){continue}
    if($global:usbRfEvidenceCase -eq 'missing_capture_open_dependency' -and [IO.Path]::GetFileName($item) -eq 'usb_capture_open_contract.py'){continue}
    Copy-Item -LiteralPath $item -Destination $global:usbRfRemote
   }
  }
 }
 if($args[-2] -like 'root@*:/tmp/*/results'){
  if($global:usbRfImportFailed){$global:LASTEXITCODE=1;return}
  if($global:usbRfEvidenceCase -eq 'copy_failure'){$global:LASTEXITCODE=1;return}
  $result=Join-Path $args[-1] 'results'
  New-Item -ItemType Directory -Path $result | Out-Null
  $status='COLLECTED_NO_SDR_RECORDING'
  if($global:usbRfEvidenceCase -in @('helper_failure','blocked_zero_exit')){$status='BLOCKED'}
  if($global:usbRfEvidenceCase -in @('qualification','invalid_qualification','legacy_qualification','missing_completion','mismatched_identity','truncated_history','persistence_failure')){$status='READY_FOR_RF_TEST'}
  $rfTested=($global:usbRfEvidenceCase -eq 'invalid_qualification')
  $guiStatus='STOPPED';if($status -eq 'BLOCKED'){$guiStatus='BLOCKED'}
  $identity=@{session_id='freedv-usb-fixture';build_id='fixture-build';source_commit=('e'*40)}
  $completion=@{session_id='freedv-usb-fixture';build_id='fixture-build';source_commit=('e'*40);terminal=$true;trace_complete=$true;trace_history_truncated=$false;storage_errno=0}
  $version=3;$sessionStatus='COMPLETE'
  if($global:usbRfEvidenceCase -eq 'legacy_qualification'){$version=2;$sessionStatus='HISTORICAL_ONLY'}
  if($global:usbRfEvidenceCase -eq 'missing_completion'){$completion=$null}
  if($global:usbRfEvidenceCase -eq 'mismatched_identity'){$identity.source_commit=('f'*40)}
  if($global:usbRfEvidenceCase -eq 'truncated_history'){$completion.trace_history_truncated=$true}
  if($global:usbRfEvidenceCase -eq 'persistence_failure'){$completion.storage_errno=5}
  @{schema='x6200.usb-rf-evidence.v1';status=$status;rf_key_commands_sent=$false;
    usb_startup_contract_version=$version;usb_capture_open_contract_version=1;session_identity=$identity;runtime_completion=$completion;
    reason='Fixture microphone flush timeout';primary_failure_stage='gui';rf_disabled=$true;rf_tested=$rfTested;
    stages=@{capture_open=@{status='PASS'};collection=@{status='PASS'};gui=@{status=$guiStatus};qualification=@{status='PASS'};session=@{status=$sessionStatus}}} |
   ConvertTo-Json -Depth 6 | Set-Content -Encoding UTF8 -LiteralPath (Join-Path $result 'rf-evidence.json')
  'partial recording placeholder' | Set-Content -LiteralPath (Join-Path $result 'microphone.raw')
 }
}
try {
 foreach($case in @('success','helper_failure','blocked_zero_exit','copy_failure','qualification','invalid_qualification','legacy_qualification','missing_completion','mismatched_identity','truncated_history','persistence_failure','missing_upload_dependency','missing_capture_open_dependency')){
  $global:usbRfEvidenceCase=$case
  $before=@(Get-ChildItem -LiteralPath $root -Filter '*.zip').Count
  $rejected=$false;$message=''
  try {& (Join-Path $root 'Invoke-UsbRfEvidence.ps1') -RadioIp '192.0.2.1'} catch {$rejected=$true;$message=$_.Exception.Message}
  if($rejected -ne ($case -notin @('success','qualification'))){throw "Wrong result: $case"}
  if($case -in @('helper_failure','blocked_zero_exit')){
   if($message -notlike '*Fixture microphone flush timeout*' -or $message -like '*Unexpected evidence*'){
    throw "Primary blocker lost: $case - $message"
   }
  }
  $archives=@(Get-ChildItem -LiteralPath $root -Filter '*.zip')
  if($archives.Count -ne $before+1){throw "Evidence not preserved: $case"}
  $latest=$archives | Sort-Object LastWriteTimeUtc -Descending | Select-Object -First 1
  $expanded=Join-Path $root ('inspect-'+$case)
  Expand-Archive -LiteralPath $latest.FullName -DestinationPath $expanded
  $collection=Get-Content -Raw -LiteralPath (Join-Path $expanded 'collection.json') | ConvertFrom-Json
  if($collection.collection_completed -ne ($case -notin @('copy_failure','missing_upload_dependency','missing_capture_open_dependency'))){throw "Collection/test distinction lost: $case"}
  if($collection.test_accepted -ne ($case -in @('success','qualification'))){throw "Acceptance result lost: $case"}
  if($case -in @('missing_upload_dependency','missing_capture_open_dependency')){
   $missingModule=if($case -eq 'missing_capture_open_dependency'){'usb_capture_open_contract'}else{'usb_output_contract'}
   $transport=Get-Content -Raw -LiteralPath (Join-Path $expanded 'transport.txt')
   if(!$global:usbRfImportFailed -or $transport -notlike "*ModuleNotFoundError*$missingModule*"){
    throw 'Missing transferred dependency was not caught by isolated import'
   }
  }
 }
 foreach($command in $global:usbRfEvidenceCommands){
  if($command -notmatch '^(umask 077 && mkdir /tmp/usb-rf-evidence-[a-f0-9]+|python3 -u /tmp/usb-rf-evidence-[a-f0-9]+/usb_rf_evidence.py --output /tmp/usb-rf-evidence-[a-f0-9]+/results)$'){
   throw "Unexpected remote command: $command"
  }
 }
 if($global:usbRfImportChecks -ne 13){throw 'Not all staged Python import probes ran'}
 $global:LASTEXITCODE=0
 Write-Host 'PASS: actual kit upload/import closure; primary blocker, real/legacy exit mismatch, qualification scope and partial/copy evidence preservation; mocked SSH/SCP only.'
} finally {
 Remove-Item Function:\ssh,Function:\scp,Function:\Read-Host -ErrorAction SilentlyContinue
 Remove-Variable usbRfEvidenceCase,usbRfEvidenceCommands,usbRfPython,usbRfRemote,usbRfImportFailed,usbRfImportChecks,usbRfImportProbe -Scope Global -ErrorAction SilentlyContinue
 Remove-Item -LiteralPath $workspace -Recurse -Force
}
