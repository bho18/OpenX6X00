# Milestone build evidence

## Baseline image and matching SDK

The manually dispatched baseline workflow completed successfully:
https://github.com/bho18/OpenX6X00/actions/runs/40417980169

Artifact `x6200-freedv-baseline`:
https://github.com/bho18/OpenX6X00/actions/runs/40417980169/artifacts/12701486870

It contains the normal card image, matching Buildroot SDK, SHA256SUMS, source
manifest and firmware/Buildroot commit records. The downstream test-card job
successfully checked the artifact checksums, verified the expected commits,
extracted the SDK and ran its relocation script.

| Input | Revision |
| --- | --- |
| Firmware milestone baseline | fd7f0bba70c0c8f8854219b6422600cbd428a6dc |
| Application VERSION in that pinned source | OpenX6X00 v0.3.5 |
| Upstream firmware starting point | d833e288026ad4a22575b897829c1c82d8437f92 |
| X6200 Buildroot external tree | 8b7ca9d6d4e82ade0fc7654c46b9f8688689a4a3 |
| Buildroot submodule | cc34b5b2abf2588f0ef55e46a4fe85a60c19ba1f |

The operator reports application 0.3.6 and baseband 1.0.7 on the working card.
The pinned milestone source reports v0.3.5; keep these identities separate and
preserve the working card. No upstream update has been silently incorporated.

The supplied live log showed GCC 12.3.0 and glibc 2.36 compilation before
GitHub's display-size truncation. That truncation was not a build failure.
Future baseline runs save full logs as an artifact and print compact milestones.

Build completion is a PASS for producing these artifacts. Boot, ordinary radio
behavior, native audio routing, PTT recovery and target codec performance are
INCONCLUSIVE until measured on hardware. The baseline artifact does not contain
the complete guarded routing diagnostics. Paired baseline/read-only diagnostic
images and a benchmark package are separate outputs with their own source hashes;
check their build records rather than assuming the baseline firmware commit.

## Host validation

A complete host benchmark package executed and validated seven paced workloads:
700E TX, synchronized RX and no-sync RX; RADE V1 TX, synchronized RX, no-sync RX
and interrupted RX with reacquisition. The host package refuses radio
qualification. These results validate execution and collection, not X6200 timing.

The offline image tool's synthetic ext4 test passed locally and in GitHub,
including boot-region/source preservation, retained settings sentinel, verified
file replacement and checksum/overwrite rejection. It does not demonstrate
that the resulting X6200 image boots.
