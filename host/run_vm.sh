#!/bin/bash
# Everyday launcher: persistent /data (qcow2 overlay), 4:3 display that fits the screen, host protected by a
# cgroup limit, and a host-health logger (~/src/isa-shim/hostwatch.log) that survives a hard freeze.
#   run_vm.sh [name]      (SDL window; GBOS_DISPLAY_ARGS="-display egl-headless -vnc 127.0.0.1:7" for headless)
set -u
NAME="${1:-daily}"
HERE="$(cd "$(dirname "$0")" && pwd)"
OVERLAY="${GBOS_OVERLAY:-$HERE/overlay/persist.qcow2}"
if pgrep -x qemu-system-x86 >/dev/null; then echo "QEMU is already running" >&2; exit 1; fi
if [ ! -f "$OVERLAY" ]; then
  qemu-img create -q -f qcow2 -b "$HOME/src/gbos-vm-linux/image-security/googlebook-security.bin" -F raw "$OVERLAY"
fi
AVAIL_MB=$(awk '/MemAvailable/{print int($2/1024)}' /proc/meminfo)
if [ "$AVAIL_MB" -lt 7000 ]; then echo "warning: only ${AVAIL_MB} MB RAM available; close heavy apps first" >&2; fi
rm -f "$HERE/hostwatch.log"
"$HERE/hostwatch.sh" "$HERE/hostwatch.log" & WATCH=$!
trap 'kill $WATCH 2>/dev/null' EXIT
cd "$HOME/src/gbos-vm-linux"
ARGS="cfi=norand loglevel=${GBOS_LOGLEVEL:-4} androidboot.selinux=permissive enforcing=0 androidboot.vendor.boot_patchlevel=2026-09-01 androidboot.boot_patchlevel=2026-09-01 androidboot.dalvik.vm.enable_uffd_gc=true androidboot.zygote.disable_gl_preload"
GBOS_OVERLAY="$OVERLAY" systemd-run --user --scope -p MemoryMax=7G -p MemorySwapMax=0 -p CPUQuota=400% \
  "$HERE/boot_vm_overlay.sh" "$PWD" "$NAME" "${GBOS_SECS:-28800}" "$ARGS"
