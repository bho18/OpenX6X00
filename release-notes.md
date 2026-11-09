USB-headset FreeDV Beta with native knob controls, live microphone-gain preview, and bounded audio recovery.

Download **x6200-usb-rf-windows.zip** for the SD-card image and **usb-rf-test-kit.zip** for the matching optional evidence helpers. Verify SHA256SUMS and follow [START-HERE.md](https://github.com/bho18/OpenX6X00/releases/download/freedv-operational-beta-20261109.1/START-HERE.md). Keep your previous working SD card.

This remains half-duplex RADE V1 using a USB microphone/headphones device and the radio's own PTT. Entry prepares automatically; there is no manual qualification or Enable TX step.

Changes:
- Gain and timeout use native whole-value controls. Microphone gain previews live; confirm saves and Cancel restores the previous value.
- Ordinary knob selection, tuning, power limits and speaker volume behavior are preserved. FreeDV Volume controls the selected USB headset.
- Audio restoration uses bounded retries and permits one GUI restart only after USB shutdown, radio restoration and fresh RX. Graceful exits share input ownership and cleanup handling.
- Remote commands report refused changes accurately. AGC restoration and WR12 power/protection reconciliation share ordinary radio policy.
- Managed raw-radio Bluetooth RX routes pause during USB FreeDV and restore afterward without changing preferences. Native Bluetooth microphone plus headphones remains a separate qualification task.
- Detailed speech/event capture is an explicit opt-in; normal sessions retain bounded summaries and fault evidence.

Software verification passed: five build variants, 91 host tests, eight upstream suites with 1,320 checks, all 104 codec/audio/controller cases repeated twice, independent scheduling checks, source-bound Linux/Windows CI, and emulated ARM/GUI/CLI checks. The full campaign uses real codec/PulseAudio streams with simulated RF; it does not qualify physical RF behavior.

The Linux/SDK baseline and stock baseband are unchanged. Previously established recording, codec, playback and RF-transmit observations retain their scope. Live reception from a known working RADE V1 peer and alternating replies remain the field milestone; the basic component trials do not need repeating.

[Compatibility details](https://github.com/bho18/OpenX6X00/blob/freedv-operational-beta-20261109.1/docs/freedv/compatibility-hardening-2026-11-09.md) · [Build and upstream integration](https://github.com/bho18/OpenX6X00/blob/freedv-operational-beta-20261109.1/docs/upstream/README.md) · [Previous release](https://github.com/bho18/OpenX6X00/releases/tag/freedv-operational-beta-20261108.1)

Source: `58a92964bf93338eb011a2798c7aa59cfbaffe1d`. All publication is confined to `bho18/OpenX6X00`; nothing is pushed upstream.
