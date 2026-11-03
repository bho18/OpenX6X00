# X6200 FreeDV route investigation — current operating guide

This candidate implements shared production/diagnostic audio routing and named microphone comparisons. It is an experimental diagnostic candidate. Ordinary FreeDV availability remains gated by hardware qualification and independent hand-PTT support.

Install the **new complete image** on the candidate card. Keep the working `.2` card as recovery. Extract the ZIP and write its single `.img` with verification. The working `.2` card is not the image under test. Boot with both PTT buttons released and the existing dummy load/attenuated SDR arrangement. Do not change the baseband firmware.

## What changed and what this test decides

The firmware's voice-message recorder suppresses the internal microphone gain; the earlier FreeDV diagnostic restored both gains. Diagnostic and production activation now share the same driver: mute inputs, select the route, confirm transport, then admit the hand microphone. Ordinary preferences return on Close, with RECORD restored before microphone gains. A retained stereo stream now refuses a change to mono instead of silently discarding every sample. The capture-loss protection remains in place.

The first question is whether the ordinary recording convention repeatedly captures the external acoustic speech. The second is whether introducing MODEM removes that capture. RF delivery, old tone cases and speaker demonstrations are not repeated as prerequisites.

## Preparation by the local agent

Use this directory's `Invoke-FreeDVAgent.ps1`, its Python helpers and `candidate-manifest.json`. Use existing SSH authentication and strict host checking for `root@192.168.100.62`; the browser is `http://192.168.100.62/`. Do not change keys, host records, execution policy, mixer controls or services independently.

```powershell
.\Invoke-FreeDVAgent.ps1 -Action Preflight
.\Invoke-FreeDVAgent.ps1 -Action SpeechFixture
```

The second command creates `numbered-speech.wav` using Windows speech synthesis. Retain its hash and the original file. Position a PC speaker near the **hand microphone**, at a fixed ordinary speaking level. Replay that WAV continuously during microphone cases. The fixture is acoustic input to the normal hand microphone; it does not provide remote microphone/PTT support.

The agent inventories input devices, source channels, mixer controls and installed identity. Report any missing prerequisite as BLOCKED. It must never reinterpret aggregate TX as independent hand-PTT release.

Open **APP → Modes → FreeDV test** locally and press **Acquire**. Leave the frequency and USB profile fixed. Both physical PTT controls remain **released throughout every test below**, including RF, unless a later specifically named physical-PTT case replaces that instruction. Run/Arm refers to the front-panel diagnostic controls, not either PTT button.

## First batch: capture without RF

The agent stages the existing voice-recorder convention:

```powershell
.\Invoke-FreeDVAgent.ps1 -Action Stage -Case record-ordinary-zero -RepeatFive
```

After staging, verify the radio shows **Record ordinary mic / internal zero** and R1 M0 P0. Start continuous independent source recording before local Run. The agent uses the staged helper and expected GUI hash:

```text
python3 /tmp/freedv-agent-kit/capture_source_watch.py --gui-sha256 <candidate gui_sha256> --channels 2 --chunks --seconds 150
```

Run that command in a separate SSH process, with stdout/stderr retained. The GUI remains running. It records the physical PA source in ten-second stereo WAV chunks; it sends no radio, playback or mixer commands. These channels may already be mixed by ALSA, so stereo alone does not prove physical separation.

Press **Run once locally**. Five eight-second recordings run with approximately twenty seconds between them. This batch cannot enable MODEM or software PTT. Keep the acoustic fixture playing and both PTT buttons released. No speaking or button choreography is needed. Stop the batch on a displayed fault; do not retry it.

The agent polls and collects:

```powershell
.\Invoke-FreeDVAgent.ps1 -Action Status
.\Invoke-FreeDVAgent.ps1 -Action Events
.\Invoke-FreeDVAgent.ps1 -Action Collect -OutputDir .\evidence-record-ordinary-zero
.\Invoke-FreeDVAgent.ps1 -Action Assess
```

Collection is incremental and verifies every transferred file against its transfer manifest. Repeat Collect after interrupted transfer. Preserve originals. Collect only after all five cases and the independent watcher finish; an active file changing during collection is an incomplete collection, not PASS. Exported `control-trace.jsonl` describes requested/cached commands and transport results, not physical register readback.

Listen to one numbered capture and inspect all five for fixture presence, flat intervals, sample continuity and restoration. Do not count a single spike or hum as usable speech. Activity remains INCONCLUSIVE until listening identifies the phrase and its first/last words.

## The next batch is selected by that result

- If ordinary/zero captures reliably, compare **record-ordinary-saved** using the same five-run procedure. This changes only internal mic gain.
- If the gain comparison matters, compare **record-hand-zero** against ordinary/zero. This changes microphone selection while keeping the internal gain zero.
- Use **record-hand-saved** only when needed to reconstruct the previous diagnostic convention.
- If the first turn succeeds and a later turn fails, inspect the intervening command/resend/restore trace before another batch. Keep the selected profile and streams retained. Do not return to automatic microphone selection between ordinary persistent voice turns.
- If the independent source has the fixture but the adapter does not, investigate sample/channel delivery. If both lose it, investigate the common route/source boundary. Do not change RADE normalization to repair missing input.
- If ordinary recording also fails, investigate that firmware microphone path first. Do not proceed to RF.

Each condition needs its own staged batch and one local Run. Change nothing else. Collect and verify evidence between batches. If temporary storage becomes insufficient, stop; transfer and verify completed evidence before removing only those identified remote test copies. Do not delete originals on Windows or unrelated radio logs.

## Introduce modem transmission after capture succeeds

The local agent stages:

```powershell
.\Invoke-FreeDVAgent.ps1 -Action Stage -Case modem-waveform
```

Start the acoustic fixture, continuous independent source capture and SDR recording before Arm. Verify **Known RADE waveform TX 8s**, R1 M1 P0, hand microphone, existing mono and saved mixer. The diagnostic requests 1 W and preserves protections. Press **Arm → Run locally for this transmission**. Both physical PTT controls stay released. The stored waveform avoids live encoder workload while testing whether MODEM changes microphone admission.

After natural completion and fresh RX, collect immediately. Associate this case with its SDR recording. Stop on any fault; do not rerun an unchanged failure.

- Capture survives MODEM: stage `live-speech` for one eight-second locally armed turn with the same fixture and profile.
- Capture disappears at MODEM activation: stage `modem-prekey-gain` for one separately armed comparison, which changes only whether the shared driver raises the hand gain before or after keying. Capture source and SDR throughout. Compare the control traces and source recordings. Return that evidence before issuing another candidate; no USB-DIG or tone sweep.
- Intelligible capture but poor local decode: replay that exact capture through fresh/retained engines and the pinned reference.
- Good local decode but poor SDR decode: inspect measured carrier offset, waveform alignment, clipping and discontinuities. Keep original SDR recordings unchanged; corrections are derived analysis copies.

After two targeted corrections fail against the same signature, suspend manual repeats and investigate the unresolved firmware behavior from preserved evidence.

## Automated tests without RF

The speech probe runs actual ARM processing and retained audio streams on a **private virtual PulseAudio server**, with a separate speech source and output sink:

```powershell
.\Invoke-FreeDVAgent.ps1 -Action Probe -Turns 4 -SpeechWav .\numbered-speech.wav
```

Start with four turns. Increase to 100 only after the short run passes and storage preflight permits retaining every output. These results do not qualify the physical microphone route or intelligibility. Existing engine tools additionally exercise 100 TX/RX turns, 100 lifecycles and valid modem RX; distinguish paced physical execution from accelerated host results.

## Stop, recovery and results

Local **Stop** is always available. The agent can also request cancellation:

```powershell
.\Invoke-FreeDVAgent.ps1 -Action Abort
```

Abort clears pending recording repetitions. It cannot force RX while a physical PTT remains held. Release both physical controls, wait for fresh RX/restoration, then Close. If recovery remains pending, preserve the status/logs and use the working card recovery path. Persisted settings never resume active TX.

Current independent hand-PTT release remains unsupported by the pinned documented control API. Normal APP-mode exposure stays gated. Later acceptance requires both physical controls, twenty successful speech turns, controlled RF reception while the speaker plays, both official-GUI directions and the conversation. A transmitting peer/RF source is needed for receive qualification. Independent RF timing and the historical 70% processing-margin failure remain open gates.

The local agent should return one verified private evidence folder and a concise result: identities, completed cases, first failure, input/source comparison, control trace findings, listening assessment and the next named action. COLLECTED means retrieved; it never means accepted.
