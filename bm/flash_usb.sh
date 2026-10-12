#!/bin/bash
# Write the Googlebook image + our ESP to an EXTERNAL USB disk. DESTRUCTIVE. Usage: sudo flash_usb.sh /dev/sdX
set -euo pipefail
DEV=${1:?device}; B=$HOME/src/isa-shim/bm; IMG=$HOME/src/gbos-vm-linux/image-security/googlebook-security.bin
[ "$(lsblk -dno TRAN "$DEV")" = usb ] || { echo "$DEV is not a USB disk"; exit 1; }
[ "$(lsblk -dbno SIZE "$DEV")" -ge $((60*1024**3)) ] || { echo "disk too small (<60GB)"; exit 1; }
lsblk -o NAME,SIZE,MODEL,TRAN "$DEV"; read -rp "Erase ALL of $DEV? type YES: " a; [ "$a" = YES ]
umount ${DEV}* 2>/dev/null || true
dd if="$IMG" of="$DEV" bs=8M conv=sparse status=progress oflag=direct; sync
sgdisk -e "$DEV"; sgdisk -n 22:0:+512M -t 22:EF00 -c 22:GBOSESP "$DEV"; partprobe "$DEV"; sleep 2
P=$(lsblk -lno PATH,PARTLABEL "$DEV" | awk '$2=="GBOSESP"{print $1}')
dd if="$B/esp.img" of="$P" bs=4M status=progress; sync; echo "done: $P"
