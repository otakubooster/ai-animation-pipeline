#!/usr/bin/env bash
# Lip-sync spike (InfiniteTalk via LightX2V) on one rented 5090: download the weights, lip-sync a 13 s English
# sample onto a presenter frame at 720p and 480p, time it, push results to job-results, self-destroy.
# Started by phase0/onstart.sh with RUN_SCRIPT=jobs/talk_spike.sh.
set -uo pipefail
REPO_DIR=/workspace/repo
WORK=/workspace/job
OUT_ROOT=$WORK/out
OUT=$OUT_ROOT/talk-spike
mkdir -p "$OUT"
RUN_ID=${RUN_ID:-$(date -u +%Y%m%d-%H%M%S)}
MAX_SECONDS=${SPIKE_MAX_SECONDS:-3600}
exec > >(tee -a "$OUT_ROOT/batch.log" /proc/1/fd/1) 2>&1
export GIT_TERMINAL_PROMPT=0 OUT_ROOT RUN_ID GH_REPO
START=$(date +%s)
stage() { echo "$(date -u +%FT%TZ) [+$(( $(date +%s) - START ))s] === $*"; echo "{\"t\": $(( $(date +%s) - START )), \"stage\": \"$*\"}" >> "$OUT_ROOT/timeline.jsonl"; }
push() { bash $REPO_DIR/jobs/push_results.sh "$1"; }
finish() { cp /workspace/spike/results/talk_models.json "$OUT/" 2>/dev/null; df -h /workspace > "$OUT/disk.txt"; push "final"; stage "self-destroy"
  curl -sS -X DELETE "https://console.vast.ai/api/v0/instances/${CONTAINER_ID}/" -H "Authorization: Bearer ${CONTAINER_API_KEY}" || true; }
trap finish EXIT
trap 'exit 1' TERM INT
( sleep "$MAX_SECONDS"; echo "HARD TIMEOUT"; pkill -f 'talk_render.py|fetch_talk.py'; sleep 5; kill -TERM $$ ) &

stage "setup"
nvidia-smi --query-gpu=name,memory.total,driver_version --format=csv,noheader | tee "$OUT/gpu.txt"
push "box alive - upload test"
command -v ffmpeg >/dev/null && command -v rsync >/dev/null || (apt-get update -qq && apt-get install -y -qq ffmpeg rsync >/dev/null)
pip install -q -U "huggingface_hub[hf_xet]" pillow

LX_DIR=$(python -c "import lightx2v,os;print(os.path.dirname(os.path.dirname(lightx2v.__file__)))" 2>/dev/null || true)
if [ -z "$LX_DIR" ] || [ ! -d "$LX_DIR/configs/infinitetalk" ]; then
  [ -d /workspace/LightX2V ] || git clone -q --depth 1 https://github.com/ModelTC/LightX2V.git /workspace/LightX2V
  pip install -q -e /workspace/LightX2V
  LX_DIR=/workspace/LightX2V
fi
(cd $LX_DIR && echo "LightX2V $(git rev-parse --short HEAD 2>/dev/null)") | tee "$OUT/versions.txt"

stage "talk models"
timeout 3000 python $REPO_DIR/phase0/fetch_talk.py
cp /workspace/spike/results/talk_models.json "$OUT/"
push "talk models"

stage "lip-sync tests"
ffmpeg -v error -y -i $REPO_DIR/phase0/talk/sample_en.mp3 -ac 1 -ar 16000 $WORK/sample.wav
for SIZE in 720 480; do
  echo "[{\"id\": \"sample_$SIZE\", \"image\": \"$REPO_DIR/phase0/talk/presenter.jpg\", \"audio\": \"$WORK/sample.wav\", \"out\": \"$OUT/sample_$SIZE.mp4\", \"prompt\": \"A woman vet talks warmly and naturally to the camera at her desk.\"}]" > $WORK/items_$SIZE.json
  timeout 1500 python $REPO_DIR/jobs/talk_render.py --models $OUT/talk_models.json \
    --base-config $LX_DIR/configs/infinitetalk/5090/infinitetalk_single_distilled.json --size $SIZE $WORK/items_$SIZE.json \
    2>&1 | tail -n 400 > "$OUT/run_$SIZE.log"
  grep -E '^\{' "$OUT/run_$SIZE.log" | tail -3
  push "test $SIZE"
done
stage "done"
