#!/usr/bin/env bash
# Render one or many ad jobs on one rented 5090 (started by phase0/onstart.sh with RUN_SCRIPT=jobs/run_job.sh).
#   JOBS="jobs/a jobs/b ..."   (or JOB=jobs/a for a single ad)
# The model loads once for the whole batch. Order: prove the GitHub push works -> download every job's frames
# + the models -> render job by job (push every few clips) -> each finished job is interpolated 16->32 fps on
# the CPU while the GPU renders the next one, then pushed -> final push -> self-destroy.
# Output per job: branch job-results, jobs/<job_id>/results/<RUN_ID>/{clips,clips_32fps,render.json}.
# Everything is mirrored to the container log (readable via the Vast logs API).
set -uo pipefail

REPO_DIR=/workspace/repo
JOBS=${JOBS:-${JOB:?set JOBS="jobs/a jobs/b" or JOB=jobs/a}}
WORK=/workspace/job
OUT_ROOT=$WORK/out
mkdir -p "$OUT_ROOT"
[ -f "$WORK/run_id" ] && RUN_ID=$(cat "$WORK/run_id") || { RUN_ID=${RUN_ID:-$(date -u +%Y%m%d-%H%M%S)}; echo "$RUN_ID" > "$WORK/run_id"; }
MAX_SECONDS=${SPIKE_MAX_SECONDS:-9000}
exec > >(tee -a "$OUT_ROOT/batch.log" /proc/1/fd/1) 2>&1
export GIT_TERMINAL_PROMPT=0 OUT_ROOT RUN_ID GH_REPO
START=$(date +%s)
stage() { echo "$(date -u +%FT%TZ) [+$(( $(date +%s) - START ))s] === $*"; echo "{\"t\": $(( $(date +%s) - START )), \"stage\": \"$*\"}" >> "$OUT_ROOT/timeline.jsonl"; }
push() { bash $REPO_DIR/jobs/push_results.sh "$1"; }
job_id() { python -c "import json;print(json.load(open('$REPO_DIR/$1/job.json'))['job_id'])"; }

finish() { push "final"; stage "self-destroy"
  curl -sS -X DELETE "https://console.vast.ai/api/v0/instances/${CONTAINER_ID}/" -H "Authorization: Bearer ${CONTAINER_API_KEY}" || true; }
trap finish EXIT
trap 'exit 1' TERM INT
( sleep "$MAX_SECONDS"; echo "HARD TIMEOUT after ${MAX_SECONDS}s"; pkill -f 'render_job.py|fetch_models.py|ffmpeg'; sleep 5; kill -TERM $$ ) &

stage "setup ($(echo $JOBS | wc -w) jobs: $JOBS)"
nvidia-smi --query-gpu=name,memory.total,driver_version --format=csv,noheader | tee "$OUT_ROOT/gpu.txt"
for J in $JOBS; do mkdir -p "$OUT_ROOT/$(job_id $J)"; cp "$REPO_DIR/$J/job.json" "$OUT_ROOT/$(job_id $J)/job.json"; done
push "box alive - upload test"
command -v ffmpeg >/dev/null && command -v rsync >/dev/null && command -v unzip >/dev/null \
  || (apt-get update -qq && apt-get install -y -qq ffmpeg rsync unzip >/dev/null)
pip install -q -U "huggingface_hub[hf_xet]" pillow

stage "download frames for every job"
SPECS=()
for J in $JOBS; do
  ID=$(job_id $J)
  bash $REPO_DIR/jobs/fetch_frames.sh "$REPO_DIR/$J/job.json" "$WORK/frames/$ID"
  SPECS+=("$REPO_DIR/$J/job.json:$WORK/frames/$ID:$OUT_ROOT/$ID")
done

stage "engine A: models"
LX_DIR=$(python -c "import lightx2v,os;print(os.path.dirname(os.path.dirname(lightx2v.__file__)))" 2>/dev/null || true)
if [ -z "$LX_DIR" ] || [ ! -d "$LX_DIR/configs" ]; then
  [ -d /workspace/LightX2V ] || git clone -q --depth 1 https://github.com/ModelTC/LightX2V.git /workspace/LightX2V
  python -c "import lightx2v" 2>/dev/null || pip install -q -e /workspace/LightX2V
  LX_DIR=/workspace/LightX2V
fi
(cd $LX_DIR && echo "LightX2V $(git rev-parse --short HEAD 2>/dev/null)") | tee "$OUT_ROOT/versions.txt"
export PYTHONPATH="$REPO_DIR/phase0:${PYTHONPATH:-}"
if [ -z "${TALK_ONLY:-}" ]; then
  timeout 2700 python $REPO_DIR/phase0/fetch_models.py engine_a
  cp /workspace/spike/results/models.json "$OUT_ROOT/models.json"
  A=$(python -c "import json;m=json.load(open('$OUT_ROOT/models.json'))['engine_a'];print(m['model_path'],m['high'],m['low'])")
  read -r A_BASE A_HIGH A_LOW <<<"$A"
fi

# Lip-sync (InfiniteTalk) for jobs with talking-presenter segments: runs before the b-roll so each job's READY
# marker (set after its b-roll) covers both. A failure here never stops the b-roll render.
TALK_ITEMS=$WORK/talk_items.json
python - "$TALK_ITEMS" "$WORK" "$OUT_ROOT" "$REPO_DIR" $JOBS <<'PY'
import json, sys
out, work, root, repo, jobs = sys.argv[1], sys.argv[2], sys.argv[3], sys.argv[4], sys.argv[5:]
items = []
for J in jobs:
    j = json.load(open(f"{repo}/{J}/job.json"))
    for g in j.get("talk", {}).get("segments", []):
        items.append({"id": f"{j['job_id']}/{g['id']}", "image": f"{work}/frames/{j['job_id']}/{g['frame']}",
                      "audio": f"{work}/frames/{j['job_id']}/talk/{g['id']}.wav", "out": f"{root}/{j['job_id']}/talk/{g['id']}.mp4",
                      "prompt": "The person talks naturally and warmly straight to the camera, with small natural head movements and blinks."})
json.dump(items, open(out, "w"), indent=1)
print(f"{len(items)} lip-sync segments")
PY
if [ -z "${SKIP_TALK:-}" ] && [ "$(python -c "import json;print(len(json.load(open('$TALK_ITEMS'))))")" -gt 0 ]; then
  stage "lip-sync: models"
  timeout 3000 python $REPO_DIR/phase0/fetch_talk.py && cp /workspace/spike/results/talk_models.json "$OUT_ROOT/talk_models.json"
  stage "lip-sync: render $(python -c "import json;print(len(json.load(open('$TALK_ITEMS'))))") segments"
  PUSH_HOOK="bash $REPO_DIR/jobs/push_results.sh talk" timeout ${TALK_TIMEOUT:-7200} python $REPO_DIR/jobs/talk_render.py \
    --models "$OUT_ROOT/talk_models.json" --base-config "$LX_DIR/configs/infinitetalk/5090/infinitetalk_single_distilled.json" \
    --size "${TALK_SIZE:-720}" "$TALK_ITEMS" || echo "lip-sync stage failed (b-roll continues)"
fi

if [ -n "${TALK_ONLY:-}" ]; then stage "talk-only run: done"; exit 0; fi
stage "render all jobs (model loaded once)"
# After each job, ON_JOB_DONE interpolates it in the background (CPU) and pushes it, while the GPU continues.
export ON_JOB_DONE="bash $REPO_DIR/jobs/finish_job.sh"
PUSH_HOOK="bash $REPO_DIR/jobs/push_results.sh clips" PUSH_EVERY=10 \
  python $REPO_DIR/jobs/render_job.py --model-path "$A_BASE" --high "$A_HIGH" --low "$A_LOW" \
  --base-config "$LX_DIR/configs/wan22/extreme/wan_moe_i2v_distill_nvfp4_sparse_attn.json" "${SPECS[@]}"
stage "waiting for every job's interpolation (READY markers)"
for i in $(seq 1 180); do
  missing=0; for S in "${SPECS[@]}"; do [ -f "${S##*:}/READY" ] || missing=$((missing+1)); done
  [ $missing -eq 0 ] && break; sleep 10
done
stage "done ($missing jobs not ready)"
