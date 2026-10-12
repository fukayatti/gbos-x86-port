#!/bin/bash
# Build a bare-metal initrd: shipped initrd + our amdgpu stack + firmware. Output: $OUT (default ~/src/isa-shim/bm/initrd.bm)
set -euo pipefail
SRC=$HOME/src/gbos-vm-linux/image-security/initrd
M=$(ls -d $HOME/src/kernel/stage/modroot/lib/modules/*)
OUT=${OUT:-$HOME/src/isa-shim/bm/initrd.bm}
W=$(mktemp -d); trap 'rm -rf "$W"' EXIT
mkdir -p "$(dirname "$OUT")"; cd "$W"
(lz4 -dc "$SRC" 2>/dev/null || zcat "$SRC") | cpio -id --quiet
L=lib/modules
# order = dependency order; amdgpu last
MODS="hwmon wmi video drm_buddy drm_exec drm_suballoc_helper drm_ttm_helper drm_panel_backlight_quirks i2c-algo-bit amdxcp gpu-sched drm_display_helper amdgpu"
for m in $MODS; do
  f=$(find "$M/kernel" -name "$m.ko" | head -1); [ -n "$f" ] || { echo "missing $m"; exit 1; }
  cp "$f" $L/$m.ko
  # modules.dep format of the shipped initrd: "/lib/modules/x.ko:" (deps listed by load order)
  echo "/lib/modules/$m.ko:" >> $L/modules.dep
  echo "$m.ko" >> $L/modules.load
done
mkdir -p lib/firmware/amdgpu
for n in renoir green_sardine; do cp /lib/firmware/amdgpu/${n}_*.bin lib/firmware/amdgpu/; done
find . | cpio -o -H newc --quiet | gzip -9 > "$OUT"
ls -l "$OUT"
