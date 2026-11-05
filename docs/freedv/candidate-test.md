# X6200 FreeDV route review — one operating guide

This guide belongs to **freedv-route-review-20261104**. It replaces the November 3 route-fix test instructions. The ZIP contains one complete flashable image, the Windows runner and this guide. This is an identified experimental candidate; physical microphone routing and a production FreeDV profile remain unqualified.

## What the review established

| Finding | Consequence for this candidate and test |
| --- | --- |
| The ordinary voice recorder enters with `IMIC=0`, then `RECORD=1`; it leaves hand gain and microphone selection untouched. Our previous purported reference also muted hand gain, rewrote selection and resent registers. | The reference now uses the ordinary recorder's hardware entry and exit operations. It records raw PCM; it does not reproduce the recorder's later bandpass/notch/MP3 processing. |
| The old reference retained RECORD between repetitions. | The five baseline recordings now exercise real recorder stop/start cycles, with twenty seconds idle after restoration. Retained voice sessions remain a separate behavior. |
| The GUI staging parser expected the wrong number of fields. The helper reported success before GUI acknowledgement. | Stage now requires a fresh, matching GUI acknowledgement with a unique request identity and the exact selected case/settings. An unacknowledged or changed selection must not be run. |
| Cleanup rewrote mixer controls even under “Keep mixer”, and forced mono while retaining a stereo stream. | Unchanged mixer settings are left alone; capture format stays consistent until stream closure. Neither can silently change the repeated comparison. |
| In the earlier USB evidence, independent-source and adapter samples matched exactly, including the missing-speech intervals. | Investigate the shared source/baseband route. Preserve the sample-loss correction and 500 ms backlog guard. Codec normalization is unchanged. This does not exclude loss upstream of both recordings. |
| The old RF case reset gains and selection as it enabled MODEM. | A new `record-then-modem` case establishes ordinary recording first, retains it for two seconds, then adds MODEM for the stored-waveform transmission. The independent source watcher covers both sides of this transition. |
| Playback could sleep after each small partial write while ready PCM remained queued. | Submit a bounded amount of already-produced PCM before waiting. No second-block startup gate, larger backlog allowance or new buffer request is introduced. Hardware underflow improvement still needs measurement. |
| The control getter reads a software cache. Full-register resends are a required keepalive. | Trace individual writes and bulk resends separately. Cache match, transport success and observed RX are separate facts; none is a physical routing measurement. |
| Existing digital-app transmission proves internal PCM delivery, not simultaneous usable microphone capture. The documented backend still lacks independent hand-PTT release observation. | Investigate capture directly. Keep production exposure gated; neither tone transmission nor aggregate TX supplies these missing capabilities. |

Acquire and preflight were checked for hidden changes: successful acquisition snapshots state and reserves ownership; preflight inventories devices and hashes binaries. Neither applies a microphone route. Shared route operations also do not make all diagnostic settings identical to a future production profile: production adds explicit filter, AGC and processing overrides. Those remain separate qualification work.

## Install and prepare once

1. Download this release's `x6200-freedv-route-review.zip` and `DOWNLOAD-SHA256SUMS.txt`. Verify the ZIP with `Get-FileHash -Algorithm SHA256`. Extract it and flash its single `.img` to the **candidate card**, using the imager's verification option. Keep the working `.2` card for recovery. The new image is the one under test; do not flash the working card or change baseband firmware.
2. Boot with **both the radio-side PTT and hand-microphone PTT released**. Use the existing dummy load and attenuated SDR setup. Keep the same suitable test frequency as before and select **USB**. Before Acquire, ensure VOX is off. Freeze microphone preferences/gains, monitor level, filters and mixer for this comparison; do not independently write ALSA or radio registers.
3. Extract all helpers beside `candidate-manifest.json`. Open Windows PowerShell in that directory. The local agent uses the existing SSH key and verified host record for `root@192.168.100.62`; web UI is `http://192.168.100.62/`. No authentication or global execution-policy changes are needed.
4. Run:

```powershell
.\Invoke-FreeDVAgent.ps1 -Action Preflight
.\Invoke-FreeDVAgent.ps1 -Action SpeechFixture
```

Preflight checks the installed GUI/build and stored modem fixture against this candidate and records source/mixer/input inventory. A failure is **BLOCKED**, with its reason. Do not substitute another build. `SpeechFixture` creates `numbered-speech.wav`: numbered phrases containing “Alpha begins. One two three four. Bravo ends.” Keep the original file and its hash. If speech synthesis is unavailable, use an existing 44.1 kHz mono PCM16 numbered-speech WAV with `-SpeechWav`; record that dependency and the exact fixture hash.

Position a PC speaker near the **hand microphone**, at a fixed normal speech level. Play the WAV on repeat. Leave its position and volume unchanged across comparisons. This supplies repeatable acoustic speech, avoiding repeated manual speaking. The test does not use remote microphone or remote PTT.

Open **APP → Modes → FreeDV test** on the radio and press **Acquire** locally. Keep USB and frequency fixed. No old speaker-tone, RF-tone or virtual lifecycle batch is a prerequisite.

**PTT instruction for every test in this guide: leave BOTH physical PTT buttons released for the entire test, including RF.** Local Arm and Run are the diagnostic screen controls. Do not press either PTT to help the test. A physical held-PTT experiment would require a separately labelled procedure.

## First test: a faithful recorder control, without RF

In the first PowerShell window:

```powershell
.\Invoke-FreeDVAgent.ps1 -Action Stage -Case record-ordinary-zero -RepeatFive
```

Require `STAGED`, and verify the radio shows **Ordinary recorder sequence / internal zero**, route **R1 M0 P0**, with mono capture and **Keep mixer**. This preserves the saved microphone selector and hand gain. It changes internal gain to zero as the ordinary recorder does. No MODEM/software PTT assertion is permitted in this batch.

In a second PowerShell window, in the same directory:

```powershell
.\Invoke-FreeDVAgent.ps1 -Action CaptureWatch -DurationSeconds 150
```

Keep that window open. It starts independent source capture in bounded stereo chunks and records its command output locally. Wait for the watcher’s `RECORDING` message (source PCM has arrived), with the acoustic fixture playing, then press **Run once on the radio**. Five eight-second recordings run, separated by twenty seconds after each restoration. Keep both PTT controls released. The source watcher captures preparation, the transitions and idle periods; case input WAVs cover the individual active recordings. Stereo here may already be downmixed by ALSA: it is not proof of two separate physical sources.

The local agent monitors the batch from the first window:

```powershell
.\Invoke-FreeDVAgent.ps1 -Action Status
.\Invoke-FreeDVAgent.ps1 -Action Events
```

On a fault, stop and preserve evidence; do not repeat an unchanged failure. After the five recordings **and** the watcher have finished:

```powershell
.\Invoke-FreeDVAgent.ps1 -Action Collect -OutputDir .\evidence-recorder-control
.\Invoke-FreeDVAgent.ps1 -Action Assess
```

Collection hashes every transferred file and resumes by skipping verified copies. If interrupted, repeat Collect to the same folder. Collection is not test acceptance. Missing recordings, incomplete repetitions, changed files or mismatched identities must remain INCONCLUSIVE/FAIL rather than PASS. Keep the source-watch local log with the evidence. Do not publish these private recordings.

Listen to the captured input, and assess all five for sustained numbered speech and flat intervals. Record the recognisable phrase numbers, first/last audible words, reviewer, file names and hashes. A spike, hum, nonzero samples or a mechanical “complete” result does not establish intelligibility. Compare the independent source and case input at the same intervals before assigning a cause.

## Choose the next test from the result

| Result | Next action |
| --- | --- |
| All five ordinary-recorder captures contain intelligible speech | Proceed to the new recording-to-MODEM comparison below. Do not automatically run all gain/selector combinations. |
| First recording works, later recording fails | Compare the stop/start, idle keepalive and source audio around the first loss. Return that evidence before another candidate; do not rerun the same five cases. |
| Ordinary recorder loses speech in both source and adapter recordings | Investigate the common microphone/source/baseband boundary. No RF or RADE test can repair absent input. Stop this batch. |
| Source contains speech but adapter input does not | Reproduce the exact saved PCM through the stream/channel delivery path. This is an automated software investigation. |
| A reliable ordinary baseline differs from the previous shared-route result | Use only the relevant one-variable comparison below to isolate the difference. |

The available no-RF comparisons use the same Stage/CaptureWatch/Run/Collect procedure: `record-ordinary-saved` changes only internal gain relative to ordinary-zero; `record-hand-zero` changes only microphone selector relative to ordinary-zero; `record-hand-saved` combines the saved internal gain with explicit hand selection and is only needed to reconstruct that previous condition. Each uses its own five-run batch and evidence folder. The agent should explain which observed difference warrants a particular comparison before staging it. Stop after two failed targeted corrections to the same signature and return to the command/audio evidence.

## Second test: add MODEM to established recording

Only after the ordinary baseline is repeatable, stage:

```powershell
.\Invoke-FreeDVAgent.ps1 -Action Stage -Case record-then-modem
```

Keep the same USB frequency, saved microphone selector/gains, monitor, filters, mixer, fixture position and speaker volume. Start a new independent source recording in the second window:

```powershell
.\Invoke-FreeDVAgent.ps1 -Action CaptureWatch -DurationSeconds 30
```

Start the SDR recording before arming. Confirm `STAGED`, **Ordinary recording then MODEM only 8s** and **R1 M1 P0**. **Both physical PTT controls stay released. Press Arm → Run locally for this one transmission.** Browser automation may monitor but must not generate local input provenance.

The case applies the declared 1 W/VOX safety settings before the recording baseline, starts ordinary recording, waits at least two seconds with fresh RX, then enables MODEM and sends eight seconds of stored RADE waveform. Stop/protection cancels preparation as well as transmission. This case uses the saved microphone selector; it does not silently force Hand, mute/re-enable the microphones or substitute live encoding at the MODEM edge. The required full-register keepalive remains active and is traced.

After natural completion, fresh RX and the watcher ending:

```powershell
.\Invoke-FreeDVAgent.ps1 -Action Collect -OutputDir .\evidence-record-then-modem
.\Invoke-FreeDVAgent.ps1 -Action Assess
```

Associate the original SDR WAV with the exact case directory, profile and source recording. Record whether numbered speech was present during the two-second preparation and throughout modem transmission. The stored waveform's decoded content differs from the acoustic microphone fixture: this permits detection of unwanted analog speech mixing. Listen for that fixture on RF and preserve the original recording; this single observation does not complete leakage qualification.

- **Speech survives the MODEM edge:** stage `live-speech`, start another 30-second watcher and SDR recording, then perform one separately armed eight-second turn. This uses the existing shared production-style activation sequence, so compare its command trace with the successful recording-to-MODEM case. Keep both PTTs released. Collect immediately.
- **Speech disappears when MODEM is introduced:** stop. We now have an isolated command boundary and continuous source recording. Return those files for firmware/baseband investigation; do not run a USB-DIG sweep or another unchanged RF attempt.
- **Stored-waveform case works, live-speech case loses capture:** investigate the differing activation sequence using the traces. Do not assume encoder load is the cause when raw input disappears.
- **Captured speech is intelligible but local decode is poor:** replay that exact capture through fresh and retained engines and the pinned reference, automatically.
- **Local decode is intelligible but SDR decode is poor:** analyze carrier offset, continuity, clipping and filtering from these recordings first. Preserve originals; offset-corrected audio is a derived artifact.

The older `modem-waveform` and `modem-prekey-gain` cases remain available for a specifically justified transition comparison. They are not the initial MODEM-isolation test and are not required in this sequence.

## Local-agent assignment and recovery

The local agent handles preflight/inventory, staging and acknowledgement, source recording, status/events, collection integrity, analysis and one result ledger. The operator positions the acoustic speaker, presses local Run/Arm, performs listening assessment and supplies the SDR recording. No camera is needed. No second process may issue raw I2C/radio writes while the GUI owns its command cache.

If software delivery needs investigation, the short isolated ARM probe is available:

```powershell
.\Invoke-FreeDVAgent.ps1 -Action Probe -Turns 4 -SpeechWav .\numbered-speech.wav
```

It uses a private virtual PulseAudio server and cannot qualify the physical microphone route. **It is optional here; 100 virtual lifecycles are not a gate to the microphone experiment.** Prior endurance results keep their original identities and limits in `software-verification.json`.

Use local **Stop** on any unexpected behavior. The helper may also request cancellation:

```powershell
.\Invoke-FreeDVAgent.ps1 -Action Abort
```

Abort cancels pending repetitions. Keep/release both physical controls, wait for fresh RX and restoration, then Close. A physical held PTT cannot be overridden by software. If recovery remains pending, preserve status/logs and use the working-card recovery path. Persisted preferences never resume TX.

Return one private evidence folder with: installed and helper identities; requested and GUI-accepted stage; completed repetition count; first failure; continuous source/case-input comparison; control-trace findings; SDR association; listening assessment with hashes; and the next justified action. A successful file transfer is COLLECTED, never an audio PASS.

## Current result ledger and limits

`candidate-manifest.json` identifies the exact image, source, GUI, engine, models and modem fixture. `software-verification.json` identifies current regression/build checks and retained earlier evidence separately. `delivery-verification.json` records complete-image reconstruction and ZIP/image integrity.

The new tests address demonstrated defects in our implementation and experimental controls. They do not establish the baseband mechanism that intermittently removes speech. Hardware route repeatability, the playback-underflow correction on the radio, independent hand-PTT release, intelligibility and RF/speaker independence remain unproven. The production mode stays gated.

After the common capture problem is resolved, complete matched USB/USB-DIG profiles, actual-application turns and both physical controls, bidirectional official-GUI interoperability and controlled RF reception while decoded speech plays. The receive-only SDR cannot supply that receive signal. The independent 500 ms RF-off measurement and historical 70% processing-margin failure retain their separate open/failed status. Earlier successful tests remain usable only within their original scope; no repetitive basic matrix is requested here.
