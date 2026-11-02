#!/bin/sh
set -eu
cd "$(dirname "$0")"
test ! -e "x6200-persistent-voice-test.img.xz" || { echo "Image already exists; verify it against SHA256SUMS."; exit 1; }
test -f "x6200-persistent-voice-test.img.xz.part01" || { echo "Incomplete download: missing x6200-persistent-voice-test.img.xz.part01"; exit 1; }
test -f "x6200-persistent-voice-test.img.xz.part02" || { echo "Incomplete download: missing x6200-persistent-voice-test.img.xz.part02"; exit 1; }
test -f "x6200-persistent-voice-test.img.xz.part03" || { echo "Incomplete download: missing x6200-persistent-voice-test.img.xz.part03"; exit 1; }
test -f "x6200-persistent-voice-test.img.xz.part04" || { echo "Incomplete download: missing x6200-persistent-voice-test.img.xz.part04"; exit 1; }
test -f "x6200-persistent-voice-test.img.xz.part05" || { echo "Incomplete download: missing x6200-persistent-voice-test.img.xz.part05"; exit 1; }
cat "x6200-persistent-voice-test.img.xz.part01" "x6200-persistent-voice-test.img.xz.part02" "x6200-persistent-voice-test.img.xz.part03" "x6200-persistent-voice-test.img.xz.part04" "x6200-persistent-voice-test.img.xz.part05" > "x6200-persistent-voice-test.img.xz"
printf "%s  %s\n" "9f1cf21ddc48e62aa94dd439fcc8d5f84db8d277dcadc19a0ce6b7ed25287aa9" "x6200-persistent-voice-test.img.xz" | shasum -a 256 -c -
