#!/usr/bin/env python3
"""gbos_detect: inspect this machine from a running Linux and write a hardware profile for the Googlebook port.

Prototype. Reads only (no root needed for most of it). Writes:
  <out>/profile.json        DMI, boot-device PCI path, GPU / Wi-Fi / Bluetooth / audio drivers and the firmware they need
  <out>/audio_config.xml    draft of /vendor/etc/audio/<dir>/audio_config.xml for the dras audio HAL (HD-Audio machines)
  <out>/mixer_paths.xml     draft mixer paths built from the controls of the analog card
  <out>/cmdline.txt         kernel command line pieces (boot_devices, ALSA card ids)

usage: gbos_detect.py [--disk nvme0n1] [--out ./profile]
"""
import argparse, glob, json, os, re, subprocess, sys


def sh(*cmd):
    try:
        return subprocess.run(cmd, capture_output=True, text=True, timeout=20).stdout
    except Exception:
        return ''


def rd(path, default=''):
    try:
        return open(path, errors='replace').read().strip()
    except OSError:
        return default


# ---------------------------------------------------------------- DMI
def dmi():
    keys = ('sys_vendor', 'product_name', 'product_version', 'product_sku', 'board_name', 'bios_version')
    return {k: rd(f'/sys/class/dmi/id/{k}') for k in keys}


# ---------------------------------------------------------------- boot device
def boot_devices(disk):
    """androidboot.boot_devices must be the PCI *bridge* above the storage controller (see docs/TROUBLESHOOTING.md)."""
    real = os.path.realpath(f'/sys/block/{disk}/device').replace('/sys/devices/', '')
    comps = real.split('/')
    pci = [c for c in comps if re.fullmatch(r'[0-9a-f]{4}:[0-9a-f]{2}:[0-9a-f]{2}\.[0-9a-f]', c)]
    root = comps[0]
    endpoint = '/'.join([root] + pci)
    bridge = '/'.join([root] + pci[:-1]) if len(pci) > 1 else endpoint
    return {'disk': disk, 'sysfs': real, 'endpoint': endpoint, 'bridge': bridge}


# ---------------------------------------------------------------- PCI / USB
def pci_devices():
    out, cur = [], None
    for line in sh('lspci', '-nnk').splitlines():
        m = re.match(r'^(\S+) (.+?) \[([0-9a-f]{4})\]: (.+) \[([0-9a-f]{4}:[0-9a-f]{4})\]', line)
        if m:
            cur = {'slot': m[1], 'class': m[2], 'class_id': m[3], 'name': m[4], 'id': m[5], 'driver': None, 'modules': []}
            out.append(cur)
        elif cur and 'Kernel driver in use:' in line:
            cur['driver'] = line.split(':', 1)[1].strip()
        elif cur and 'Kernel modules:' in line:
            cur['modules'] = [x.strip() for x in line.split(':', 1)[1].split(',')]
    return out


def usb_devices():
    out = []
    for d in sorted(glob.glob('/sys/bus/usb/devices/*')):
        vid, pid = rd(d + '/idVendor'), rd(d + '/idProduct')
        if not vid or os.path.basename(d).startswith('usb'):
            continue
        drivers = sorted({os.path.basename(os.path.realpath(i + '/driver'))
                          for i in glob.glob(d + ':*') if os.path.exists(i + '/driver')})
        classes = sorted({rd(i + '/bInterfaceClass') for i in glob.glob(d + ':*')})
        out.append({'id': f'{vid}:{pid}', 'product': rd(d + '/product'), 'drivers': drivers, 'interface_classes': classes})
    return out


# ---------------------------------------------------------------- modules / firmware
def module_closure(mod):
    deps = [os.path.basename(l.split()[-1]).replace('.ko', '').replace('.zst', '').replace('.xz', '')
            for l in sh('modprobe', '--show-depends', mod).splitlines() if l.startswith('insmod')]
    return deps or [mod]


def firmware_for(mods):
    found, seen = {}, set()
    for m in mods:
        for fw in sh('modinfo', '-F', 'firmware', m).split():
            if fw in seen:
                continue
            seen.add(fw)
            for ext in ('', '.zst', '.xz'):
                p = f'/lib/firmware/{fw}{ext}'
                if os.path.exists(p):
                    found[fw] = os.path.realpath(p)
                    break
    return found


def loaded_firmware():
    """Firmware files the running kernel actually asked for (best effort: needs permission to read the kernel log)."""
    log = sh('dmesg')
    return sorted(set(re.findall(r'(?:loading|Loading firmware:?|firmware:?|Direct firmware load for)\s+([\w./-]+\.(?:bin|fw|ucode|mbn|cis))', log)))


# ---------------------------------------------------------------- audio
def alsa_cards():
    cards = []
    for line in rd('/proc/asound/cards').splitlines():
        m = re.match(r'\s*(\d+) \[(\S+)\s*\]: (\S+) - (.+)', line)
        if not m:
            continue
        n = int(m[1])
        codec = ''
        for c in sorted(glob.glob(f'/proc/asound/card{n}/codec#*')):
            mm = re.search(r'^Codec: (.+)$', rd(c), re.M)
            codec = mm[1] if mm else codec
        pcms = []
        for p in sorted(glob.glob(f'/proc/asound/card{n}/pcm*')):
            info = rd(p + '/info')
            pcms.append({'dev': int(re.search(r'pcm(\d+)', p)[1]), 'dir': p[-1], 'name': (re.search(r'^name: (.+)$', info, re.M) or [0, ''])[1]})
        cards.append({'index': n, 'id': m[2], 'driver': m[3], 'name': m[4], 'codec': codec, 'pcms': pcms,
                      'is_hdmi': 'HDMI' in codec or all('HDMI' in p['name'] for p in pcms if p['dir'] == 'p') and bool(pcms)})
    return cards


def mixer(card):
    ctls = {}
    for line in sh('amixer', '-c', str(card), 'contents').split('numid=')[1:]:
        name = re.search(r"name='([^']+)'", line)
        if not name:
            continue
        typ = (re.search(r'type=(\w+)', line) or [0, ''])[1]
        r = re.search(r'min=(-?\d+),max=(-?\d+)', line)
        ctls[name[1]] = {'type': typ, 'min': int(r[1]) if r else None, 'max': int(r[2]) if r else None}
    return ctls


def input_jacks(card_name):
    names = re.findall(r'^N: Name="([^"]+)"', rd('/proc/bus/input/devices'), re.M)
    return [n for n in names if n.startswith(card_name) and ('Headphone' in n or 'Mic' in n) and 'HDMI' not in n]


def audio_files(cards, new_ids):
    analog = next((c for c in cards if not c['is_hdmi'] and any(p['dir'] == 'p' for p in c['pcms'])), None)
    if not analog:
        return None, None, {}
    cid = new_ids.get(analog['index'], analog['id'])
    pb = next(p['dev'] for p in analog['pcms'] if p['dir'] == 'p')
    cap = next((p['dev'] for p in analog['pcms'] if p['dir'] == 'c'), None)
    jacks = input_jacks(analog['name'].replace('HD-Audio Generic', 'HD-Audio Generic'))
    hp = next((j for j in jacks if 'Headphone' in j), None)
    mic = next((j for j in jacks if 'Mic' in j), None)
    x = ["<?xml version='1.0' encoding='utf-8'?>", '<audio_config>', f'\t<SoundCardName>hw:{cid}</SoundCardName>', '\t<HiFi>',
         f'\t\t<Speaker>\n\t\t\t<PlaybackPCM>hw:{cid},{pb}</PlaybackPCM>\n\t\t</Speaker>',
         f'\t\t<Headphone>\n\t\t\t<PlaybackPCM>hw:{cid},{pb}</PlaybackPCM>' + (f'\n\t\t\t<JackDev>{hp}</JackDev>\n\t\t\t<JackSwitch>2</JackSwitch>' if hp else '') + '\n\t\t</Headphone>']
    if cap is not None:
        x.append(f'\t\t<InternalMic>\n\t\t\t<CapturePCM>hw:{cid},{cap}</CapturePCM>\n\t\t\t<IntrinsicSensitivity>-2600</IntrinsicSensitivity>\n\t\t\t<RampUpMs>100</RampUpMs>\n\t\t</InternalMic>')
        x.append(f'\t\t<Mic>\n\t\t\t<CapturePCM>hw:{cid},{cap}</CapturePCM>\n\t\t\t<CaptureMixerElem>Headset Mic</CaptureMixerElem>' + (f'\n\t\t\t<JackDev>{mic}</JackDev>' if mic else '') + '\n\t\t</Mic>')
    x += ['\t</HiFi>', '</audio_config>', '']
    ctl = mixer(analog['index'])
    m = ["<?xml version='1.0' encoding='utf-8'?>", '<mixer>']
    if 'Auto-Mute Mode' in ctl:
        m.append('\t<ctl name="Auto-Mute Mode" value="Disabled" />')
    for n in ('Master Playback Switch',):
        if n in ctl:
            m.append(f'\t<ctl name="{n}" value="1" />')
    for n, c in ctl.items():
        if n.endswith('Playback Volume') and c['max'] is not None and n in ('Master Playback Volume', 'PCM Playback Volume', 'Speaker Playback Volume', 'Headphone Playback Volume'):
            v = ' '.join([str(c['max'])] * (1 if n.startswith('Master') else 2))
            m.append(f'\t<ctl name="{n}" value="{v}" />')
    for n in ('Speaker Playback Switch', 'Headphone Playback Switch'):
        if n in ctl:
            m.append(f'\t<ctl name="{n}" value="0 0" />')
    if 'Capture Switch' in ctl:
        m.append('\t<ctl name="Capture Switch" value="1 1" />')
    if 'Capture Volume' in ctl and ctl['Capture Volume']['max']:
        m.append(f'\t<ctl name="Capture Volume" value="{ctl["Capture Volume"]["max"] * 2 // 3} {ctl["Capture Volume"]["max"] * 2 // 3}" />')
    for name, key in (('Speaker', 'Speaker Playback Switch'), ('Headphone', 'Headphone Playback Switch')):
        m.append(f'\t<path name="{name}">' + (f'\n\t\t<ctl name="{key}" value="1 1" />\n\t' if key in ctl else '') + '</path>')
    for name, key, v in (('InternalMic', 'Internal Mic Boost Volume', ctl.get('Internal Mic Boost Volume')),
                         ('Mic', 'Headset Mic Boost Volume', ctl.get('Headset Mic Boost Volume'))):
        if v and v['max'] is not None:
            val = v['max'] - (1 if name == 'InternalMic' else 0)
            m.append(f'\t<path name="{name}">\n\t\t<ctl name="{key}" value="{val} {val}" />\n\t</path>')
    m += ['</mixer>', '']
    return '\n'.join(x), '\n'.join(m), {'analog_card': analog['index'], 'card_id': cid}


def main():
    ap = argparse.ArgumentParser()
    ap.add_argument('--disk', default='nvme0n1')
    ap.add_argument('--out', default='./profile')
    a = ap.parse_args()
    os.makedirs(a.out, exist_ok=True)

    prof = {'dmi': dmi(), 'boot': boot_devices(a.disk), 'pci': pci_devices(), 'usb': usb_devices(), 'alsa': alsa_cards()}

    def pick(pred):
        return [d for d in prof['pci'] if pred(d)]
    gpu = pick(lambda d: d['class_id'] in ('0300', '0302', '0380'))
    wifi = pick(lambda d: d['class_id'] == '0280')
    bt = [u for u in prof['usb'] if 'btusb' in u['drivers']]
    prof['gpu'] = [{'slot': d['slot'], 'name': d['name'], 'driver': d['driver']} for d in gpu]
    prof['wifi'] = [{'slot': d['slot'], 'name': d['name'], 'driver': d['driver']} for d in wifi]
    prof['bluetooth'] = [{'usb_id': u['id'], 'product': u['product'], 'drivers': u['drivers']} for u in bt]
    mods = {}
    for d in gpu + wifi:
        if d['driver']:
            mods[d['driver']] = module_closure(d['driver'])
    for u in bt:
        mods['btusb'] = module_closure('btusb')
    prof['modules'] = mods
    allm = sorted({m for v in mods.values() for m in v})
    prof['firmware'] = firmware_for(allm)   # everything the modules *could* load (broad)
    prof['firmware_requested_at_runtime'] = loaded_firmware()  # what this boot really asked for; run as root so dmesg is readable
    if os.geteuid() != 0:
        print('note: run as root to also learn which firmware files were actually requested (narrows the broad list)')

    cards = prof['alsa']
    # pin ALSA card ids: HDMI and analog HD-Audio cards would otherwise both be called "Generic" (see docs)
    ids = {c['index']: ('HDMIAMD' if c['is_hdmi'] else 'Analog') for c in cards if c['driver'] == 'HDA-Intel'}
    ax, mx, info = audio_files(cards, ids)
    prof['audio'] = {**info, 'pinned_ids': ids}
    if ax:
        open(f'{a.out}/audio_config.xml', 'w').write(ax)
        open(f'{a.out}/mixer_paths.xml', 'w').write(mx)
    ordered = [ids[c['index']] for c in sorted(cards, key=lambda c: c['index']) if c['index'] in ids]
    cmd = [f"androidboot.boot_devices={prof['boot']['bridge']}"]
    if ordered:
        cmd += [f'snd_hda_intel.index={",".join(str(i) for i in range(len(ordered)))}', f'snd_hda_intel.id={",".join(ordered)}']
    open(f'{a.out}/cmdline.txt', 'w').write(' '.join(cmd) + '\n')
    json.dump(prof, open(f'{a.out}/profile.json', 'w'), indent=2, ensure_ascii=False)
    print(f"{prof['dmi']['sys_vendor']} {prof['dmi']['product_name']} -> {a.out}/")
    print(' boot_devices :', prof['boot']['bridge'])
    print(' gpu          :', [(g['driver'], g['name']) for g in prof['gpu']])
    print(' wifi         :', [(w['driver'], w['name']) for w in prof['wifi']])
    print(' bluetooth    :', [(b['usb_id'], b['product']) for b in prof['bluetooth']])
    print(' firmware     :', len(prof['firmware']), 'files found on this host')
    print(' audio        :', info)
    print(' cmdline      :', ' '.join(cmd))


if __name__ == '__main__':
    sys.exit(main())
