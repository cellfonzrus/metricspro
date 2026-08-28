#!/usr/bin/env python3
"""What does RECEIVING a WebRTC stream cost, before a single pixel is looked at?

Every capacity estimate so far has priced decode + inference. A Nest camera does not hand
you an mp4: aiortc must depacketize RTP, decrypt SRTP, run a jitter buffer, answer RTCP and
feed the decoder — and all of that except the crypto primitive itself is Python. On a central
box holding 21 streams that is a line item, not a rounding error, so it is measured here.

Shape: two processes over real UDP on loopback. The SENDER re-encodes the test clip and
offers it; the RECEIVER does exactly what WebRtcFrameSource._pump does. Only the RECEIVER's
CPU time is reported, so the encoder's cost cannot contaminate it.

  python3 webrtc_loopback.py            # runs both halves, prints the receiver's bill
  python3 webrtc_loopback.py --recv N   # internal
"""
import argparse, asyncio, json, os, resource, socket, subprocess, sys, time

HERE = os.path.dirname(os.path.abspath(__file__))
PORT = 45871


def cpu_now():
    r = resource.getrusage(resource.RUSAGE_SELF)
    return r.ru_utime + r.ru_stime


# ────────────────────────────────────────────────────────────────────────────────────
async def receiver(seconds, to_bgr):
    from aiortc import RTCPeerConnection, RTCSessionDescription

    pc = RTCPeerConnection()
    pc.addTransceiver("video", direction="recvonly")

    stats = {"frames": 0, "bytes": 0, "cpu_pump": 0.0, "cpu_bgr": 0.0,
             "w": 0, "h": 0}
    done = asyncio.Event()

    @pc.on("track")
    def on_track(track):
        async def pump():
            t_end = time.perf_counter() + seconds
            c0 = cpu_now()
            while time.perf_counter() < t_end:
                try:
                    frame = await track.recv()
                except Exception:
                    break
                stats["frames"] += 1
                stats["w"], stats["h"] = frame.width, frame.height
                if to_bgr:
                    b0 = cpu_now()
                    frame.to_ndarray(format="bgr24")
                    stats["cpu_bgr"] += cpu_now() - b0
            stats["cpu_pump"] = cpu_now() - c0 - stats["cpu_bgr"]
            done.set()
        asyncio.ensure_future(pump())

    # signalling over a plain TCP socket
    s = socket.socket()
    s.setsockopt(socket.SOL_SOCKET, socket.SO_REUSEADDR, 1)
    s.bind(("127.0.0.1", PORT))
    s.listen(1)
    print("READY", flush=True)
    conn, _ = s.accept()
    buf = b""
    while b"\n" not in buf:
        buf += conn.recv(65536)
    offer = json.loads(buf.split(b"\n")[0])
    await pc.setRemoteDescription(RTCSessionDescription(offer["sdp"], offer["type"]))
    await pc.setLocalDescription(await pc.createAnswer())
    conn.sendall(json.dumps({"sdp": pc.localDescription.sdp,
                             "type": pc.localDescription.type}).encode() + b"\n")

    c_all0 = cpu_now()
    t0 = time.perf_counter()
    try:
        await asyncio.wait_for(done.wait(), timeout=seconds + 20)
    except asyncio.TimeoutError:
        pass
    wall = time.perf_counter() - t0
    cpu_all = cpu_now() - c_all0
    await pc.close()
    conn.close(); s.close()
    n = max(1, stats["frames"])
    print(json.dumps({
        "frames": stats["frames"], "wall_s": round(wall, 2),
        "resolution": "%dx%d" % (stats["w"], stats["h"]),
        "fps": round(stats["frames"] / wall, 1),
        "cpu_total_s": round(cpu_all, 3),
        "cpu_bgr_s": round(stats["cpu_bgr"], 3),
        "cpu_recv_and_decode_s": round(cpu_all - stats["cpu_bgr"], 3),
        "cpu_ms_per_frame_recv_decode": round((cpu_all - stats["cpu_bgr"]) / n * 1000, 3),
        "cpu_ms_per_frame_bgr": round(stats["cpu_bgr"] / n * 1000, 3),
        "cpu_ms_per_video_second": round(cpu_all / wall * 1000, 1),
    }), flush=True)


# ────────────────────────────────────────────────────────────────────────────────────
async def sender(clip, seconds):
    from aiortc import RTCPeerConnection, RTCSessionDescription
    from aiortc.contrib.media import MediaPlayer

    player = MediaPlayer(clip, loop=True)
    pc = RTCPeerConnection()
    pc.addTrack(player.video)
    await pc.setLocalDescription(await pc.createOffer())
    while pc.iceGatheringState != "complete":
        await asyncio.sleep(0.1)

    s = socket.socket()
    for _ in range(100):
        try:
            s.connect(("127.0.0.1", PORT)); break
        except OSError:
            await asyncio.sleep(0.1)
    s.sendall(json.dumps({"sdp": pc.localDescription.sdp,
                          "type": pc.localDescription.type}).encode() + b"\n")
    buf = b""
    while b"\n" not in buf:
        buf += s.recv(65536)
    ans = json.loads(buf.split(b"\n")[0])
    await pc.setRemoteDescription(RTCSessionDescription(ans["sdp"], ans["type"]))
    await asyncio.sleep(seconds + 3)
    await pc.close()
    s.close()


if __name__ == "__main__":
    ap = argparse.ArgumentParser()
    ap.add_argument("--recv", action="store_true")
    ap.add_argument("--seconds", type=float, default=12.0)
    ap.add_argument("--clip", default=HERE + "/store_1080p.mp4")
    ap.add_argument("--bgr", action="store_true",
                    help="also convert every frame to BGR, as WebRtcFrameSource does today")
    a = ap.parse_args()
    if a.recv:
        asyncio.run(receiver(a.seconds, a.bgr))
    else:
        env = dict(os.environ)
        rp = subprocess.Popen([sys.executable, __file__, "--recv",
                               "--seconds", str(a.seconds)] + (["--bgr"] if a.bgr else []),
                              stdout=subprocess.PIPE, env=env, text=True)
        line = rp.stdout.readline()
        if "READY" not in line:
            print("receiver failed:", line); sys.exit(1)
        asyncio.run(sender(a.clip, a.seconds))
        out = rp.stdout.read()
        rp.wait()
        print(out.strip())
