#!/usr/bin/env python3
"""Build realistic fixed-camera store clips for throughput benchmarking.

Why synthetic-from-real: there is no Nest camera here. But decode cost is driven by
resolution + bitrate + how much of the frame changes, and detector cost by real texture.
So: a real photographic background held STILL (a fixed security camera), with real COCO
person crops walking across it, plus per-frame sensor noise (real cameras are never
noise-free, and noise-free video is unrealistically cheap to decode).
"""
import sys, os, glob, math, random
import numpy as np, cv2, av

COCO = "/tmp/vbench/coco128/images/train2017"
LABELS = "/tmp/vbench/coco128/labels/train2017"
OUT = os.path.dirname(os.path.abspath(__file__))


def person_crops(n=8):
    """Real person crops from COCO, using the YOLO-format labels (class 0 = person)."""
    out = []
    for lab in sorted(glob.glob(LABELS + "/*.txt")):
        img_p = lab.replace("/labels/", "/images/").replace(".txt", ".jpg")
        if not os.path.exists(img_p):
            continue
        im = cv2.imread(img_p)
        if im is None:
            continue
        H, W = im.shape[:2]
        for line in open(lab):
            p = line.split()
            if not p or p[0] != "0":
                continue
            cx, cy, w, h = [float(v) for v in p[1:5]]
            if h < 0.35 or w * W < 40:
                continue
            x1 = int(max(0, (cx - w / 2) * W)); x2 = int(min(W, (cx + w / 2) * W))
            y1 = int(max(0, (cy - h / 2) * H)); y2 = int(min(H, (cy + h / 2) * H))
            if x2 - x1 < 30 or y2 - y1 < 80:
                continue
            out.append(im[y1:y2, x1:x2].copy())
            if len(out) >= n:
                return out
    return out


def background(w, h):
    """A busy real photo as the static scene, sized to the camera resolution."""
    cands = sorted(glob.glob(COCO + "/*.jpg"))
    im = cv2.imread(cands[17])
    im = cv2.resize(im, (w, h), interpolation=cv2.INTER_CUBIC)
    return im


def render(path, w, h, fps, seconds, n_people, kbps, seed=3):
    rng = np.random.default_rng(seed)
    bg = background(w, h)
    crops = person_crops(8)
    walkers = []
    for i in range(n_people):
        c = crops[i % len(crops)]
        ph = int(h * (0.30 + 0.18 * rng.random()))
        pw = max(8, int(c.shape[1] * ph / c.shape[0]))
        walkers.append({
            "img": cv2.resize(c, (pw, ph)),
            "x": rng.uniform(-pw, w),
            "y": int(h * (0.45 + 0.25 * rng.random())) - ph,
            "vx": float(rng.choice([-1, 1])) * (w / (fps * rng.uniform(4.0, 9.0))),
        })

    cont = av.open(path, mode="w")
    st = cont.add_stream("libx264", rate=fps)
    st.width, st.height = w, h
    st.pix_fmt = "yuv420p"
    st.bit_rate = kbps * 1000
    st.options = {"preset": "veryfast", "g": str(fps * 2), "tune": "zerolatency"}

    nframes = int(fps * seconds)
    for fi in range(nframes):
        f = bg.copy()
        for wk in walkers:
            wk["x"] += wk["vx"]
            pw = wk["img"].shape[1]
            if wk["x"] < -pw - 20:
                wk["x"] = w + 10
            if wk["x"] > w + 20:
                wk["x"] = -pw - 10
            x = int(wk["x"]); y = wk["y"]
            x1, x2 = max(0, x), min(w, x + pw)
            y1, y2 = max(0, y), min(h, y + wk["img"].shape[0])
            if x2 > x1 and y2 > y1:
                f[y1:y2, x1:x2] = wk["img"][y1 - y:y2 - y, x1 - x:x2 - x]
        noise = rng.normal(0, 3.0, (h, w, 1)).astype(np.float32)
        f = np.clip(f.astype(np.float32) + noise, 0, 255).astype(np.uint8)
        frame = av.VideoFrame.from_ndarray(f, format="bgr24")
        for pkt in st.encode(frame):
            cont.mux(pkt)
    for pkt in st.encode():
        cont.mux(pkt)
    cont.close()
    sz = os.path.getsize(path)
    print("%-28s %dx%d %dfps %ds  %8.0f KiB  actual %6.0f kbps  people=%d"
          % (os.path.basename(path), w, h, fps, seconds, sz / 1024,
             sz * 8 / seconds / 1000, n_people))


if __name__ == "__main__":
    render(OUT + "/store_1080p.mp4", 1920, 1080, 30, 20, 4, 2000)
    render(OUT + "/store_720p.mp4", 1280, 720, 30, 20, 4, 1200)
    render(OUT + "/store_1080p_quiet.mp4", 1920, 1080, 30, 20, 0, 2000)
    render(OUT + "/store_1080p_15fps.mp4", 1920, 1080, 15, 20, 4, 2000)
