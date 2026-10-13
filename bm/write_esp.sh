#!/bin/bash
# Write bm/esp.img to the GBOSESP partition (nvme0n1p20). Run as root.
set -euo pipefail
[ "$(id -u)" = 0 ] || { echo need root; exit 1; }
P=/dev/nvme0n1p20
[ "$(lsblk -no PARTLABEL $P)" = "GBOSESP" ] || { echo "p20 is not GBOSESP - abort"; exit 1; }
! findmnt -S $P >/dev/null || { echo "p20 is mounted - abort"; exit 1; }
dd if="$(cd "$(dirname "$0")" && pwd)/esp.img" of=$P bs=4M conv=fsync status=none && echo "wrote $P"
