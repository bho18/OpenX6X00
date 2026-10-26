#!/usr/bin/env bash
# Run the pinned upstream rade_c_v1_wav feature-loss check without hiding
# upstream process exit codes behind a pipeline or PASS_REGULAR_EXPRESSION.
set -euo pipefail
if [ "$#" -ne 5 ]; then
    echo "usage: bash validate_rade_v1_loss.sh RADAE_SOURCE RADE_SOURCE RADE_BUILD PYTHON NEW_OUTPUT_DIR" >&2
    exit 2
fi
radae=$1
rade=$2
build=$3
python=$4
output=$5
test "$(git -C "$radae" rev-parse HEAD)" = 89220e28fcc25138c0e038849e653dba30ec2dd6
test "$(git -C "$rade" rev-parse HEAD)" = c8a3dc156045cae2cd251e1a4be0c304c9ddf2f9
printf '%s  %s\n' 88cad4597e0b3be99d70ffd216e4f0c0b83fe353017dad369893068260f1fc5a "$radae/wav/all.wav" | sha256sum -c -
mkdir "$output"
mkdir "$output/cache" "$output/matplotlib"
"$build/src/rade_tx_wav" "$radae/wav/all.wav" "$output/tx.wav" -f "$output/tx.f32" > "$output/tx.log" 2>&1
"$build/src/rade_rx_wav" "$output/tx.wav" "$output/rx.wav" -f "$output/rx.f32" > "$output/rx.log" 2>&1
MPLCONFIGDIR="$output/matplotlib" XDG_CACHE_HOME="$output/cache" \
    "$python" "$radae/loss.py" "$output/tx.f32" "$output/rx.f32" \
    --loss_test 0.119 --clip_start 100 --clip_end 300 > "$output/loss.log" 2>&1
cat "$output/loss.log"
grep -qx PASS "$output/loss.log"
