#!/bin/bash
# DESTRUCTIVE (authorized by the user 2026-10-13): replace /dev/nvme0n1p3 (old Bliss OS) with the Googlebook
# partitions + a GBOSESP, inside p3's sector range only. p1/p2/p4/p5 are not touched. Default = dry run; --yes writes.
set -euo pipefail
DEV=/dev/nvme0n1; P3=${DEV}p3
B=$(cd "$(dirname "$0")" && pwd); IMG=${GBOS_IMAGE:?set GBOS_IMAGE to the googlebook-security.bin path}; T=$(mktemp -d)
# Region of the old p3. Resumable: if p3 is already gone, use the recorded region and clear leftovers inside it.
START=869134336; SIZE=123242188; END=$((START+SIZE-1))
if [ -e /sys/block/nvme0n1/nvme0n1p3 ]; then
  [ "$(lsblk -no PARTLABEL $P3)" = "Bliss OS" ] || { echo "p3 is not 'Bliss OS' - abort"; exit 1; }
  ! findmnt -S $P3 >/dev/null || { echo "p3 is mounted - abort"; exit 1; }
  [ "$(cat /sys/block/nvme0n1/nvme0n1p3/start)" = $START ] || { echo "p3 geometry changed - abort"; exit 1; }
fi
ESP_SECT=$((512*2048)); OFF=$(( (START+2047)/2048*2048 ))
echo "region: sectors $START..$END ($((SIZE*512/1073741824)) GiB)"
python3 - "$OFF" "$ESP_SECT" "$IMG" > $T/img_plan.txt <<'PY'
import sys,struct,uuid
off,esp,img=int(sys.argv[1]),int(sys.argv[2]),sys.argv[3]; base=off+esp
f=open(img,'rb'); f.seek(1024); ents=[]
hdr=open(img,'rb'); hdr.seek(512); h=hdr.read(92); n,sz=struct.unpack('<II',h[80:88])
f.seek(1024)
for i in range(n):
    e=f.read(sz)
    if e[:16]==b'\0'*16: continue
    tg=str(uuid.UUID(bytes_le=e[:16])).upper(); s,l=struct.unpack('<QQ',e[32:48])
    name=e[56:128].decode('utf-16le').rstrip('\0'); ents.append((s,l,name,tg))
lo=min(x[0] for x in ents)
for s,l,name,tg in sorted(ents):
    print(name,s,l,base+(s-lo),base+(l-lo),tg)
PY
cat $T/img_plan.txt
LAST=$(awk '{print $5}' $T/img_plan.txt | sort -n | tail -1); [ "$LAST" -le "$END" ] || { echo "does not fit"; exit 1; }
[ "${1:-}" = --yes ] || { echo "dry run OK (last sector $LAST <= $END). Re-run with --yes as root."; exit 0; }
[ "$(id -u)" = 0 ] || { echo need root; exit 1; }
for d in /sys/block/nvme0n1/nvme0n1p*; do   # delete p3 and leftovers of an earlier partial run, only inside the region
  n=${d##*nvme0n1p}; st=$(cat $d/start)
  if [ "$st" -ge "$START" ] && [ "$st" -le "$END" ]; then echo "deleting $DEV p$n (start $st)"; sgdisk -d $n $DEV >/dev/null; fi
done
sgdisk -a 1 -n 20:$OFF:$((OFF+ESP_SECT-1)) -t 20:EF00 -c 20:GBOSESP $DEV
i=21
while read -r name s e ns ne code; do sgdisk -a 1 -n $i:$ns:$ne -t $i:$code -c $i:"$name" $DEV; i=$((i+1)); done < $T/img_plan.txt
partprobe $DEV; sleep 2
dd if="$B/esp.img" of=${DEV}p20 bs=4M conv=fsync status=none
while read -r name s e ns ne code; do
  dev=$(lsblk -lno PATH,PARTLABEL $DEV | awk -v n="$name" '$2==n{print $1}')
  dd if="$IMG" of="$dev" bs=512 skip=$s count=$((e-s+1)) conv=fsync,sparse status=none
  echo "wrote $name -> $dev"
done < $T/img_plan.txt
efibootmgr -c -d $DEV -p 20 -L "Googlebook OS" -l '\EFI\BOOT\BOOTX64.EFI'
