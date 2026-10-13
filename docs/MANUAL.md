# Manual: Googlebook OS (x86_64) on an AMD laptop, internal disk

What was done on a Lenovo 82R9 (Ryzen 7 5700U / Renoir, RTL8822CE Wi-Fi, ALC257 audio). Everything below overwrites partitions:
read each script before you run it, and keep a backup of anything you care about.

## 0. Ingredients
* The recovery image of an x86_64 build (`lapis`, `moonstone`, `ruby` - Intel Panther Lake builds). Download it yourself.
* A Linux host with the Android NDK (r30 used), clang/LLVM, meson/ninja, `mkfs.erofs`/`dump.erofs`, GRUB, mtools, sgdisk.
* The helpers from [chaitan3/gbos-vm-linux](https://github.com/chaitan3/gbos-vm-linux) (image layout, erofs overlay) - not copied here (no license).

## 1. The CPU problem
The userland uses GFNI, AVX-VNNI and friends. Zen 2 has none of them. `module/gfni_emu.c` traps `#UD` and emulates them; it needs `cfi=norand`.
`patches/build_libz.sh` rebuilds zlib for a baseline CPU, otherwise Adler-32 traps ~300k times per second.

## 2. Kernel
`kernel/kmake.sh` builds AOSP `kernel/common` (tag `android17-6.18-2026-06_r52`) out of tree with the shipped config plus amdgpu, I2C_HID_ACPI,
PINCTRL_AMD, cfg80211/mac80211/rtw88. `KBUILD_GENDWARFKSYMS_STABLE=1` and `LOCALVERSION` matching the shipped release string are what make
the shipped modules load (`kernel/check_crc.py` verifies this). The shipped modules are signed with Google's key: apply
`kernel/patches/0001-*.patch` (the "protected symbol" check).

## 3. Graphics
Stock stack is ANGLE on Vulkan. Only Intel and virtio drivers ship, so:
* Build Mesa RADV for Android x86_64 (`mesa-radv/`). No LLVM needed.
* gralloc: minigbm has no amdgpu backend, but it ships a generic "dumb" backend called `radeon`. Renaming that string to `amdgpu` (6 bytes,
  `patches/make_radv_runtime.sh`) makes it accept the amdgpu kernel driver. Buffers are linear (slow, but it works).
* HWUI needs two graphics queues; RADV reports one. Report two and map both onto hardware ring 0 (patch in `mesa-radv/`).
* `GBOS_GFX=radv python3 patches/apply_vendor_update.py` switches the properties back to the stock configuration.

## 4. Disk layout and boot
The image is a whole-disk GPT. Lay its partitions out on the target disk (names must be unique and exact: Android finds `by-name/super` etc.),
write them with plain `dd` (**never `conv=sparse`** - stale data in the holes corrupts erofs), add a FAT ESP with GRUB, kernel and
`initrd.bm` (`bm/make_baremetal_initrd.sh`: shipped initrd + amdgpu + rtw88 + firmware).
Kernel command line essentials: `root=/dev/ram0`, `androidboot.hardware=android-desktop`, `androidboot.slot_suffix=_a`,
`androidboot.boot_devices=<PCI path>`, `cfi=norand`, `androidboot.selinux=permissive`. The `boot_devices` value must be the **PCI bridge** above
the NVMe/xHCI controller (`pci0000:00/0000:00:02.4`), not the device itself, or no `by-name` links appear.

## 5. Debugging without a screen
* `bm/get_logs.sh` copies logs that `gbos-persist-log` writes to `/metadata` (unencrypted; `/data/local` is file-name encrypted).
* EFI pstore (`/sys/fs/pstore` / `/var/lib/systemd/pstore`) keeps kernel panics.
* Dual booting means you cannot `adb` from the other OS; a second machine can use the root adb over TCP (`GBOS_ADB`).

## 6. Status
Works: boot, desktop, Chrome, Wi-Fi, Bluetooth (Realtek firmware in /vendor/firmware), battery, brightness keys, lid suspend/resume.
Not yet: audio (the HAL cannot find its "internal card"), YouTube quality (Widevine L3 without a TEE?), fingerprint, OTA.
