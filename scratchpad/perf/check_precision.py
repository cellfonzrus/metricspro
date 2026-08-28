#!/usr/bin/env python3
"""What precision is OpenVINO actually running, and what does forcing fp32 cost?

OpenVINO downcasts an fp32 graph to bf16 by default on hardware that has AMX. That is a real
speed-up and, on the parity test, a harmless one — but it is a PRECISION CHANGE and must not
be reported as "the same fp32 model, just faster". This prints the chosen precision and times
both.
"""
import os, time, statistics
import numpy as np, openvino as ov
HERE = os.path.dirname(os.path.abspath(__file__))
p = HERE + "/eng_384x640.onnx"
x = np.random.rand(1, 3, 384, 640).astype(np.float32)
core = ov.Core()
print("CPU:", core.get_property("CPU", "FULL_DEVICE_NAME"))
try:
    print("device default inference precision:",
          core.get_property("CPU", "INFERENCE_PRECISION_HINT"))
except Exception as e:
    print("precision hint unavailable:", e)

for hint in (None, ov.Type.f32, ov.Type.bf16):
    cfg = {"PERFORMANCE_HINT": "LATENCY", "INFERENCE_NUM_THREADS": 1,
           "NUM_STREAMS": 1, "ENABLE_CPU_PINNING": False}
    if hint is not None:
        cfg["INFERENCE_PRECISION_HINT"] = hint
    try:
        cm = core.compile_model(core.read_model(p), "CPU", cfg)
        chosen = cm.get_property("INFERENCE_PRECISION_HINT")
        req = cm.create_infer_request()
        for _ in range(5):
            req.infer({0: x})
        ts = []
        for _ in range(25):
            t = time.perf_counter(); req.infer({0: x}); ts.append((time.perf_counter() - t) * 1000)
        print("  requested %-6s -> running %-6s  %6.2f ms"
              % (str(hint), str(chosen), statistics.median(ts)))
    except Exception as e:
        print("  requested %-6s failed: %s" % (str(hint), e))
