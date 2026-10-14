"""Build the bare-metal initrd from a hardware profile: shipped initrd + the profile's driver modules (with
dependencies) + the firmware those drivers need."""
import os, re, shutil, subprocess, tempfile
from .kconfig import RENAMES


def read_deps(moddir):
    dep = {}
    for line in open(os.path.join(moddir, 'modules.dep')):
        k, _, v = line.partition(':')
        dep[os.path.basename(k)[:-3]] = [os.path.basename(x)[:-3] for x in v.split()]
    paths = {}
    for r, _, fs in os.walk(os.path.join(moddir, 'kernel')):
        for f in fs:
            if f.endswith('.ko'):
                paths[f[:-3]] = os.path.join(r, f)
    return dep, paths


def pick_modules(profile, dep):
    """Top-level modules = the drivers the profile says are bound to GPU / Wi-Fi / Bluetooth hardware, renamed for the target kernel."""
    tops = []
    for key in ('gpu', 'wifi'):
        for d in profile.get(key, []):
            if d.get('driver'):
                tops.append(d['driver'])
    if profile.get('bluetooth'):
        tops.append('btusb')
    out = []
    for t in tops:
        t = t.replace('-', '_')
        t = RENAMES.get(t, t)
        cands = [t, t.replace('_', '-')]
        for c in cands:
            if c in dep:
                out.append(c)
                break
        else:
            raise SystemExit(f'driver {t!r} is not built in the target kernel (enable it with `gbos kconfig`)')
    return out


def closure(tops, dep):
    order = []

    def visit(n):
        if n in order:
            return
        for d in dep.get(n, []):
            visit(d)
        order.append(n)
    for t in tops:
        visit(t)
    return order


def firmware_files(profile, order):
    """Firmware the profile saw requested at runtime, plus whatever the chosen modules declare for the detected chip family."""
    want = set(profile.get('firmware_requested_at_runtime', []))
    avail = profile.get('firmware', {})
    out = {}
    for fw in want:
        if fw in avail:
            out[fw] = avail[fw]
    # drivers that request their firmware late (after the rootfs switch) are listed so the caller can put them in /vendor/firmware
    return out


def build(profile, moddir, shipped_initrd, out_path, hostfw='/lib/firmware', extra_fw=()):
    dep, paths = read_deps(moddir)
    tops = pick_modules(profile, dep)
    order = closure(tops, dep)
    fw = firmware_files(profile, order)
    work = tempfile.mkdtemp()
    try:
        cpio = subprocess.run(['lz4', '-dc', shipped_initrd], capture_output=True)
        if cpio.returncode:
            cpio = subprocess.run(['zcat', shipped_initrd], capture_output=True, check=True)
        subprocess.run(['cpio', '-id', '--quiet'], input=cpio.stdout, cwd=work, check=True)
        L = os.path.join(work, 'lib/modules')
        have = set(os.listdir(L))
        with open(os.path.join(L, 'modules.dep'), 'a') as dl, open(os.path.join(L, 'modules.load'), 'a') as ld:
            for n in order:
                shutil.copy(paths[n], os.path.join(L, n + '.ko'))
                dl.write(f'/lib/modules/{n}.ko:\n')
                ld.write(n + '.ko\n')
        for rel, src in list(fw.items()) + [(e, os.path.join(hostfw, e)) for e in extra_fw]:
            dst = os.path.join(work, 'lib/firmware', rel)
            os.makedirs(os.path.dirname(dst), exist_ok=True)
            shutil.copy(os.path.realpath(src), dst)
        files = subprocess.run(['find', '.'], cwd=work, capture_output=True, text=True).stdout.encode()
        packed = subprocess.run(['cpio', '-o', '-H', 'newc', '--quiet'], input=files, cwd=work, capture_output=True, check=True).stdout
        with open(out_path, 'wb') as f:
            f.write(subprocess.run(['gzip', '-9'], input=packed, capture_output=True, check=True).stdout)
    finally:
        shutil.rmtree(work, ignore_errors=True)
    return {'modules': order, 'firmware': sorted(fw)}
