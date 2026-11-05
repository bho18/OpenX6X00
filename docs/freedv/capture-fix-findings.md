# Physical two-turn failure and capture isolation change

Evidence: `freedv-collect-20261102-220624-49de1f86.zip`, SHA256 `10d090ae426499dfa3d83c606f7745d646313d373bd33b6e0928f0ea4b4d891c`. ZIP CRC and collector hashes verified. Installed source: `6279ae76e15c368a507bfcf8cef93bb0445130c9`. Recordings and logs remain private.

Case 001 completed mechanically: 800 speech features, 67 modem blocks, one EOO, 352512 input and 369734 output samples. Its single playback underflow occurred after drain was requested, at the end of accepted output. It is not evidence of an in-speech underflow. Case 002 failed at generation 2 after approximately 769 ms: 4410 accepted input samples and zero output; processing/frame counters unchanged from case 001. Observed fault depths 21389 + 4410 exceed 22050. Final capture depth 31389 is a later snapshot. Capture holes/errors/drops and playback stream failures were zero. Both cases restored settings and observed fresh RX. Software-clear command latencies (41/48 ms) do not measure RF cessation.

This rules out encoding a new modem frame as the immediate source of the second-turn stall. It does not distinguish delayed server delivery from a blocked/scheduled-out capture consumer: the installed build lacked callback and pump timing. Legacy callback work under the shared PA lock and server-default capture buffering were concrete isolation weaknesses. The new retained, bounded voice capture stream and suppression of competing legacy consumers address those weaknesses; new timing measurements are needed to confirm the physical result. No timeout/backlog limit was relaxed and no input is silently discarded to obtain a pass.

The first SDR turn matches the saved modem waveform with coherence 0.957501 at +67.5 Hz; after correction the pinned backend decoder reports 64 valid frames, estimated SNR 17.6 dB, and 7.63 seconds of speech. This is backend decode evidence only. Official GUI acceptance, recognizable first/last words, analog leakage and speaker/RF isolation remain unassessed.

Earlier corrected virtual-device four-turn evidence is retained separately: it fixed the null-sink's default two-second block, not the physical failure. The new regression adds long idle intervals and injected 600 ms legacy-consumer work. Such synthetic contention establishes isolation behavior, not reproduction of the radio's unknown scheduling cause. The historical 70% processing-margin failure remains a failure.

Current operator workflow: [candidate-test.md](candidate-test.md). No unchanged basic tests are required.
