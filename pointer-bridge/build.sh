#!/bin/bash
set -euo pipefail
HERE="$(cd "$(dirname "$0")" && pwd)"
NDK="${ANDROID_NDK:-$HOME/Android/android-ndk-r30}/toolchains/llvm/prebuilt/linux-x86_64/bin"
"$NDK/x86_64-linux-android35-clang" -O2 -Wall -Wextra -fPIE -pie -o "$HERE/vm-pointer-bridge" "$HERE/vm_pointer_bridge.c" -llog
"$NDK/llvm-strip" --strip-unneeded "$HERE/vm-pointer-bridge"
ls -l "$HERE/vm-pointer-bridge"
