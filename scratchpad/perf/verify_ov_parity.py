#!/usr/bin/env python3
"""Is the OpenVINO fp32 graph the SAME detector, or a different one that happens to be faster?

Speed that comes from a changed answer is not speed, it is a different product. This feeds
identical letterboxed tensors to torch and to OpenVINO fp32 and compares raw head output and
final person boxes. Accuracy proper is the other engineer's ground; this only establishes
that the fast path is numerically the same model.
"""
import os, sys, numpy as np, torch, av, cv2
HERE = os.path.dirname(os.path.abspath(__file__))
from ultralytics import YOLO
import openvino as ov

H, W = 384, 640
onnx = HERE + "/eng_%dx%d.onnx" % (H, W)

cont = av.open(HERE + "/store_1080p.mp4")
st = cont.streams.video[0]; st.thread_type = "AUTO"
ims = []
for f in cont.decode(st):
    ims.append(f.to_ndarray(format="bgr24"))
    if len(ims) >= 12:
        break
cont.close()


def letterbox(im):
    h, w = im.shape[:2]
    r = min(W / w, H / h)
    nw, nh = int(round(w * r)), int(round(h * r))
    out = np.full((H, W, 3), 114, np.uint8)
    rz = cv2.resize(im, (nw, nh), interpolation=cv2.INTER_LINEAR)
    top, left = (H - nh) // 2, (W - nw) // 2
    out[top:top + nh, left:left + nw] = rz
    x = out[:, :, ::-1].transpose(2, 0, 1)[None].astype(np.float32) / 255.0
    return np.ascontiguousarray(x)


m = YOLO("yolov8n.pt").model.float().eval()
core = ov.Core()
cm = core.compile_model(core.read_model(onnx), "CPU", {"PERFORMANCE_HINT": "LATENCY"})
req = cm.create_infer_request()

maxdiff = 0.0
for im in ims:
    x = letterbox(im)
    with torch.inference_mode():
        t = m(torch.from_numpy(x))[0].numpy()
    req.infer({0: x})
    o = req.get_output_tensor(0).data
    maxdiff = max(maxdiff, float(np.abs(t - o).max()))
    # person-channel confidences above 0.25
    tp = (t[0, 4, :] > 0.25).sum(); op = (o[0, 4, :] > 0.25).sum()
    print("  raw max|torch-ov| = %.3e   person-anchors>0.25  torch %3d  ov %3d"
          % (float(np.abs(t - o).max()), int(tp), int(op)))
print("\nWORST ELEMENTWISE DIFFERENCE OVER %d FRAMES: %.3e" % (len(ims), maxdiff))
print("(head output is in pixel units, ~0-640; a diff of 1e-3 is float rounding, not a different model)")
