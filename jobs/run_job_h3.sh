#!/usr/bin/env bash
# Render one or many ad jobs with MiniMax H3 on one rented RTX 5090 (started by phase0/onstart.sh with
# RUN_SCRIPT=jobs/run_job_h3.sh; host outside the EU/UK/US/KR - H3 licence, launch.py --territory h3).
#   JOBS="jobs/a jobs/b ..."   SHARD="i/N" when N boxes split the batch (same RUN_ID on every box)
# Order: push test -> frames -> ComfyUI + H3 FL2VA files (~45 GB) -> render (model loaded once, push every few
# clips) -> final push -> self-destroy. No interpolation: H3 renders 24 fps.
# Output per job: branch job-results, jobs/<job_id>/results/<RUN_ID>/{clips/<id>.mp4, render[_i].json, READY[.i]}.
set -uo pipefail
REPO_DIR=/workspace/repo
JOBS=${JOBS:-${JOB:?set JOBS="jobs/a jobs/b" or JOB=jobs/a}}
export WORK=/workspace/job
OUT_ROOT=$WORK/out
mkdir -p "$OUT_ROOT"
RUN_ID=${RUN_ID:-$(date -u +%Y%m%d-%H%M%S)}
MAX_SECONDS=${SPIKE_MAX_SECONDS:-14400}
SHARD=${SHARD:-0/1}
exec > >(tee -a "$OUT_ROOT/batch_${SHARD%/*}.log" /proc/1/fd/1) 2>&1
export GIT_TERMINAL_PROMPT=0 OUT_ROOT RUN_ID GH_REPO SHARD
START=$(date +%s)
stage() { echo "$(date -u +%FT%TZ) [+$(( $(date +%s) - START ))s] === $*"; echo "{\"t\": $(( $(date +%s) - START )), \"shard\": \"$SHARD\", \"stage\": \"$*\"}" >> "$OUT_ROOT/timeline_${SHARD%/*}.jsonl"; }
push() { bash $REPO_DIR/jobs/push_results.sh "$1"; }
job_id() { python3 -c "import json;print(json.load(open('$REPO_DIR/$1/job.json'))['job_id'])"; }
finish() { cp /workspace/ComfyUI/comfy.log "$OUT_ROOT/comfy_${SHARD%/*}.txt" 2>/dev/null; push "shard $SHARD final"; stage "self-destroy"
  curl -sS -X DELETE "https://console.vast.ai/api/v0/instances/${CONTAINER_ID}/" -H "Authorization: Bearer ${CONTAINER_API_KEY}" || true; }
trap finish EXIT
trap 'exit 1' TERM INT
( sleep "$MAX_SECONDS"; echo "HARD TIMEOUT after ${MAX_SECONDS}s"; pkill -f 'render_job_h3.py|main.py'; sleep 5; kill -TERM $$ ) &

# Results can only come back through GitHub: without a working token, stop now (before any download is paid for).
if [ "$(curl -s -o /dev/null -w '%{http_code}' -H "Authorization: Bearer ${GH_TOKEN:-}" "https://api.github.com/repos/$GH_REPO")" != 200 ]; then
  echo "FATAL: GH_TOKEN rejected by GitHub - renew it in the Vast account environment variables"; exit 1
fi
stage "setup shard $SHARD ($(echo $JOBS | wc -w) jobs)"
{ nvidia-smi --query-gpu=name,memory.total,driver_version --format=csv,noheader; free -g | sed -n 2p; curl -s https://ipinfo.io/country; echo; } \
  | tee "$OUT_ROOT/gpu_${SHARD%/*}.txt"
for J in $JOBS; do mkdir -p "$OUT_ROOT/$(job_id $J)"; cp "$REPO_DIR/$J/job.json" "$OUT_ROOT/$(job_id $J)/job.json"; done
push "shard $SHARD alive"
command -v ffmpeg >/dev/null && command -v rsync >/dev/null && command -v unzip >/dev/null \
  || (apt-get update -qq && apt-get install -y -qq ffmpeg rsync unzip >/dev/null)
pip install -q -U "huggingface_hub[hf_xet]" pillow

stage "frames"
SPECS=()
for J in $JOBS; do
  ID=$(job_id $J)
  bash $REPO_DIR/jobs/fetch_frames.sh "$REPO_DIR/$J/job.json" "$WORK/frames/$ID"
  [ "$(ls "$WORK/frames/$ID" | wc -l)" -gt 0 ] || { echo "FATAL: no frames for $ID"; exit 1; }
  SPECS+=("$REPO_DIR/$J/job.json:$WORK/frames/$ID:$OUT_ROOT/$ID")
done

H3_FAMILIES=fl2va bash $REPO_DIR/jobs/h3_setup.sh "$OUT_ROOT" || exit 1

stage "render (shard $SHARD)"
PUSH_HOOK="bash $REPO_DIR/jobs/push_results.sh" PUSH_EVERY=${PUSH_EVERY:-3} python3 $REPO_DIR/jobs/render_job_h3.py "${SPECS[@]}"
stage "done"
