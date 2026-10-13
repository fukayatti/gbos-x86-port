#!/bin/bash
# Build a bare-metal initrd: shipped initrd + our amdgpu stack + firmware. Output: $OUT (default ~/src/isa-shim/bm/initrd.bm)
set -euo pipefail
SRC=$HOME/src/gbos-vm-linux/image-security/initrd
M=$(ls -d $HOME/src/kernel/stage/modroot/lib/modules/*)
OUT=${OUT:-$HOME/src/isa-shim/bm/initrd.bm}
W=$(mktemp -d); trap 'rm -rf "$W"' EXIT
mkdir -p "$(dirname "$OUT")"; cd "$W"
(lz4 -dc "$SRC" 2>/dev/null || zcat "$SRC") | cpio -id --quiet
L=lib/modules
# Top-level modules to add; dependencies are resolved from our modules.dep (ours replace the vendor ones of the same name)
python3 - "$M" "$L" amdgpu rtw88_8822ce <<'PY'
import sys, os
M, L = sys.argv[1], sys.argv[2]; tops = sys.argv[3:]
dep = {}
for line in open(M + '/modules.dep'):
    k, _, v = line.partition(':'); dep[os.path.basename(k)[:-3]] = [os.path.basename(x)[:-3] for x in v.split()]
paths = {}
for r, _, fs in os.walk(M + '/kernel'):
    for f in fs:
        if f.endswith('.ko'): paths[f[:-3]] = os.path.join(r, f)
order = []
def visit(n):
    if n in order: return
    for d in dep.get(n, []): visit(d)
    order.append(n)
for t in tops: visit(t)
have = set(os.listdir(L))
with open(L + '/modules.dep', 'a') as dl, open(L + '/modules.load', 'a') as ld:
    for n in order:
        import shutil; shutil.copy(paths[n], L + '/' + n + '.ko')
        dl.write('/lib/modules/%s.ko:\n' % n); ld.write(n + '.ko\n')
print('added:', ' '.join(order))
PY
mkdir -p lib/firmware/amdgpu
for n in renoir green_sardine; do cp /lib/firmware/amdgpu/${n}_*.bin lib/firmware/amdgpu/; done
mkdir -p lib/firmware/rtw88; cp /lib/firmware/rtw88/rtw8822c_*.bin lib/firmware/rtw88/
find . | cpio -o -H newc --quiet | gzip -9 > "$OUT"
ls -l "$OUT"
