#!/bin/bash
# OpenX6X00 - Pat (the Winlink client, https://github.com/la5nta/pat) for the radios: one static
# ARMv7 binary for the X6100 and the X6200, built with the official Go toolchain at the pinned
# version, from the pinned Pat tag and commit, with our patches (patches/*.patch, in order) on
# top. See UPSTREAM.md.
#
#   tools/pat/build.sh        prints the binary's path on stdout, and nothing else
#
# buildroot/local-build.sh runs it (in the build container and on the GitHub runner) and gives
# the path to the GUI's CMake (-DPAT_BINARY), which installs it as /usr/bin/pat. Everything is
# kept in $HOME/.cache/openx6x00: the toolchain (downloaded once, its SHA-256 checked), Go's
# module and build caches, and the binary, under a name made of the Pat tag, the Go version and
# the hash of the patches and of this script. A second run with nothing changed prints that
# path and downloads nothing.
#
# Copyright (c) 2026 Joeri Van Dooren ON6URE / Guru-RF
set -eo pipefail
exec 3>&1 1>&2          # stdout is the binary's path only: everything else goes to stderr

HERE=$(cd "$(dirname "$0")" && pwd)
PAT_TAG=v1.0.1
PAT_COMMIT=2ad31e8fc1f1748ac1b00cac9d88da54e3e0efe2
PAT_REPO=https://github.com/la5nta/pat
# The newest stable Go on 2026-10-26 (Pat v1.0.1 needs 1.26 or newer); the hash is go.dev's
# (https://go.dev/dl/?mode=json&include=all)
GO_VER=1.27.3
GO_SHA256=65efbc80fdc1fa2cd2631cad5b481777a68e8725f34e89f50fc6ac714e379b40
GO_URL=https://go.dev/dl/go$GO_VER.linux-amd64.tar.gz

CACHE=${OPENX6X00_CACHE:-$HOME/.cache/openx6x00}
GOROOT_DIR=$CACHE/go-$GO_VER
PATCHES=("$HERE"/patches/*.patch)
[ -e "${PATCHES[0]}" ] || PATCHES=()
KEY=$(cat "${PATCHES[@]}" "$HERE/build.sh" | sha256sum | cut -c1-12)
OUT=$CACHE/pat/pat-$PAT_TAG-go$GO_VER-$KEY/pat

if [ -x "$OUT" ] && [ -f "$(dirname "$OUT")/doc/LICENSE" ]; then
    echo "== Pat $PAT_TAG (Go $GO_VER, patches $KEY): built already"
    echo "$OUT" >&3
    exit 0
fi

[ "$(uname -m)" = x86_64 ] || { echo "tools/pat/build.sh: the pinned Go toolchain is for x86_64 hosts, not $(uname -m)"; exit 1; }
mkdir -p "$CACHE/pat"

fetch() {   # URL FILE
    if command -v curl > /dev/null; then
        curl -fsSL --retry 3 -o "$2" "$1"
    else
        wget -q -O "$2" "$1"
    fi
}

if [ ! -x "$GOROOT_DIR/bin/go" ]; then
    echo "== Go $GO_VER from go.dev"
    T=$(mktemp -d "$CACHE/go-$GO_VER.tmp.XXXXXX")
    trap 'rm -rf "$T"' EXIT     # a download or unpack that fails leaves nothing in the cache
    GO_ARCHIVE=$CACHE/pat/dl/go$GO_VER.linux-amd64.tar.gz
    mkdir -p "${GO_ARCHIVE%/*}"
    if ! { [ -f "$GO_ARCHIVE" ] && echo "$GO_SHA256  $GO_ARCHIVE" | sha256sum -c --quiet -; }; then
        fetch "$GO_URL" "$GO_ARCHIVE.part.$$"
        echo "$GO_SHA256  $GO_ARCHIVE.part.$$" | sha256sum -c --quiet - \
            || { echo "tools/pat/build.sh: $GO_URL does not have the pinned SHA-256"; rm -f "$GO_ARCHIVE.part.$$"; exit 1; }
        mv "$GO_ARCHIVE.part.$$" "$GO_ARCHIVE"
    fi
    tar xzf "$GO_ARCHIVE" -C "$T"
    # Moved into place whole (a build next to this one may have done it in the meantime)
    [ -x "$GOROOT_DIR/bin/go" ] || { rm -rf "$GOROOT_DIR"; mv "$T/go" "$GOROOT_DIR"; }
    rm -rf "$T"
fi
"$GOROOT_DIR/bin/go" version | grep -q "^go version go$GO_VER linux/amd64$" \
    || { echo "tools/pat/build.sh: $GOROOT_DIR is not Go $GO_VER"; exit 1; }

# Go as pinned and nothing else: no toolchain switch, no go.mod or go.sum changes, the public
# proxy and checksum database for every module, no settings from the environment or a go.env
unset GOROOT GOPATH GOBIN GOFLAGS GOPROXY GOSUMDB GONOPROXY GONOSUMDB GOPRIVATE GOINSECURE GOWORK GOEXPERIMENT GOFIPS140
export PATH=$GOROOT_DIR/bin:$PATH
export GOENV=off GOTOOLCHAIN=local GOFLAGS=-mod=readonly GOWORK=off
export GOPROXY=https://proxy.golang.org,direct GOSUMDB=sum.golang.org
export GOPATH=$CACHE/gopath GOCACHE=$CACHE/gocache

SRC=$(mktemp -d "$CACHE/pat/src.XXXXXX")
trap 'rm -rf "$SRC"' EXIT
echo "== Pat $PAT_TAG"
git -c advice.detachedHead=false clone -q --depth 1 --branch "$PAT_TAG" "$PAT_REPO" "$SRC/pat"
cd "$SRC/pat"
HEAD=$(git rev-parse HEAD)
[ "$HEAD" = "$PAT_COMMIT" ] || { echo "tools/pat/build.sh: $PAT_TAG is $HEAD, not the pinned $PAT_COMMIT"; exit 1; }
# Keep the exact unpatched source with the pinned archive inputs; subsequent
# cache retirement can reconstruct this build without preserving its checkout.
git archive --format=tar HEAD | gzip -9 -n > "$CACHE/pat/dl/pat-$PAT_COMMIT.tar.gz"
for p in "${PATCHES[@]}"; do
    echo "   $(basename "$p")"
    git apply "$p"
done
# The web UI's source maps: 3 MB of the binary, for the browser's developer tools only
rm -f web/dist/js/*.map

echo "== modules (checked against go.sum)"
go mod download
go mod verify

echo "== static ARMv7 binary"
CGO_ENABLED=0 GOOS=linux GOARCH=arm GOARM=7 go build -trimpath -ldflags="-s -w -buildid=" -o "$SRC/pat.bin" .
# A 32-bit little-endian ELF (7f 'E' 'L' 'F' 01 01) for ARM (e_machine 0x28, bytes 18-19)
ELF=$(od -An -tx1 -N20 "$SRC/pat.bin" | tr -d ' \n')
[ "${ELF:0:12}" = 7f454c460101 ] && [ "${ELF:36:4}" = 2800 ] \
    || { echo "tools/pat/build.sh: the result is not a 32-bit ARM ELF"; exit 1; }
mkdir -p "$(dirname "$OUT")"
# Retain the source/toolchain/module notices beside the executable for image
# assembly. The module versions and their checksum-verified source remain
# explicit; this does not depend on the build's temporary checkout surviving.
DOC=$(dirname "$OUT")/doc
mkdir -p "$DOC/modules"
cp LICENSE "$DOC/LICENSE"
cp "$GOROOT_DIR/LICENSE" "$DOC/GO-LICENSE"
go list -m all > "$DOC/MODULES"
go list -m -f '{{if .Dir}}{{.Path}} {{.Dir}}{{end}}' all | while read -r module directory; do
    [ -n "$directory" ] || continue
    name=$(printf '%s' "$module" | tr '/:' '__')
    mkdir -p "$DOC/modules/$name"
    for notice in "$directory"/LICENSE* "$directory"/LICENCE* "$directory"/COPYING* "$directory"/NOTICE*; do
        if [ -f "$notice" ]; then cp "$notice" "$DOC/modules/$name/"; fi
    done
done
mv -f "$SRC/pat.bin" "$OUT.tmp.$$"
mv -f "$OUT.tmp.$$" "$OUT"
echo "== $(wc -c < "$OUT") bytes, sha256 $(sha256sum "$OUT" | cut -d' ' -f1)"
echo "$OUT" >&3
