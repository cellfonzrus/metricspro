#!/bin/sh
# What does this workload lose without AVX-512?
#
# The measuring box is an Emerald Rapids Xeon with AVX-512, AVX-VNNI and AMX. Every mini PC
# on the shortlist (N100, N305, Ryzen 5000/7000 mobile) has AVX2 at most. oneDNN — the kernel
# library under both OpenVINO and PyTorch — honours ONEDNN_MAX_CPU_ISA, so capping it at AVX2
# gives a measured, same-silicon lower bound for the instruction-set half of the gap. It does
# NOT capture clock speed, cache or memory bandwidth, which must still be measured on the
# real machine.
cd "$(dirname "$0")"
for isa in AVX512_CORE_AMX AVX512_CORE AVX2; do
  echo "ONEDNN_MAX_CPU_ISA=$isa"
  ONEDNN_MAX_CPU_ISA=$isa FORCE_THREADS=1 SHAPE=384,640 python3 bench_cputime.py ov 4 2>/dev/null | tail -1
  ONEDNN_MAX_CPU_ISA=$isa FORCE_THREADS=1 SHAPE=192,320 python3 bench_cputime.py ov 4 2>/dev/null | tail -1
  ONEDNN_MAX_CPU_ISA=$isa FORCE_THREADS=1 SHAPE=384,640 python3 bench_cputime.py torch 4 2>/dev/null | tail -1
done
