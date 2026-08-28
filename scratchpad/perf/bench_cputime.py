#!/usr/bin/env python3
"""CPU-SECONDS per detection, not wall-clock per detection.

Wall-clock per inference flatters a multi-threaded engine: 8 ms of latency can be 32 ms of
CPU spread over 4 cores. Capacity is set by CPU-seconds, so that is what this measures —
process CPU time (user+sys) divided by detections — alongside the wall latency, for every
thread setting. The ratio of the two is the parallel efficiency.

usage: bench_cputime.py [engine] [seconds]
"""
import os, sys, time, json
HERE = os.path.dirname(os.path.abspath(__file__))
ENGINE = sys.argv[1] if len(sys.argv) > 1 else "ov"
SECONDS = float(sys.argv[2]) if len(sys.argv) > 2 else 5.0
SHAPES = [(384, 640), (288, 512), (192, 320)]


def measure(engine, threads, h, w, seconds):
    import resource
    import numpy as np
    x = np.random.rand(1, 3, h, w).astype(np.float32)
    onnx = HERE + "/eng_%dx%d.onnx" % (h, w)
    if engine == "ov":
        import openvino as ov
        core = ov.Core()
        cm = core.compile_model(core.read_model(onnx), "CPU",
                                {"PERFORMANCE_HINT": "LATENCY", "INFERENCE_NUM_THREADS": threads,
                                 "NUM_STREAMS": 1})
        req = cm.create_infer_request()
        run = lambda: req.infer({0: x})
    elif engine == "onnx":
        import onnxruntime as ort
        so = ort.SessionOptions()
        so.intra_op_num_threads = threads
        so.inter_op_num_threads = 1
        s = ort.InferenceSession(onnx, so, providers=["CPUExecutionProvider"])
        nm = s.get_inputs()[0].name
        run = lambda: s.run(None, {nm: x})
    else:
        import torch
        torch.set_num_threads(threads)
        from ultralytics import YOLO
        m = YOLO("yolov8n.pt").model.float().eval()
        xt = torch.from_numpy(x)

        def run():
            with torch.inference_mode():
                m(xt)
    for _ in range(5):
        run()
    r0 = resource.getrusage(resource.RUSAGE_SELF)
    t0 = time.perf_counter()
    n = 0
    while time.perf_counter() - t0 < seconds:
        run()
        n += 1
    wall = time.perf_counter() - t0
    r1 = resource.getrusage(resource.RUSAGE_SELF)
    cpu = (r1.ru_utime - r0.ru_utime) + (r1.ru_stime - r0.ru_stime)
    return wall / n * 1000, cpu / n * 1000, cpu / wall


if __name__ == "__main__":
    for v in ("OMP_NUM_THREADS", "MKL_NUM_THREADS", "OPENBLAS_NUM_THREADS"):
        os.environ[v] = os.environ.get("FORCE_THREADS", "4")
    t = int(os.environ.get("FORCE_THREADS", "4"))
    h, w = [int(v) for v in os.environ.get("SHAPE", "384,640").split(",")]
    wall, cpu, eff = measure(ENGINE, t, h, w, SECONDS)
    print("%-6s %dx%d threads=%d : wall %7.2f ms   CPU %7.2f ms   cores used %.2f"
          % (ENGINE, w, h, t, wall, cpu, eff))
