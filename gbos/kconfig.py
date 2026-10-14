"""Map kernel module names to CONFIG_ symbols by scanning the Makefiles of a kernel source tree.

`obj-$(CONFIG_FOO) += bar.o`            -> bar  needs FOO
`bar-y := a.o b.o` / `bar-objs := ...`  -> composite modules are found through their obj- line
The target kernel can be older or newer than the host that ran gbos_detect, so a few known renames are tried too.
"""
import os, re

# host (new kernel) name -> target (6.18) name
RENAMES = {
    'rtw_8822ce': 'rtw88_8822ce', 'rtw_8822c': 'rtw88_8822c', 'rtw_pci': 'rtw88_pci', 'rtw_core': 'rtw88_core',
    'rtw_8821ce': 'rtw88_8821ce', 'rtw_8821c': 'rtw88_8821c', 'rtw_8723de': 'rtw88_8723de', 'rtw_8723d': 'rtw88_8723d',
    'rtw_8822be': 'rtw88_8822be', 'rtw_8822b': 'rtw88_8822b', 'rtw_usb': 'rtw88_usb', 'rtw_sdio': 'rtw88_sdio',
}

OBJ = re.compile(r'^\s*obj-\$\((CONFIG_\w+)\)\s*[:+]?=\s*(.+)$')


def _norm(n):
    return n.replace('-', '_')


def build_index(src):
    """module name -> set of CONFIG symbols, from every Makefile/Kbuild under drivers/, net/, sound/, crypto/, lib/, fs/."""
    idx = {}
    for top in ('drivers', 'net', 'sound', 'crypto', 'lib', 'fs'):
        for root, _, files in os.walk(os.path.join(src, top)):
            for f in files:
                if f not in ('Makefile', 'Kbuild'):
                    continue
                try:
                    lines = open(os.path.join(root, f), errors='replace').read().replace('\\\n', ' ').split('\n')
                except OSError:
                    continue
                for line in lines:
                    m = OBJ.match(line)
                    if not m:
                        continue
                    for tok in m[2].split():
                        if tok.endswith('.o'):
                            idx.setdefault(_norm(os.path.basename(tok)[:-2]), set()).add(m[1][7:])
    return idx


def configs_for(modules, idx):
    """-> (config symbols to set to =m, unresolved module names)"""
    syms, missing = set(), []
    for mod in modules:
        n = _norm(mod)
        for cand in (n, _norm(RENAMES.get(mod, mod)), _norm(RENAMES.get(n, n))):
            if cand in idx:
                syms |= idx[cand]
                break
        else:
            missing.append(mod)
    return sorted(syms), missing
