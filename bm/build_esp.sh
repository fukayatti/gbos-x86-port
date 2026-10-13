#!/bin/bash
# Build bm/esp.img (FAT32 ESP: GRUB + our kernel + bare-metal initrd). Touches no real disk.
set -euo pipefail
B=$HOME/src/isa-shim/bm; cd "$B"
KERNEL=${KERNEL:-$HOME/src/kernel/out/arch/x86/boot/bzImage}
INITRD=${INITRD:-$B/initrd.bm}
# boot_devices = the two xHCI controllers of this laptop (Lenovo 82R9); any USB port on them works.
BD="${BD:-pci0000:00/0000:00:08.1/0000:04:00.3,pci0000:00/0000:00:08.1/0000:04:00.4}"
BASE="panic=30 root=/dev/ram0 loglevel=7 log_buf_len=8M cgroup_disable=pressure firmware_class.path=/vendor/firmware \
binder.impl=rust ia32_emulation=false vsyscall=none printk.devkmsg=on \
androidboot.hardware=android-desktop androidboot.hardware.platform=android-desktop androidboot.slot_suffix=_a \
androidboot.boot_devices=$BD androidboot.vbmeta.size=6720 androidboot.vbmeta.hash_alg=sha256 \
androidboot.vbmeta.digest=7531ae828445265b27337eab486fe6eca7f009136495564d0e17f9dad3e71b3e \
androidboot.vbmeta.device_state=unlocked androidboot.verifiedbootstate=orange androidboot.veritymode=enforcing \
androidboot.vendor.apex.com.android.hardware.keymint.strongbox.desktop=none \
cfi=norand androidboot.selinux=permissive enforcing=0 androidboot.vendor.boot_patchlevel=2026-09-01 androidboot.boot_patchlevel=2026-09-01 \
androidboot.dalvik.vm.enable_uffd_gc=true androidboot.zygote.disable_gl_preload"
# EXTRA is for experiments, e.g. QEMU test: "console=ttyS0,115200 earlycon=uart8250,io,0x3f8"
CMD="$BASE ${EXTRA:-}"
cat > grub.cfg <<G
set timeout=3
set default=0
set timeout_style=menu
insmod part_gpt
insmod fat
menuentry "Googlebook OS (gbos x86_64)" {
    search --no-floppy --label --set=root GBOSESP
    linux /bzImage $CMD
    initrd /initrd
    boot
}
G
grub-mkimage -O x86_64-efi -o BOOTX64.EFI -p /grub -c grub.cfg \
  part_gpt fat normal boot linux search search_label echo configfile efi_gop all_video gzio ls true sleep
rm -f esp.img; truncate -s ${ESP_MB:-512}M esp.img; mformat -i esp.img -F -v GBOSESP ::
mmd -i esp.img ::/EFI ::/EFI/BOOT ::/grub
mcopy -i esp.img BOOTX64.EFI ::/EFI/BOOT/BOOTX64.EFI
mcopy -i esp.img grub.cfg ::/grub/grub.cfg
mcopy -i esp.img "$KERNEL" ::/bzImage
mcopy -i esp.img "$INITRD" ::/initrd
mdir -i esp.img ::/
