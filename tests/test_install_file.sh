#!/bin/bash
# End-to-end test of `gbos install` on a synthetic image and a disk *file* (no root, no real disk).
set -euo pipefail
cd "$(dirname "$0")/.."; T=$(mktemp -d); trap 'rm -rf "$T"' EXIT
truncate -s 600M "$T/image.bin"; sgdisk -o "$T/image.bin" >/dev/null
sgdisk -n 1:2048:+20M -c 1:boot_a "$T/image.bin" >/dev/null
sgdisk -n 2:0:+200M -c 2:super "$T/image.bin" >/dev/null
sgdisk -n 3:0:+100M -c 3:userdata "$T/image.bin" >/dev/null
python3 - "$T/image.bin" <<'PY'
import re, subprocess, sys
f = sys.argv[1]; out = subprocess.run(['sgdisk', '-p', f], capture_output=True, text=True).stdout
with open(f, 'r+b') as h:
    for m in re.finditer(r'^\s*(\d+)\s+(\d+)\s+(\d+)', out, re.M):
        n, s, e = map(int, m.groups()); h.seek(s * 512); h.write(bytes([n]) * 512 * 64); h.seek(e * 512); h.write(bytes([0x40 + n]) * 512)
PY
truncate -s 2G "$T/disk.img"; sgdisk -o "$T/disk.img" >/dev/null; sgdisk -n 1:2048:+100M -c 1:other "$T/disk.img" >/dev/null
truncate -s 64M "$T/esp.img"; mformat -i "$T/esp.img" -F -v GBOSESP ::
python3 -m gbos install --image "$T/image.bin" --esp "$T/esp.img" --disk "$T/disk.img" --allow-file --region 1000000:3800000 \
    --confirm "ERASE $T/disk.img nothing" --backup "$T/gpt.bak" >/dev/null
python3 - "$T" <<'PY'
import re, subprocess, sys
T = sys.argv[1]
def parts(f):
    out = subprocess.run(['sgdisk', '-p', f], capture_output=True, text=True).stdout
    return {m[4]: (int(m[2]), int(m[3])) for m in re.finditer(r'^\s*(\d+)\s+(\d+)\s+(\d+)\s+\S+ \S+\s+\S+\s*(\S*)', out, re.M)}
src, dst = parts(T + '/image.bin'), parts(T + '/disk.img')
a, b = open(T + '/image.bin', 'rb'), open(T + '/disk.img', 'rb')
for name, (s, e) in src.items():
    a.seek(s * 512); b.seek(dst[name][0] * 512); n = (e - s + 1) * 512
    assert a.read(n) == b.read(n), name
assert 'other' in dst and 'GBOSESP' in dst
print('ok: all partitions copied byte-for-byte, the unrelated partition is untouched')
PY
