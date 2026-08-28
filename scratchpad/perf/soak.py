#!/usr/bin/env python3
"""REAL-TIME SOAK: N simulated cameras on this box at once, does each one keep up?

A CPU budget divided by core count is a prediction. This is the test of it. Each worker is
one camera: it decodes a 30 fps stream in real time (paced to wall clock, exactly as a live
stream forces you to), converts only the frames a detection will consume, detects at
detect_fps, tracks, and runs the counting geometry. Nothing is allowed to run ahead.

A camera is HEALTHY when it holds both rates. When the machine runs out, decode falls behind
first (frames arrive whether you are ready or not) and detections thin out — which is exactly
the failure an operator sees as "the counts went soft on Saturday".

WHAT THIS DOES NOT INCLUDE: the aiortc RTP/SRTP receive layer, which cannot be soaked here
because the sender would have to run on the same box. It is measured separately in
webrtc_loopback.py (1080p30: 6.4 CPU-ms/frame total, of which ~3.6 is the decode this soak
does perform) and added analytically in the capacity model.

usage: soak.py N [clip] [detect_fps] [seconds] [mode]
"""
import os, sys, time, json, resource, multiprocessing as mp
HERE = os.path.dirname(os.path.abspath(__file__))
sys.path.insert(0, os.path.join(HERE, "..", "..", "backend"))
sys.path.insert(0, HERE)


def camera(idx, clip, detect_fps, seconds, mode, q):
    for v in ("OMP_NUM_THREADS", "MKL_NUM_THREADS", "OPENBLAS_NUM_THREADS"):
        os.environ[v] = "1"
    import av
    from vision_edge_analyzer import Tracker
    from app.modules.vision import geometry as GEO

    if mode.startswith("patched"):
        # the REAL patched analyzer, not a prototype standing in for it
        import vision_edge_analyzer as V
        prec = "f32" if mode.endswith("f32") else None
        det = V.PersonDetector(prefer_yolo=True, ov_threads=1, ov_precision=prec)
        detect = det
        lazy = True
    elif mode == "current":
        import torch
        torch.set_num_threads(int(os.environ.get("CUR_THREADS", "4")))
        from ultralytics import YOLO
        model = YOLO("yolov8n.pt")

        def detect(im):
            r = model(im, verbose=False, classes=[0])
            h, w = im.shape[:2]
            return [{"x": float(b.xyxy[0][0]) / w, "y": float(b.xyxy[0][1]) / h,
                     "w": float(b.xyxy[0][2] - b.xyxy[0][0]) / w,
                     "h": float(b.xyxy[0][3] - b.xyxy[0][1]) / h,
                     "conf": float(b.conf[0])} for b in r[0].boxes]
        lazy = False
    elif True:
        from fast_detector import FastPersonDetector
        det = FastPersonDetector(HERE + "/eng_384x640.onnx", threads=1,
                                 motion_gate=(mode == "fast_gated"))
        detect = det
        lazy = True

    tracker = Tracker()
    zones = [{"kind": "line", "is_active": True, "inward": "left",
              "geometry": {"x1": 0.1, "y1": 0.6, "x2": 0.9, "y2": 0.55}}]
    interval = 1.0 / detect_fps

    r0 = resource.getrusage(resource.RUSAGE_SELF)
    c0 = r0.ru_utime + r0.ru_stime
    t0 = time.perf_counter()
    deadline = t0 + seconds
    nframe = ndet = 0
    src_fps = None
    t_sleep = 0.0
    t_open = 0.0
    while time.perf_counter() < deadline:
        _o = time.perf_counter()
        cont = av.open(clip)
        t_open += time.perf_counter() - _o
        st = cont.streams.video[0]
        st.thread_count = 1
        st.thread_type = "NONE"
        if src_fps is None:
            src_fps = float(st.average_rate)
        last_detect = -1e9
        for f in cont.decode(st):
            now = time.perf_counter()
            if now >= deadline:
                break
            # pace to the source frame rate: a live stream will not wait for you
            due = t0 + nframe / src_fps
            if now < due:
                time.sleep(due - now)
                t_sleep += due - now
            nframe += 1
            if not lazy:
                im = f.to_ndarray(format="bgr24")
            t = time.perf_counter()
            if t - last_detect < interval:
                continue
            last_detect = t
            if lazy:
                im = f.to_ndarray(format="bgr24")
            dets = detect(im)
            ndet += 1
            tracks = tracker.update(dets, t)
            feet = {tk["key"]: GEO.foot_point(tk["box"]) for tk in tracks}
            for tk in tracks:
                foot = feet[tk["key"]]
                if GEO.excluded(zones, foot):
                    continue
                if tk.get("prev_foot"):
                    GEO.crossing_direction(zones[0], tk["prev_foot"], foot)
                tk["prev_foot"] = foot
                GEO.grid_cell(foot, 24, 16)
        cont.close()
    wall = time.perf_counter() - t0
    r1 = resource.getrusage(resource.RUSAGE_SELF)
    cpu = (r1.ru_utime + r1.ru_stime) - c0
    q.put({"cam": idx, "wall": wall, "decode_fps": nframe / wall,
           "detect_fps": ndet / wall, "cpu_s": cpu, "cores": cpu / wall,
           "sleep_s": t_sleep, "open_s": t_open, "frames": nframe,
           "target_decode_fps": src_fps})


def main():
    n = int(sys.argv[1])
    clip = sys.argv[2] if len(sys.argv) > 2 else HERE + "/store_1080p.mp4"
    dfps = float(sys.argv[3]) if len(sys.argv) > 3 else 6.0
    secs = float(sys.argv[4]) if len(sys.argv) > 4 else 25.0
    mode = sys.argv[5] if len(sys.argv) > 5 else "fast"
    q = mp.Queue()
    ps = [mp.Process(target=camera, args=(i, clip, dfps, secs, mode, q)) for i in range(n)]
    t0 = time.perf_counter()
    [p.start() for p in ps]
    res = [q.get() for _ in ps]
    [p.join() for p in ps]
    wall = time.perf_counter() - t0
    dec = sum(r["decode_fps"] for r in res) / n
    det = sum(r["detect_fps"] for r in res) / n
    cores = sum(r["cores"] for r in res)
    tgt = res[0]["target_decode_fps"]
    # 0.88 rather than 1.0: the loop samples detections against a 30 fps frame clock, so the
    # achievable rate at df=6 is 5 or 6 per second depending on phase. Decode is held to 0.97
    # — falling behind the source rate is the real failure and has no such rounding.
    healthy = all(r["decode_fps"] > 0.97 * tgt and r["detect_fps"] > 0.88 * dfps for r in res)
    slp = sum(r["sleep_s"] for r in res) / n
    opn = sum(r["open_s"] for r in res) / n
    wl = sum(r["wall"] for r in res) / n
    print("%-11s %-20s N=%-3d df=%-4.1f  decode %5.1f/%.0f fps  detect %5.2f/%.1f fps  "
          "cores %4.2f/%d  idle %4.0f%% open %4.0f%%  %s"
          % (mode, os.path.basename(clip), n, dfps, dec, tgt, det, dfps, cores,
             os.cpu_count(), 100 * slp / wl, 100 * opn / wl,
             "OK" if healthy else "BEHIND"))


if __name__ == "__main__":
    mp.set_start_method("spawn")
    main()
