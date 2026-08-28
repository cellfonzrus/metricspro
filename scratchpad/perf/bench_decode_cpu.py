#!/usr/bin/env python3
"""Decode CPU-ms per frame, single-threaded vs frame-threaded.

Frame threading cuts decode LATENCY and raises decode COST. aiortc leaves PyAV on its
default (AUTO), so this says how much of the WebRTC receive bill is avoidable simply by
pinning the decoder to one thread — and how much of the gap between "decode an mp4" and
"receive a WebRTC stream" is really RTP/SRTP rather than threading.
"""
import os, sys, time, resource
import av

HERE = os.path.dirname(os.path.abspath(__file__))


def cpu():
    r = resource.getrusage(resource.RUSAGE_SELF)
    return r.ru_utime + r.ru_stime


def run(path, threads, ttype, loops=3):
    best = None
    for _ in range(loops):
        cont = av.open(path)
        st = cont.streams.video[0]
        st.thread_count = threads
        st.thread_type = ttype
        n = 0
        c0, t0 = cpu(), time.perf_counter()
        for _f in cont.decode(st):
            n += 1
        c, w = cpu() - c0, time.perf_counter() - t0
        cont.close()
        if best is None or c < best[0]:
            best = (c, w, n)
    c, w, n = best
    return c / n * 1000, w / n * 1000


print("%-22s %-16s %10s %10s %10s" % ("clip", "threading", "CPU ms/fr", "wall ms/fr", "CPU/s@30fps"))
for clip in ("store_1080p.mp4", "store_720p.mp4"):
    for threads, ttype, label in ((1, "NONE", "1 thread"), (0, "AUTO", "AUTO (default)"),
                                  (4, "FRAME", "4 frame-threads"), (4, "SLICE", "4 slice-threads")):
        try:
            c, w = run(HERE + "/" + clip, threads, ttype)
            print("%-22s %-16s %10.2f %10.2f %10.1f" % (clip, label, c, w, c * 30))
        except Exception as e:
            print("%-22s %-16s  failed: %s" % (clip, label, e))
