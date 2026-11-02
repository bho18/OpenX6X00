# X6200 persistent FreeDV: test download

This is a transport package for the same verified, unqualified spare-card candidate.
The SD image and all supporting files are unchanged; the image is split for GitHub storage.

1. Download the complete GitHub branch ZIP and extract it into one folder.
2. Windows: double-click **Assemble-Image.cmd**. Linux/macOS: run **sh Assemble-Image.sh**.
3. The helper joins the local segments and verifies the compressed image's SHA256. It does not access the network, install software, flash a card or control a radio.
4. Follow **START-HERE.md**: write the verified image to a separate spare card, preserve the working .2 card, boot with PTT released and a dummy load, then open **APP → Modes → FreeDV test**.

This is a hardware-test candidate, not a qualified native release. Independent hand-mic release is unresolved and normal automatic PTT remains gated.

The checksum in SHA256SUMS applies to the reconstructed image and supporting files, not to GitHub's outer download ZIP.
