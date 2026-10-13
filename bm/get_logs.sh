#!/bin/bash
# Copy the Googlebook logs (kept on /metadata by gbos-persist-log) next to this script. Root only.
set -euo pipefail
[ "$(id -u)" = 0 ] || { echo need root; exit 1; }
B=$(cd "$(dirname "$0")" && pwd); M=$(mktemp -d)
e2fsck -fp /dev/nvme0n1p36 || true
mount -o ro /dev/nvme0n1p36 "$M"
rm -f "$B"/gb-*; cp "$M"/gbos-log/gb-* "$B"/; umount "$M"
chown "${SUDO_USER:-root}": "$B"/gb-*; ls -l "$B"/gb-*
