# gbos-x86-port

Running **Googlebook OS (Android 17, x86_64 "lapis" build)** on a PC that does not meet its CPU
requirements — a Ryzen 7 5700U (Zen 2) laptop — first in a KVM VM, then (work in progress) from an external USB SSD.
Brunch-style: keep Google's system image, replace the kernel, initramfs and a few vendor bits.

**Status (2026-10-13): it boots to the setup wizard and desktop on a real laptop** (Lenovo 82R9, Ryzen 7 5700U / Renoir, internal NVMe).
Working: kernel, amdgpu, Android UI via ANGLE + Mesa **RADV**, Wi-Fi (RTL8822CE via rtw88), Bluetooth, audio (analog), battery readout, brightness keys, lid suspend/resume.
Not done: HDMI audio, fingerprint, OTA, hardware video decode. Graphics buffers are linear "dumb" buffers (works, not fast). YouTube quality is limited (Widevine L3, no TEE).
Step-by-step: `docs/MANUAL.md`; symptom -> cause -> fix: `docs/TROUBLESHOOTING.md`.
A KVM VM path also works (see `host/`). This is a collection of parts, not a one-command framework, and it **overwrites partitions**: read `bm/`
before running anything there.

## What is here
| Dir | What |
|---|---|
| `module/` | `gfni_emu.ko`: traps `#UD` and emulates GFNI / VPCLMULQDQ / AVX-VNNI / VAES (Google's userland is built for newer CPUs). Needs `cfi=norand`. |
| `pointer-bridge/` | In-guest helper: reads QEMU's usb-tablet and re-emits a pen-style absolute pointer plus a wheel mouse via uinput (exact clicks at any aspect ratio, working scroll). |
| `kernel/` | `kmake.sh` (reproducible build env), shipped kernel config (extracted from the image). Config deltas and the CRC pitfalls are in `docs/`. |
| `patches/` | `apply_system_update.py`, `apply_vendor_update.py` (incremental erofs overlay edits: native bridge off, fake battery, Vulkan hidden, libz, pointer bridge) and `build_libz.sh` (baseline-CPU zlib). |
| `bm/` | `make_baremetal_initrd.sh` (adds amdgpu, rtw88, firmware), `build_esp.sh` (GRUB ESP), `test_uefi_usb.sh` (QEMU test of the boot path), `flash_usb.sh` (**destructive**, USB disks only), `install_nvme_p3.sh` / `rewrite_images.sh` / `write_esp.sh` (**destructive**, written for one specific laptop's partition layout; read and adapt), `get_logs.sh` (reads logcat that `gbos-persist-log` keeps on /metadata), `fix_boot.sh`. |
| `mesa-radv/` | Build script + patch for Mesa RADV on Android x86_64 (needs the NDK and Mesa 26.2.4 with the mapper5 patch from skylartaylor/gbos-vm). |
| `host/` | Launchers with host protection (cgroup limits, health logger), `vnc.py` headless driver. |
| `tools/gbos_detect.py` | **Prototype**: run it on a Linux that already works on your machine; it writes a hardware profile (boot device PCI bridge, GPU/Wi-Fi/Bluetooth drivers and firmware, ALSA cards and a draft audio config for the dras HAL, kernel command line pieces). On the author's laptop it reproduces the hand-made audio config and `boot_devices` exactly. |
| `docs/` | Status notes and the SIGILL investigation. |

## What is NOT here
No Google images, extracted system files or firmware. Get the recovery image yourself
(`https://dl.google.com/device/recovery/<codename>-user/<build>/recovery.zip`; lapis/moonstone/ruby are x86_64).
The `apply_*.py` scripts import helpers (`lp_tools`, `erofs_metadata`, `make_lapis_security_port`) from
[chaitan3/gbos-vm-linux](https://github.com/chaitan3/gbos-vm-linux), which has no license, so it is not copied: clone it
yourself and set `GBOS_LINUX_DIR`. Scratch data lives under `GBOS_WORK` (default `~/gbos-x86-work`).
Paths in `bm/`, `host/` and `kernel/` still assume my layout (`~/src/...`); adjust before use.

## Bare-metal recipe (what worked)
1. Build the kernel (`kernel/`), then `bm/make_baremetal_initrd.sh` (amdgpu + rtw88 and their firmware go into the initrd).
2. `mesa-radv/build.sh`, then `patches/make_radv_runtime.sh`; `GBOS_GFX=radv python3 patches/apply_vendor_update.py`.
3. Lay the image's GPT partitions out on a disk (see `bm/install_nvme_p3.sh`), write them **without `conv=sparse`** (stale data in the holes broke erofs),
   `bm/build_esp.sh` -> ESP with GRUB + kernel + initrd.
Gotchas found on the way: `androidboot.boot_devices` must be the PCI *bridge* path above the NVMe/xHCI, not the device itself; `/data/local` is
file-name encrypted so logs must go to unencrypted /metadata; minigbm has no amdgpu backend, but its dumb backend works once renamed
(`radeon` -> `amdgpu`); HWUI needs >= 2 graphics queues, RADV reports 1 (see `mesa-radv/`).

## Kernel notes
Source: AOSP `kernel/common` tag `android17-6.18-2026-06_r52`, LLVM=1, `KBUILD_GENDWARFKSYMS_STABLE=1` (otherwise 400+ module CRC mismatches),
`LOCALVERSION` set to match the shipped release string. Shipped modules are signed with Google's key, so
`kernel/module/main.c` ("Cannot use protected symbol") is patched out, see `kernel/patches/`. `kernel/check_crc.py` compares every shipped module's symbol CRCs with your `Module.symvers` (0 mismatches over ~500 modules).
Added: amdgpu (+DC), I2C_HID_ACPI, PINCTRL_AMD, ACPI battery/AC, cfg80211/mac80211/rtw88 (8822CE). Audio and EC are not done.

## Safety
VMs froze my host once (cause unknown), so the launchers use a memory/CPU cgroup and keep ≥6 GB free. Never run the flash script on an internal disk.
Not affiliated with Google. Use your own legally obtained images.

License: GPL-2.0 for the kernel module, MIT for the rest (see `LICENSE`).
