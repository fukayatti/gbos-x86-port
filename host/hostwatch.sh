#!/bin/bash
# Host health logger: one line every 2 s, fsync'd, so the last seconds survive a hard freeze.
OUT="${1:-$HOME/src/isa-shim/hostwatch.log}"
GPU=/sys/class/drm/card0/device
while true; do
  t=$(date +%T)
  mem=$(awk '/MemAvailable/{a=$2} /MemTotal/{t=$2} /SwapFree/{s=$2} END{printf "avail=%dM swapfree=%dM", a/1024, s/1024}' /proc/meminfo)
  temps=$(for z in /sys/class/thermal/thermal_zone*; do printf "%s " $(( $(cat $z/temp) / 1000 )); done)
  cpu=$(cut -d' ' -f1-3 /proc/loadavg)
  q=$(ps -o rss= -C qemu-system-x86_64 2>/dev/null | awk '{s+=$1} END{printf "qemu_rss=%dM", s/1024}')
  g="gpu_busy=$(cat $GPU/gpu_busy_percent 2>/dev/null)% gtt=$(( $(cat $GPU/mem_info_gtt_used 2>/dev/null || echo 0) / 1048576 ))M vram=$(( $(cat $GPU/mem_info_vram_used 2>/dev/null || echo 0) / 1048576 ))M"
  freq=$(awk '/cpu MHz/{s+=$4;n++} END{printf "mhz=%d", s/n}' /proc/cpuinfo)
  echo "$t $mem temp=[${temps}] load=$cpu $q $g $freq" >> "$OUT"
  sync -f "$OUT" 2>/dev/null || sync
  sleep 2
done
