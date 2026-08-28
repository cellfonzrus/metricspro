#!/usr/bin/env python3
"""Every number in the buying table, generated from capacity_model.py rather than typed.

Run this to regenerate the staff-facing table after re-measuring on real hardware; nothing
in the published table is a figure somebody keyed in by hand.
"""
import capacity_model as C

CLASSES = [("AVX2 only", "avx2_ov"), ("AVX-512", "avx512_fp32"), ("AVX-512 + AMX", "amx_bf16")]
CORES = [2, 4, 8]


def cams(cores, res, df, eng):
    return C.cameras(cores, C.cost_per_camera(res, 30, df, eng))


print("=" * 96)
print("1 · CAMERAS PER MACHINE — patched build, real WebRTC receive included, no spare headroom")
print("=" * 96)
print("%-16s %6s | %10s %10s | %10s %10s" %
      ("CPU class", "cores", "1080p df6", "1080p df3", "720p df6", "720p df3"))
for label, eng in CLASSES:
    for c in CORES:
        print("%-16s %6d | %10.1f %10.1f | %10.1f %10.1f" %
              (label, c, cams(c, "1080p", 6, eng), cams(c, "1080p", 3, eng),
               cams(c, "720p", 6, eng), cams(c, "720p", 3, eng)))

print()
print("=" * 96)
print("2 · WHERE A SINGLE CAMERA'S CPU GOES — and how much of it detect_fps can even touch")
print("=" * 96)
for res in ("1080p", "720p"):
    for eng, name in (("amx_bf16", "AMX"), ("avx512_fp32", "AVX-512"), ("avx2_ov", "AVX2")):
        p6 = C.cost_per_camera(res, 30, 6, eng)
        p1 = C.cost_per_camera(res, 30, 1, eng)
        fixed = p6["receive"] + p6["decode"] + p6["process"]
        print("%-6s %-8s df6 %6.0f CPU-ms/s   df1 %6.0f   FIXED (transport+decode+loop) %5.0f = %2.0f%% of df6"
              % (res, name, p6["total"], p1["total"], fixed, 100 * fixed / p6["total"]))

print()
print("=" * 96)
print("3 · 21 CAMERAS — cores needed, and what the realistic LuxeLink mix changes")
print("=" * 96)
for label, mix in (
    ("all 21 entrance @ df6", [(21, 6)]),
    ("18 entrance @ df6 + 3 floor @ df1", [(18, 6), (3, 1)]),
    ("all 21 entrance @ df4", [(21, 4)]),
    ("all 21 entrance @ df3", [(21, 3)]),
):
    for eng, name in (("amx_bf16", "AMX"), ("avx512_fp32", "AVX-512")):
        tot = sum(n * C.cost_per_camera("1080p", 30, df, eng)["total"] for n, df in mix)
        need = tot / 1000.0 / C.USABLE
        print("  %-36s %-8s %6.0f CPU-ms/s  -> %4.1f cores full, %4.1f cores at 1.4x headroom"
              % (label, name, tot, need, need * 1.4))

print()
print("=" * 96)
print("4 · THE LEVERS, as multipliers on cameras-per-box (1080p df6 AMX = 1.00)")
print("=" * 96)
base = C.cost_per_camera("1080p", 30, 6, "amx_bf16")["total"]
levers = [
    ("detect_fps 6 -> 4", C.cost_per_camera("1080p", 30, 4, "amx_bf16")["total"]),
    ("detect_fps 6 -> 3", C.cost_per_camera("1080p", 30, 3, "amx_bf16")["total"]),
    ("detect_fps 6 -> 1 (floor camera)", C.cost_per_camera("1080p", 30, 1, "amx_bf16")["total"]),
    ("1080p -> 720p, same df6", C.cost_per_camera("720p", 30, 6, "amx_bf16")["total"]),
    ("1080p -> 720p AND df 6 -> 3", C.cost_per_camera("720p", 30, 3, "amx_bf16")["total"]),
    ("stream arrives at 15 fps not 30", C.cost_per_camera("1080p", 15, 6, "amx_bf16")["total"]),
]
for name, t in levers:
    print("  %-38s %6.0f CPU-ms/s   x%.2f cameras" % (name, t, base / t))
# motion gating is measured, not modelled — it removes the detect term on frames with no motion
mg = C.cost_per_camera("1080p", 30, 6, "amx_bf16")
empty = mg["total"] - mg["detect"] * (1 - 3.4 / 126.9)
print("  %-38s %6.0f CPU-ms/s   x%.2f cameras   (only while nothing moves)"
      % ("motion gating, empty shop", empty, base / empty))
