# X6200 native FreeDV diagnostic card: short operator session

This guide is for the paired native-diagnostics image release, after its automated
build and correctness gates pass. It is **not** a production FreeDV mode.
A candidate image being ready for this procedure does not mean physical routing,
RF interoperability, PTT recovery or radio performance has passed.

**Current continuation:** use [Session 1](native-session-1.md) for hand-mic RADE
RF proof on the installed image, followed by [Session 2](native-session-2.md).
The operator selected proof of concept first after functional recovery passed.
Functional Stop confirmation permits that short demonstration; numerical external
RF cutoff remains a separate INCONCLUSIVE gate until measured. The historical
sequence below does not require that precision measurement before the proof.

**After the first live TX results:** the original image produced a valid modem
signal but badly distorted RF-recovered speech. The verified updated image uses
the shorter `TX-DELIVERY-TEST.md` procedure included in its image artifact, with
a known-waveform TX comparison before live mic tests. Do not repeat the original
image's ten-turn batch while voice delivery remains unresolved.

## Prepare the spare card

1. Download the completed **FreeDV native diagnostic images** workflow artifact
   from `bho18/OpenX6X00`, branch `experiment/x6200-native-diagnostics`.
   Use the diagnostic `.img` and its `SHA256SUMS` from the same artifact. On
   Windows, compare `Get-FileHash <image-path> -Algorithm SHA256` with that entry.
   Do not mix images, libraries or models from different runs.
2. Back up settings to the PC and preserve the working card. Flash the raw image
   to the **spare microSD** with the usual SD-image writing application. Flashing
   replaces that selected card's contents. Do not format the unexplained data
   partition on the working card. First boot may initialize the spare image's data
   partition through the normal service scripts.
3. Connect the dummy load and set up the receive-only SDR to record the radio's
   RF output. Start the radio with PTT released. No baseband update is required;
   retain baseband 1.0.7 and the normal update-signature protections.
4. Verify ordinary receive, controls, waterfall, speaker and hand microphone.
   For a brief ordinary microphone check into the dummy load, choose low power,
   press PTT briefly, speak and release. Check RF has stopped before continuing.
5. Before diagnostics, select an allowed HF frequency, **USB**, split OFF and TX
   shift zero. Choose the desired normal speaker/monitor levels. The diagnostic
   backend refuses unsupported tuning arrangements rather than moving the VFO.
   It snapshots these settings; cases request 1 W transiently.

Keep the normal GUI/waterfall running and close other digital/audio applications,
recorders, screen recording and TCI sessions. Do not use an external audio route
as a substitute for missing native-route evidence.

## Operate the panel

Open **Settings → Info → Open FreeDV Diagnostics**. The small panel appears beside
part of the normal waterfall. Use the **MFK encoder**: turn to focus a control,
press to edit/select it, and turn/press again to make the choice. The simulator
also supports its local mouse. Remote key/knob injection is blocked while this
panel owns the local-input fence; PC tools cannot arm or run RF cases.

- **Acquire** reserves the radio and audio writers. A refusal is meaningful:
  release PTT, close competing sessions and retry. If startup is awaiting RX,
  release microphone PTT and wait for fresh packet status.
- Select the named case, route bits, microphone, capture channel and mixer option.
  Selection itself never transmits or starts a case.
- For a routing/TX case, select **Arm**, then **Run** for the exact same choices.
  Each case needs new local arming. Typical RF cases last eight seconds;
  software-controlled TX cannot exceed ten seconds. The timeout case lasts three.
- Receive/capture cases use **Run** without RF arming. Speaker playback requires
  an unkeyed route confirmed during this same owned session and exact microphone,
  channel and mixer selection.
- **Stop** cancels pending commands, fences generated audio and requests software
  unkeying. It remains available while tests run. **Close** requests Stop and waits
  for safe restoration before closing the panel and releasing ownership.
- If the display says **Release microphone PTT**, release it. Software cannot
  override a hardware-held PTT. Analog routing is not restored while TX or stale
  status remains observed.
- **Export** saves results locally. No recordings/results are uploaded.

Keep a short PC notes file with case numbers, microphone and generated signals,
what was heard at the speaker, SDR recording names/hashes, GUI responsiveness and
any limitations. The firmware exports observations and counters; it does not
infer an RF PASS from cleared command bits.

## One consolidated session

Start with **Hand mic / Existing mono / Keep mixer**. Run promising routes first
so audible decoding can be demonstrated before finishing all remaining coverage.

| Step | Named cases and actions | Observe and record |
|---|---|---|
| Baseline capture | Capture 10s; speak a recognizable phrase | What Linux capture receives; no software TX |
| Native-route probes | Each required `Route R… M… P…`, Arm then Run, with dummy load and SDR | Distinguish microphone speech from generated 735 Hz tone; speaker, Linux capture and RF; concurrency and analog leakage. Immediately after the chosen independent TX probe passes, select its exact route/mic/channel/mixer and `Confirm independent mic/TX route`, then Run |
| Qualify RX route | After a completed unkeyed probe, choose its exact route/mic/channel/mixer, select `Confirm unkeyed RX route`, then Run | Confirm speaker/capture behavior as well as packet RX; confirmation alone is an operator observation |
| First audible decode | `RADE fixture to speaker 120s`, Run on the confirmed unkeyed route | Intelligible speech, waveform capture, counters, bounded queues and playback continuity; GUI/waterfall responsive |
| Receive independence | `Local tone during receive 20s` on a confirmed unkeyed route; use a stable off-air signal observed simultaneously by SDR | Whether receive capture continues and local playback contaminates it. If an antenna is needed, attach only for these unkeyed receive cases and reconnect the dummy load before any RF Arm |
| Stop and recovery | On the chosen RF route: `Manual Stop test` (press Stop before expiry), `Watchdog timeout test` (let expire), `Audio failure test` (let fail) | RF stop and fresh post-clear RX; exact restoration; expected first stop cause. Each case needs Arm/Run |
| Qualify functional stop path | After all three stop tests, select `Confirm Stop/timeout/failure observations`, Run | Use the same mic/channel/mixer/route as the already confirmed TX probe. Confirm functional RF stopping/restoration; wrong stop causes or unexpected audio/storage failures do not qualify. Measure the separate 500 ms RF bound with correlated external evidence. |
| Live microphone TX | `Hand/built-in mic RADE TX 8s`, Arm/Run, speak into the hand mic | SDR record and reference-decode on the PC. Listen for beginning/end analog leakage; verify recognizable recovered speech |
| Short mixed turns | About ten cycles of ~20s `Interrupted RADE fixture 20s` or known-fixture/live RX, one locally armed short TX, ~5s idle/acquisition | Turnaround, no prior-turn audio, interruptions/reacquisition, queue and resource behavior. Allow 5–10 minutes; stop receive early for short turns |
| Complete native coverage | Finish all eight routing combinations, relevant built-in-mic keyed cases, physical PTT behavior and supported channel/mixer alternatives | Do not conclude built-in-mic failure from receive-only absence. Preserve unsupported/unrun cases as INCONCLUSIVE |

An RX confirmation depends on the last completed matching unkeyed probe. A TX
confirmation depends on the last completed matching route with observed TX and
usable audio evidence. Confirm TX before running other cases that replace that
last result. The three Stop checks are tracked for the selected microphone,
channel, mixer and route. Qualification expires when the radio session is
released; it is never restored after restart.

For built-in or channel/mixer alternatives, repeat the dependent brief route and
stop checks for that exact choice before live encoding. Supported ADC alternatives
are enumerated by the device; unsupported controls are reported, not guessed.
If monitor/speaker levels need changing, Close/release, change them normally and
start a new owned session. Native alternatives must be documented before external
audio is considered.

## Physical PTT and restart

Separately observe GUI/front-panel and directly connected hand-mic PTT press,
hold and release. Include Stop while PTT is held; restoration must remain blocked
until release and fresh RX. Record any first/last-frame analog leakage.

Only after ordinary Stop/timeout/failure behavior is predictable, perform the
controlled restart case into the dummy load. Export first, then use the service's
stop/start mechanism, normally `/etc/init.d/S95gui stop` and
`/etc/init.d/S95gui start`; verify the service path on the installed image.
Do not merely kill `x6100_gui`: its supervisor restarts it. Observe RF and the
analog microphone while hardware PTT remains held, then release PTT. Startup
must not resume arming or a test. A process crash is a separate limitation from
an in-process software abort guarantee.

## Export once to Windows

Select Stop, wait for restoration, then Export. Note the exact folder shown,
for example `/tmp/freedv-native-20261028-150000-123`. Copy it before reboot:

```powershell
$RadioIp = "192.168.100.64"
$RemoteResults = "/tmp/freedv-native-REPLACE-WITH-DISPLAYED-FOLDER"
$Results = Join-Path $env:USERPROFILE ("Downloads\freedv-native-" + (Get-Date -Format yyyyMMdd-HHmmss))
New-Item -ItemType Directory -Path $Results | Out-Null
scp -r "root@${RadioIp}:${RemoteResults}/." $Results
Compress-Archive -Path "$Results\*" -DestinationPath "$Results.zip"
Get-FileHash "$Results.zip" -Algorithm SHA256
```

If modern `scp` reports that the radio has no SFTP server, retry with `scp -O`
if your Windows OpenSSH supports it. Copying/collecting results never arms RF.
Add the PC notes and SDR references before making the final ZIP. For TX evidence, record demodulated USB audio to a WAV with about one second
before/after keying; note its sample rate. If no PC reference decoder is set up,
include that demodulated WAV in the result bundle for decoding with the pinned
host reference tools. Keep transmitted-speech recovery INCONCLUSIVE until that
decode is intelligible. Keep large raw-IQ SDR recordings on
the PC; include their case association and hashes. Recordings on
radio are bounded excerpts (normally the first four seconds of selected streams),
while counters cover the whole case. Intentional excerpt truncation and explicit
abort tail cancellation are reported separately from unexplained sample loss.

Results include source/model/SDK manifests, state events, before/after cached
radio and selected ALSA settings, native/codec statistics, selected WAVs and
resources. Confirm actual run durations: an early Stop/EOF/failure is not a
completed two-minute case. Any missing evidence remains INCONCLUSIVE.

Fixture decoding to the speaker is not controlled RF receive interoperability.
`Live receive RADE 120s` is available when a valid signal exists, but controlled
RF receive interoperability still needs a peer transmitter or suitable signal
source. No routine 30-minute audio run is required. Keep the original 70% margin
failure as a separate finding; intelligible speech does not change that result.
