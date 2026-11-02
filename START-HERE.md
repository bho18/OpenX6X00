# X6200 persistent FreeDV test candidate

1. Preserve the working `.2` card. Use a separate spare card for this X6200 image.
2. Verify `SHA256SUMS`, then write `x6200-persistent-voice-test.img.xz` with an
   image writer that supports xz and enable write verification.
3. Boot with PTT released and a dummy load connected.
4. Open **APP → Modes → FreeDV test**. Follow
   [the candidate test instructions](guide/docs/freedv/candidate-test.md).
5. Export and copy the displayed results directory before reboot. If needed,
   power down and boot the preserved working card to recover.

The new persistent TX/RX cases exercise the actual production engine/adapter.
This is a locally armed hardware-test candidate; normal automatic-PTT mode is
still qualification-gated. [Completed checks and limits](TEST-RESULTS.md).
