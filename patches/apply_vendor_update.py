#!/usr/bin/env python3
import sys, os, subprocess, tempfile, shutil
from pathlib import Path
LINUX_DIR = os.environ.get('GBOS_LINUX_DIR', os.path.expanduser('~/src/gbos-vm-linux'))  # checkout of chaitan3/gbos-vm-linux (provides lp_tools, erofs_metadata, make_lapis_security_port)
WORK = Path(os.environ.get('GBOS_WORK', os.path.expanduser('~/gbos-x86-work')))
sys.path.insert(0, LINUX_DIR)
import lp_tools
import erofs_metadata
from make_lapis_security_port import (
    dump_cat, overlay_tar, dir_entries, restore_root_xattr,
    compress_option, _sb_fields, _label, _exists, BLOCK, VM_GRAPHICS_RC, NEUTERED
)

# The Venus Vulkan driver cannot import the virgl (GLES) gralloc buffers Chrome hands it, which kills
# Chrome's GPU process (host: "vkr: invalid res_id"). Point the Vulkan loader at a driver name that does
# not exist so apps see no Vulkan and use GLES. GBOS_VULKAN_DRIVER=on restores vulkan.virtio.so.
VK_HW = 'virtio' if os.environ.get('GBOS_VULKAN_DRIVER') == 'on' else 'novulkan'

# Bare metal on an AMD GPU (GBOS_GFX=radv): stock graphics stack (ANGLE on Vulkan) with Mesa RADV as the Vulkan
# driver, and the dumb DRM backend of minigbm switched on for the amdgpu kernel driver. See radv-runtime/.
GFX_RADV = os.environ.get('GBOS_GFX') == 'radv'
if GFX_RADV:
    VK_HW = 'radv'
GFX_SWAPS = [(b'ro.hardware.egl virgl', b'ro.hardware.egl angle'), (b'persist.graphics.egl virgl', b'persist.graphics.egl angle'),
             (b'ro.hwui.use_vulkan false', b'ro.hwui.use_vulkan true'),
             (b'debug.renderengine.backend skiaglthreaded', b'debug.renderengine.backend skiavkthreaded'),
             (b'debug.renderengine.vulkan false', b'debug.renderengine.vulkan true'),
             (b'debug.hwui.renderer skiagl', b'debug.hwui.renderer skiavk'),
             (b'ro.gfx.angle.supported false', b'ro.gfx.angle.supported true'),
             (b'    setprop debug.ld.all dlerror,dlopen\n', b'')]
def gfx_fix(b):
    if GFX_RADV:
        for old, new in GFX_SWAPS:
            b = b.replace(old, new)
    return b

def main():
    target = Path(LINUX_DIR) / 'image-security/googlebook-security.bin'
    super_base = 746586112 # 1458176 * 512
    sup = lp_tools.Super(target, super_base)
    vendor = sup.layout('vendor_a')
    vendor_off, vendor_size = vendor[0]
    print(f"vendor_a offset: {vendor_off}, size: {vendor_size}")

    tmp = Path(tempfile.mkdtemp(prefix='patch-vendor-props-'))
    try:
        src_fs = tmp / 'vendor.erofs'
        print("Extracting vendor_a from target image...")
        with target.open('rb') as f:
            f.seek(vendor_off + 1024)
            sb = f.read(128)
            blocks = int.from_bytes(sb[36:40], 'little')
            fs_size = blocks << sb[12]
            build_time = int.from_bytes(sb[24:32], 'little')
            zopt = compress_option(sb)
            f.seek(vendor_off)
            remaining = fs_size
            with src_fs.open('wb') as g:
                while remaining:
                    b = f.read(min(remaining, 1 << 22))
                    if not b: break
                    g.write(b)
                    remaining -= len(b)
        print(f"Extracted vendor.erofs ({fs_size} bytes)")

        files = {}
        dirs = {}
        def add(rel, data, label, mode=0o644, uid=0, gid=0):
            files[rel] = (data, label, mode, uid, gid)

        # 1. fstab
        orig_fstab = dump_cat(src_fs, '/etc/fstab.android-desktop').decode()
        new_fstab_lines = []
        for line in orig_fstab.splitlines():
            if line.startswith('/dev/block/by-name/userdata '):
                line = line.replace(',keydirectory=/metadata/vold/metadata_encryption', '')
                line = line.replace(',checkpoint=block', '')
            new_fstab_lines.append(line)
        new_fstab = ('\n'.join(new_fstab_lines) + '\n').encode()
        fst_m = erofs_metadata.metadata(src_fs, '/etc/fstab.android-desktop')
        add('etc/fstab.android-desktop', new_fstab, _label(fst_m), fst_m['mode'] & 0o7777, fst_m['uid'], fst_m['gid'])

        # 2. vm-graphics.rc
        VM_GRAPHICS_RC_CUSTOM = (
            b'# Zygote and graphics config for lapis VM\n'
            b'import /system/etc/init/hw/init.zygote64.rc\n'
            b'import /system/etc/init/hw/init.boringssl.zygote64.rc\n\n'
            b'on early-init\n'
            b'    setprop ro.build.type user\n'
            b'    setprop ro.build.version.sdk 37\n'
            b'    setprop ro.build.version.release 17\n'
            b'    setprop ro.build.version.release_or_codename 17\n'
            b'    setprop ro.build.version.codename REL\n'
            b'    setprop ro.build.id CL3B.260622.271.R1\n'
            b'    setprop ro.build.version.incremental 16452207\n'
            b'    setprop ro.build.tags release-keys\n'
            b'    setprop ro.build.flavor lapis-user\n'
            b'    export MESA_GLES_VERSION_OVERRIDE 3.1\n'
            b'    export MESA_GLSL_VERSION_OVERRIDE 310\n'
            b'    export MESA_EXTENSION_OVERRIDE GL_OES_EGL_image_external\n'
            b'    export LIBGL_DEBUG verbose\n'
            b'    export MESA_DEBUG 1\n'
            b'    setprop ro.crypto.set_dun true\n'
            b'    setprop ro.sf.lcd_density 240\n'
            b'    setprop qemu.sf.lcd_density 240\n'
            b'    setprop ro.zygote zygote64\n'
            b'    setprop ro.dalvik.vm.enable_uffd_gc true\n'
            b'    setprop dalvik.vm.enable_uffd_gc true\n'
            b'    setprop dalvik.vm.extra-opts -Xgc:CMC\n'
            b'    setprop ro.zygote.disable_gl_preload true\n'
            b'    setprop ro.hardware.egl virgl\n'
            b'    setprop ro.hardware.vulkan ' + VK_HW.encode() + b'\n'
            b'    setprop ro.hwui.use_vulkan false\n'
            b'    setprop ro.opengles.version 196609\n'
            b'    setprop persist.graphics.egl virgl\n'
            b'    setprop debug.renderengine.backend skiaglthreaded\n'
            b'    setprop debug.renderengine.vulkan false\n'
            b'    setprop debug.renderengine.graphite false\n'
            b'    setprop debug.renderengine.graphite_desktop_optin false\n'
            b'    setprop debug.hwui.renderer skiagl\n'
            b'    setprop ro.gfx.angle.supported false\n'
            b'    setprop ro.surface_flinger.has_wide_color_display false\n'
            b'    setprop ro.surface_flinger.has_HDR_display false\n'
            b'    setprop ro.surface_flinger.supports_background_blur false\n'
            b'    setprop debug.renderengine.blur_algorithm none\n'
            b'    setprop debug.mesa.gles.version.override 3.1\n'
            b'    setprop debug.mesa.glsl.version.override 310\n'
            b'    setprop debug.mesa.debug 1\n'
            b'    setprop debug.mesa.log.level debug\n'
            b'    setprop debug.egl.trace 1\n'
            b'    setprop debug.egl.debug_proc 1\n'
            b'    setprop log.tag.SurfaceFlinger VERBOSE\n'
            b'    setprop log.tag.RenderEngine VERBOSE\n'
            b'    setprop log.tag.Skia VERBOSE\n'
            b'    setprop log.tag.skia VERBOSE\n\n'
            b'on init\n'
            b'    restorecon_recursive /sys/devices/pci0000:00/0000:00:01.0\n'
        )
        add('etc/init/vm-graphics.rc', gfx_fix(VM_GRAPHICS_RC_CUSTOM), 'vendor_configs_file')

        # 2b. Temporary debug: show INFO logs from zygote64/ART (-verbose:jni) on the serial console.
        if _exists(src_fs, 'etc/init/vm-diagnostic-log.rc'):
            diag = dump_cat(src_fs, '/etc/init/vm-diagnostic-log.rc')
            if b'zygote64:V' not in diag:
                diag = diag.replace(b'linker:I ', b'', 1).replace(b'zygote64:I ', b'', 1)
                diag = diag.replace(b'*:W ', b'*:W zygote64:V ', 1)
            # Early-boot WTF reports retry a missing "dropbox" service and print a stack trace each time,
            # flooding the serial console (tens of thousands of lines). Silence that tag and show the
            # system_server boot phases instead.
            if b'SystemServiceRegistry:S' not in diag:
                diag = diag.replace(b'*:W ', b'*:W SystemServiceRegistry:S SystemServer:I ActivityManager:I PackageManager:I SystemServiceManager:I ', 1)
            # Debugging the "/data does not survive a second boot" problem: show how vold / keystore2 /
            # the software KeyMint restore (or fail to restore) the disk-encryption key.
            if b'vold:V' not in diag:
                diag = diag.replace(b'*:W ', b'*:W vold:V keystore2:V keymint-hal-nonsecure:V ', 1)
            d_m = erofs_metadata.metadata(src_fs, '/etc/init/vm-diagnostic-log.rc')
            add('etc/init/vm-diagnostic-log.rc', diag, _label(d_m), d_m['mode'] & 0o7777, d_m['uid'], d_m['gid'])

        # 2c. The ChromeOS-EC based health HAL cannot open /dev/cros_ec in a VM, so the system reports a
        # 0% battery and the setup wizard refuses to continue. Pretend we are on AC at 100% and keep the
        # screen on (the display otherwise sleeps a few minutes after boot).
        VM_FAKE_BATTERY_RC = (
            b'# VM: fake a charged, plugged-in battery and keep the screen awake\n'
            b'service vm-fake-battery /system/bin/sh -c "for i in 1 2 3 4 5 6 7 8; do cmd battery set ac 1 && break; sleep 5; done; '
            b'cmd battery set usb 0; cmd battery set status 2; cmd battery set level 100; svc power stayon true"\n'
            b'    class late_start\n'
            b'    user shell\n'
            b'    group shell\n'
            b'    seclabel u:r:shell:s0\n'
            b'    oneshot\n'
            b'    disabled\n\n'
            b'on property:sys.boot_completed=1\n'
            b'    start vm-fake-battery\n'
        )
        if not GFX_RADV:   # bare metal has a real battery (ACPI), do not fake it
            add('etc/init/vm-fake-battery.rc', VM_FAKE_BATTERY_RC, 'vendor_configs_file')

        # 2c3. Bare metal: show the battery percentage in the status bar (the real ACPI battery works through the
        # health HAL, but the percentage is off by default).
        if GFX_RADV:
            add('etc/init/gbos-battery-percent.rc', (
                b'service gbos-battery-percent /system/bin/sh -c "for i in 1 2 3 4 5 6; do cmd settings put system status_bar_show_battery_percent 1 && break; sleep 10; done"\n'
                b'    class late_start\n'
                b'    user root\n'
                b'    seclabel u:r:shell:s0\n'
                b'    oneshot\n'
                b'    disabled\n\n'
                b'on property:sys.boot_completed=1\n'
                b'    start gbos-battery-percent\n'), 'vendor_configs_file')

        # 2c5. Bare metal audio: the vendor blocklists snd_hda_intel (its Intel boards use SOF), so load the HD-Audio driver
        # and the Realtek / ATI HDMI codec drivers explicitly. modprobe without -b ignores the blocklist.
        if GFX_RADV:
            add('etc/init/gbos-audio-modules.rc', (
                b'service gbos-audio-modules /system/bin/sh -c "mkdir -p /metadata/gbos-log; '
                b'(/system/bin/modprobe -v -a -d /vendor_dlkm/lib/modules snd-hda-intel snd-hda-codec-alc269 '
                b'snd-hda-codec-generic; echo modprobe-exit=$$?; sleep 3; '
                b'cat /proc/asound/cards; ls /dev/snd; ls /sys/class/sound; echo == dras-help; /apex/com.android.hardware.audio.desktop/bin/hw/dras.core.audio --help 2>&1 | head -60; '
                b'echo == props; getprop ro.product.device; getprop vendor.device.sku.id; ls /vendor/etc/audio; for c in /proc/asound/card*; do echo $$c; cat $$c/id; done) > /metadata/gbos-log/audio.txt 2>&1"\n'
                b'    class core\n'
                b'    user root\n'
                b'    seclabel u:r:shell:s0\n'
                b'    oneshot\n'
                b'    disabled\n\n'
                b'on early-init\n'
                b'    start gbos-audio-modules\n'), 'vendor_configs_file')

        # 2c6. Bare metal Bluetooth: make sure the USB Bluetooth driver stack is loaded and leave a diagnostic file.
        if GFX_RADV:
            add('etc/init/gbos-bt-modules.rc', (
                b'service gbos-bt-modules /system/bin/sh -c "mkdir -p /metadata/gbos-log; '
                b'(/system/bin/modprobe -v -a -d /system_dlkm/lib/modules bluetooth; /system/bin/modprobe -v -a -d /vendor_dlkm/lib/modules btusb; '
                b'echo modprobe-exit=$$?; sleep 15; dmesg | grep -iE \'blue|hci|btusb|btrtl|rtl8822|rtl_bt|firmware\' | tail -40; '
                b'ls -la /sys/class/bluetooth; lsmod | grep -E \'^(bt|blue|hci|rfkill)\'; echo == protocols; grep -iE \'HCI|L2CAP|RFCOMM|SCO|BT\' /proc/net/protocols; '
                b'echo == hcitool; /vendor/bin/hw/hcitool.android-desktop dev; echo hcitool-exit=$$?; echo == dev; ls -l /dev/rfkill /dev/vhci) > /metadata/gbos-log/bt.txt 2>&1"\n'
                b'    class core\n'
                b'    user root\n'
                b'    seclabel u:r:shell:s0\n'
                b'    oneshot\n'
                b'    disabled\n\n'
                b'on early-boot\n'
                b'    start gbos-bt-modules\n'), 'vendor_configs_file')

        # 2c7. Bare metal audio config for the AMD HD-Audio codec (ALC257). The audio HAL (dras) learns its card from
        # /vendor/etc/data/hal_configs/audio_*.xml, which only knows Intel SOF boards; point it at our own config dir.
        if GFX_RADV:
            ac = Path(__file__).resolve().parent.parent / 'audio-config' / 'hda-alc257'
            dirs.setdefault('etc/audio/hda-alc257', (0o755, 0, 0, 'vendor_configs_file'))
            for fn in ('audio_config.xml', 'mixer_paths.xml', 'volume_curves.xml'):
                add('etc/audio/hda-alc257/' + fn, (ac / fn).read_bytes(), 'vendor_configs_file', 0o644, 0, 0)
            hal_audio = (b'<AudioConfiguration>\n  <soundcard>Analog</soundcard>\n'
                         b'  <audio-config-dir>hda-alc257</audio-config-dir>\n</AudioConfiguration>\n')
            for fn in ('audio_default.xml', 'audio_cs42l43_cs42l43_cs35l56.xml'):
                rel = 'etc/data/hal_configs/' + fn
                if _exists(src_fs, rel):
                    hm = erofs_metadata.metadata(src_fs, '/' + rel)
                    add(rel, hal_audio, _label(hm), hm['mode'] & 0o7777, hm['uid'], hm['gid'])
            print('Added audio config hda-alc257')

        # 2c4. Bare metal: root adb over TCP (no USB gadget on a laptop). Connect with `adb connect <ip>:5555`.
        # The image is a "user" build, so debuggable/unauthenticated adb has to be forced from init.
        if GFX_RADV and os.environ.get('GBOS_ADB', 'on') != 'off':
            add('etc/init/gbos-adb.rc', (
                b'on early-init\n'
                b'    setprop ro.debuggable 1\n'
                b'    setprop ro.adb.secure 0\n\n'
                b'on property:sys.boot_completed=1\n'
                b'    setprop service.adb.tcp.port 5555\n'
                b'    stop adbd\n'
                b'    start adbd\n'), 'vendor_configs_file')

        # 2c2. Bare-metal debugging without a screen: persist logcat and dmesg under /data/local/tmp (unencrypted),
        # readable afterwards from another OS. GBOS_PERSIST_LOG=off leaves it out.
        if os.environ.get('GBOS_PERSIST_LOG', 'on') != 'off':
            add('etc/init/gbos-persist-log.rc', (
                b'# debug: keep logs on /metadata (unencrypted, small: ~64 MB) so they survive a black screen\n'
                b'service gbos-persist-log /system/bin/sh -c "mkdir -p /metadata/gbos-log; '
                b'logcat -b all -v threadtime -f /metadata/gbos-log/gb-logcat.txt -r 4096 -n 3 \'*:W\' & '
                b'(dmesg -w | grep --line-buffered -E \'amdgpu|drm|nvme|rtw|usb |wlan|snd|hda|codec|sound|bluetooth|hci|btusb|btrtl|reset|timeout|fault|BUG|WARN|Oops|panic|firmware|hung|lockup|thermal|ACPI Error\' | head -c 6000000 > /metadata/gbos-log/gb-kmsg.txt) & '
                b'while true; do getprop > /metadata/gbos-log/gb-props.txt; sleep 30; done"\n'
                b'    class core\n'
                b'    user root\n'
                b'    group root log system\n'
                b'    seclabel u:r:shell:s0\n'
                b'    disabled\n\n'
                b'on post-fs-data\n'
                b'    start gbos-persist-log\n'), 'vendor_configs_file')
            print("Added gbos-persist-log")

        # 2d. Baseline rebuild of libz.so (see apply_system_update.py): the stock one traps on AVX-VNNI.
        libz_new = WORK / 'zlib-build/out/libz.so'
        if libz_new.exists() and os.environ.get('GBOS_STOCK_LIBZ') != '1' and _exists(src_fs, 'lib64/libz.so'):
            vz_m = erofs_metadata.metadata(src_fs, '/lib64/libz.so')
            add('lib64/libz.so', libz_new.read_bytes(), _label(vz_m), vz_m['mode'] & 0o7777, vz_m['uid'], vz_m['gid'])
            print("Replaced vendor lib64/libz.so with the baseline rebuild")

        # 2e. Chrome renders through ANGLE on Vulkan whenever the device declares Vulkan features, and
        # then its GPU process dies: the AHardwareBuffers it imports are virgl (GLES) resources that the
        # host's Venus context cannot see ("vkr: invalid res_id" -> ring fatal -> abort in
        # vn_ring_submit_locked). Un-declare the Vulkan features so Chrome falls back to GLES (virgl).
        # GBOS_VULKAN_FEATURES=on restores the original declarations.
        orig_perm_dir = WORK / 'orig-vulkan-permissions'
        vk_mode = os.environ.get('GBOS_VULKAN_FEATURES', 'on' if GFX_RADV else 'off')
        for vk_xml in ('android.hardware.vulkan.level.xml', 'android.hardware.vulkan.version.xml',
                       'android.hardware.vulkan.compute.xml', 'android.software.vulkan.deqp.level.xml'):
            rel = 'etc/permissions/' + vk_xml
            if not _exists(src_fs, rel):
                continue
            vk_m = erofs_metadata.metadata(src_fs, '/' + rel)
            if vk_mode == 'on':
                data = (orig_perm_dir / vk_xml).read_bytes()
            else:
                data = (b'<?xml version="1.0" encoding="utf-8"?>\n'
                        b'<!-- VM: Vulkan feature declaration removed so apps (Chrome) use GLES via virgl -->\n'
                        b'<permissions>\n</permissions>\n')
            add(rel, data, _label(vk_m), vk_m['mode'] & 0o7777, vk_m['uid'], vk_m['gid'])
        print(f"Vulkan feature declarations: {vk_mode}")

        # 2f. Pointer bridge: turns QEMU's usb-tablet (absolute, buttons, wheel) into inputs Android accepts
        # (a pen-style absolute pointer sized to the display, plus a wheel-only mouse). See
        # ~/src/isa-shim/vm-pointer-bridge. GBOS_POINTER_BRIDGE=off leaves it out.
        pb_bin = Path(__file__).resolve().parent.parent / 'pointer-bridge/vm-pointer-bridge'
        if pb_bin.exists() and os.environ.get('GBOS_POINTER_BRIDGE', 'on') != 'off':
            add('bin/vm-pointer-bridge', pb_bin.read_bytes(), 'vendor_file', 0o755, 0, 0)
            add('etc/init/vm-pointer-bridge.rc', (
                b'# VM: usb-tablet -> uinput (pointer + wheel); the helper waits for the tablet itself\n'
                b'service vm-pointer-bridge /vendor/bin/vm-pointer-bridge\n'
                b'    class late_start\n'
                b'    user root\n'
                b'    group root input uhid\n'
                b'    seclabel u:r:shell:s0\n'), 'vendor_configs_file')
            print("Added vm-pointer-bridge")

        # 3. init.android-desktop.rc
        if _exists(src_fs, 'etc/init/hw/init.android-desktop.rc'):
            orig_rc = dump_cat(src_fs, '/etc/init/hw/init.android-desktop.rc')
            # remove old prefix if already applied
            if b'import /system/etc/init/hw/init.zygote64.rc' in orig_rc:
                parts = orig_rc.split(b'import /system/etc/init/hw/init.zygote64.rc', 1)
                sub = parts[1]
                idx = sub.find(b'on early-init\n')
                if idx != -1:
                    idx2 = sub.find(b'on init\n', idx)
                    if idx2 != -1:
                        orig_rc = sub[idx2:]
            prefix = (
                b'import /system/etc/init/hw/init.zygote64.rc\n'
                b'import /system/etc/init/hw/init.boringssl.zygote64.rc\n\n'
                b'on early-init\n'
                b'    setprop ro.build.type user\n'
                b'    setprop ro.build.version.sdk 37\n'
                b'    setprop ro.build.version.release 17\n'
                b'    setprop ro.build.version.release_or_codename 17\n'
                b'    setprop ro.build.version.codename REL\n'
                b'    setprop ro.build.id CL3B.260622.271.R1\n'
                b'    setprop ro.build.version.incremental 16452207\n'
                b'    setprop ro.build.tags release-keys\n'
                b'    setprop ro.build.flavor lapis-user\n'
                b'    export MESA_GLES_VERSION_OVERRIDE 3.1\n'
                b'    export MESA_GLSL_VERSION_OVERRIDE 310\n'
                b'    export MESA_EXTENSION_OVERRIDE GL_OES_EGL_image_external\n'
                b'    export LIBGL_DEBUG verbose\n'
                b'    export MESA_DEBUG 1\n'
                b'    setprop ro.crypto.set_dun true\n'
                b'    setprop ro.sf.lcd_density 240\n'
                b'    setprop qemu.sf.lcd_density 240\n'
                b'    setprop ro.zygote zygote64\n'
                b'    setprop ro.dalvik.vm.enable_uffd_gc true\n'
                b'    setprop dalvik.vm.enable_uffd_gc true\n'
                b'    setprop dalvik.vm.extra-opts -Xgc:CMC\n'
                b'    setprop ro.zygote.disable_gl_preload true\n'
                b'    setprop ro.hardware.egl virgl\n'
                b'    setprop ro.hardware.vulkan ' + VK_HW.encode() + b'\n'
                b'    setprop ro.hwui.use_vulkan false\n'
                b'    setprop ro.opengles.version 196609\n'
                b'    setprop persist.graphics.egl virgl\n'
                b'    setprop debug.renderengine.backend skiaglthreaded\n'
                b'    setprop debug.renderengine.vulkan false\n'
                b'    setprop debug.renderengine.graphite false\n'
                b'    setprop debug.renderengine.graphite_desktop_optin false\n'
                b'    setprop debug.hwui.renderer skiagl\n'
                b'    setprop ro.gfx.angle.supported false\n'
                b'    setprop ro.surface_flinger.has_wide_color_display false\n'
                b'    setprop ro.surface_flinger.has_HDR_display false\n'
                b'    setprop ro.surface_flinger.supports_background_blur false\n'
                b'    setprop debug.renderengine.blur_algorithm none\n'
                b'    setprop debug.mesa.gles.version.override 3.1\n'
                b'    setprop debug.mesa.glsl.version.override 310\n'
                b'    setprop debug.mesa.debug 1\n'
                b'    setprop debug.mesa.log.level debug\n'
                b'    setprop debug.egl.trace 1\n'
                b'    setprop debug.egl.debug_proc 1\n'
                b'    setprop log.tag.SurfaceFlinger VERBOSE\n'
                b'    setprop log.tag.RenderEngine VERBOSE\n'
                b'    setprop log.tag.Skia VERBOSE\n'
                b'    setprop log.tag.skia VERBOSE\n'
                b'    setprop debug.ld.all dlerror,dlopen\n\n'
            )
            rc_m = erofs_metadata.metadata(src_fs, '/etc/init/hw/init.android-desktop.rc')
            add('etc/init/hw/init.android-desktop.rc', gfx_fix(prefix) + orig_rc, _label(rc_m), rc_m['mode'] & 0o7777, rc_m['uid'], rc_m['gid'])

        # 3.5 build.prop
        orig_prop = dump_cat(src_fs, '/build.prop').decode()
        prop_overrides = {
            'ro.hardware.vulkan': VK_HW,
            'ro.hardware.egl': 'virgl',
            'ro.hwui.use_vulkan': 'false',
            'ro.opengles.version': '196609',
            'ro.sf.lcd_density': '240',
            'debug.renderengine.backend': 'skiaglthreaded',
            'debug.renderengine.vulkan': 'false',
            'debug.renderengine.graphite_desktop_optin': 'false',
            'debug.hwui.renderer': 'skiagl',
            'ro.gfx.angle.supported': 'false',
            'ro.surface_flinger.has_wide_color_display': 'false',
            'ro.surface_flinger.has_HDR_display': 'false',
            'ro.surface_flinger.supports_background_blur': 'false',
            'ro.crypto.set_dun': 'true',
            'debug.mesa.gles.version.override': '3.1',
            'debug.mesa.glsl.version.override': '310',
            'debug.mesa.debug': '1',
            'debug.mesa.log.level': 'debug',
            'debug.egl.trace': '1',
            'debug.egl.debug_proc': '1',
            'log.tag.SurfaceFlinger': 'VERBOSE',
            'log.tag.RenderEngine': 'VERBOSE',
            'ro.dalvik.vm.enable_uffd_gc': 'true',
            'dalvik.vm.enable_uffd_gc': 'true',
            'dalvik.vm.extra-opts': '-Xgc:CMC',
            'ro.zygote.disable_gl_preload': 'true',
        }
        if GFX_RADV:
            for k, v in (('ro.hardware.egl', 'angle'), ('ro.hwui.use_vulkan', 'true'), ('debug.renderengine.backend', 'skiavkthreaded'),
                         ('debug.renderengine.vulkan', 'true'), ('debug.hwui.renderer', 'skiavk'), ('ro.gfx.angle.supported', 'true')):
                prop_overrides[k] = v
        FORBIDDEN_PROPS = {
            'ro.vendor.boot_security_patch',
            'LIBGL_DEBUG',
            'MESA_DEBUG',
            'ro.build.type',
            'ro.build.version.sdk',
            'ro.build.version.release',
            'ro.build.version.release_or_codename',
            'ro.build.version.codename',
            'ro.build.id',
            'ro.build.version.incremental',
            'ro.build.tags',
            'ro.build.flavor',
            'qemu.sf.lcd_density',
        }
        prop_lines = [
            l for l in orig_prop.splitlines()
            if l.split('=', 1)[0].strip() not in prop_overrides
            and l.split('=', 1)[0].strip() not in FORBIDDEN_PROPS
        ]
        prop_lines += [f'{k}={v}' for k, v in prop_overrides.items()]
        new_prop = ('\n'.join(prop_lines) + '\n').encode()
        p_m = erofs_metadata.metadata(src_fs, '/build.prop')
        add('build.prop', new_prop, _label(p_m), p_m['mode'] & 0o7777, p_m['uid'], p_m['gid'])

        # 4. NEUTERED firmware tools
        for path, data in NEUTERED.items():
            if _exists(src_fs, path):
                mm = erofs_metadata.metadata(src_fs, '/' + path)
                add(path, data, _label(mm), mm['mode'] & 0o7777, mm['uid'], mm['gid'])

        # 4b. AMD bare metal: RADV + libdrm_amdgpu, and minigbm (gralloc/gbm) with its dumb backend enabled for "amdgpu"
        if GFX_RADV:
            rt = WORK / 'radv-runtime'
            for rel, fn in (('lib64/hw/vulkan.radv.so', 'vulkan.radv.so'), ('lib64/libdrm_amdgpu.so', 'libdrm_amdgpu.so')):
                add(rel, (rt / fn).read_bytes(), 'same_process_hal_file', 0o644, 0, 0)
            for fn in ('libminigbm_gralloc.so', 'libgbm.so'):
                mm = erofs_metadata.metadata(src_fs, '/lib64/' + fn)
                add('lib64/' + fn, (rt / fn).read_bytes(), _label(mm), mm['mode'] & 0o7777, mm['uid'], mm['gid'])
            # Firmware that is loaded after the initrd is gone (kernel looks in /vendor/firmware via firmware_class.path):
            # Realtek Bluetooth (USB 0bda:c123 = RTL8822C) and a copy of the Wi-Fi / GPU blobs for re-loads (resume).
            for rel, src in (('firmware/rtl_bt/rtl8822cu_fw.bin', '/lib/firmware/rtl_bt/rtl8822cu_fw.bin'),
                             ('firmware/rtl_bt/rtl8822cu_config.bin', '/lib/firmware/rtl_bt/rtl8761bu_config.bin'),
                             ('firmware/rtw88/rtw8822c_fw.bin', '/lib/firmware/rtw88/rtw8822c_fw.bin')):
                if os.path.exists(src):
                    dirs.setdefault(os.path.dirname(rel), (0o755, 0, 0, 'vendor_file'))
                    add(rel, Path(src).read_bytes(), 'vendor_file', 0o644, 0, 0)
                    print('Added', rel)
            print("Added RADV runtime and patched minigbm (GBOS_GFX=radv)")

        # 5. Patched Mesa VirGL Libraries (GLES 3 context support)
        work_dir = WORK
        egl_raw = bytearray((work_dir / 'libEGL_virgl.so').read_bytes())
        # A. EGL 1.5 in eglInitialize
        egl_raw[0x16b18] = 0x0f
        # B. Accept 0x31BD unconditionally
        egl_raw[0x1fee7:0x1feee] = bytes([0xe9, 0x1f, 0x02, 0x00, 0x00, 0x90, 0x90])
        # C. Accept 0x3138 unconditionally
        egl_raw[0x200f0:0x200f7] = bytes([0xeb, 0x19, 0x90, 0x90, 0x90, 0x90, 0x90])
        # D. Bypass adding RESET_STRATEGY to DRI context attribs
        egl_raw[0x2551f:0x25521] = bytes([0xeb, 0x16])
        add('lib64/egl/libEGL_virgl.so', bytes(egl_raw), 'same_process_hal_file', 0o644, 0, 0)
        print("Patched lib64/egl/libEGL_virgl.so added to overlay.")

        gallium_raw = bytearray((work_dir / 'libgallium_dri.so').read_bytes())
        # A. Bypass max_gl_es2_version check in validate_context_version
        off1 = 0x9746ab - 0x4000
        gallium_raw[off1:off1+6] = bytes([0x90, 0x90, 0x90, 0x90, 0x90, 0x90])
        # B. Bypass allowed_attribs check in dri_create_context
        off2 = 0x9700f6 - 0x4000
        gallium_raw[off2:off2+2] = bytes([0xeb, 0x23])
        # C. Force GLES 3.1 in compute_version_es2 (0xbc2230 -> 0xbc2f06 returning 31)
        off3 = 0xbc2230 - 0x4000
        gallium_raw[off3:off3+11] = bytes.fromhex("83fa030f84cd0c000089d0")
        off4 = 0xbc2f06 - 0x4000
        gallium_raw[off4:off4+6] = bytes.fromhex("b81f000000c3")
        add('lib64/libgallium_dri.so', bytes(gallium_raw), 'same_process_hal_file', 0o644, 0, 0)
        print("Patched lib64/libgallium_dri.so (with GLES 3.1 force) added to overlay.")

        # 6. Patched keymint-service (forces boot_patchlevel fallback to 2026-09-01)
        donor_km = Path(LINUX_DIR) / 'donor/keymint-service'
        km_raw = bytearray(donor_km.read_bytes())
        km_raw[0x1411c3:0x1411cd] = bytes.fromhex("48b9323032362d30392d")
        km_raw[0x1411d3:0x1411da] = bytes.fromhex("6641c747083031")
        add('bin/hw/android.hardware.security.keymint-service', bytes(km_raw), 'hal_keymint_default_exec', 0o755, 0, 0)
        print("Patched bin/hw/android.hardware.security.keymint-service (boot_patchlevel 2026-09-01) added to overlay.")

        tar = tmp / 'overlay.tar'
        overlay_tar(tar, dir_entries(src_fs, files, dirs), files)

        new_fs = tmp / 'vendor.new.erofs'
        shutil.copyfile(src_fs, new_fs)
        cmd = ['mkfs.erofs', '--incremental=data', '--tar=f', '-x0', f'-b{BLOCK}', f'-T{build_time}']
        if zopt:
            cmd.append(zopt)
        cmd += [str(new_fs), str(tar)]
        print("Running mkfs.erofs incremental build...")
        subprocess.run(cmd, check=True)
        restore_root_xattr(new_fs, src_fs)
        subprocess.run(['fsck.erofs', str(new_fs)], check=True)

        new_size = new_fs.stat().st_size
        print(f"Rebuilt vendor EROFS size: {new_size} (previous {vendor_size})")
        new_bytes = new_fs.read_bytes()

        if new_size <= vendor_size:
            print("Writing back in-place to googlebook-security.bin...")
            with target.open('r+b') as f:
                f.seek(vendor_off)
                f.write(new_bytes)
                f.write(bytes(vendor_size - new_size))
        else:
            super_end = 26624000 * 512
            align = 1024 * 1024
            used = []
            for p in sup.names():
                if p == 'vendor_a': continue
                for s, sz in sup.layout(p):
                    used.append((s, s + sz))
            used.sort()
            cur = super_base + 1024 * 1024
            start = None
            length = (new_size + 511) // 512 * 512
            for s, e in used:
                aligned = (cur + align - 1) // align * align
                if s > aligned and s - aligned >= length:
                    start = aligned
                    break
                cur = max(cur, e)
            if start is None:
                aligned = (cur + align - 1) // align * align
                if aligned + length <= super_end:
                    start = aligned
                else:
                    raise SystemExit('not enough free space in super to relocate vendor_a')
            print(f"Relocating vendor_a to free offset {start}, length {length}...")
            with target.open('r+b') as f:
                f.seek(start)
                f.write(new_bytes)
                f.write(bytes(length - new_size))
            sup.set_single_extent('vendor_a', length // 512, (start - super_base) // 512)
            print("Updated lp metadata for relocated vendor_a!")

        print("Successfully updated vendor_a in target image!")
    finally:
        shutil.rmtree(tmp)

if __name__ == '__main__':
    main()
