#!/bin/sh
cd "$(dirname "$0")"
S=${SECS:-22}
echo "SHIPPED TOPOLOGY: one process, one loop, N cameras taken in turn"
for n in 1 2 3 4 5; do
  python3 soak_single.py $n store_1080p.mp4 6 $S torch 2>/dev/null
done
echo
echo "same topology, OpenVINO detector swapped in (still one loop, all cores per inference)"
for n in 4 6 8 10 12; do
  python3 soak_single.py $n store_1080p.mp4 6 $S ov 2>/dev/null
done
