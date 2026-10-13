#!/bin/bash
# Restore a working GRUB menu on the p1 ESP (the old menu lived on the deleted p3). Run as root.
# Backs up p1:/EFI/ubuntu/grub.cfg first. Does not touch p2/Kali.img.
set -euo pipefail
[ "$(id -u)" = 0 ] || { echo need root; exit 1; }
M=$(mktemp -d); mount /dev/nvme0n1p1 "$M"
cp -a "$M/EFI/ubuntu/grub.cfg" "$M/EFI/ubuntu/grub.cfg.bak-$(date +%s)"
cat > "$M/EFI/ubuntu/grub.cfg" <<'G'
set timeout=8
set default=0
insmod part_gpt
insmod ext2
insmod fat
insmod loopback
insmod chain
menuentry "Kali Linux (Kali.img on p2)" {
    search --no-floppy --fs-uuid --set=p2 f9da029d-d6cf-4ced-81dd-15aa3b0bba89
    loopback lo ($p2)/Kali.img
    linux (lo,gpt2)/vmlinuz-7.1.5+kali-amd64 root=UUID=109e1caa-fa3a-45ca-b308-80e959e2e785 ro rdinit=/linuxloops img_path=/Kali.img img_uuid=d4508bfe-45ea-4d75-9ce8-79e12228f683 resume=UUID=109e1caa-fa3a-45ca-b308-80e959e2e785 resume_offset=702464 systemd.log_target=null quiet splash loglevel=3
    initrd (lo,gpt2)/initrd.img-7.1.5+kali-amd64
}
menuentry "Googlebook OS (nvme p20)" {
    search --no-floppy --label --set=gb GBOSESP
    chainloader ($gb)/EFI/BOOT/BOOTX64.EFI
}
menuentry "UEFI firmware settings" { fwsetup }
G
umount "$M"
# Remove entries that cannot boot anything (Grub2Win, BlissOS, Manjaro, old Ubuntu label) and add one clean menu entry.
for n in 0000 0001 0002 0003; do efibootmgr -b $n -B >/dev/null 2>&1 || true; done
efibootmgr -c -d /dev/nvme0n1 -p 1 -L "GRUB menu (Kali / Googlebook)" -l '\EFI\ubuntu\shimx64.efi' >/dev/null
NEW=$(efibootmgr | awk '/GRUB menu \(Kali/{sub("Boot","",$1); sub("\\*","",$1); print $1}')
efibootmgr -o $NEW,0004,0014,0015,0016,0017,0018
efibootmgr | head -8 | cut -c1-120
