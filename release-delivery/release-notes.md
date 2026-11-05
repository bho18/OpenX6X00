The baseband report shows that the stock TX path mixes microphone and line samples, RECORD replaces normal receive audio, and zero microphone gain does not mute the input. This Linux revision removes those assumptions and withdraws the previous route/gain trial sequence.

Download **x6200-native-development-windows.zip** for one complete `.img`, the updated operator guide, Windows helpers and verification records. The `.img.xz` is an alternative download of the same image. Check `DOWNLOAD-SHA256SUMS.txt` before installing on the candidate card.

Changes include explicit RX/TX stereo slot selection, retained audio streams, a versioned applied-route and transmit-lease contract, fault and cancellation regressions, and traces that distinguish requests, applied configuration and physical observations. Stock X6200 backends do not implement the new contract, so native digital-voice transmission remains blocked. Ordinary radio operation and recording remain available.

**Next test: the included read-only Preflight inventory.** It collects ALSA/PulseAudio endpoints and input-device availability without staging transmission. Physical endpoint mapping and the later end-to-end conversation test have separate prerequisites in the guide. Do not repeat the withdrawn RECORD/MODEM/gain round.

Software validation passed: 42 host regression tests, five application build variants, four real-codec checks, and ARM/QEMU processing of 33 modem blocks with zero failures. The image package includes the exact results and source/image identities.

This package updates Linux software only. It does not update the MCU/baseband or FPGA, and passing software/image checks does not qualify physical audio isolation, PTT release, RF cessation or radio performance. The corresponding public source excludes incidental private research documents and private Git history.
