# FreeDV input-route check — tools update, no reflash

Keep the installed **capture-fix image, source 6976603**. This ZIP contains scripts only. It is not an image and does not replace the GUI.

The returned November 3 morning collection has two mechanically completed turns and one retained capture stream/codec set. Both microphone WAVs are essentially DC 149 with AC RMS about 1 count. They do not establish usable microphone capture. The first turn also has an early playback underflow before drain. Capture delivery gaps are 762/773 ms while pump gaps are only 9/10 ms. Each eight-second case contains about 7.43 seconds of capture. The existing timing fields cannot distinguish initial acquisition delay from later sample loss.

Both modem files strongly match the SDR recording, at approximately +88.55 Hz and +84.55 Hz offset. Frequency correction cannot recover speech absent from microphone PCM. The radio's saved speaker/AF level is 0; the earlier speech-bearing run used 4. The physical source, stream volume/mute and saved ALSA mixer settings otherwise match. AF dependence is a hypothesis to test, not an established hardware fact.

## One changed-setting test

1. **Close FreeDV first.** On the ordinary receive screen, set speaker/AF volume to **4** and leave the same 14.100 MHz USB-DIG setup, hand microphone, dummy load and attenuated SDR connection. Both physical PTT controls remain released. Do not use the .2 recovery card for this check.
2. Extract this tools ZIP to a new folder. Create its `recordings` subfolder. Start one continuous SDR WAV recording in upper sideband, with unchanged frequency/filter/gain. Save it there as `volume4.wav`.
3. Open PowerShell in the extracted folder and run:

   ```powershell
   powershell -ExecutionPolicy Bypass -File .\tools\Start-FreeDVInputCheck.ps1 -RadioIp 192.168.100.62 -CandidateManifest .\candidate-manifest.json
   ```

   This checks the installed GUI hash, recording utility, physical input source and storage. It starts a **60-second independent recording of the same input source**. It sends no RF, playback, routing or mixer commands, and uses the existing SSH key with strict host checking. If preflight prints BLOCKED, preserve that output and stop this check. The execution-policy argument applies only to this PowerShell process.
4. While that recording runs, open **APP → Modes → FreeDV test**. Select **Repeat hand-mic TX 8s + finish / R1 M1 P0 / Hand mic / Existing mono / Keep mixer**. Acquire, then **Arm → Run once**, using the existing 1 W diagnostic setting. Speak throughout the case without pressing physical PTT: “Volume four microphone check, one two three four five.” Let the case finish; export the result, then Close. Stop the SDR recording. Do not repeat an aborted case.
5. After 60 seconds from starting the helper, run:

   ```powershell
   powershell -ExecutionPolicy Bypass -File .\tools\Invoke-FreeDVRetest.ps1 -Action Collect -RadioIp 192.168.100.62 -RecordingDir .\recordings
   ```

   Upload the one resulting `freedv-collect-*.zip`. The collector includes the independent raw/WAV recording and its read timestamps, GUI case recordings, manifests and device inventory. The scripts do not publish evidence or modify the radio installation. You can return AF to your preferred level after closing FreeDV.

This is one new input-route comparison, not another lifecycle test. We will compare the independent source recording with the GUI's capture to locate missing samples, test whether microphone activity returns at AF 4, and correlate the saved modem with SDR output. The observer's pipe-read timestamps are not ADC timestamps or RF timing measurements. An additional recording client may affect scheduling, so its presence and settings are recorded explicitly.

## For local Codex

Follow the steps above. Automate preflight, starting the independent recording, waiting for its bounded completion and collection; the user operates Arm/Run locally. Do not change volume, routing, services, keys or RF remotely. Use this ZIP's collector so the new reference recording is included. Verify collected SHA256SUMS and ZIP/member integrity. Report COLLECTED separately from voice-test acceptance. The updated assessment rejects near-constant microphone input and counts only current-generation underflows before drain; varying audio still requires listening review. No virtual-device probe or additional RF batch is requested.
