# gbos-x86-port

Running **Googlebook OS (Android 17, x86_64 "lapis" build)** on a PC that does not meet its CPU
requirements — a Ryzen 7 5700U (Zen 2) laptop — first in a KVM VM, then (work in progress) from an external USB SSD.
Brunch-style: keep Google's system image, replace the kernel, initramfs and a few vendor bits.

**Status (2026-10-13):** VM on KVM boots to the desktop; Chrome renders; `/data` persists; pointer, scroll and Gboard work.
A self-built kernel boots the shipped image in QEMU, including the OVMF → GRUB → USB path.
**Bare metal is untested** (no screen yet is the expected failure mode: the kernel has no fbcon; see `docs/`).
This is a collection of parts, not a one-command framework.

## What is here
| Dir | What |
|---|---|
| `module/` | `gfni_emu.ko`: traps `#UD` and emulates GFNI / VPCLMULQDQ / AVX-VNNI / VAES (Google's userland is built for newer CPUs). Needs `cfi=norand`. |
| `pointer-bridge/` | In-guest helper: reads QEMU's usb-tablet and re-emits a pen-style absolute pointer plus a wheel mouse via uinput (exact clicks at any aspect ratio, working scroll). |
| `kernel/` | `kmake.sh` (reproducible build env), shipped kernel config (extracted from the image). Config deltas and the CRC pitfalls are in `docs/`. |
| `patches/` | `apply_system_update.py`, `apply_vendor_update.py` (incremental erofs overlay edits: native bridge off, fake battery, Vulkan hidden, libz, pointer bridge) and `build_libz.sh` (baseline-CPU zlib). |
| `bm/` | `make_baremetal_initrd.sh` (adds amdgpu + firmware), `build_esp.sh` (GRUB ESP), `test_uefi_usb.sh` (QEMU test of the USB boot path), `flash_usb.sh` (**destructive**, USB disks only, asks for `YES`). |
| `host/` | Launchers with host protection (cgroup limits, health logger), `vnc.py` headless driver. |
| `docs/` | Status notes and the SIGILL investigation. |

## What is NOT here
No Google images, extracted system files or firmware. Get the recovery image yourself
(`https://dl.google.com/device/recovery/<codename>-user/<build>/recovery.zip`; lapis/moonstone/ruby are x86_64).
The `apply_*.py` scripts import helpers (`lp_tools`, `erofs_metadata`, `make_lapis_security_port`) from
[chaitan3/gbos-vm-linux](https://github.com/chaitan3/gbos-vm-linux), which has no license, so it is not copied: clone it
yourself and set `GBOS_LINUX_DIR`. Scratch data lives under `GBOS_WORK` (default `~/gbos-x86-work`).
Paths in `bm/`, `host/` and `kernel/` still assume my layout (`~/src/...`); adjust before use.

## Kernel notes
Source: AOSP `kernel/common` tag `android17-6.18-2026-06_r52`, LLVM=1, `KBUILD_GENDWARFKSYMS_STABLE=1` (otherwise 400+ module CRC mismatches),
`LOCALVERSION` set to match the shipped release string. Shipped modules are signed with Google's key, so
`kernel/module/main.c` ("Cannot use protected symbol") is patched out: `if (0 && fsa.is_protected && !mod->sig_ok)`.
Added: amdgpu (+DC), I2C_HID_ACPI, PINCTRL_AMD, ACPI battery/AC. Wi-Fi, audio, EC are not done.

## Safety
VMs froze my host once (cause unknown), so the launchers use a memory/CPU cgroup and keep ≥6 GB free. Never run the flash script on an internal disk.
Not affiliated with Google. Use your own legally obtained images.

License: GPL-2.0 for the kernel module, MIT for the rest (see `LICENSE`).
