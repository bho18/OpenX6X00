# X6200 persistent FreeDV: test download

This is a transport package for the same verified, unqualified spare-card candidate.
The SD image and firmware are unchanged; the image is split for GitHub storage.
Operator instructions have been consolidated into **[START-HERE.md](START-HERE.md)**.

1. Download the complete GitHub branch ZIP and extract it into one folder.
2. Windows: double-click **Assemble-Image.cmd**. Linux/macOS: run **sh Assemble-Image.sh**.
3. The helper joins the local segments and verifies the compressed image's SHA256. It does not access the network, install software, flash a card or control a radio.
4. Follow **[START-HERE.md](START-HERE.md)** for installation, every test in order, SDR++ recording, listening checks, USB/USB-DIG comparison and one results collection. It supersedes the older Session 1, Session 2 and TX-delivery procedures for this candidate. If already installed, no reflash or full package download is needed to use the updated guide.

This is a hardware-test candidate, not a qualified native release. Independent hand-mic release is unresolved and normal automatic PTT remains gated.

The checksum in SHA256SUMS applies to the reconstructed image and supporting files, not to GitHub's outer download ZIP.
