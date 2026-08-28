#!/bin/sh
cd "$(dirname "$0")"
row() {
  MODE=$1 CUR_THREADS=4 python3 bench_pipeline.py "$2" "$3" "$4" 2>/dev/null \
   | python3 -c "import sys,json;d=json.load(sys.stdin);b=d['cpu_ms_per_video_second'];print('%-12s %-24s df=%-4s sf=%-4s  dec %6.1f  bgr %6.1f  det %7.1f  other %4.1f  TOTAL %7.1f  cam/4core %5.1f' % (d['mode'],'$2','$3','$4',b['decode'],b['to_bgr'],b['detect'],b['track']+b['geom'],d['total_cpu_ms_per_video_second'],4000.0/d['total_cpu_ms_per_video_second']))"
}
echo "CPU-ms consumed per SECOND OF VIDEO, per camera. 4000 CPU-ms/s = a fully used 4-core box."
echo
for clip in store_1080p.mp4 store_720p.mp4 store_1080p_quiet.mp4; do
  for df in 6 3 2; do
    row current "$clip" "$df" 30
    row fast "$clip" "$df" 30
    row fast_gated "$clip" "$df" 30
  done
  echo
done
echo "15 fps source (Nest often delivers below 30):"
row current store_1080p_15fps.mp4 6 15
row fast store_1080p_15fps.mp4 6 15
row fast_gated store_1080p_15fps.mp4 6 15
