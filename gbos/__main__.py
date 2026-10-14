import argparse, json, os, subprocess, sys
from . import kconfig, initrd, esp, plan, gpt

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


def cmd_esp(a):
    prof = json.load(open(a.profile))
    cmd = esp.build(prof, a.image, a.kernel, a.initrd, a.out, a.esp_mb, a.extra or '')
    print('kernel command line:'); print(' ', cmd)


def cmd_plan(a):
    if a.profile:
        c = json.load(open(a.profile)).get('cpu', {})
        if c.get('verdict') == 'unsupported':
            sys.exit('this CPU cannot run the image: missing ' + ', '.join(c['missing_required']))
        if c.get('verdict') == 'emulated':
            print('note: CPU lacks', ', '.join(c['to_emulate']), '-> gfni_emu.ko (instruction emulation) will be used')
    rs, re_ = (int(x) for x in a.region.split(':'))
    pl = plan.make_plan(a.image, a.disk, (rs, re_), set(a.erase or []), a.allow_system_disk)
    plan.show(pl)
    if a.json:
        json.dump(pl, open(a.json, 'w'), indent=1)
    sys.exit(1 if pl['problems'] else 0)


def main():
    ap = argparse.ArgumentParser(prog='gbos')
    sp = ap.add_subparsers(dest='cmd', required=True)
    p = sp.add_parser('detect'); p.add_argument('--disk', default='nvme0n1'); p.add_argument('--out', default='./profile'); p.set_defaults(f=cmd_detect)
    p = sp.add_parser('kconfig'); p.add_argument('--profile', required=True); p.add_argument('--kernel-src', required=True); p.add_argument('--out', default='./profile.config'); p.set_defaults(f=cmd_kconfig)
    p = sp.add_parser('initrd'); p.add_argument('--profile', required=True); p.add_argument('--moddir', required=True, help='lib/modules/<release> of the target kernel')
    p.add_argument('--shipped-initrd', required=True); p.add_argument('--out', required=True); p.set_defaults(f=cmd_initrd)
    p = sp.add_parser('esp'); p.add_argument('--profile', required=True); p.add_argument('--image', required=True); p.add_argument('--kernel', required=True)
    p.add_argument('--initrd', required=True); p.add_argument('--out', required=True); p.add_argument('--esp-mb', type=int, default=500); p.add_argument('--extra'); p.set_defaults(f=cmd_esp)
    p = sp.add_parser('plan', help='show what installing would do; writes nothing'); p.add_argument('--image', required=True); p.add_argument('--disk', required=True)
    p.add_argument('--region', required=True, help='START:END sectors (inclusive) that may be overwritten'); p.add_argument('--erase', type=int, nargs='*', help='partition numbers inside the region to erase')
    p.add_argument('--profile', help='profile.json (refuses when the CPU cannot run the image)'); p.add_argument('--allow-system-disk', action='store_true'); p.add_argument('--json'); p.set_defaults(f=cmd_plan)
    a = ap.parse_args()
    a.f(a)


main()
