#!/usr/bin/env python3
"""How much CPU does H.264 decode actually cost, and how many live streams fit?

Three separate things, measured separately, because they are three different bills:
  1. DECODE  : compressed packets -> YUV planes            (libavcodec)
  2. TO_BGR  : YUV420p -> a contiguous BGR numpy array     (swscale + a 6 MB alloc)
  3. REALTIME: how many 30 fps streams one core sustains   ( = 1 / cost_per_frame / 30 )

aiortc pays (1) and (2) for EVERY arriving frame in its own decoder thread, whether or not
the analyzer's detect_fps wants that frame. That is why (2) matters as much as (1) here.

usage: bench_decode.py [clip.mp4 ...]
"""
import os, sys, time, statistics
import av

HERE = os.path.dirname(os.path.abspath(__file__))
CLIPS = sys.argv[1:] or [HERE + "/store_1080p.mp4", HERE + "/store_720p.mp4"]


def pass_over(path, threads, to_bgr, loops=3):
    """Wall-clock for a full decode pass. threads=1 => one core's true cost."""
    best = None
    for _ in range(loops):
        cont = av.open(path)
        st = cont.streams.video[0]
        st.thread_count = threads
        st.thread_type = "AUTO" if threads != 1 else "NONE"
        n = 0
        t0 = time.perf_counter()
        for frame in cont.decode(st):
            if to_bgr:
                frame.to_ndarray(format="bgr24")
            n += 1
        dt = time.perf_counter() - t0
        cont.close()
        if best is None or dt < best[0]:
            best = (dt, n)
    dt, n = best
    return dt / n * 1000, n


def main():
    print("H.264 DECODE COST — best of 3 full passes, ms per frame\n")
    print("%-24s %8s %10s %10s %10s" % ("clip", "frames", "dec 1thr", "dec+bgr1", "dec AUTO"))
    rows = {}
    for c in CLIPS:
        if not os.path.exists(c):
            continue
        d1, n = pass_over(c, 1, False)
        db1, _ = pass_over(c, 1, True)
        da, _ = pass_over(c, 0, False)
        rows[c] = (d1, db1, da, n)
        print("%-24s %8d %9.2f %10.2f %10.2f" % (os.path.basename(c), n, d1, db1, da))

    print("\nWHAT ONE CORE SUSTAINS (single-threaded cost, no inference at all)")
    print("%-24s %14s %14s %14s" % ("clip", "@30fps streams", "@15fps streams", "@30fps +BGR"))
    for c, (d1, db1, da, n) in rows.items():
        print("%-24s %14.1f %14.1f %14.1f" %
              (os.path.basename(c), 1000.0 / d1 / 30, 1000.0 / d1 / 15, 1000.0 / db1 / 30))

    print("\nCOLOR CONVERT ALONE (to_ndarray bgr24), ms per frame")
    for c, (d1, db1, da, n) in rows.items():
        print("  %-22s %6.2f ms" % (os.path.basename(c), db1 - d1))


if __name__ == "__main__":
    main()
