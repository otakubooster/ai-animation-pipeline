#!/usr/bin/env bash
# Lip-sync talking shots with LatentSync 1.6 on one rented RTX 5090 (started by phase0/onstart.sh with
# RUN_SCRIPT=jobs/run_lipsync.sh). Inputs: branch lipsync-in, folder runs/$LS_RUN/ (manifest.json + per job
# <job_id>/<id>.mp4 25 fps video window + <id>.wav VO slice, made by the session's lip-sync prep).
# Output: branch job-results, jobs/<job_id>/results/$RUN_ID/lipsync/<id>.mp4 (+ report.json). Self-destroys.
# Face detection: MediaPipe (jobs/lipsync/mp_face_detector.py), not InsightFace (non-commercial models).
set -uo pipefail
REPO_DIR=/workspace/repo
export WORK=/workspace/job OUT_ROOT=/workspace/job/out
mkdir -p "$OUT_ROOT"
RUN_ID=${RUN_ID:-$(date -u +%Y%m%d-%H%M%S)}
LS_RUN=${LS_RUN:?set LS_RUN}
MAX_SECONDS=${SPIKE_MAX_SECONDS:-10800}
exec > >(tee -a "$OUT_ROOT/lipsync.log" /proc/1/fd/1) 2>&1
export GIT_TERMINAL_PROMPT=0 OUT_ROOT RUN_ID GH_REPO
START=$(date +%s)
stage() { echo "$(date -u +%FT%TZ) [+$(( $(date +%s) - START ))s] === $*"; echo "{\"t\": $(( $(date +%s) - START )), \"stage\": \"$*\"}" >> "$OUT_ROOT/timeline.jsonl"; }
push() { bash $REPO_DIR/jobs/push_results.sh "$1"; }
finish() { push "lipsync final"; stage "self-destroy"
  curl -sS -X DELETE "https://console.vast.ai/api/v0/instances/${CONTAINER_ID}/" -H "Authorization: Bearer ${CONTAINER_API_KEY}" || true; }
trap finish EXIT
trap 'exit 1' TERM INT
( sleep "$MAX_SECONDS"; echo "HARD TIMEOUT"; pkill -f ls_driver.py; sleep 5; kill -TERM $$ ) &

if [ "$(curl -s -o /dev/null -w '%{http_code}' -H "Authorization: Bearer ${GH_TOKEN:-}" "https://api.github.com/repos/$GH_REPO")" != 200 ]; then
  echo "FATAL: GH_TOKEN rejected"; exit 1
fi
stage "setup"
{ nvidia-smi --query-gpu=name,memory.total,driver_version --format=csv,noheader; free -g | sed -n 2p; curl -s https://ipinfo.io/country; echo; } | tee "$OUT_ROOT/gpu.txt"
apt-get update -qq && apt-get install -y -qq ffmpeg git libegl1 libgles2 libgl1 >/dev/null
stage "inputs"
git clone -q --depth 1 -b lipsync-in "https://x-access-token:${GH_TOKEN}@github.com/${GH_REPO}.git" /workspace/ls_in || { echo "FATAL: no lipsync-in branch"; exit 1; }
IN=/workspace/ls_in/runs/$LS_RUN
[ -f $IN/manifest.json ] || { echo "FATAL: no manifest for $LS_RUN"; exit 1; }
python3 -c "import json;m=json.load(open('$IN/manifest.json'));print(len(m['items']),'items')"
for j in $(python3 -c "import json;print(' '.join(sorted({i['job_id'] for i in json.load(open('$IN/manifest.json'))['items']})))"); do mkdir -p $OUT_ROOT/$j/lipsync; done
push "lipsync alive"

stage "latentsync install"
git clone -q --depth 1 https://github.com/bytedance/LatentSync /workspace/LatentSync
cd /workspace/LatentSync
python3 -m venv --system-site-packages /workspace/lsenv      # keeps the image's Blackwell-ready torch
. /workspace/lsenv/bin/activate
pip install -q diffusers==0.32.2 transformers==4.48.0 decord==0.6.0 "accelerate>=0.26.1,<1.0" einops==0.7.0 omegaconf==2.3.0 \
  opencv-python-headless==4.9.0.80 mediapipe python_speech_features==0.6 librosa==0.10.1 scenedetect==0.6.1 ffmpeg-python==0.2.0 \
  imageio==2.31.1 imageio-ffmpeg==0.5.1 kornia==0.8.0 DeepCache==0.1.1 "huggingface-hub==0.30.2" "numpy<2" 2>&1 | tail -3
python3 -c "import torch;print('torch',torch.__version__,torch.cuda.get_device_name(0))"
cp $REPO_DIR/jobs/lipsync/mp_face_detector.py latentsync/utils/face_detector.py
mkdir -p checkpoints/auxiliary
curl -sSL -o checkpoints/auxiliary/face_landmarker.task https://storage.googleapis.com/mediapipe-models/face_landmarker/face_landmarker/float16/latest/face_landmarker.task
huggingface-cli download ByteDance/LatentSync-1.6 latentsync_unet.pt whisper/tiny.pt --local-dir checkpoints 2>&1 | tail -2
ls -la checkpoints checkpoints/whisper
push "lipsync models ready"

stage "lipsync"
PUSH_HOOK="bash $REPO_DIR/jobs/push_results.sh" PUSH_EVERY=${PUSH_EVERY:-6} python3 $REPO_DIR/jobs/lipsync/ls_driver.py $IN/manifest.json $IN $OUT_ROOT
stage "done"
