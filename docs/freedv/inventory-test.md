# First hardware test: baseline inventory

Run this on the X6200 with the current working OpenX6X00 firmware. This test
requires Python 3 and your existing shell access to the radio. It does not require
flashing a card, stopping the GUI or touching PTT. It reads selected inventory,
ALSA/mixer settings, CPU/memory information and image metadata. It does not record
audio, change settings, copy configuration databases or upload anything.

Download the `x6200-freedv-inventory-test` Actions artifact to your computer and
extract it. Verify files against SHA256SUMS. Transfer the inventory-test directory
to a writable directory on the radio using your existing file-transfer method.
Leave the normal GUI running. From that directory on the radio, run:

```sh
python3 collect_inventory.py --output /mnt/freedv-inventory-01
```

Return the archive path printed by the helper: `/mnt/freedv-inventory-01.tar.gz`,
or `/mnt/freedv-inventory-01.tar` when Python has no gzip support. No additional
Python modules need to be installed on the radio. Also provide the application and Xiegu
baseband versions shown on Settings → Info, microphone model, ordinary audio
behavior and waterfall settings. The archive contains an OPERATOR.md form listing
those observations; sending the observations alongside it is sufficient.

Missing commands/endpoints are reported as missing evidence. An empty audio
endpoint list is not proof that native audio routing is unusable. This test does
not qualify microphone routing, PTT recovery or codec performance. Preserve your
working card and settings for later spare-card firmware testing.

If an older helper reports `gzip module is not available` after writing the
inventory, the result directory still contains the collected data. Do not rerun
with the same output path. Copy the directory to your computer (for example,
`scp -r root@RADIO_IP:/mnt/freedv-inventory-01 .`) and return `inventory.json`
and `manifest.json`, or compress that directory on your computer. Do not return
an empty or incomplete `.tar.gz` from the failed packaging attempt.
