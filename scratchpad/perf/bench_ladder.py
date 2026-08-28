#!/usr/bin/env python3
"""The optimisation ladder: same frames, same detector semantics, one dial changed at a time.

Every row is measured on the SAME 40 real decoded frames from the same clip, so the numbers
are comparable to each other and to the analyzer as it stands today (row 0).

`boxes` is a sanity column, not an accuracy claim — it says whether a speed-up quietly stopped
finding people. Accuracy is somebody else's measurement; this one only flags a cliff.

usage: bench_ladder.py [clip.mp4] [n_frames] [threads]
"""
import os, sys, time, statistics, json
HERE = os.path.dirname(os.path.abspath(__file__))
sys.path.insert(0, os.path.join(HERE, "..", "..", "backend"))

THREADS = int(sys.argv[3]) if len(sys.argv) > 3 else 0   # 0 = leave default
if THREADS:
    for v in ("OMP_NUM_THREADS", "MKL_NUM_THREADS", "OPENBLAS_NUM_THREADS"):
        os.environ[v] = str(THREADS)

import numpy as np, cv2, av, torch

if THREADS:
    torch.set_num_threads(THREADS)

CLIP = sys.argv[1] if len(sys.argv) > 1 else HERE + "/store_1080p.mp4"
N = int(sys.argv[2]) if len(sys.argv) > 2 else 40


def frames(path, n):
    cont = av.open(path)
    st = cont.streams.video[0]
    st.thread_type = "AUTO"
    out = []
    for f in cont.decode(st):
        out.append(f.to_ndarray(format="bgr24"))
        if len(out) >= n:
            break
    cont.close()
    return out


def timed(fn, ims, warm=3):
    for im in ims[:warm]:
        fn(im)
    ts, nb = [], []
    for im in ims:
        t0 = time.perf_counter()
        b = fn(im)
        ts.append((time.perf_counter() - t0) * 1000)
        nb.append(b)
    return statistics.median(ts), statistics.mean(ts), statistics.mean(nb)


RESULTS = []


def record(name, ms_med, ms_mean, boxes, base=None):
    RESULTS.append((name, ms_med, ms_mean, boxes))
    sp = ""
    if base:
        sp = "  %5.2fx" % (base / ms_med)
    print("  %-42s %8.2f ms (mean %7.2f)  boxes %4.1f%s" % (name, ms_med, ms_mean, boxes, sp))


def main():
    ims = frames(CLIP, N)
    h, w = ims[0].shape[:2]
    print("clip %s  %d frames @ %dx%d   torch threads=%d\n"
          % (os.path.basename(CLIP), len(ims), w, h, torch.get_num_threads()))

    from ultralytics import YOLO

    # ── 0. baseline: exactly what PersonDetector.__call__ does today ────────────────
    base_model = YOLO(HERE + "/yolov8n.pt" if os.path.exists(HERE + "/yolov8n.pt") else "yolov8n.pt")

    def run_ul(model, **kw):
        def f(im):
            r = model(im, verbose=False, classes=[0], **kw)
            return len(r[0].boxes)
        return f

    print("A · MODEL INPUT SIZE  (ultralytics / torch, full frame)")
    b_med, b_mean, b_box = timed(run_ul(base_model), ims)
    record("baseline yolov8n imgsz=640 (letterbox 640x384)", b_med, b_mean, b_box)
    BASE = b_med
    for sz in (512, 416, 320, 256):
        m, mn, bx = timed(run_ul(base_model, imgsz=sz), ims)
        record("yolov8n imgsz=%d" % sz, m, mn, bx, BASE)

    # ── B. ROI crop to the doorway third of the frame ───────────────────────────────
    print("\nB · ROI CROP  (only the doorway region is fed to the network)")
    # a plausible entrance ROI: middle 55% width, lower 60% height
    x0, x1 = int(0.22 * w), int(0.78 * w)
    y0, y1 = int(0.30 * h), int(1.00 * h)
    print("    roi = %dx%d  (%.0f%% of frame area)"
          % (x1 - x0, y1 - y0, 100.0 * (x1 - x0) * (y1 - y0) / (w * h)))

    def run_roi(model, sz):
        def f(im):
            r = model(im[y0:y1, x0:x1], verbose=False, classes=[0], imgsz=sz)
            return len(r[0].boxes)
        return f
    for sz in (640, 416, 320):
        m, mn, bx = timed(run_roi(base_model, sz), ims)
        record("ROI crop + imgsz=%d" % sz, m, mn, bx, BASE)

    # ── C. motion gate ──────────────────────────────────────────────────────────────
    print("\nC · MOTION GATE  (cost of the gate itself; skip-rate measured separately)")
    small_prev = [None]

    def gate(im):
        g = cv2.cvtColor(cv2.resize(im, (160, 90)), cv2.COLOR_BGR2GRAY)
        p = small_prev[0]
        small_prev[0] = g
        if p is None:
            return 1
        d = cv2.absdiff(g, p)
        return 1 if int((d > 12).sum()) > 20 else 0
    m, mn, bx = timed(gate, ims)
    record("motion gate only (160x90 absdiff)", m, mn, bx, BASE)

    # ── D. exported runtimes ────────────────────────────────────────────────────────
    print("\nD · RUNTIME  (same weights, different engine)")
    for sz in (640, 320):
        onnx_path = HERE + "/yolov8n_%d.onnx" % sz
        if not os.path.exists(onnx_path):
            try:
                p = YOLO(HERE + "/yolov8n.pt" if os.path.exists(HERE + "/yolov8n.pt")
                         else "yolov8n.pt").export(format="onnx", imgsz=sz, simplify=True,
                                                   dynamic=False, verbose=False)
                os.replace(p, onnx_path)
            except Exception as e:
                print("    onnx export imgsz=%d failed: %s" % (sz, e))
                continue
        try:
            om = YOLO(onnx_path, task="detect")
            m, mn, bx = timed(run_ul(om, imgsz=sz), ims)
            record("onnxruntime imgsz=%d" % sz, m, mn, bx, BASE)
        except Exception as e:
            print("    onnx run imgsz=%d failed: %s" % (sz, e))

    for sz in (640, 320):
        ov_dir = HERE + "/yolov8n_%d_openvino_model" % sz
        if not os.path.isdir(ov_dir):
            try:
                p = YOLO(HERE + "/yolov8n.pt" if os.path.exists(HERE + "/yolov8n.pt")
                         else "yolov8n.pt").export(format="openvino", imgsz=sz, verbose=False)
                os.replace(p, ov_dir)
            except Exception as e:
                print("    openvino export imgsz=%d failed: %s" % (sz, e))
                continue
        try:
            om = YOLO(ov_dir, task="detect")
            m, mn, bx = timed(run_ul(om, imgsz=sz), ims)
            record("openvino fp32 imgsz=%d" % sz, m, mn, bx, BASE)
        except Exception as e:
            print("    openvino run imgsz=%d failed: %s" % (sz, e))

    # ── E. pose weights (the activity feature's price) ──────────────────────────────
    print("\nE · MODEL CHOICE")
    try:
        pm = YOLO("yolov8n-pose.pt")
        m, mn, bx = timed(run_ul(pm), ims)
        record("yolov8n-pose imgsz=640", m, mn, bx, BASE)
    except Exception as e:
        print("    pose load failed: %s" % e)

    json.dump([{"name": n, "med_ms": a, "mean_ms": b, "boxes": c} for n, a, b, c in RESULTS],
              open(HERE + "/ladder_%dthr.json" % (THREADS or torch.get_num_threads()), "w"),
              indent=1)


if __name__ == "__main__":
    main()
