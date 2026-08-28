#!/bin/sh
cd "$(dirname "$0")"
for e in ov onnx torch; do
  for t in 1 2 4; do
    for s in 384,640 288,512 192,320; do
      FORCE_THREADS=$t SHAPE=$s python3 bench_cputime.py $e 4 2>&1 | tail -1
    done
  done
done
