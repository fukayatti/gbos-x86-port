#!/bin/bash
# Usage: rewrite_images.sh [partition-name ...]  (default: all)
# Re-write every Googlebook partition from the image WITHOUT conv=sparse (stale Bliss OS data was left in the holes). Root only.
# Partition layout is unchanged; devices are found by PARTLABEL, source offsets come from the image's own GPT.
set -euo pipefail
[ "$(id -u)" = 0 ] || { echo need root; exit 1; }
DEV=/dev/nvme0n1; IMG=${GBOS_IMAGE:?set GBOS_IMAGE to the googlebook-security.bin path}
lsblk -no PARTLABEL ${DEV}p20 | grep -qx GBOSESP || { echo "unexpected layout - abort"; exit 1; }
python3 - "$IMG" <<'PY' | while read -r name s e; do
import sys,struct
f=open(sys.argv[1],'rb'); f.seek(512); h=f.read(92); n,sz=struct.unpack('<II',h[80:88]); f.seek(1024)
for i in range(n):
    x=f.read(sz)
    if x[:16]==b'\0'*16: continue
    s,l=struct.unpack('<QQ',x[32:48]); print(x[56:128].decode('utf-16le').rstrip('\0'),s,l)
PY
  if [ $# -gt 0 ]; then case " $* " in *" $name "*) ;; *) continue;; esac; fi   # optional: only the named partitions
  dev=$(lsblk -lno PATH,PARTLABEL $DEV | awk -v n="$name" '$2==n{print $1}')
  [ -n "$dev" ] || { echo "no device for $name"; exit 1; }
  size=$(( ($(blockdev --getsize64 $dev)) / 512 )); want=$((e-s+1))
  [ "$size" -eq "$want" ] || { echo "size mismatch $name: $size vs $want"; exit 1; }
  echo -n "$name -> $dev ... "
  dd if="$IMG" of="$dev" bs=512 skip=$s count=$want conv=fsync status=none
  echo done
done
sync; echo ALL DONE
