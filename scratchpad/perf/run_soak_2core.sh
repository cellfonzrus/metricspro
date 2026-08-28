#!/bin/sh
# The 2-core row, measured rather than divided by two. The usable fraction (how much of a
# machine you can actually reach before cameras slip) was measured at 0.93 on four cores;
# there is no reason to assume it holds on two, so this checks.
cd "$(dirname "$0")"
echo "== 2 cores (taskset 0,1), patched build, 1080p30 =="
for n in 3 4 5 6 7; do
  taskset -c 0,1 python3 soak.py $n store_1080p.mp4 6 ${SECS:-20} patched 2>/dev/null
done
echo
echo "== 2 cores, detect_fps 3 =="
for n in 6 7 8 9; do
  taskset -c 0,1 python3 soak.py $n store_1080p.mp4 3 ${SECS:-20} patched 2>/dev/null
done
