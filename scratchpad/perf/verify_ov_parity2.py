#!/usr/bin/env python3
"""Parity, judged where it matters: the boxes that survive confidence + NMS.

The raw-head max-diff is dominated by box-regression channels on anchors that are discarded,
so it says nothing. What the counter consumes is the post-NMS person boxes; those are compared
here by count, confidence and IoU against the torch answer on the same frames.
"""
import os, numpy as np, torch, av, cv2
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
    if len(ims) >= 20:
        break
cont.close()


def letterbox(im):
    h, w = im.shape[:2]
    r = min(W / w, H / h)
    nw, nh = int(round(w * r)), int(round(h * r))
    out = np.full((H, W, 3), 114, np.uint8)
    out[(H - nh) // 2:(H - nh) // 2 + nh, (W - nw) // 2:(W - nw) // 2 + nw] = \
        cv2.resize(im, (nw, nh), interpolation=cv2.INTER_LINEAR)
    x = out[:, :, ::-1].transpose(2, 0, 1)[None].astype(np.float32) / 255.0
    return np.ascontiguousarray(x)


def decode_head(o, conf=0.25, iou=0.45):
    """(N,4 xyxy),(N,) conf for class 0, after NMS. o is (1,84,A)."""
    p = o[0]
    c = p[4, :]
    keep = c > conf
    if not keep.any():
        return np.zeros((0, 4)), np.zeros((0,))
    b = p[:4, keep].T
    cc = c[keep]
    xy = np.stack([b[:, 0] - b[:, 2] / 2, b[:, 1] - b[:, 3] / 2,
                   b[:, 0] + b[:, 2] / 2, b[:, 1] + b[:, 3] / 2], 1)
    idx = cv2.dnn.NMSBoxes(
        [[float(x1), float(y1), float(x2 - x1), float(y2 - y1)] for x1, y1, x2, y2 in xy],
        cc.astype(np.float32).tolist(), conf, iou)
    idx = np.array(idx).reshape(-1)
    return xy[idx], cc[idx]


def iou_mat(a, b):
    if len(a) == 0 or len(b) == 0:
        return np.zeros((len(a), len(b)))
    x1 = np.maximum(a[:, None, 0], b[None, :, 0]); y1 = np.maximum(a[:, None, 1], b[None, :, 1])
    x2 = np.minimum(a[:, None, 2], b[None, :, 2]); y2 = np.minimum(a[:, None, 3], b[None, :, 3])
    inter = np.clip(x2 - x1, 0, None) * np.clip(y2 - y1, 0, None)
    aa = (a[:, 2] - a[:, 0]) * (a[:, 3] - a[:, 1]); bb = (b[:, 2] - b[:, 0]) * (b[:, 3] - b[:, 1])
    return inter / (aa[:, None] + bb[None, :] - inter + 1e-9)


m = YOLO("yolov8n.pt").model.float().eval()
core = ov.Core()
cm = core.compile_model(core.read_model(onnx), "CPU", {"PERFORMANCE_HINT": "LATENCY"})
req = cm.create_infer_request()

tot_t = tot_o = matched = 0
worst_iou = 1.0
dconf = []
for im in ims:
    x = letterbox(im)
    with torch.inference_mode():
        t = m(torch.from_numpy(x))[0].numpy()
    req.infer({0: x})
    o = req.get_output_tensor(0).data
    bt, ct = decode_head(t)
    bo, co = decode_head(o)
    tot_t += len(bt); tot_o += len(bo)
    M = iou_mat(bt, bo)
    for i in range(len(bt)):
        if M.shape[1] and M[i].max() > 0.9:
            matched += 1
            worst_iou = min(worst_iou, float(M[i].max()))
            dconf.append(abs(float(ct[i]) - float(co[int(M[i].argmax())])))

print("frames               : %d" % len(ims))
print("torch person boxes   : %d" % tot_t)
print("openvino person boxes: %d" % tot_o)
print("matched at IoU>0.90  : %d  (%.1f%% of torch boxes)" % (matched, 100.0 * matched / max(1, tot_t)))
print("worst IoU among those: %.4f" % worst_iou)
print("max |conf difference|: %.2e" % (max(dconf) if dconf else 0))
