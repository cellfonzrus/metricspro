#!/usr/bin/env python3
"""Raw engine time, tensor in / tensor out, at MATCHED rectangular input shapes.

The ladder's first cut compared apples to oranges: ultralytics letterboxes a 16:9 frame to
640x384, but `export(imgsz=640)` writes a 640x640 graph — 1.67x the pixels. This isolates
the engine from the Python wrapper and feeds every engine exactly the same tensor.

Shapes are all 16:9 and stride-32 legal, which is what a fixed ceiling camera always gives.

usage: bench_engines.py [threads]
"""
import os, sys, time, statistics, json
HERE = os.path.dirname(os.path.abspath(__file__))
THREADS = int(sys.argv[1]) if len(sys.argv) > 1 else 4
for v in ("OMP_NUM_THREADS", "MKL_NUM_THREADS", "OPENBLAS_NUM_THREADS"):
    os.environ[v] = str(THREADS)

import numpy as np, torch
torch.set_num_threads(THREADS)

SHAPES = [(384, 640), (288, 512), (256, 416), (192, 320), (160, 256)]
REPS = 30


def bench(fn, x, reps=REPS, warm=5):
    for _ in range(warm):
        fn(x)
    ts = []
    for _ in range(reps):
        t0 = time.perf_counter()
        fn(x)
        ts.append((time.perf_counter() - t0) * 1000)
    return statistics.median(ts)


def main():
    from ultralytics import YOLO
    out = {}
    print("RAW ENGINE TIME, tensor in/out, %d thread(s)\n" % THREADS)
    print("%-14s %10s %10s %10s %10s" % ("input", "torch", "onnxrt", "openvino", "ov-int8"))

    for (h, w) in SHAPES:
        row = {}
        x = np.random.rand(1, 3, h, w).astype(np.float32)

        # ── torch ────────────────────────────────────────────────────────────────
        try:
            m = YOLO("yolov8n.pt").model.float().eval()
            xt = torch.from_numpy(x)
            with torch.inference_mode():
                row["torch"] = bench(lambda t: m(t), xt)
        except Exception as e:
            row["torch"] = float("nan"); print("torch fail", e)

        # ── onnxruntime ──────────────────────────────────────────────────────────
        p = HERE + "/eng_%dx%d.onnx" % (h, w)
        if not os.path.exists(p):
            try:
                q = YOLO("yolov8n.pt").export(format="onnx", imgsz=(h, w), simplify=True,
                                              dynamic=False, verbose=False)
                os.replace(q, p)
            except Exception as e:
                print("onnx export fail", (h, w), e)
        if os.path.exists(p):
            import onnxruntime as ort
            so = ort.SessionOptions()
            so.intra_op_num_threads = THREADS
            so.inter_op_num_threads = 1
            so.graph_optimization_level = ort.GraphOptimizationLevel.ORT_ENABLE_ALL
            s = ort.InferenceSession(p, so, providers=["CPUExecutionProvider"])
            nm = s.get_inputs()[0].name
            row["onnx"] = bench(lambda t: s.run(None, {nm: t}), x)

        # ── openvino fp32 ────────────────────────────────────────────────────────
        try:
            import openvino as ov
            core = ov.Core()
            core.set_property("CPU", {"INFERENCE_NUM_THREADS": THREADS})
            mo = core.read_model(p)
            cm = core.compile_model(mo, "CPU", {"PERFORMANCE_HINT": "LATENCY",
                                                "INFERENCE_NUM_THREADS": THREADS})
            req = cm.create_infer_request()

            def ovrun(t):
                req.infer({0: t})
                return req.get_output_tensor(0).data
            row["ov"] = bench(ovrun, x)
        except Exception as e:
            row["ov"] = float("nan")
            print("  openvino fail %s: %s" % ((h, w), e))

        # ── openvino int8 (post-training quantization, NNCF) ──────────────────────
        q8 = HERE + "/eng_%dx%d_int8.xml" % (h, w)
        try:
            import openvino as ov
            if not os.path.exists(q8):
                import nncf
                core = ov.Core()
                mo = core.read_model(p)
                cal = [{0: np.random.rand(1, 3, h, w).astype(np.float32)} for _ in range(24)]
                qm = nncf.quantize(mo, nncf.Dataset(cal), subset_size=24)
                ov.save_model(qm, q8)
            core = ov.Core()
            cm = core.compile_model(core.read_model(q8), "CPU",
                                    {"PERFORMANCE_HINT": "LATENCY",
                                     "INFERENCE_NUM_THREADS": THREADS})
            req = cm.create_infer_request()

            def ov8(t):
                req.infer({0: t})
                return req.get_output_tensor(0).data
            row["ov_int8"] = bench(ov8, x)
        except Exception as e:
            row["ov_int8"] = float("nan")

        out["%dx%d" % (w, h)] = row
        print("%-14s %10.2f %10.2f %10.2f %10.2f"
              % ("%dx%d" % (w, h), row.get("torch", float("nan")), row.get("onnx", float("nan")),
                 row.get("ov", float("nan")), row.get("ov_int8", float("nan"))))

    json.dump(out, open(HERE + "/engines_%dthr.json" % THREADS, "w"), indent=1)


if __name__ == "__main__":
    main()
