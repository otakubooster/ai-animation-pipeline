#!/usr/bin/env bash
# Called by render_job.py when one job's clips are all rendered: interpolate 16 -> 32 fps on the CPU
# (in the background, so the GPU moves on to the next job) and push that job's results.
# Usage: finish_job.sh OUT_DIR   (needs OUT_ROOT, RUN_ID, GH_REPO, GH_TOKEN exported)
OUT=$1
(
  mkdir -p "$OUT/clips_32fps"
  ls "$OUT"/clips/*.mp4 | OUT=$OUT xargs -P "$(nproc)" -I{} sh -c \
    'ffmpeg -loglevel error -y -i "{}" -vf "minterpolate=fps=32:mi_mode=mci:mc_mode=aobmc:me_mode=bidir:vsbmc=1" -c:v libx264 -crf 17 -pix_fmt yuv420p "$OUT/clips_32fps/$(basename {})"'
  touch "$OUT/READY"
  bash "$(dirname "$0")/push_results.sh" "$(basename "$OUT") ready"
) &
