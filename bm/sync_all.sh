#!/bin/bash
# One command for the usual cycle (run from Kali, as root): write the ESP and the super partition, then offer to reboot.
# /data, /metadata and the other partitions are not touched.
set -euo pipefail
[ "$(id -u)" = 0 ] || { echo need root; exit 1; }
D=$(cd "$(dirname "$0")" && pwd)
"$D/write_esp.sh"
GBOS_IMAGE=${GBOS_IMAGE:?set GBOS_IMAGE} "$D/rewrite_images.sh" super
echo "done. Reboot into Googlebook, use it until the problem shows, power it off from the menu, boot Kali and run: sudo $D/get_logs.sh"
