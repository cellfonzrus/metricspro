#!/bin/sh
# The integrated patch, not the prototype: soak.py mode "patched" builds
# vision_edge_analyzer.PersonDetector straight out of the patched module.
cd "$(dirname "$0")"
S=${SECS:-22}
for n in 8 10 12 14; do
  python3 soak.py $n store_1080p.mp4 6 $S patched 2>/dev/null
done
echo
for n in 8 10 12; do
  python3 soak.py $n store_1080p.mp4 6 $S patched_f32 2>/dev/null
done
