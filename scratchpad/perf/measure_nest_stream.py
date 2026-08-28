#!/usr/bin/env python3
"""ON-SITE: what does ONE REAL Nest camera actually cost and deliver?

Three numbers decide the whole rollout and none of them can be measured without a real
camera and real credentials, so this script exists to be run on the owner's hardware:

  RESOLUTION and FRAME RATE Google actually sends. Everything in the capacity model is
      priced per arriving frame. If Nest sends 1080p30 the cost is what the model says; if
      it sends 15 fps, receive+decode halves and a box carries nearly twice as many cameras.
      Assuming 30 is the safe error; assuming 15 is not.

  BITRATE, which sets both the RTP/SRTP CPU (packets per second) and the bandwidth bill.
      The model uses 2 Mbps at 1080p because that is what the synthetic clips were encoded
      at. Nest's real number is unknown to this analysis and should not be guessed.

  CPU PER CAMERA, end to end, on the machine that will actually run it — receive, decrypt,
      decode, convert, detect, track. This is the one number that makes every capacity
      estimate in the report either right or wrong on this hardware.

    python3 measure_nest_stream.py --api https://… --seconds 120
    python3 measure_nest_stream.py --api https://… --device enterprises/…/devices/… --detect

It holds ONE camera. Run it once per camera model in the fleet, not once per camera. It
writes nothing to disk and posts nothing to the platform.
"""
import argparse, os, sys, time
HERE = os.path.dirname(os.path.abspath(__file__))
sys.path.insert(0, os.path.join(HERE, "..", "..", "backend"))

import vision_edge_analyzer as V


def main():
    p = argparse.ArgumentParser()
    p.add_argument("--api", required=True)
    p.add_argument("--cred-file", default="")
    p.add_argument("--agent-key", default="")
    p.add_argument("--secret", default="")
    p.add_argument("--device", default="")
    p.add_argument("--seconds", type=float, default=120.0)
    p.add_argument("--detect", action="store_true",
                   help="also run the detector at --detect-fps, for the full per-camera cost")
    p.add_argument("--detect-fps", type=float, default=6.0)
    a = p.parse_args()

    if not (a.agent_key and a.secret):
        saved = V.load_credentials(a.cred_file)
        a.agent_key = a.agent_key or saved.get("agent_key", "")
        a.secret = a.secret or saved.get("secret", "")
    api = V.Api(a.api, a.agent_key, a.secret)
    cfg = api.call("GET", "config")
    cams = cfg.get("cameras") or []
    if not cams:
        print("no cameras available to this analyzer"); return 1
    cam = next((c for c in cams if c["device_name"] == a.device), cams[0])
    print("camera:", cam["device_name"])

    src = V.WebRtcFrameSource(api, cam["device_name"])
    src.start()

    det = None
    if a.detect:
        det = V.PersonDetector(prefer_yolo=True, ov_threads=1)
        print("detector:", det.kind)

    # Count bytes at the transport. aiortc exposes them through getStats; if the shape of
    # that changes, the frame count and resolution still stand on their own.
    n, first_at, last_shape = 0, None, None
    last_detect = 0.0
    ndet = 0
    c0 = V.process_cpu_seconds()
    t0 = time.time()
    deadline = t0 + a.seconds
    extend_at = t0 + 200
    while time.time() < deadline:
        if time.time() > extend_at and src.session:
            try:
                r = api.call("POST", "stream/extend",
                             {"session_id": src.session.get("session_id")})
                extend_at = time.time() + max(30, int(r.get("extend_after_seconds") or 200))
                print("  [%4.0fs] stream extended" % (time.time() - t0))
            except Exception as e:
                print("  extend FAILED: %s" % e)
                break
        ok, lf = src.read()
        if not ok:
            time.sleep(0.01)
            continue
        raw = lf._src
        at = getattr(raw, "pts", None)
        if at != getattr(main, "_last_pts", None):
            main._last_pts = at
            n += 1
            if first_at is None:
                first_at = time.time()
            last_shape = (raw.width, raw.height)
            if det is not None and time.time() - last_detect >= 1.0 / a.detect_fps:
                last_detect = time.time()
                det(lf.array())
                ndet += 1
        else:
            time.sleep(0.005)
    elapsed = time.time() - t0
    cpu = V.process_cpu_seconds() - c0
    src.close()

    print("\n--- measured over %.0f s ---" % elapsed)
    print("resolution        : %s" % (("%dx%d" % last_shape) if last_shape else "no frames"))
    print("frames received   : %d  (%.1f fps)" % (n, n / max(elapsed, 1e-9)))
    if det is not None:
        print("detections run    : %d  (%.2f fps, target %.1f)"
              % (ndet, ndet / max(elapsed, 1e-9), a.detect_fps))
    print("CPU used          : %.1f s  = %.0f CPU-ms per second of wall clock"
          % (cpu, cpu / max(elapsed, 1e-9) * 1000))
    print("\nHow many cameras this machine carries, at this measured cost:")
    per = cpu / max(elapsed, 1e-9) * 1000
    cores = os.cpu_count() or 1
    print("  %d cores x 1000 CPU-ms x 0.93 usable / %.0f = %.1f cameras at full tilt"
          % (cores, per, cores * 1000 * 0.93 / max(per, 1e-9)))
    print("  leave a third for the busy hour and for one box covering a failed neighbour:"
          " %.1f" % (cores * 1000 * 0.93 * 0.67 / max(per, 1e-9)))
    print("\nSTILL TO CHECK BY HAND: the bitrate. Run this alongside it and take the delta on")
    print("the analyzer machine's interface counters, or read rx_bytes from `ip -s link`:")
    print("  cat /proc/net/dev   (before and after, 60 s apart)")
    return 0


if __name__ == "__main__":
    sys.exit(main())
