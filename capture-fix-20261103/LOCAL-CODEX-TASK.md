# Local Codex task — capture-fix retest

Use START-HERE.md in this folder as the only current operator workflow. Verify the ZIP/image hashes from DOWNLOAD-SHA256SUMS and candidate-manifest.json. Preserve the working .2 card. The user flashes the new single image to the candidate card.

Use the existing Windows SSH key and strict verified host record for root@192.168.100.62. Do not change keys, host records, global execution policy, GUI/system PulseAudio, mixer or RF controls. Run tools/Start-FreeDVCaptureWatch.ps1 with this candidate-manifest.json once the image is booted; its hash check must pass. It observes for 120 seconds in the background. Let the user perform exactly the two locally armed USB-DIG speech turns in START-HERE.md; both physical PTT controls remain released. No old virtual probe, A/B tones, USB comparison or RX/TX matrix is requested.

After the user stops the continuous SDR recording and the observation period ends, run tools/Invoke-FreeDVRetest.ps1 -Action Collect -RadioIp 192.168.100.62 -RecordingDir ./recordings. Verify helper SHA256SUMS and ZIP CRC/member hashes. Return the one collection ZIP and its SHA256, with actual case outcomes and any missing evidence. COLLECTED is not PASS. Do not install analysis dependencies or repeat RF cases after a fault; upstream analysis will use the saved modem/SDR pairs. Do not publish private evidence.
