"""Read the GPT of a Googlebook disk image (no external tools)."""
import hashlib, struct, uuid


def partitions(image):
    with open(image, 'rb') as f:
        f.seek(512)
        h = f.read(92)
        assert h[:8] == b'EFI PART', 'not a GPT image'
        n, sz = struct.unpack('<II', h[80:88])
        f.seek(1024)
        out = []
        for _ in range(n):
            e = f.read(sz)
            if e[:16] == b'\0' * 16:
                continue
            s, last = struct.unpack('<QQ', e[32:48])
            out.append({'name': e[56:128].decode('utf-16le').rstrip('\0'), 'start': s, 'last': last, 'sectors': last - s + 1,
                        'type': str(uuid.UUID(bytes_le=e[:16])).upper()})
    return sorted(out, key=lambda p: p['start'])


def vbmeta_digest(image, size=6720, name='vbmeta_a'):
    p = next(p for p in partitions(image) if p['name'] == name)
    with open(image, 'rb') as f:
        f.seek(p['start'] * 512)
        return hashlib.sha256(f.read(size)).hexdigest()
