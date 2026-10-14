# Troubleshooting log (symptom -> cause -> fix)

| Symptom | Cause | Fix |
|---|---|---|
| `init` dies with SIGILL, kernel panic | GFNI/VNNI instructions on a CPU without them | `module/gfni_emu.c`, `cfi=norand` |
| Module load: `disagrees about version of symbol` | CRCs computed differently | `KBUILD_GENDWARFKSYMS_STABLE=1`, force rebuild of objects with `#SYMVER` lines |
| Module load: `Cannot use protected symbol` | shipped modules signed with another key | disable the check in `kernel/module/main.c` |
| `realpath failed: /dev/block/by-name/super` | wrong `androidboot.boot_devices` | use the PCI bridge path |
| `erofs: failed to decompress -12`, panic in init | partitions written with `dd conv=sparse` over old data | rewrite without `sparse` |
| Black screen, `surfaceflinger` restarts every 5 s | `MESA-EGL: Failed to open any DRM device`; minigbm has no amdgpu backend, VM-flavoured properties | RADV + minigbm string patch, `GBOS_GFX=radv` |
| `Assertion failed: queueProps[i].queueFamilyProperties.queueCount < kRequestedQueueCount` | HWUI wants >= 2 graphics queues | report 2, map to ring 0 |
| `VK_ERROR_DEVICE_LOST` flood, launcher restarts, screen blinks | queue 2 used hardware ring 1 | `queue_index = 0` for the general family |
| `VK_ERROR_DEVICE_LOST` flood **after you added logging** | `if (ret < 0) log(); return LOST;` - logging inserted into an unbraced `if` made the return unconditional | always add braces; never insert statements between an `if` and its body |
| Logs vanish / only the last 40 s kept | verbose logcat, 12 MB budget | `logcat '*:W'`, rotate 4 MB x 3; /metadata is only ~64 MB |
| GRUB drops to a prompt | menu entry did not end with `boot` / `boot` module missing from the standalone image | add the module and the command |
| Bluetooth HAL aborts (`initializationComplete`) | Realtek firmware missing | put `rtl_bt/rtl8822cu_*` in `/vendor/firmware`, `firmware_class.path=/vendor/firmware` (the kernel still falls back to `/lib/firmware` in the initrd) |
| No sound, `AlsaCardManager ... internal card not found` | `snd_hda_intel` is blocklisted by the vendor; the audio HAL only knows Intel SOF boards | load the HDA modules explicitly; per-machine audio config still needed |
| Kernel panic in `btrtl_download_firmware` (NULL deref), Bluetooth works only sometimes | `btrtl_initialize()` jumps to `err_free` after `rc = ...` failed but returns `ERR_PTR(ret)` with `ret == 0`, i.e. NULL, which `IS_ERR()` lets through; triggered when the Realtek USB device drops off the bus (-71) during firmware download | `kernel/patches/0002-*.patch`; build btusb/btrtl yourself and load them from the initrd |
| `init: ... ro.xxx` / `echo $?` in an rc `service` never runs | init expands `$` in arguments | write `$$` for a literal `$` |
| Audio HAL (`dras`): `AlsaCardManager ... internal card not found` | the card is chosen from `/vendor/etc/data/hal_configs/audio_*.xml` (`<soundcard>` = ALSA card **id**, `<audio-config-dir>`); only Intel SOF boards ship | add `audio-config/hda-alc257`, point `audio_default.xml` at it, pin the card ids on the command line (`snd_hda_intel.id=HDMIAMD,Analog`) because the HDMI and analog cards would both be called `Generic` |
| Logs missing a subsystem you are debugging | the in-image kmsg filter in `gbos-persist-log` only keeps listed keywords | add the keyword (`snd`, `bluetooth`, ...) before concluding anything |
