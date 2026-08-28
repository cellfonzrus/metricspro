#!/usr/bin/env python3
"""Stage-by-stage wall-clock profile of ONE camera tick as vision_edge_analyzer.py runs it today.

Stages, in the order the analyzer pays for them:
  demux+decode   av packet -> decoded VideoFrame           (aiortc's decoder thread)
  to_bgr24       VideoFrame -> numpy BGR                   (WebRtcFrameSource._pump)
  yolo.pre       letterbox + normalize                     (inside PersonDetector.__call__)
  yolo.infer     the network                               (inside PersonDetector.__call__)
  yolo.post      NMS + Results object                      (inside PersonDetector.__call__)
  det.wrap       Results -> normalized dicts               (PersonDetector.__call__ tail)
  track          Tracker.update                            (CameraWorker.step)
  geometry       foot points, exclusion, line crossing     (CameraWorker.step)

Run:  python3 profile_stages.py [clip.mp4] [n_ticks]
"""
import os, sys, time, statistics
sys.path.insert(0, os.path.join(os.path.dirname(os.path.abspath(__file__)), "..", "..", "backend"))

import numpy as np
import av

HERE = os.path.dirname(os.path.abspath(__file__))
CLIP = sys.argv[1] if len(sys.argv) > 1 else HERE + "/store_1080p.mp4"
NTICK = int(sys.argv[2]) if len(sys.argv) > 2 else 60


def decoded_frames(path, n):
    """Return (raw_av_frames, bgr_frames, decode_ms_each, tobgr_ms_each)."""
    cont = av.open(path)
    st = cont.streams.video[0]
    st.thread_type = "AUTO"
    dec_ms, bgr_ms, bgr = [], [], []
    got = 0
    for packet in cont.demux(st):
        t0 = time.perf_counter()
        try:
            frames = packet.decode()
        except Exception:
            continue
        t1 = time.perf_counter()
        for f in frames:
            t2 = time.perf_counter()
            img = f.to_ndarray(format="bgr24")
            t3 = time.perf_counter()
            bgr.append(img)
            bgr_ms.append((t3 - t2) * 1000)
            got += 1
        if frames:
            dec_ms.append((t1 - t0) * 1000 / len(frames))
        if got >= n:
            break
    cont.close()
    return bgr, dec_ms, bgr_ms


def main():
    from vision_edge_analyzer import PersonDetector, Tracker
    from app.modules.vision import geometry as GEO

    print("clip:", os.path.basename(CLIP))
    bgr, dec_ms, bgr_ms = decoded_frames(CLIP, NTICK)
    h, w = bgr[0].shape[:2]
    print("frames: %d @ %dx%d" % (len(bgr), w, h))

    det = PersonDetector(prefer_yolo=True)
    print("detector:", det.kind)
    yolo = det._yolo
    # warm up
    for f in bgr[:3]:
        det(f)

    pre, inf, post, wrap, trk, geo = [], [], [], [], [], []
    tracker = Tracker()
    zones = [{"kind": "line", "is_active": True, "inward": "left",
              "geometry": {"x1": 0.1, "y1": 0.6, "x2": 0.9, "y2": 0.55}},
             {"kind": "exclude", "geometry": {"points": [[0.0, 0.0], [0.25, 0.0],
                                                         [0.25, 0.2], [0.0, 0.2]]}}]
    lines = [z for z in zones if z.get("kind") == "line"]
    ndet = []
    for f in bgr:
        t0 = time.perf_counter()
        res = yolo(f, verbose=False, classes=[0])
        t1 = time.perf_counter()
        sp = res[0].speed
        pre.append(sp["preprocess"]); inf.append(sp["inference"]); post.append(sp["postprocess"])
        # the wrapping tail of PersonDetector.__call__
        t2 = time.perf_counter()
        out = []
        for r in res:
            for b in r.boxes:
                x1, y1, x2, y2 = [float(v) for v in b.xyxy[0].tolist()]
                out.append({"x": x1 / w, "y": y1 / h, "w": (x2 - x1) / w,
                            "h": (y2 - y1) / h, "conf": float(b.conf[0])})
        t3 = time.perf_counter()
        wrap.append((t3 - t2) * 1000)
        ndet.append(len(out))
        now = time.time()
        t4 = time.perf_counter()
        tracks = tracker.update(out, now)
        t5 = time.perf_counter()
        trk.append((t5 - t4) * 1000)
        t6 = time.perf_counter()
        feet = {t["key"]: GEO.foot_point(t["box"]) for t in tracks}
        for t in tracks:
            foot = feet[t["key"]]
            if GEO.excluded(zones, foot):
                continue
            prev = t.get("prev_foot")
            if prev:
                for ln in lines:
                    GEO.crossing_direction(ln, prev, foot)
            t["prev_foot"] = foot
            GEO.grid_cell(foot, 24, 16)
        t7 = time.perf_counter()
        geo.append((t7 - t6) * 1000)

    def row(name, xs):
        if not xs:
            return
        print("  %-14s mean %7.2f  med %7.2f  p95 %7.2f  ms" %
              (name, statistics.mean(xs), statistics.median(xs),
               sorted(xs)[int(0.95 * (len(xs) - 1))]))
        return statistics.mean(xs)

    print("\nPER-FRAME WALL CLOCK (%d frames, mean %.1f people detected)"
          % (len(bgr), statistics.mean(ndet)))
    m = {}
    m["demux+decode"] = row("demux+decode", dec_ms)
    m["to_bgr24"] = row("to_bgr24", bgr_ms)
    m["yolo.pre"] = row("yolo.pre", pre)
    m["yolo.infer"] = row("yolo.infer", inf)
    m["yolo.post"] = row("yolo.post", post)
    m["det.wrap"] = row("det.wrap", wrap)
    m["track"] = row("track", trk)
    m["geometry"] = row("geometry", geo)
    tot = sum(m.values())
    print("  %-14s      %7.2f ms" % ("TOTAL", tot))
    print("\nSHARE OF A TICK")
    for k, v in sorted(m.items(), key=lambda kv: -kv[1]):
        print("  %-14s %5.1f%%" % (k, 100 * v / tot))
    print("\n  decode-side  (decode+to_bgr24) : %6.2f ms  %5.1f%%"
          % (m["demux+decode"] + m["to_bgr24"],
             100 * (m["demux+decode"] + m["to_bgr24"]) / tot))
    print("  detect-side  (yolo.*+wrap)     : %6.2f ms  %5.1f%%"
          % (m["yolo.pre"] + m["yolo.infer"] + m["yolo.post"] + m["det.wrap"],
             100 * (m["yolo.pre"] + m["yolo.infer"] + m["yolo.post"] + m["det.wrap"]) / tot))
    print("  count-side   (track+geometry)  : %6.2f ms  %5.1f%%"
          % (m["track"] + m["geometry"], 100 * (m["track"] + m["geometry"]) / tot))


if __name__ == "__main__":
    main()
