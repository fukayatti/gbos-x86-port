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
    compress_option, _sb_fields, _label, _exists, BLOCK,
    _shared_xattr_offset, update_sb_checksum
)
import struct

def restore_system_root_xattr(image, source):
    sb, bs, meta, _xbase, root_nid = _sb_fields(source)
    src_pos = meta * bs + root_nid * 32
    with source.open('rb') as f:
        f.seek(src_pos)
        src_inode = f.read(96)
    src_fmt = struct.unpack_from('<H', src_inode, 0)[0]
    with source.open('rb') as f:
        f.seek(src_pos + (64 if src_fmt & 1 else 32))
        header = bytearray(f.read(12))
    header[4] = 1

    _sb2, bs2, meta2, _x2, root_nid2 = _sb_fields(image)
    pos = meta2 * bs2 + root_nid2 * 32
    with image.open('r+b') as f:
        f.seek(pos)
        inode = bytearray(f.read(96))
        fmt = struct.unpack_from('<H', inode, 0)[0]
        struct.pack_into('<H', inode, 4, 16877) # S_IFDIR | 0755
        if fmt & 1:
            struct.pack_into('<I', inode, 24, 0)
            struct.pack_into('<I', inode, 28, 0)
        else:
            struct.pack_into('<H', inode, 24, 0)
            struct.pack_into('<H', inode, 26, 0)
        f.seek(pos)
        f.write(inode)
        off = _shared_xattr_offset(image, value=b'u:object_r:rootfs:s0')
        if off % 4:
            raise ValueError('shared xattr offset not 4-byte aligned')
        f.seek(pos + (64 if fmt & 1 else 32))
        f.write(bytes(header) + struct.pack('<I', off // 4))
    update_sb_checksum(image)
    print("Repaired system root xattr to u:object_r:rootfs:s0")

def main():
    target = Path(LINUX_DIR) / 'image-security/googlebook-security.bin'
    super_base = 746586112 # 1458176 * 512
    sup = lp_tools.Super(target, super_base)
    system = sup.layout('system_a')
    system_off, system_size = system[0]
    print(f"system_a offset: {system_off}, size: {system_size}")

    tmp = Path(tempfile.mkdtemp(prefix='patch-system-sf-'))
    try:
        src_fs = tmp / 'system.erofs'
        print("Extracting system_a from target image...")
        with target.open('rb') as f:
            f.seek(system_off + 1024)
            sb = f.read(128)
            blocks = int.from_bytes(sb[36:40], 'little')
            fs_size = blocks << sb[12]
            build_time = int.from_bytes(sb[24:32], 'little')
            zopt = compress_option(sb)
            f.seek(system_off)
            remaining = fs_size
            with src_fs.open('wb') as g:
                while remaining:
                    b = f.read(min(remaining, 1 << 22))
                    if not b: break
                    g.write(b)
                    remaining -= len(b)
        print(f"Extracted system.erofs ({fs_size} bytes)")

        files = {}
        dirs = {}
        def add(rel, data, label, mode=0o755, uid=0, gid=0):
            files[rel] = (data, label, mode, uid, gid)

        work_dir = WORK
        sf_patched = (work_dir / 'surfaceflinger.patched.elf').read_bytes()
        sf_m = erofs_metadata.metadata(src_fs, '/system/bin/surfaceflinger')
        print(f"Original /system/bin/surfaceflinger metadata: {sf_m}")
        add('system/bin/surfaceflinger', sf_patched, _label(sf_m), sf_m['mode'] & 0o7777, sf_m['uid'], sf_m['gid'])
        print("Added patched system/bin/surfaceflinger to overlay.")

        ar_patched = (work_dir / 'libandroid_runtime.patched.so').read_bytes()
        ar_m = erofs_metadata.metadata(src_fs, '/system/lib64/libandroid_runtime.so')
        add('system/lib64/libandroid_runtime.so', ar_patched, _label(ar_m), ar_m['mode'] & 0o7777, ar_m['uid'], ar_m['gid'])
        print("Added patched system/lib64/libandroid_runtime.so to overlay.")

        orig_jar = (work_dir / 'framework.jar').read_bytes()
        fj_m = erofs_metadata.metadata(src_fs, '/system/framework/framework.jar')
        add('system/framework/framework.jar', orig_jar, _label(fj_m), fj_m['mode'] & 0o7777, fj_m['uid'], fj_m['gid'])
        print("Restored original system/framework/framework.jar to overlay.")


        init_patched = (work_dir / 'init.patched.elf').read_bytes()
        init_m = erofs_metadata.metadata(src_fs, '/system/bin/init')
        add('system/bin/init', init_patched, _label(init_m), init_m['mode'] & 0o7777, init_m['uid'], init_m['gid'])
        print("Added patched system/bin/init (permissive SELinux) to overlay.")

        # TEMPORARY: restore original libandroid.so (earlier bisect left a stub in the image).
        orig_la = (WORK / 'scan/system_a/system/lib64/libandroid.so').read_bytes()
        la0_m = erofs_metadata.metadata(src_fs, '/system/lib64/libandroid.so')
        add('system/lib64/libandroid.so', orig_la, _label(la0_m), la0_m['mode'] & 0o7777, la0_m['uid'], la0_m['gid'])

        # TEMPORARY BISECT (revert afterwards): stand-in for libperfetto_framework_jni.so.
        if os.environ.get('GBOS_BISECT_PERFETTO'):
            small = dump_cat(src_fs, '/system/lib64/libjnigraphics.so')
            la_m = erofs_metadata.metadata(src_fs, '/system/lib64/libperfetto_framework_jni.so')
            add('system/lib64/libperfetto_framework_jni.so', small, _label(la_m), la_m['mode'] & 0o7777, la_m['uid'], la_m['gid'])
            print("BISECT: libperfetto_framework_jni.so replaced by libjnigraphics.so")
        else:
            # The overlay is applied on top of the current image, so an earlier BISECT stub would
            # stay forever (system_server then dies with UnsatisfiedLinkError in PerfettoTrace).
            orig_pf = (WORK / 'scan/system_a/system/lib64/libperfetto_framework_jni.so').read_bytes()
            pf_m = erofs_metadata.metadata(src_fs, '/system/lib64/libperfetto_framework_jni.so')
            add('system/lib64/libperfetto_framework_jni.so', orig_pf, _label(pf_m), pf_m['mode'] & 0o7777, pf_m['uid'], pf_m['gid'])
            print("Restored original libperfetto_framework_jni.so")

        # The shipped libz.so was built with a Panther-Lake-class -march, so its Adler-32 loop is
        # auto-vectorized with AVX-VNNI (vpdpwssd). Every APK/JAR read hits it, which cost ~330k
        # #UD traps per second in this VM. Use a rebuild of the same sources (AOSP external/zlib,
        # 1.3.2.1-motley, same 98 exports) for baseline x86-64: see ~/src/zlib-build/build.sh.
        libz_new = WORK / 'zlib-build/out/libz.so'
        if libz_new.exists() and os.environ.get('GBOS_STOCK_LIBZ') != '1':
            libz_m = erofs_metadata.metadata(src_fs, '/system/lib64/libz.so')
            add('system/lib64/libz.so', libz_new.read_bytes(), _label(libz_m), libz_m['mode'] & 0o7777, libz_m['uid'], libz_m['gid'])
            print("Replaced system/lib64/libz.so with the baseline rebuild")

        # Patch init.zygote64.rc to include -Xgc:CMC (keep standard preload for shared libs)
        orig_zygote = dump_cat(src_fs, '/system/etc/init/hw/init.zygote64.rc').decode()
        new_zygote = orig_zygote.replace(' --enable-lazy-preload', '')
        if '-Xgc:CMC' not in new_zygote:
            new_zygote = new_zygote.replace(
                'service zygote /system/bin/app_process64 -Xzygote /system/bin --zygote',
                'service zygote /system/bin/app_process64 -Xzygote -Xgc:CMC /system/bin --zygote'
            )
        # Temporary debug flag: log JNI library loads to locate the silent zygote abort.
        if '-verbose:jni' not in new_zygote:
            new_zygote = new_zygote.replace('-Xgc:CMC ', '-Xgc:CMC -verbose:jni ', 1)
        new_zygote_bytes = new_zygote.encode()
        z_m = erofs_metadata.metadata(src_fs, '/system/etc/init/hw/init.zygote64.rc')
        add('system/etc/init/hw/init.zygote64.rc', new_zygote_bytes, _label(z_m), z_m['mode'] & 0o7777, z_m['uid'], z_m['gid'])
        print("Added patched init.zygote64.rc (-Xgc:CMC) to overlay.")

        # Update system/build.prop
        orig_sys_prop = dump_cat(src_fs, '/system/build.prop').decode()
        sys_prop_extra_lines = [
            'ro.dalvik.vm.enable_uffd_gc=true',
            'dalvik.vm.enable_uffd_gc=true',
            'dalvik.vm.extra-opts=-Xgc:CMC',
            'ro.zygote.disable_gl_preload=true',
        ]
        prop_lines = [
            l for l in orig_sys_prop.splitlines()
            if not any(l.startswith(k) for k in [
                'ro.dalvik.vm.enable_uffd_gc',
                'dalvik.vm.enable_uffd_gc',
                'dalvik.vm.extra-opts',
                'ro.zygote.disable_gl_preload',
                '# ART UFFD'
            ])
        ]
        # EXPERIMENT toggle for the silent zygote abort: GBOS_NATIVE_BRIDGE=off disables the ARM->x86
        # translator in zygote (ro.dalvik.vm.native.bridge=0); =on restores libhoudini.so; unset = leave as is.
        nb = os.environ.get('GBOS_NATIVE_BRIDGE', '')
        if nb in ('off', 'on'):
            val = '0' if nb == 'off' else 'libhoudini.so'
            prop_lines = [('ro.dalvik.vm.native.bridge=' + val) if l.startswith('ro.dalvik.vm.native.bridge=') else l
                          for l in prop_lines]
            print(f"EXPERIMENT: ro.dalvik.vm.native.bridge={val}")
        new_sys_prop = ('\n'.join(prop_lines) + '\n# ART UFFD CMC GC & Graphics Configuration\n' + '\n'.join(sys_prop_extra_lines) + '\n').encode()
        p_m = erofs_metadata.metadata(src_fs, '/system/build.prop')
        add('system/build.prop', new_sys_prop, _label(p_m), p_m['mode'] & 0o7777, p_m['uid'], p_m['gid'])
        print("Added updated system/build.prop to overlay.")

        # Disable failing hardware daemons: gscd (TPM) and timberslide (ChromeOS EC)
        orig_gscd = dump_cat(src_fs, '/system/etc/init/gscd.rc').decode()
        if 'disabled' not in orig_gscd:
            orig_gscd = orig_gscd.replace('    group hsm\n', '    group hsm\n    disabled\n')
        new_gscd = orig_gscd.encode()
        gscd_m = erofs_metadata.metadata(src_fs, '/system/etc/init/gscd.rc')
        add('system/etc/init/gscd.rc', new_gscd, _label(gscd_m), gscd_m['mode'] & 0o7777, gscd_m['uid'], gscd_m['gid'])
        print("Added disabled system/etc/init/gscd.rc to overlay.")

        orig_ts = dump_cat(src_fs, '/system/etc/init/timberslide.rc').decode()
        if 'disabled' not in orig_ts:
            orig_ts = orig_ts.replace('   class core\n', '   class core\n   disabled\n')
        new_ts = orig_ts.encode()
        ts_m = erofs_metadata.metadata(src_fs, '/system/etc/init/timberslide.rc')
        add('system/etc/init/timberslide.rc', new_ts, _label(ts_m), ts_m['mode'] & 0o7777, ts_m['uid'], ts_m['gid'])
        print("Added disabled system/etc/init/timberslide.rc to overlay.")

        tar = tmp / 'overlay.tar'
        overlay_tar(tar, dir_entries(src_fs, files, dirs), files)

        new_fs = tmp / 'system.new.erofs'
        shutil.copyfile(src_fs, new_fs)
        cmd = ['mkfs.erofs', '--incremental=data', '--tar=f', '-x0', f'-b{BLOCK}', f'-T{build_time}']
        if zopt:
            cmd.append(zopt)
        cmd += [str(new_fs), str(tar)]
        print("Running mkfs.erofs incremental build for system_a...")
        subprocess.run(cmd, check=True)
        restore_system_root_xattr(new_fs, src_fs)
        subprocess.run(['fsck.erofs', str(new_fs)], check=True)

        new_size = new_fs.stat().st_size
        print(f"Rebuilt system EROFS size: {new_size} (partition size {system_size})")
        new_bytes = new_fs.read_bytes()

        if new_size <= system_size:
            print("Writing back in-place to googlebook-security.bin...")
            with target.open('r+b') as f:
                f.seek(system_off)
                f.write(new_bytes)
                f.write(bytes(system_size - new_size))
        else:
            super_end = 26624000 * 512
            align = 1024 * 1024
            used = []
            for p in sup.names():
                if p == 'system_a': continue
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
                    raise SystemExit('not enough free space in super to relocate system_a')
            print(f"Relocating system_a to free offset {start}, length {length}...")
            with target.open('r+b') as f:
                f.seek(start)
                f.write(new_bytes)
                f.write(bytes(length - new_size))
            sup.set_single_extent('system_a', length // 512, (start - super_base) // 512)
            print("Updated lp metadata for relocated system_a!")

        print("Successfully updated system_a in target image!")
    finally:
        shutil.rmtree(tmp)

if __name__ == '__main__':
    main()
