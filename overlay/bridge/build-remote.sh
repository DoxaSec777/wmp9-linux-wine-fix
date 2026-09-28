#!/bin/sh
# Build the 32-bit Windows COM probe beside this script.
set -eu

SCRIPT_DIR=$(CDPATH= cd -- "$(dirname -- "$0")" && pwd)
cd "$SCRIPT_DIR"

# Point MINGW_CXX at an extracted or system MinGW compiler when the default
# executable is not on PATH.  The compiler's configured sysroot supplies the
# Windows SDK headers and import libraries; no host-specific directory is used.
MINGW_CXX=${MINGW_CXX:-i686-w64-mingw32-g++}
OUTPUT=${WMP_PROBE_OUTPUT:-wmp_remote_probe.exe}
TEMP_OUTPUT="${OUTPUT}.new.$$"

cleanup() {
    rm -f -- "$TEMP_OUTPUT"
}
trap cleanup EXIT HUP INT TERM

"$MINGW_CXX" \
    -std=c++17 -O2 -Wall -Wextra \
    -static -static-libgcc -static-libstdc++ \
    wmp_remote_probe.cpp -o "$TEMP_OUTPUT" \
    -lole32 -loleaut32 -luuid -luser32

# Rename only after a successful link so a failed build leaves the previous
# executable intact.  Both paths are in this directory, making the move atomic.
mv -f -- "$TEMP_OUTPUT" "$OUTPUT"
trap - EXIT HUP INT TERM
if command -v file >/dev/null 2>&1; then
    file "$OUTPUT"
else
    printf 'Built %s\n' "$SCRIPT_DIR/$OUTPUT"
fi
