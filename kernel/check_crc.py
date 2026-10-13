#!/usr/bin/env python3
"""Compare the symbol CRCs required by prebuilt modules (__versions) with our Module.symvers.
usage: check_crc.py Module.symvers dir-or-ko [...]"""
import sys, os, struct, glob
def versions(path):
    d = open(path, 'rb').read()
    if d[:4] != b'\x7fELF': return None
    shoff, = struct.unpack_from('<Q', d, 0x28); shentsize, shnum, shstrndx = struct.unpack_from('<HHH', d, 0x3a)
    secs = []
    for i in range(shnum):
        o = shoff + i * shentsize
        name, typ, flags, addr, off, size = struct.unpack_from('<IIQQQQ', d, o)
        secs.append((name, off, size))
    stroff = secs[shstrndx][1]
    for name, off, size in secs:
        n = d[stroff + name: d.index(b'\0', stroff + name)]
        if n == b'__versions':
            return [(struct.unpack_from('<Q', d, off + k)[0], d[off + k + 8: off + k + 64].split(b'\0')[0].decode()) for k in range(0, size, 64)]
    return []
sym = {}
for l in open(sys.argv[1]):
    p = l.split('\t')
    if len(p) > 2: sym[p[1]] = int(p[0], 16)
files = []
for a in sys.argv[2:]:
    files += glob.glob(a + '/**/*.ko', recursive=True) if os.path.isdir(a) else [a]
bad = missing = tot = 0; badmods = {}
for f in files:
    v = versions(f)
    if v is None: continue
    for crc, name in v:
        if name == 'module_layout': continue
        tot += 1
        if name not in sym: missing += 1; continue
        if sym[name] != crc & 0xffffffff: bad += 1; badmods.setdefault(os.path.basename(f), []).append(name)
print(f'modules={len(files)} required={tot} not-in-ours={missing} crc-mismatch={bad}')
for m, l in sorted(badmods.items()): print(' ', m, len(l), l[:3])
