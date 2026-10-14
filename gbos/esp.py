"""Assemble the ESP image: GRUB + kernel + initrd + a generated kernel command line."""
import os, subprocess, tempfile
from . import gpt

BASE = ("panic=30 root=/dev/ram0 loglevel=7 log_buf_len=8M cgroup_disable=pressure firmware_class.path=/vendor/firmware "
        "binder.impl=rust ia32_emulation=false vsyscall=none printk.devkmsg=on "
        "androidboot.hardware=android-desktop androidboot.hardware.platform=android-desktop androidboot.slot_suffix=_a "
        "androidboot.vbmeta.size=6720 androidboot.vbmeta.hash_alg=sha256 "
        "androidboot.vbmeta.device_state=unlocked androidboot.verifiedbootstate=orange androidboot.veritymode=enforcing "
        "androidboot.vendor.apex.com.android.hardware.keymint.strongbox.desktop=none "
        "cfi=norand androidboot.selinux=permissive enforcing=0 androidboot.vendor.boot_patchlevel=2026-09-01 "
        "androidboot.boot_patchlevel=2026-09-01 androidboot.dalvik.vm.enable_uffd_gc=true androidboot.zygote.disable_gl_preload")
SAFETY = "softlockup_panic=1 hardlockup_panic=1 nmi_watchdog=1 panic_on_oops=1"


def cmdline(profile, image, extra=''):
    parts = [BASE, f"androidboot.vbmeta.digest={gpt.vbmeta_digest(image)}",
             f"androidboot.boot_devices={profile['boot']['bridge']}", SAFETY]
    ids = profile.get('audio', {}).get('pinned_ids') or {}
    if ids:
        order = [ids[k] for k in sorted(ids, key=int)]
        parts.append('snd_hda_intel.index=' + ','.join(str(i) for i in range(len(order))))
        parts.append('snd_hda_intel.id=' + ','.join(order))
    if any('btusb' in u.get('drivers', []) for u in profile.get('usb', [])):
        parts.append('usbcore.autosuspend=-1 btusb.enable_autosuspend=0')
    if extra:
        parts.append(extra)
    return ' '.join(parts)


def build(profile, image, kernel, initrd, out, esp_mb=500, extra='', label='GBOSESP'):
    cmd = cmdline(profile, image, extra)
    cfg = (f'set timeout=3\nset default=0\nset timeout_style=menu\ninsmod part_gpt\ninsmod fat\n'
           f'menuentry "Googlebook OS" {{\n    search --no-floppy --label --set=root {label}\n'
           f'    linux /bzImage {cmd}\n    initrd /initrd\n    boot\n}}\n')
    with tempfile.TemporaryDirectory() as d:
        open(f'{d}/grub.cfg', 'w').write(cfg)
        subprocess.run(['grub-mkimage', '-O', 'x86_64-efi', '-o', f'{d}/BOOTX64.EFI', '-p', '/grub', '-c', f'{d}/grub.cfg',
                        'part_gpt', 'fat', 'normal', 'boot', 'linux', 'search', 'search_label', 'echo', 'configfile', 'efi_gop',
                        'all_video', 'gzio', 'ls', 'true', 'sleep'], check=True)
        if os.path.exists(out):
            os.remove(out)
        subprocess.run(['truncate', '-s', f'{esp_mb}M', out], check=True)
        subprocess.run(['mformat', '-i', out, '-F', '-v', label, '::'], check=True)
        for args in (['mmd', '-i', out, '::/EFI', '::/EFI/BOOT', '::/grub'],
                     ['mcopy', '-i', out, f'{d}/BOOTX64.EFI', '::/EFI/BOOT/BOOTX64.EFI'],
                     ['mcopy', '-i', out, f'{d}/grub.cfg', '::/grub/grub.cfg'],
                     ['mcopy', '-i', out, kernel, '::/bzImage'],
                     ['mcopy', '-i', out, initrd, '::/initrd']):
            subprocess.run(args, check=True, stdout=subprocess.DEVNULL)
    return cmd
