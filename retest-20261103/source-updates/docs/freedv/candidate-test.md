# FreeDV focused retest — 2026-11-03

This replaces the previous A/B/P runbook for this iteration. Use the **new image** on the candidate card. The working `.2` card is the recovery card only.

The objective is to resolve the failed second transmission and missing microphone input. Do **not** repeat the basic no-RF, local tone, fixture-playback, B1 tone, Stop, timeout or injected-failure confirmations. The retest image reuses those reviewed observations for the exact tested routes. It does not mark microphone isolation, intelligibility or a native production profile as qualified.

## What the previous recordings established

- USB case 10 completed its modem output, but microphone capture after the initial transient was essentially a DC level of 149 with about one PCM count of AC variation. There was no useful speech to encode. A peak meter was misleading here; the new image measures AC activity and excludes the initial 200 ms from the diagnostic level calculation.
- USB-DIG case 28 captured varying microphone audio and produced a complete modem stream. The corresponding SDR recording was approximately **70.55 Hz high**. The earlier USB-DIG fixed-waveform recording was about **79 Hz high**. Both exceed the pinned receiver's approximately ±50 Hz initial search range. Correcting derived copies recovered 7.63 seconds from P1, matching the local modem file's decoded duration. Listening acceptance remains separate. The frequency offset cannot yet be assigned specifically to the SDR or radio oscillator.
- The second persistent transmission failed after only 80–130 ms of accepted capture, before the 200 ms reserve and before any new modem output. That rules out slow encoding of a new modem frame as its immediate cause. The export omitted the precise first-fault reason and capture queue depth. Its original cause remains unresolved.
- Further attempts reused the faulted adapter and could briefly key before failing again. The new image rejects these attempts before RF arming, retains the original fault identity, and directs recovery through Close/Acquire.
- A2/A3 microphone audio mixed into the speaker, and the capture files also contained local playback. This remains a route-isolation failure. Repeating those same tone tests would not resolve it. No full native receive qualification is claimed.

The codec/model arithmetic and USB/USB-DIG route settings are unchanged. A proposed extra prebuffer operation was discarded after checking PulseAudio's implementation: flush already restores prebuffering.

## 1. Install and connect

1. Download the release's single **x6200-freedv-retest.zip** and extract it completely. It contains one **x6200-freedv-retest.img**, not image parts. Write that `.img` to the candidate SD card with your normal image writer and enable verification. Keep the working `.2` card untouched.
2. Boot the X6200 with both physical PTT controls released. For the later RF portion, use the same dummy load and attenuated SDR connection as before. Requested test power remains **1 W**.
3. Keep the extracted `candidate-manifest.json` and `tools` directory together. The current SSH destination is `root@192.168.100.62`; the browser GUI is `http://192.168.100.62/`. If DHCP changes it, substitute the new address.
4. Use your existing Windows SSH key. No new key is required for local Codex or PowerShell. The browser can be used to view the screen; RF Acquire/Arm/Run controls intentionally still require the radio's local controls.

## 2. Run one automated adapter test over SSH

From PowerShell in the extracted release folder:

```powershell
.\tools\Invoke-FreeDVRetest.ps1 -Action Probe -RadioIp 192.168.100.62 -CandidateManifest .\candidate-manifest.json
```

Alternatively, give local Codex `LOCAL-CODEX-TASK.md` and let it run and inspect this command. This runs four eight-second turns through the actual engine, production adapter and PulseAudio lifecycle on the X6200 CPU. It uses a **separate private PulseAudio server with virtual capture and playback**. It does not transmit, play through the speaker, change a mixer, or stop the running GUI/system audio server. It takes roughly 40 seconds and collects its own results.

The helper verifies the candidate hashes and required virtual-device modules. Missing prerequisites produce **BLOCKED**, not PASS, with no fallback to physical devices. PASS requires all four turns, one retained RADE instance, the expected EOO count, substantial input/output and acknowledged device flushes. This is a device-lifecycle test with silent virtual input, not a microphone, speech or RF qualification.

If this test fails, keep its ZIP and **skip the manual RF batch**. That failure can be investigated without another RF attempt. If it is BLOCKED, send the ZIP; the manual batch below may still proceed if the radio is otherwise operating normally.

## 3. One short RF batch per mode

This is the only required manual transmission work. Leave physical PTT released: these bounded cases key through software. Speak into the hand microphone without pressing its PTT.

Start with **USB**, at the same frequency used previously. Keep the SDR demodulator in **upper sideband** for both radio profiles. Record demodulated mono audio at 48 kHz/16-bit, with processing/noise reduction disabled. Keep the original tuning and recording settings in your notes; identical displayed frequencies alone do not establish frequency alignment. Frequency correction can be performed on copies afterward, so no separate calibration-tone test is required.

1. Open **APP → Modes → FreeDV test**. The new selector has only two cases. Defaults are **Repeat hand-mic TX 8s + finish**, **R1 M1 P0**, **Hand mic**, **Existing mono**, **Keep mixer**. Select **Acquire** once. There is no preliminary confirmation sequence.
2. Start one continuous SDR recording for this mode, named `USB.wav` if convenient. Select **Arm**, then **Run**. As RF starts, say: **“USB one. First word apple. This is the first transmission. Last word orange.”** Let it finish and restore by itself. Note the displayed microphone AC level while speaking and whether RF stayed up for the full turn.
3. Without Close, Acquire or restarting the app, select **Arm → Run** again. Say: **“USB two. First word blue. This is the second transmission. Last word green.”** Let it finish. This second turn is the critical regression test.
4. Stop the SDR recording. Select **Export**, then **Close**. Wait for the ordinary receive screen before changing the radio mode.
5. Change the radio to **USB-DIG**, preserve the same frequency and repeat steps 1–3 with a new continuous `USB-DIG.wav`, saying **“Digital one…”** and **“Digital two…”** with different first/last words. Keep the SDR demodulator in upper sideband.
6. **Only if both digital turns completed**, keep the session open and select **Receive after TX**. The route selector automatically changes to **R1 M0 P0**. Select **Run**, wait five seconds, then **Stop** and wait for restoration. This checks the TX-to-RX lifecycle only; no controlled RF source is required.
7. Select **Repeat hand-mic TX** again; its route changes back automatically. **Arm → Run** once, saying **“Digital three. First word silver. This is after receive. Last word gold.”** Let it finish, stop the SDR recording, then **Export → Close**.

**On an audio fault:** do not make repeated attempts in that session. The new image should refuse further arming with an explicit latched-fault message, without keying RF. Export, then Close. You may perform the other mode's one fresh batch after restoration. Stop that batch at its first fault too. If restoration waits for RX, release both PTT controls; use the preserved recovery card if the radio cannot return safely to ordinary receive.

An AC level near one throughout loud speech identifies the same missing-input problem as the original USB P1. Record that observation. A nonzero level alone does not prove the input contains your speech or that analog leakage is absent.

## 4. Collect automatically

Put the two SDR WAVs and a short `notes.txt` into a `recordings` folder. Notes only need:

- Which turns completed or aborted, and the displayed fault code/line if any.
- Microphone AC level during speech, and any analog speech heard through the speaker or SDR.
- Radio profile, frequency and SDR demodulator/tuning settings.

Then run:

```powershell
.\tools\Invoke-FreeDVRetest.ps1 -Action Collect -RadioIp 192.168.100.62 -RecordingDir .\recordings
```

This copies the case WAVs, coordinator results, current/recent GUI logs, build identity, mixer/device inventory and your recordings into one timestamped ZIP. It does not restart the radio or delete evidence. Keep large IQ files on the PC with their hashes; the helper does not copy them. Save the ZIP before reboot. Share the probe ZIP and collection ZIP together.

## 5. Automated frequency-offset analysis and listening

Local Codex can do this; it does not require another radio test. On the PC, install `numpy==2.3.5` and `scipy==1.17.0` if needed. Pair each completed case's saved `output.wav` with that mode's continuous SDR WAV:

```powershell
python .\tools\analyze_sdr_offset.py --radio-wav .\CASE\output.wav --sdr-wav .\recordings\USB-DIG.wav --output .\aligned-digital-one
```

Replace `CASE` with the collected case directory identified by its coordinator JSON. The helper matches a distinctive modem segment, measures a constant offset and creates `corrected-modem-8000.wav` only when the match is strong. It never modifies the originals. A poor match stays INCONCLUSIVE; do not guess a case association.

Feed the corrected modem copy to your official FreeDV GUI using the same file-playback method as before, with **RADE V1** selected. Save decoded speech and note the first/last words you understand. “CORRECTED,” synchronization and nonzero decoded output are not intelligibility acceptance. Keep the GUI release/version and listening notes with the results.

These tests do not attempt physical-PTT independence, held-PTT abort timing, long conversations or final RF receive qualification. Those become useful after the current turn/capture failures are resolved; repeating them now would not move this diagnosis forward.
