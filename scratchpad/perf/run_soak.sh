#!/bin/sh
cd "$(dirname "$0")"
S=${SECS:-25}
echo "REAL-TIME SOAK on $(nproc) cores — decode paced to the source rate, detect at df"
echo "(does NOT include the aiortc RTP/SRTP receive layer; see webrtc_loopback.py)"
echo
echo "-- CURRENT code shape: PyTorch yolov8n imgsz 640, all cores, eager BGR"
for n in 1 2 3 4; do
  python3 soak.py $n store_1080p.mp4 6 $S current 2>/dev/null
done
echo
echo "-- FAST: OpenVINO fp32 640x384, one thread per camera, lazy BGR"
for n in 4 8 10 12 14 16 18; do
  python3 soak.py $n store_1080p.mp4 6 $S fast 2>/dev/null
done
echo
echo "-- FAST at 720p"
for n in 14 18 22 26; do
  python3 soak.py $n store_720p.mp4 6 $S fast 2>/dev/null
done
echo
echo "-- FAST at detect_fps 3, 1080p"
for n in 16 20 24 28; do
  python3 soak.py $n store_1080p.mp4 3 $S fast 2>/dev/null
done
