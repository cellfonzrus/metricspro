#!/usr/bin/env python3
"""CAMERAS PER BOX — a capacity model with every coefficient traceable to a measurement.

THE MODEL
─────────
A machine has (cores x 1000) CPU-milliseconds to spend per second of wall clock. A camera
spends, per second of wall clock:

    receive   = stream_fps x rtp_ms          RTP depacketize + SRTP decrypt + jitter buffer
    decode    = stream_fps x decode_ms       H.264 -> YUV
    convert   = convert_fps x bgr_ms         YUV -> BGR numpy  (convert_fps = detect_fps if
                                             the conversion is lazy, stream_fps if it is not)
    detect    = detect_fps x (pre + infer + post)
    count     = detect_fps x 0.25            tracker + line/zone geometry

    cameras   = floor( cores x 1000 x USABLE / cost_per_camera )

USABLE is not a safety fudge, it is a measured fact: the soak reached 3.72 of 4 cores before
cameras began to slip, so 0.93 is what the scheduler and memory system actually leave you.
Deployment headroom (leaving room for the register, for a busy Saturday, for one machine to
cover a failed neighbour) is a SEPARATE multiplier, applied on top and stated as a choice.

WHERE IT BREAKS — read this before planning a rollout on it
───────────────────────────────────────────────────────────
1. IT IS AN AVERAGE-CPU MODEL. It says a camera fits when its mean cost fits. Inference cost
   is nearly constant per frame, but decode cost rises with scene motion and NMS cost rises
   with people in frame, so a shop at 5pm is dearer than the same shop at 3pm. The soak
   footage holds 4 people continuously, which is a busy shop, not a worst case.
2. IT ASSUMES THE STREAM ARRIVES AT stream_fps. If Google's encoder drops to 15 fps in low
   light, receive+decode halves and the box gets cheaper, not dearer. Setting stream_fps too
   low is the dangerous error.
3. IT IGNORES MEMORY BANDWIDTH as a separate resource. On 4 cores it did not bind (measured).
   On a 16-core box with 16 cameras it might, and this model would not see it coming.
4. IT SAYS NOTHING ABOUT GOOGLE'S API LIMITS, which bound a CENTRAL deployment before CPU
   does. See the report.
5. THE COEFFICIENTS ARE FROM ONE CPU. Instruction set matters more than core count for the
   inference term: the same core measured 17.1 ms/detection with AMX and 85.1 ms restricted
   to AVX2. Re-measure on the candidate machine — `--measure` does it.

usage:
  python3 capacity_model.py                        the shipped defaults, on this box's numbers
  python3 capacity_model.py --measure              re-derive the coefficients HERE, then model
  python3 capacity_model.py --cores 8 --profile avx2
"""
import argparse, json, os, sys

HERE = os.path.dirname(os.path.abspath(__file__))

# ── measured coefficients, CPU-ms ────────────────────────────────────────────────────
# Everything below was measured in this worktree on an Intel Xeon (Emerald Rapids class,
# 4 cores, 2.10 GHz, AVX-512 + AMX). Script named per line.
MEASURED = {
    # per received frame — webrtc_loopback.py
    "rtp_ms": {"1080p": 2.8, "720p": 1.8},          # 6.44 - 3.62 and 3.81 - 2.03
    # per decoded frame, single-threaded — bench_decode_cpu.py
    "decode_ms": {"1080p": 3.14, "720p": 1.47},
    # per converted frame — webrtc_loopback.py (--bgr delta)
    "bgr_ms": {"1080p": 3.04, "720p": 2.90},
    # per detection, 1 thread, 640x384 letterbox — bench_cputime.py / run_avx2.sh
    "infer_ms": {
        "amx_bf16": 17.1,      # OpenVINO default on Sapphire/Emerald Rapids Xeon
        "avx512_fp32": 45.7,   # OpenVINO, INFERENCE_PRECISION_HINT=f32
        "avx2_ov": 85.1,       # OpenVINO, oneDNN capped at AVX2
        "onnxrt": 48.2,        # ONNX Runtime MLAS (ISA could not be capped; see report)
        "torch_1thr": 102.1,   # PyTorch, one thread
        "torch_4thr": 175.8,   # PyTorch, four threads — CPU cost, not latency
    },
    # letterbox + NMS + normalize around the engine — bench_pipeline.py (24.8 - 20.0)
    "prepost_ms": 4.8,
    "count_ms": 0.25,
    # PER-CAMERA PROCESS OVERHEAD, CPU-ms per second. Not a fudge factor: the soak spent
    # 310 CPU-ms/s per camera where the stage costs account for 245, and the gap is the
    # Python loop, the container reopen, the frame-slot handoff and the interpreter itself.
    # Calibrated from soak.py at N=12 (3.72 cores / 12 cameras) and validated below.
    "process_ms": 65.0,
}

# measured ceiling before cameras slipped in the real-time soak (soak.py, N=12 of 4 cores)
USABLE = 0.93

PROFILES = {
    "amx":    "amx_bf16",       # modern Xeon: AWS c7i/m7i, GCP C3/C4, Azure Dv5+
    "avx512": "avx512_fp32",    # Skylake-SP..Ice Lake Xeon, Rocket Lake desktop
    "avx2":   "avx2_ov",        # N100/N305, Ryzen mobile, anything pre-2017 Intel
    "onnx":   "onnxrt",
    "torch":  "torch_1thr",
    "shipped": "torch_4thr",    # what vision_edge_analyzer.py does today
}


def cost_per_camera(res, stream_fps, detect_fps, engine, lazy_bgr=True, motion_duty=1.0,
                    webrtc=True, m=MEASURED):
    conv_fps = detect_fps if lazy_bgr else stream_fps
    parts = {
        "receive": (stream_fps * m["rtp_ms"][res]) if webrtc else 0.0,
        "decode": stream_fps * m["decode_ms"][res],
        "convert": conv_fps * m["bgr_ms"][res],
        "detect": detect_fps * (m["infer_ms"][engine] + m["prepost_ms"]) * motion_duty,
        "count": detect_fps * m["count_ms"] * motion_duty,
        "process": m["process_ms"],
    }
    parts["total"] = sum(parts.values())
    return parts


def cameras(cores, parts, headroom=1.0):
    return (cores * 1000.0 * USABLE * headroom) / parts["total"]


def measure_here():
    """Re-derive the two coefficients that move most: inference and decode."""
    import subprocess
    out = dict(MEASURED)
    print("measuring on this machine…")
    r = subprocess.run([sys.executable, HERE + "/bench_cputime.py", "ov", "4"],
                       env=dict(os.environ, FORCE_THREADS="1", SHAPE="384,640"),
                       capture_output=True, text=True)
    for line in r.stdout.splitlines():
        if "CPU" in line:
            ms = float(line.split("CPU")[1].split("ms")[0])
            out["infer_ms"] = dict(out["infer_ms"]); out["infer_ms"]["measured"] = ms
            print("  inference here: %.1f CPU-ms/detection @640x384, 1 thread" % ms)
    return out


def main():
    ap = argparse.ArgumentParser()
    ap.add_argument("--cores", type=int, default=4)
    ap.add_argument("--res", choices=("1080p", "720p"), default="1080p")
    ap.add_argument("--stream-fps", type=float, default=30)
    ap.add_argument("--detect-fps", type=float, default=6)
    ap.add_argument("--profile", default=None)
    ap.add_argument("--headroom", type=float, default=1.0,
                    help="deployment headroom on TOP of the measured 0.93 usable fraction")
    ap.add_argument("--measure", action="store_true")
    a = ap.parse_args()
    m = measure_here() if a.measure else MEASURED
    engines = [PROFILES[a.profile]] if a.profile else list(PROFILES.values())
    if a.measure and "measured" in m["infer_ms"]:
        engines = ["measured"] + engines

    print("\n%d cores, %s @ %.0f fps, detect_fps %.1f, headroom %.2f"
          % (a.cores, a.res, a.stream_fps, a.detect_fps, a.headroom))
    print("CPU-ms per camera per second, and how many fit\n")
    print("  %-14s %8s %8s %8s %8s %8s %8s %9s %7s"
          % ("engine", "receive", "decode", "convert", "detect", "count", "proc",
             "TOTAL", "cams"))
    for e in engines:
        lazy = e != "torch_4thr"     # the shipped code converts every frame
        p = cost_per_camera(a.res, a.stream_fps, a.detect_fps, e, lazy_bgr=lazy, m=m)
        print("  %-14s %8.0f %8.0f %8.0f %8.0f %8.0f %8.0f %9.0f %7.1f"
              % (e, p["receive"], p["decode"], p["convert"], p["detect"], p["count"],
                 p["process"], p["total"], cameras(a.cores, p, a.headroom)))

    print("\nMODEL vs the real-time soak (soak.py, same box, WebRTC receive excluded)")
    for label, res, df, meas in (("1080p df=6", "1080p", 6.0, "12 ok / 14 behind"),
                                 ("720p  df=6", "720p", 6.0, "14 ok / 18 behind"),
                                 ("1080p df=3", "1080p", 3.0, "20 ok / 24 behind")):
        p = cost_per_camera(res, a.stream_fps, df, "amx_bf16", webrtc=False, m=m)
        print("  %-12s model %5.1f cameras   measured %s" % (label, cameras(4, p), meas))

    print("\nSensitivity of the best engine to the two dials an operator actually has:")
    best = engines[0] if a.measure else "amx_bf16"
    print("  %-10s" % "detect_fps", "".join("%9s" % ("%gfps" % f) for f in (2, 3, 4, 6, 8)))
    for res in ("1080p", "720p"):
        row = []
        for f in (2, 3, 4, 6, 8):
            p = cost_per_camera(res, a.stream_fps, f, best, m=m)
            row.append("%9.1f" % cameras(a.cores, p, a.headroom))
        print("  %-10s" % res, "".join(row))


if __name__ == "__main__":
    main()
