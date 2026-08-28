#!/usr/bin/env python3
"""A drop-in replacement for vision_edge_analyzer.PersonDetector that is ~9x cheaper per
detection on the same CPU, returning the same normalized dicts.

Three changes, each measured in bench_engines.py / bench_cputime.py:

  1. OPENVINO fp32 instead of PyTorch, same yolov8n weights, no quantization.
     640x384: 20.0 CPU-ms vs 102.1 CPU-ms single-threaded — 5.1x. Post-NMS boxes match
     torch at IoU > 0.976 on 75 of 76 boxes (verify_ov_parity2.py).

  2. ONE INFERENCE THREAD. Multi-threaded inference cuts LATENCY and raises COST: OpenVINO
     at 4 threads spends 43.0 CPU-ms for the same 640x384 detection it does in 20.0 CPU-ms
     at one thread — a 2.15x tax to make one camera faster than it needs to be. Cameras are
     an embarrassingly parallel workload; give each one a core, not each detection four.

  3. A FIXED RECTANGULAR INPUT (16:9, stride-32). A fixed camera has one aspect ratio
     forever, so the graph is compiled once at 640x384 and never re-letterboxed to a square.

Optional and off by default:
  * `roi` — crop to the doorway before letterboxing, so a smaller network input still puts
    the same number of pixels on a person. This changes what the detector can see and is an
    ACCURACY decision, not a throughput one; it is exposed, not enabled.
  * `motion_gate` — skip inference when nothing in the frame moved.

The interface is deliberately identical to PersonDetector: __call__(frame_bgr) -> list of
{"x","y","w","h","conf"} in normalized coordinates, so geometry.py and the tracker are
untouched and the counting rules cannot drift.
"""
import os
import numpy as np
import cv2

DEFAULT_SHAPE = (384, 640)          # (h, w) — 16:9, both stride-32 legal


class FastPersonDetector:
    def __init__(self, onnx_path, shape=DEFAULT_SHAPE, threads=1, conf=0.25, iou=0.45,
                 roi=None, motion_gate=False, motion_pixels=20, motion_delta=12):
        import openvino as ov
        self.h, self.w = shape
        self.conf, self.iou = conf, iou
        self.roi = roi                       # (x0, y0, x1, y1) normalized, or None
        self.kind = "yolov8n-openvino-%dx%d" % (self.w, self.h)
        self.supports_pose = False
        core = ov.Core()
        # ENABLE_CPU_PINNING must be OFF. OpenVINO pins its inference threads to specific
        # cores, and it decides WHICH cores per process with no knowledge of the other
        # processes. Run one analyzer process per camera with pinning left on and every one
        # of them pins to core 0: measured here as 8 workers taking 159 ms of wall clock for
        # 27 ms of CPU each while the machine sat 61% idle. The symptom is a box that looks
        # bored and still cannot keep up, which is the hardest kind of capacity bug to see.
        self._cm = core.compile_model(core.read_model(onnx_path), "CPU", {
            "PERFORMANCE_HINT": "LATENCY",
            "INFERENCE_NUM_THREADS": threads,
            "NUM_STREAMS": 1,
            "ENABLE_CPU_PINNING": False,
        })
        self._req = self._cm.create_infer_request()
        self._buf = np.empty((1, 3, self.h, self.w), np.float32)
        self.motion_gate = motion_gate
        self.motion_pixels, self.motion_delta = motion_pixels, motion_delta
        self._prev_small = None
        self.skipped = 0
        self.ran = 0

    # ── preprocessing ────────────────────────────────────────────────────────────────
    def _letterbox(self, im):
        h, w = im.shape[:2]
        r = min(self.w / w, self.h / h)
        nw, nh = int(round(w * r)), int(round(h * r))
        top, left = (self.h - nh) // 2, (self.w - nw) // 2
        canvas = np.full((self.h, self.w, 3), 114, np.uint8)
        cv2.resize(im, (nw, nh), dst=canvas[top:top + nh, left:left + nw],
                   interpolation=cv2.INTER_LINEAR)
        # HWC BGR uint8 -> NCHW RGB float32/255, into a reused buffer
        x = canvas.astype(np.float32, copy=False)
        np.multiply(x, 1.0 / 255.0, out=x)
        self._buf[0, 0] = x[:, :, 2]
        self._buf[0, 1] = x[:, :, 1]
        self._buf[0, 2] = x[:, :, 0]
        return r, left, top

    def _moved(self, im):
        g = cv2.cvtColor(cv2.resize(im, (160, 90), interpolation=cv2.INTER_NEAREST),
                         cv2.COLOR_BGR2GRAY)
        p, self._prev_small = self._prev_small, g
        if p is None:
            return True
        return int(cv2.countNonZero(cv2.threshold(cv2.absdiff(g, p), self.motion_delta,
                                                  255, cv2.THRESH_BINARY)[1])) > self.motion_pixels

    # ── the call the analyzer already makes ──────────────────────────────────────────
    def __call__(self, frame):
        if frame is None:
            return []
        H, W = frame.shape[:2]
        if self.motion_gate and not self._moved(frame):
            self.skipped += 1
            return []
        self.ran += 1
        ox, oy, sub = 0, 0, frame
        if self.roi:
            x0, y0, x1, y1 = self.roi
            ox, oy = int(x0 * W), int(y0 * H)
            sub = frame[oy:int(y1 * H), ox:int(x1 * W)]
        sh, sw = sub.shape[:2]
        r, left, top = self._letterbox(sub)
        self._req.infer({0: self._buf})
        out = self._req.get_output_tensor(0).data      # (1, 84, A)
        return self._decode(out, r, left, top, sw, sh, ox, oy, W, H)

    def _decode(self, o, r, left, top, sw, sh, ox, oy, W, H):
        p = o[0]
        c = p[4, :]
        keep = c > self.conf
        if not keep.any():
            return []
        b = p[:4, keep].T
        cc = c[keep].astype(np.float32)
        x1 = (b[:, 0] - b[:, 2] / 2 - left) / r
        y1 = (b[:, 1] - b[:, 3] / 2 - top) / r
        ww = b[:, 2] / r
        hh = b[:, 3] / r
        boxes = [[float(a), float(bb), float(cw), float(ch)]
                 for a, bb, cw, ch in zip(x1, y1, ww, hh)]
        idx = cv2.dnn.NMSBoxes(boxes, cc.tolist(), self.conf, self.iou)
        if idx is None or len(idx) == 0:
            return []
        out = []
        for i in np.array(idx).reshape(-1):
            bx, by, bw, bh = boxes[int(i)]
            out.append({"x": (bx + ox) / W, "y": (by + oy) / H,
                        "w": bw / W, "h": bh / H, "conf": float(cc[int(i)])})
        return out

    def unavailable_message(self):
        return "OpenVINO detector failed to load."


def export_once(onnx_path, shape=DEFAULT_SHAPE, weights="yolov8n.pt"):
    """Write the fixed-shape ONNX graph OpenVINO compiles. Runs once, on the build machine
    or on first start; it needs ultralytics, the running analyzer does not."""
    if os.path.exists(onnx_path):
        return onnx_path
    from ultralytics import YOLO
    p = YOLO(weights).export(format="onnx", imgsz=shape, simplify=True, dynamic=False,
                             verbose=False)
    os.replace(p, onnx_path)
    return onnx_path
