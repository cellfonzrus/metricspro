#!/usr/bin/env python3
"""Aggregate throughput: N single-threaded workers vs one N-threaded worker.

This is the threading-model question and it decides the whole process architecture.
The analyzer today runs ONE loop that calls the detector sequentially for every camera,
so one inference at a time gets all cores. The alternative is one single-threaded worker
per camera. A small conv net does not parallelise perfectly, so the second shape can win
outright — measured here rather than argued.

Measures DETECTIONS PER SECOND ACROSS THE WHOLE MACHINE, which is the only number the
capacity model needs.

usage: bench_parallel.py [seconds]
"""
import os, sys, time, json, multiprocessing as mp
HERE = os.path.dirname(os.path.abspath(__file__))
SECONDS = float(sys.argv[1]) if len(sys.argv) > 1 else 6.0
H, W = 384, 640
ONNX = HERE + "/eng_%dx%d.onnx" % (H, W)


def worker(threads, seconds, q, engine):
    for v in ("OMP_NUM_THREADS", "MKL_NUM_THREADS", "OPENBLAS_NUM_THREADS"):
        os.environ[v] = str(threads)
    import numpy as np
    x = np.random.rand(1, 3, H, W).astype(np.float32)
    if engine == "ov":
        import openvino as ov
        core = ov.Core()
        cm = core.compile_model(core.read_model(ONNX), "CPU",
                                {"PERFORMANCE_HINT": "LATENCY",
                                 "INFERENCE_NUM_THREADS": threads})
        req = cm.create_infer_request()

        def run():
            req.infer({0: x})
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
    n, t0 = 0, time.perf_counter()
    while time.perf_counter() - t0 < seconds:
        run()
        n += 1
    q.put(n / (time.perf_counter() - t0))


def trial(engine, nproc, threads):
    q = mp.Queue()
    ps = [mp.Process(target=worker, args=(threads, SECONDS, q, engine)) for _ in range(nproc)]
    [p.start() for p in ps]
    rates = [q.get() for _ in ps]
    [p.join() for p in ps]
    return sum(rates), rates


def main():
    ncpu = os.cpu_count()
    print("machine: %d logical CPUs\n" % ncpu)
    out = {}
    for engine in ("ov", "torch"):
        print("%s — aggregate detections/sec over the whole machine (%dx%d input)"
              % ({"ov": "OPENVINO fp32", "torch": "PYTORCH"}[engine], W, H))
        print("  %-34s %10s %10s" % ("shape", "det/sec", "per proc"))
        for nproc, threads in ((1, ncpu), (1, 2), (1, 1), (2, 2), (2, 1), (4, 1), (6, 1), (8, 1)):
            if nproc * threads > 2 * ncpu:
                continue
            tot, rates = trial(engine, nproc, threads)
            key = "%dproc x %dthr" % (nproc, threads)
            out.setdefault(engine, {})[key] = tot
            print("  %-34s %10.1f %10.1f" % (key, tot, tot / nproc))
        print()
    json.dump(out, open(HERE + "/parallel.json", "w"), indent=1)


if __name__ == "__main__":
    mp.set_start_method("spawn")
    main()
