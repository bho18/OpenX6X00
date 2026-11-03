# X6200 FreeDV capture-fix candidate — current test instructions

This guide supersedes the earlier A/B/P checklist for this iteration. Use the **new capture-fix candidate**, not the working .2 card. The .2 card is recovery only. The download ZIP contains one complete `.img`; extract and flash that file with verification to the candidate card. Nothing needs assembling.

## What changed and what remains uncertain

The November 2 two-turn USB-DIG bundle established that turn one completed: 352,512 microphone samples, one EOO and 369,734 modem samples. Turn two admitted only 4,410 microphone samples (100 ms), produced no output and hit FD_AUDIO_BACKLOG with observed capture/engine depths of 21,389 + 4,410 samples. No new speech frame was encoded on turn two. Those fault depths were re-observed by the installed GUI; the next candidate freezes the guard operands themselves.

The previous production path borrowed the ordinary GUI capture stream (100 ms fragments, server-default maximum buffering), whose callback also ran competing GUI audio consumers under the shared PulseAudio lock. This candidate gives voice capture its own retained stream, requests 10 ms fragments and a 200 ms maximum, suppresses competing GUI audio consumers while FreeDV owns capture, and exports actual grants, callback count/largest fragment/longest gap, pump gaps and time spent observing playback. It retains the 500 ms backlog protection, codec contexts, playback lifecycle and local RF arming. This is a targeted fix for an identified isolation weakness. The uploaded evidence alone does **not** prove that weakness caused the physical second-turn stall; the next two turns test that hypothesis.

The first turn's SDR recording matches the saved modem output at coherence 0.958 after correcting approximately +67.5 Hz. The pinned reference decoder's initial search is approximately ±50 Hz. The corrected copy yields 7.63 seconds of speech output. Listening remains unassessed, and this cannot yet assign the oscillator error to the radio or SDR. Do not change the radio frequency to compensate for this offline recording issue.

## Install and start read-only observation

1. Preserve the working .2 card. Flash the single `.img` from this ZIP to the candidate card, verify it, insert it and boot. Keep the dummy load and existing attenuated SDR connection. Keep both physical PTT controls released.
2. Extract all files from the ZIP on the Windows computer. Open PowerShell in that extracted directory. The existing SSH key and verified host record must work; the scripts do not add keys or accept a changed host key.
3. Run this once:

   ```powershell
   powershell -ExecutionPolicy Bypass -File .\tools\Start-FreeDVCaptureWatch.ps1 -RadioIp 192.168.100.62 -CandidateManifest .\candidate-manifest.json
   ```

   This verifies the actual GUI hash, then records process/thread scheduling and PulseAudio source/stream details for 120 seconds. It does not key RF, change routing/mixers, restart services or touch preferences. A hash or SSH failure stops the helper; resolve that before RF work. The command-line execution-policy flag applies only to this process.

## Run exactly two USB-DIG speech turns

4. Use the same 14.100 MHz test setup and **USB-DIG**. Set the SDR demodulator to upper sideband, keep its tuning/filter/gain unchanged between turns, and start one continuous mono 16-bit WAV recording. Save it as `recordings\USB-DIG.wav`. Avoid clipping. No preliminary tones, USB comparison or virtual probe is required.
5. Open **APP → Modes → FreeDV test**. Select **Repeat hand-mic TX 8s + finish**, **R1 M1 P0**, **Hand mic**, **Existing mono**, **Keep mixer**. The “Existing mono” selector still means the mono channel choice; this candidate supplies it through the new retained capture stream. Select **Acquire once**. Use the existing **1 W** diagnostic setting.
6. **Arm → Run**. Speak without pressing microphone PTT: “Digital one, alpha start. One two three four five. Alpha finish.” Continue speaking naturally until the eight-second case ends. Wait for automatic unkey and completed restoration. Record whether it finished or faulted.
7. Leave the same session acquired. Wait **20 seconds**, then **Arm → Run** again. Say “Digital two, bravo start. Six seven eight nine ten. Bravo finish.” This tests the failing retained turn after idle. Do not Close/Acquire between these turns.
8. If either turn faults, stop this batch. Export the result and keep the recordings; do not retry the faulted session. Otherwise export after the second turn. Stop the SDR recording, then Close FreeDV. No RX/TX case, additional PTT test, tone matrix or second profile is requested this round.

## Collect once

9. After the watcher's two-minute observation period, run:

   ```powershell
   powershell -ExecutionPolicy Bypass -File .\tools\Invoke-FreeDVRetest.ps1 -Action Collect -RadioIp 192.168.100.62 -RecordingDir .\recordings
   ```

   It collects both case records/WAVs, the GUI log, installed manifest, read-only watcher output, mixer and PulseAudio inventory, plus the SDR WAV. Upload the resulting `freedv-collect-*.zip`. Do not reboot before collection. Include a short note if the on-screen behavior differs from the saved result.

Acceptance for this iteration is two completed turns, no capture/backlog/device fault, one additional EOO per turn, one retained voice capture stream, no unexplained in-speech underflow and correct turn identity. We will compare source callback gaps against pump/lock delays, match the SDR recording to each saved modem stream, and decode derived offset-corrected copies. Synchronization/nonzero output does not establish intelligibility; first/last words require listening assessment. Ordinary native-mode exposure, physical PTT qualification and final bidirectional RF acceptance remain blocked.
