#!/bin/bash
# Copy the Googlebook logs (kept on /metadata by gbos-persist-log) next to this script. Root only.
set -uo pipefail
[ "$(id -u)" = 0 ] || { echo need root; exit 1; }
B=$(cd "$(dirname "$0")" && pwd); M=$(mktemp -d)
trap 'umount "$M" 2>/dev/null; rmdir "$M" 2>/dev/null' EXIT
e2fsck -fp /dev/nvme0n1p36 || true
mount -o ro /dev/nvme0n1p36 "$M" || { echo "mount failed"; exit 1; }
ls -la "$M/gbos-log" 2>&1 | head -20
rm -f "$B"/gb-* "$B"/audio.txt "$B"/bt.txt
for f in "$M"/gbos-log/*; do [ -f "$f" ] && cp "$f" "$B"/; done
chown "${SUDO_USER:-root}": "$B"/gb-* "$B"/audio.txt "$B"/bt.txt 2>/dev/null
ls -l "$B"/gb-* "$B"/audio.txt "$B"/bt.txt 2>&1
