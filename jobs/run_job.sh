#!/usr/bin/env bash
# Render a whole job on the rented 5090 (started by phase0/onstart.sh with RUN_SCRIPT=jobs/run_job.sh JOB=<dir>).
# Order: prove the GitHub push works -> voiceover candidates -> download frames + models -> render all shots
# (push every few clips) -> 16->32 fps interpolation -> final push -> self-destroy.
# Everything is mirrored to the container log so it can be read from outside via the Vast logs API.
set -uo pipefail

REPO_DIR=/workspace/repo
JOB_DIR=$REPO_DIR/${JOB:?JOB env var required, e.g. jobs/meow-mouth-uk}
JOB_JSON=$JOB_DIR/job.json
JOB_ID=$(python -c "import json;print(json.load(open('$JOB_JSON'))['job_id'])")
WORK=/workspace/job
OUT=$WORK/out
mkdir -p "$OUT"
[ -f "$WORK/run_id" ] && RUN_ID=$(cat "$WORK/run_id") || { RUN_ID=${RUN_ID:-$(date -u +%Y%m%d-%H%M%S)}; echo "$RUN_ID" > "$WORK/run_id"; }
MAX_SECONDS=${SPIKE_MAX_SECONDS:-9000}
exec > >(tee -a "$OUT/job.log" /proc/1/fd/1) 2>&1
export GIT_TERMINAL_PROMPT=0
START=$(date +%s)
stage() { echo "$(date -u +%FT%TZ) [+$(( $(date +%s) - START ))s] === $*"; echo "{\"t\": $(( $(date +%s) - START )), \"stage\": \"$*\"}" >> "$OUT/timeline.jsonl"; }

export OUT JOB_ID RUN_ID GH_REPO
push() { bash $REPO_DIR/jobs/push_results.sh "$1"; }

finish() { push "final"; stage "self-destroy"
  curl -sS -X DELETE "https://console.vast.ai/api/v0/instances/${CONTAINER_ID}/" -H "Authorization: Bearer ${CONTAINER_API_KEY}" || true; }
trap finish EXIT
trap 'exit 1' TERM INT
( sleep "$MAX_SECONDS"; echo "HARD TIMEOUT after ${MAX_SECONDS}s"; pkill -f 'render_job.py|tts_job.py|fetch_models.py|ffmpeg'; sleep 5; kill -TERM $$ ) &

stage "setup"
nvidia-smi --query-gpu=name,memory.total,driver_version --format=csv,noheader | tee "$OUT/gpu.txt"
cp "$JOB_JSON" "$OUT/job.json"
push "box alive - upload test"

command -v ffmpeg >/dev/null && command -v rsync >/dev/null && command -v espeak-ng >/dev/null \
  || (apt-get update -qq && apt-get install -y -qq ffmpeg rsync espeak-ng unzip >/dev/null)
pip install -q -U "huggingface_hub[hf_xet]" pillow

stage "voiceover candidates (kokoro, isolated venv so the render stack's torch is untouched)"
python -m venv --system-site-packages /workspace/tts-venv
/workspace/tts-venv/bin/pip install -q soundfile "kokoro>=0.9.2" || /workspace/tts-venv/bin/pip install -q soundfile kokoro || echo "kokoro install failed"
timeout 1200 /workspace/tts-venv/bin/python $REPO_DIR/jobs/tts_job.py "$JOB_JSON" "$OUT" || echo "TTS failed (non-fatal)"
push "voiceover"

stage "download frames from release"
REL=$(python -c "import json;r=json.load(open('$JOB_JSON'))['frames_release'];print(r['repo'],r['tag'],r['asset'])")
read -r REL_REPO REL_TAG REL_ASSET <<<"$REL"
ASSET_ID=$(curl -sS -H "Authorization: Bearer $GH_TOKEN" "https://api.github.com/repos/$REL_REPO/releases/tags/$REL_TAG" \
  | python -c "import sys,json;print(next(a['id'] for a in json.load(sys.stdin)['assets'] if a['name']=='$REL_ASSET'))")
mkdir -p $WORK/frames
curl -sSL -H "Authorization: Bearer $GH_TOKEN" -H "Accept: application/octet-stream" \
  "https://api.github.com/repos/$REL_REPO/releases/assets/$ASSET_ID" -o $WORK/frames.zip
unzip -q -o -j $WORK/frames.zip -d $WORK/frames && ls $WORK/frames | wc -l

stage "engine A: models"
LX_DIR=$(python -c "import lightx2v,os;print(os.path.dirname(os.path.dirname(lightx2v.__file__)))" 2>/dev/null || true)
if [ -z "$LX_DIR" ] || [ ! -d "$LX_DIR/configs" ]; then
  [ -d /workspace/LightX2V ] || git clone -q --depth 1 https://github.com/ModelTC/LightX2V.git /workspace/LightX2V
  python -c "import lightx2v" 2>/dev/null || pip install -q -e /workspace/LightX2V
  LX_DIR=/workspace/LightX2V
fi
(cd $LX_DIR && echo "LightX2V $(git rev-parse --short HEAD 2>/dev/null)") | tee "$OUT/versions.txt"
export PYTHONPATH="$REPO_DIR/phase0:${PYTHONPATH:-}"
timeout 2700 python $REPO_DIR/phase0/fetch_models.py engine_a
cp /workspace/spike/results/models.json "$OUT/models.json"
A=$(python -c "import json;m=json.load(open('$OUT/models.json'))['engine_a'];print(m['model_path'],m['high'],m['low'])")
read -r A_BASE A_HIGH A_LOW <<<"$A"

stage "render all shots"
PUSH_HOOK="bash $REPO_DIR/jobs/push_results.sh clips" PUSH_EVERY=5 \
  python $REPO_DIR/jobs/render_job.py "$JOB_JSON" $WORK/frames "$OUT" "$A_BASE" "$A_HIGH" "$A_LOW" \
  "$LX_DIR/configs/wan22/extreme/wan_moe_i2v_distill_nvfp4_sparse_attn.json"
push "all clips rendered"

stage "interpolate 16 -> 32 fps"
mkdir -p "$OUT/clips_32fps"
ls "$OUT"/clips/*.mp4 | xargs -P "$(nproc)" -I{} sh -c \
  'ffmpeg -loglevel error -y -i "{}" -vf "minterpolate=fps=32:mi_mode=mci:mc_mode=aobmc:me_mode=bidir:vsbmc=1" -c:v libx264 -crf 17 -pix_fmt yuv420p "$OUT/clips_32fps/$(basename {})"'
stage "done"
