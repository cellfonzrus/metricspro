#!/bin/sh
# The cheap-hardware case: same silicon, oneDNN capped at AVX2, which is all an N100 /
# N305 / Ryzen-mobile mini PC has. Clock and cache still differ on real hardware — this
# isolates the instruction set only.
cd "$(dirname "$0")"
echo "AVX2-ONLY (no AVX-512, no AMX) — CPU-ms per detection, 1 thread"
for e in ov onnx torch; do
  for s in 384,640 288,512 192,320; do
    ONEDNN_MAX_CPU_ISA=AVX2 FORCE_THREADS=1 SHAPE=$s python3 bench_cputime.py $e 4 2>/dev/null | tail -1
  done
done
echo
echo "for reference, the same box unrestricted (AVX-512 + AMX, bf16 by default)"
for e in ov onnx torch; do
  for s in 384,640 288,512 192,320; do
    FORCE_THREADS=1 SHAPE=$s python3 bench_cputime.py $e 4 2>/dev/null | tail -1
  done
done
