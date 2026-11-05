# FreeDV backlog probe correction — 2026-11-03

Keep the installed 2026-11-03 retest image (firmware `6279ae76e15c368a507bfcf8cef93bb0445130c9`). **Do not reflash, repeat the earlier RF batch, or run Acquire/Arm/Run for this update.**

## What failed and what changed

The first-turn virtual probe failure was reproduced with the actual engine, audio adapter and PulseAudio on a host. The original fixture stopped accepting playback at exactly 2,646 samples. It then failed with fault 2 after accepting 48,572 microphone samples, almost identical to the target's 48,574. The host failure showed only about 10 ms of processing time and about 500 ms of output-queue waiting.

A separate playback-only reproduction found nearly two seconds of cold sink latency while the stream was uncorked, healthy, and accepting no more samples. PulseAudio 16.1's null sink defaults to a two-second scheduling block. The fixture now uses its documented `norewinds=1` option, which caps that virtual block at 50 ms. This changes the private test fixture. The engine's 500 ms input-backlog protection, 200 ms microphone reserve, modem cadence and codec arithmetic are unchanged.

With the corrected fixture, all four host engine/adapter turns completed with one retained RADE context and EOO counts 1–4. This establishes the correction on the host; the X6200 result is still pending. It does not resolve or pass the earlier physical-route second-turn failure, microphone isolation, RF reception or intelligibility.

The staged probe now exports the exact two depths used by the backlog guard, engine output depth, cumulative processing/output-wait/scheduling time, per-turn process CPU time, and a bounded 100 ms trace. The source and binary are identified in `probe-manifest.json`.

## Run once from Windows

1. Download and extract `freedv-backlog-probe-20261103.zip` completely into a new folder. Keep all files together.
2. Leave the radio on its ordinary receive screen, with physical PTT released. Use its existing network connection at `192.168.100.62`. No SDR recording, microphone speech, tuning or image change is needed.
3. In PowerShell opened in that extracted folder, run:

   ```powershell
   powershell.exe -NoProfile -ExecutionPolicy Bypass -File .\Run-BacklogProbe.ps1 -RadioIp 192.168.100.62
   ```

   This execution-policy setting applies only to that child process. Existing Windows SSH keys and strict host checking are used. No host records or keys are changed.
4. Wait about one minute. The command stages a hash-verified probe under a new `/tmp/freedv-retest-*` directory, starts its own private virtual-device PulseAudio server, runs four eight-second turns, collects existing logs/device inventory and saves **one `freedv-backlog-<timestamp>.zip`** beside the extracted folder.
5. Return that ZIP, whether the result is PASS, FAIL, BLOCKED or INCONCLUSIVE. **Stop here; this update requests no manual RF work.**

The installed GUI and probe are not replaced. The running GUI, system PulseAudio, radio settings, microphone routing and transmitter controls are not changed. The private server is stopped after the test; the remote evidence directory remains available. Missing virtual-device support is BLOCKED with no physical-device fallback.

If it passes, all four turns must complete with substantial input/output, EOO counts 1–4, one RADE creation and acknowledged device flushes. A synchronized stream or nonzero output alone is insufficient. Silent virtual input means speech intelligibility remains UNASSESSED even on PASS.

## For local Codex

Run the single command above using the existing verified SSH connection. Inspect `bundle/probe/result.json`, each `turn-*/result.json` and `trace.jsonl`, `fixture.json`, and `bundle/collect`. Verify the returned helper `SHA256SUMS`. Report the first fault-time capture plus engine input depth, output queue depth, output waiting versus processing/CPU time, and number of completed turns. A collection result is evidence retrieval, not test acceptance. Do not perform any RF/UI actions, restart services, change keys, or publish collected evidence. Return the ZIP to this conversation.
