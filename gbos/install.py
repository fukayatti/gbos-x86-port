"""The only code in this project that writes to a disk.

Order of operations (everything before step 4 can be aborted without any change to the disk):
  1. re-compute the plan and refuse if it has problems
  2. show it and require the exact confirmation string  ERASE <disk> <partition numbers>
  3. back up the partition table (sgdisk --backup) - the restore command is printed
  4. delete the listed partitions, create the new ones, write the ESP and every partition image
     (never with sparse writes on a block device: stale data in skipped holes corrupts erofs)
  5. optionally add a UEFI boot entry
`--dry-run` prints every command and changes nothing (also works without root).
"""
import os, shlex, stat, subprocess, sys, time
from . import plan as planmod


def run(cmd, dry, check=True):
    print('  $', ' '.join(shlex.quote(c) for c in cmd))
    if dry:
        return 0
    return subprocess.run(cmd, check=check).returncode


def free_numbers(existing, count):
    used = set(existing)
    out, n = [], 1
    while len(out) < count:
        if n not in used:
            out.append(n)
        n += 1
    return out


def commands(pl, image, esp_img, disk, is_block):
    """-> list of (description, argv) in execution order, plus the partition numbers chosen for the new partitions."""
    existing = [p['num'] for p in planmod.disk_partitions(disk)]
    keep = [n for n in existing if n not in pl['erase']]
    nums = free_numbers(keep, len(pl['new']))
    cmds = []
    for n in pl['erase']:
        cmds.append((f'delete p{n}', ['sgdisk', '-d', str(n), disk]))
    for num, p in zip(nums, pl['new']):
        cmds.append((f"create p{num} {p['name']}", ['sgdisk', '-a', '1', '-n', f"{num}:{p['start']}:{p['last']}",
                                                     '-t', f"{num}:{p['type']}", '-c', f"{num}:{p['name']}", disk]))
    if is_block:
        cmds.append(('re-read the partition table', ['partprobe', disk]))
    for p in pl['new']:
        count = (p['last'] - p['start'] + 1) * 512
        src = esp_img if p['src'] is None else image
        skip = [] if p['src'] is None else [f"skip={p['src'] * 512}"]
        args = ['dd', f'if={src}', f'of={disk}', 'bs=1M', 'iflag=skip_bytes,count_bytes', 'oflag=seek_bytes',
                f"count={count if p['src'] is not None else os.path.getsize(esp_img)}", *skip, f"seek={p['start'] * 512}",
                'conv=fsync' + ('' if is_block else ',notrunc,sparse'), 'status=none']
        cmds.append((f"write {p['name']}", args))
    return cmds, nums


def install(a):
    disk = a.disk
    mode = os.stat(disk).st_mode
    is_block = stat.S_ISBLK(mode)
    if not is_block and not a.allow_file:
        sys.exit(f'{disk} is not a block device (use --allow-file for test images)')
    rs, re_ = (int(x) for x in a.region.split(':'))
    erase = set(a.erase or [])
    pl = planmod.make_plan(a.image, disk, (rs, re_), erase, a.allow_system_disk)
    planmod.show(pl)
    if pl['problems']:
        sys.exit('refusing: fix the problems above first')
    cmds, nums = commands(pl, a.image, a.esp, disk, is_block)
    want = 'ERASE %s %s' % (disk, ' '.join(str(n) for n in sorted(erase)) or 'nothing')
    print('\ncommands that will run:')
    for d, c in cmds:
        print(f'  - {d}')
    if a.dry_run:
        print('\n--dry-run: commands as they would be executed:')
        for _, c in cmds:
            run(c, True)
        return
    if is_block and os.geteuid() != 0:
        sys.exit('needs root (run with sudo), or use --dry-run')
    typed = a.confirm if a.confirm is not None else input(f'\nThis erases {len(erase)} partition(s) on {disk}.\nType exactly  {want}  to continue: ')
    if typed.strip() != want:
        sys.exit('confirmation did not match; nothing was changed')
    backup = a.backup or f'gbos-gpt-backup-{os.path.basename(disk)}-{int(time.time())}.bin'
    run(['sgdisk', f'--backup={backup}', disk], False)
    print(f'partition table saved; restore with:  sgdisk --load-backup={backup} {disk}')
    for d, c in cmds:
        print(f'* {d}')
        run(c, False)
    if a.boot_entry:
        esp_num = nums[0]
        run(['efibootmgr', '-c', '-d', disk, '-p', str(esp_num), '-L', 'Googlebook OS', '-l', '\\EFI\\BOOT\\BOOTX64.EFI'], False)
    print('done.')
