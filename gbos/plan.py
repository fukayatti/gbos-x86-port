"""Plan (and, only with explicit confirmation, perform) the placement of the Googlebook partitions on a disk.

Safety rules, in order of importance:
  * `plan` never writes anything.
  * `install` needs root, a plan that has been shown, and the exact confirmation string
    ``ERASE <disk> <partition numbers>`` typed by the user.
  * Partitions inside the target region are never deleted implicitly: every one of them must be listed with --erase.
  * Refuses when a partition to be erased is mounted, or when the region is too small, or when the disk is the one the
    running system boots from and --allow-system-disk was not given.
"""
import json, os, re, stat, subprocess
from . import gpt

ESP_SECTORS = 512 * 2048  # 512 MiB
ALIGN = 2048


def lsblk(disk):
    out = subprocess.run(['lsblk', '-J', '-b', '-o', 'NAME,PATH,TYPE,SIZE,PARTLABEL,FSTYPE,LABEL,MOUNTPOINTS', disk],
                         capture_output=True, text=True).stdout
    return json.loads(out)['blockdevices'][0].get('children', [])


def file_partitions(path):
    """Partitions of a disk *image file* (test harness) via `sgdisk -p`."""
    out = subprocess.run(['sgdisk', '-p', path], capture_output=True, text=True).stdout
    parts = []
    for m in re.finditer(r'^\s*(\d+)\s+(\d+)\s+(\d+)\s+\S+ \S+\s+\S+\s*(\S*)', out, re.M):
        n, s, e = int(m[1]), int(m[2]), int(m[3])
        parts.append({'num': n, 'path': f'{path}#{n}', 'start': s, 'last': e, 'sectors': e - s + 1, 'label': m[4] or None, 'fstype': None, 'mounts': []})
    return sorted(parts, key=lambda p: p['start'])


def disk_partitions(disk):
    if not stat.S_ISBLK(os.stat(disk).st_mode):
        return file_partitions(disk)
    base = os.path.basename(disk)
    parts = []
    for c in lsblk(disk):
        n = os.path.basename(c['path'])
        sysp = f'/sys/block/{base}/{n}'
        if not os.path.exists(sysp + '/start'):
            continue
        start = int(open(sysp + '/start').read())
        size = int(open(sysp + '/size').read())
        num = int(re.search(r'(\d+)$', n)[1])
        parts.append({'num': num, 'path': c['path'], 'start': start, 'last': start + size - 1, 'sectors': size, 'label': c.get('partlabel'),
                      'fstype': c.get('fstype'), 'mounts': [m for m in (c.get('mountpoints') or []) if m]})
    return sorted(parts, key=lambda p: p['start'])


def is_system_disk(disk):
    if not stat.S_ISBLK(os.stat(disk).st_mode):
        return False
    src = ''
    for line in open('/proc/mounts'):
        f = line.split()
        if f[1] in ('/', '/boot', '/boot/efi'):
            src += f[0] + ' '
    return os.path.basename(disk) in src


def layout(image, region_start, esp_sectors=ESP_SECTORS):
    """Where each image partition goes: ESP first, then the image's own layout, relative offsets preserved."""
    start = (region_start + ALIGN - 1) // ALIGN * ALIGN
    ps = gpt.partitions(image)
    lo = min(p['start'] for p in ps)
    base = start + esp_sectors
    out = [{'name': 'GBOSESP', 'type': 'C12A7328-F81F-11D2-BA4B-00A0C93EC93B', 'start': start, 'last': start + esp_sectors - 1, 'src': None}]
    for p in ps:
        out.append({'name': p['name'], 'type': p['type'], 'start': base + p['start'] - lo, 'last': base + p['last'] - lo, 'src': p['start']})
    return out


def make_plan(image, disk, region, erase, allow_system=False):
    rs, re_ = region
    existing = disk_partitions(disk)
    inside = [p for p in existing if p['start'] <= re_ and p['last'] >= rs]
    problems, notes = [], []
    new = layout(image, rs)
    need_last = max(p['last'] for p in new)
    if need_last > re_:
        problems.append(f'region too small: needs sector {need_last}, region ends at {re_} ({(need_last - re_) * 512 / 2**30:.1f} GiB short)')
    for p in inside:
        if p['num'] not in erase:
            problems.append(f"{p['path']} ({p['label'] or p['fstype'] or 'no label'}) is inside the region: add --erase {p['num']} to confirm you want it erased")
        elif p['mounts']:
            problems.append(f"{p['path']} is mounted at {', '.join(p['mounts'])}")
    for n in erase:
        if n not in [p['num'] for p in inside]:
            problems.append(f'--erase {n}: no such partition inside the region (refusing to touch partitions outside it)')
    if is_system_disk(disk) and not allow_system:
        problems.append(f'{disk} holds the running system; pass --allow-system-disk if you really intend to repartition it')
    others = [p for p in existing if p not in inside]
    return {'disk': disk, 'region': [rs, re_], 'erase': sorted(erase), 'new': new, 'untouched': [p['path'] for p in others],
            'problems': problems, 'notes': notes}


def show(plan):
    print(f"disk {plan['disk']}  region sectors {plan['region'][0]}..{plan['region'][1]}")
    print('untouched partitions:', ' '.join(plan['untouched']) or '-')
    print('to be ERASED        :', ' '.join(f'p{n}' for n in plan['erase']) or '-')
    print(f"to be created       : {len(plan['new'])} partitions, first {plan['new'][0]['name']} @{plan['new'][0]['start']}, last {plan['new'][-1]['name']} ends @{plan['new'][-1]['last']}")
    for pr in plan['problems']:
        print('PROBLEM:', pr)
    if not plan['problems']:
        print('plan is consistent. nothing has been written.')
