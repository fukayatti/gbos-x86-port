import argparse, json, os, subprocess, sys
from . import kconfig, initrd

HERE = os.path.dirname(os.path.abspath(__file__))


def cmd_detect(a):
    sys.exit(subprocess.call([sys.executable, os.path.join(HERE, '..', 'tools', 'gbos_detect.py'), '--disk', a.disk, '--out', a.out]))


def cmd_kconfig(a):
    prof = json.load(open(a.profile))
    idx = kconfig.build_index(a.kernel_src)
    mods = sorted({m for v in prof['modules'].values() for m in v})
    syms, missing = kconfig.configs_for(mods, idx)
    frag = ''.join(f'CONFIG_{s}=m\n' for s in syms)
    open(a.out, 'w').write(frag)
    print(f'{len(syms)} symbols -> {a.out}')
    if missing:
        print('unresolved modules (need a manual mapping):', ' '.join(missing))


def cmd_initrd(a):
    prof = json.load(open(a.profile))
    r = initrd.build(prof, a.moddir, a.shipped_initrd, a.out)
    print('modules :', ' '.join(r['modules']))
    print('firmware:', ' '.join(r['firmware']) or '(none: run `gbos detect` as root to learn the requested firmware)')


def main():
    ap = argparse.ArgumentParser(prog='gbos')
    sp = ap.add_subparsers(dest='cmd', required=True)
    p = sp.add_parser('detect'); p.add_argument('--disk', default='nvme0n1'); p.add_argument('--out', default='./profile'); p.set_defaults(f=cmd_detect)
    p = sp.add_parser('kconfig'); p.add_argument('--profile', required=True); p.add_argument('--kernel-src', required=True); p.add_argument('--out', default='./profile.config'); p.set_defaults(f=cmd_kconfig)
    p = sp.add_parser('initrd'); p.add_argument('--profile', required=True); p.add_argument('--moddir', required=True, help='lib/modules/<release> of the target kernel')
    p.add_argument('--shipped-initrd', required=True); p.add_argument('--out', required=True); p.set_defaults(f=cmd_initrd)
    a = ap.parse_args()
    a.f(a)


main()
