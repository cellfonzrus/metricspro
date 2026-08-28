#!/bin/sh
cd "$(dirname "$0")"
echo "aiortc RECEIVE cost — RTP depacketize + SRTP decrypt + jitter buffer + H.264 decode"
echo
for c in store_1080p.mp4 store_720p.mp4; do
  echo "--- $c  (no BGR conversion)"
  timeout 200 python3 webrtc_loopback.py --seconds 12 --clip $c 2>/dev/null | tail -1
  echo "--- $c  (+ to_ndarray bgr24 on EVERY frame, as WebRtcFrameSource does today)"
  timeout 200 python3 webrtc_loopback.py --seconds 12 --clip $c --bgr 2>/dev/null | tail -1
done
