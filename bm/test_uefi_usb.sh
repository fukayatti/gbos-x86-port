#!/bin/bash
# Test the bare-metal boot path in QEMU: OVMF -> GRUB (ESP on USB) -> kernel+initrd, Android image on a second USB disk.
set -u
H=$HOME/src/isa-shim; B=$H/bm; NAME=${1:-bmtest}; SECS=${GBOS_SECS:-600}
pgrep -x qemu-system-x86 >/dev/null && { echo "QEMU already running"; exit 1; }
[ -f $B/test.qcow2 ] || qemu-img create -q -f qcow2 -b $HOME/src/gbos-vm-linux/image-security/googlebook-security.bin -F raw $B/test.qcow2
cp /usr/share/OVMF/OVMF_VARS_4M.fd $B/vars.fd
mkdir -p $B/logs; rm -f $B/logs/$NAME.log
"$H/hostwatch.sh" "$H/hostwatch.log" & W=$!; trap 'kill $W 2>/dev/null' EXIT
systemd-run --user --scope -p MemoryMax=7G -p MemorySwapMax=0 -p CPUQuota=400% \
 timeout $SECS qemu-system-x86_64 -enable-kvm -cpu host -smp 4 -m 4G -machine q35 -nodefaults -no-reboot \
  -drive if=pflash,format=raw,readonly=on,file=/usr/share/OVMF/OVMF_CODE_4M.fd -drive if=pflash,format=raw,file=$B/vars.fd \
  -device qemu-xhci,id=xhci,addr=0x5 \
  -drive if=none,id=esp,format=raw,file=$B/esp-qemutest.img -device usb-storage,bus=xhci.0,drive=esp,bootindex=0 \
  -drive if=none,id=disk,format=qcow2,file=$B/test.qcow2 -device usb-storage,bus=xhci.0,drive=disk,bootindex=1 \
  -device virtio-gpu-gl-pci,hostmem=2G,blob=true,venus=true,addr=0x1,xres=1440,yres=1080 \
  -device qemu-xhci,id=xhci2,addr=0x6 -device usb-kbd,bus=xhci2.0 -device usb-tablet,bus=xhci2.0 \
  -netdev user,id=n0 -device virtio-net-pci,netdev=n0 \
  -display egl-headless -vnc 127.0.0.1:7 -serial file:$B/logs/$NAME.log -monitor none
