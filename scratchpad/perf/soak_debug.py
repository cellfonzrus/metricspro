#!/usr/bin/env python3
"""Where does a soak worker's WALL time go when the machine is not busy?

Splits the worker loop into next-frame / to_bgr / detect / track+geom and reports wall AND
CPU for each, so a stage that is waiting rather than computing shows up as a gap.
"""
import os, sys, time, resource, multiprocessing as mp
HERE = os.path.dirname(os.path.abspath(__file__))
sys.path.insert(0, os.path.join(HERE, "..", "..", "backend"))
sys.path.insert(0, HERE)


def cpu():
    r = resource.getrusage(resource.RUSAGE_SELF)
    return r.ru_utime + r.ru_stime


def camera(idx, clip, dfps, seconds, q):
    for v in ("OMP_NUM_THREADS", "MKL_NUM_THREADS", "OPENBLAS_NUM_THREADS"):
        os.environ[v] = "1"
    import av
    from fast_detector import FastPersonDetector
    det = FastPersonDetector(HERE + "/eng_384x640.onnx", threads=1)

    W = {k: [0.0, 0.0] for k in ("next", "bgr", "detect")}   # [wall, cpu]
    t0 = time.perf_counter()
    deadline = t0 + seconds
    n = nd = 0
    interval = 1.0 / dfps
    last = -1e9
    while time.perf_counter() < deadline:
        cont = av.open(clip)
        st = cont.streams.video[0]
        st.thread_count = 1
        st.thread_type = "NONE"
        it = cont.decode(st)
        while True:
            w0, c0 = time.perf_counter(), cpu()
            try:
                f = next(it)
            except StopIteration:
                break
            W["next"][0] += time.perf_counter() - w0; W["next"][1] += cpu() - c0
            n += 1
            now = time.perf_counter()
            if now >= deadline:
                break
            due = t0 + n / 30.0
            if now < due:
                time.sleep(due - now)
            t = time.perf_counter()
            if t - last < interval:
                continue
            last = t
            w0, c0 = time.perf_counter(), cpu()
            im = f.to_ndarray(format="bgr24")
            W["bgr"][0] += time.perf_counter() - w0; W["bgr"][1] += cpu() - c0
            w0, c0 = time.perf_counter(), cpu()
            det(im)
            W["detect"][0] += time.perf_counter() - w0; W["detect"][1] += cpu() - c0
            nd += 1
        cont.close()
    wall = time.perf_counter() - t0
    q.put({"idx": idx, "wall": wall, "frames": n, "dets": nd,
           "stages": {k: (round(v[0], 2), round(v[1], 2)) for k, v in W.items()}})


if __name__ == "__main__":
    mp.set_start_method("spawn")
    N = int(sys.argv[1]); secs = float(sys.argv[2]) if len(sys.argv) > 2 else 20
    clip = HERE + "/store_1080p.mp4"
    q = mp.Queue()
    ps = [mp.Process(target=camera, args=(i, clip, 6.0, secs, q)) for i in range(N)]
    [p.start() for p in ps]
    res = [q.get() for _ in ps]
    [p.join() for p in ps]
    print("N=%d  (wall_s, cpu_s) per worker, averaged" % N)
    for k in ("next", "bgr", "detect"):
        w = sum(r["stages"][k][0] for r in res) / N
        c = sum(r["stages"][k][1] for r in res) / N
        print("  %-8s wall %6.2f s   cpu %6.2f s   waiting %6.2f s" % (k, w, c, w - c))
    wall = sum(r["wall"] for r in res) / N
    acct = sum(sum(r["stages"][k][0] for k in r["stages"]) for r in res) / N
    print("  worker wall %.2f s, accounted %.2f s, unaccounted %.2f s"
          % (wall, acct, wall - acct))
    print("  decode fps %.1f   detect fps %.2f"
          % (sum(r["frames"] for r in res) / N / wall,
             sum(r["dets"] for r in res) / N / wall))
