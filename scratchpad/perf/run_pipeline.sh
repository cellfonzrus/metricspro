#!/bin/sh
cd "$(dirname "$0")"
CLIP=${1:-store_1080p.mp4}
DF=${2:-6}
SF=${3:-30}
echo "=== CURRENT (torch, 4 threads, eager BGR) ==="
MODE=current CUR_THREADS=4 python3 bench_pipeline.py "$CLIP" "$DF" "$SF" 2>/dev/null | tail -25
echo "=== FAST (openvino 1 thread, lazy BGR) ==="
MODE=fast python3 bench_pipeline.py "$CLIP" "$DF" "$SF" 2>/dev/null | tail -25
echo "=== FAST + MOTION GATE ==="
MODE=fast_gated python3 bench_pipeline.py "$CLIP" "$DF" "$SF" 2>/dev/null | tail -25
