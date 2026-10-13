#!/bin/bash
# Prepare $GBOS_WORK/radv-runtime for apply_vendor_update.py (GBOS_GFX=radv):
#   vulkan.radv.so + libdrm_amdgpu.so  (from mesa-radv/build.sh)
#   libminigbm_gralloc.so + libgbm.so  = the stock vendor libraries with the dumb backend renamed "radeon" -> "amdgpu"
#                                         (same length, so a 6-byte patch; makes minigbm accept the amdgpu kernel driver)
# usage: make_radv_runtime.sh <mesa-build-dir> <extracted-vendor-dir>
set -euo pipefail
B=${1:?mesa build dir}; V=${2:?extracted vendor dir (contains lib64/)}; O=${GBOS_WORK:-$HOME/gbos-x86-work}/radv-runtime
mkdir -p "$O"; STRIP=${ANDROID_NDK:?}/toolchains/llvm/prebuilt/linux-x86_64/bin/llvm-strip
$STRIP --strip-unneeded -o "$O/vulkan.radv.so" "$B/src/amd/vulkan/libvulkan_radeon.so"
$STRIP --strip-unneeded -o "$O/libdrm_amdgpu.so" "$(ls "$B"/subprojects/libdrm-*/amdgpu/libdrm_amdgpu.so)"
python3 - "$V" "$O" <<'PY'
import sys, re
v, o = sys.argv[1:]
for n in ('libminigbm_gralloc.so', 'libgbm.so'):
    d = bytearray(open(f'{v}/lib64/{n}', 'rb').read())
    m = list(re.finditer(rb'\x00radeon\x00', d)); assert m, n
    for x in m: d[x.start() + 1:x.start() + 7] = b'amdgpu'
    open(f'{o}/{n}', 'wb').write(d); print('patched', n, len(m))
PY
