#!/usr/bin/env bash
# Phase 0 spike, run on the rented 5090 by onstart.sh.
# Engine A (LightX2V NVFP4) first, then Engine B (ComfyUI fp8 + LoRAs).
# Results are pushed to the phase0-results branch after every stage, so a crash keeps partial data.
# The instance destroys itself on exit (success, failure or timeout).
set -uo pipefail

REPO_DIR=/workspace/repo
SPIKE=/workspace/spike
RESULTS=$SPIKE/results
RUN_ID=${RUN_ID:-$(date -u +%Y%m%d-%H%M%S)}
MAX_SECONDS=${SPIKE_MAX_SECONDS:-9000}
mkdir -p "$RESULTS/clips"
exec > >(tee -a "$RESULTS/spike.log") 2>&1
START=$(date +%s)
cd "$REPO_DIR/phase0"

stage() { echo "$(date -u +%FT%TZ) [+$(( $(date +%s) - START ))s] === $*"; echo "{\"t\": $(( $(date +%s) - START )), \"stage\": \"$*\"}" >> "$RESULTS/timeline.jsonl"; }

push_results() {
  stage "push results ($1)"
  python report.py >/dev/null 2>&1 || true
  local dst=/workspace/results-repo
  if [ ! -d $dst/.git ]; then
    git clone -q --depth 1 "https://x-access-token:${GH_TOKEN}@github.com/${GH_REPO}.git" $dst || return 0
    (cd $dst && (git fetch -q --depth 1 origin phase0-results && git checkout -q -B phase0-results FETCH_HEAD) || git checkout -q --orphan phase0-results)
  fi
  mkdir -p $dst/phase0/results/$RUN_ID
  rsync -a --exclude '*.tmp' "$RESULTS/" $dst/phase0/results/$RUN_ID/
  (cd $dst && git add -A phase0/results && git -c user.name=vast-spike -c user.email=spike@localhost \
     commit -qm "Phase 0 spike results $RUN_ID ($1)" && git push -q origin HEAD:phase0-results) || true
}

self_destroy() {
  push_results "final"
  stage "self-destroy"
  curl -sS -X DELETE "https://console.vast.ai/api/v0/instances/${CONTAINER_ID}/" \
       -H "Authorization: Bearer ${CONTAINER_API_KEY}" || true
  pip install -q vastai >/dev/null 2>&1 && vastai destroy instance "$CONTAINER_ID" --api-key "$CONTAINER_API_KEY" || true
}
trap self_destroy EXIT
trap 'exit 1' TERM INT
# Hard stop: kill the workers after MAX_SECONDS, then this script; the EXIT trap still pushes and destroys.
( sleep "$MAX_SECONDS"; echo "HARD TIMEOUT after ${MAX_SECONDS}s" >> "$RESULTS/spike.log"
  pkill -f 'engine_a.py|engine_b.py|fetch_models.py|main.py --listen|pip install' ; sleep 5; kill -TERM $$ ) &

stage "setup"
nvidia-smi | tee "$RESULTS/nvidia-smi.txt"
command -v ffmpeg >/dev/null && command -v rsync >/dev/null || (apt-get update -qq && apt-get install -y -qq ffmpeg rsync git >/dev/null)
pip install -q -U "huggingface_hub[hf_xet]" pillow
export HF_HUB_ENABLE_HF_TRANSFER=0

# Use the LightX2V source shipped in the image if there is one (keeps code and kernels matched).
LX_DIR=$(python -c "import lightx2v,os;print(os.path.dirname(os.path.dirname(lightx2v.__file__)))" 2>/dev/null || true)
if [ -z "$LX_DIR" ] || [ ! -d "$LX_DIR/configs" ]; then
  git clone -q --depth 1 https://github.com/ModelTC/LightX2V.git /workspace/LightX2V
  [ -z "$LX_DIR" ] && pip install -q -e /workspace/LightX2V
  LX_DIR=/workspace/LightX2V
fi
echo "LightX2V at $LX_DIR ($(cd $LX_DIR && git rev-parse --short HEAD 2>/dev/null || echo no-git))" | tee "$RESULTS/lightx2v_version.txt"
export PYTHONPATH="$REPO_DIR/phase0:${PYTHONPATH:-}"
python prepare_cases.py "$LX_DIR" | tee -a "$RESULTS/cases.txt"
cp $SPIKE/cases.json "$RESULTS/cases.json"; cp -r $SPIKE/frames "$RESULTS/frames"

# ---------------- Engine A ----------------
stage "engine A: download"
python fetch_models.py engine_a
A=$(python -c "import json;m=json.load(open('$RESULTS/models.json'))['engine_a'];print(m.get('model_path',''),m.get('high',''),m.get('low',''))")
read -r A_BASE A_HIGH A_LOW <<<"$A"
NVFP4_CFG=$LX_DIR/configs/wan22/extreme/wan_moe_i2v_distill_nvfp4_sparse_attn.json
FLF_CFG=$LX_DIR/configs/wan22/wan_distill_moe_flf2v.json

ok_tasks() { python -c "
import json,sys
try: r=json.load(open('$RESULTS/engine_a_$1.json'))
except Exception: print(''); sys.exit()
print(','.join(sorted({x['task'] for x in r.get('runs',[]) if x.get('ok')})))"; }

if [ -n "$A_HIGH" ]; then
  stage "engine A: nvfp4 + sparse attention (i2v, flf2v)"
  python engine_a.py --variant nvfp4 --model-path "$A_BASE" --high "$A_HIGH" --low "$A_LOW" --base-config "$NVFP4_CFG"
  if [ -z "$(ok_tasks nvfp4)" ]; then
    stage "engine A: nvfp4 dense attention retry"
    python engine_a.py --variant nvfp4_dense --model-path "$A_BASE" --high "$A_HIGH" --low "$A_LOW" --base-config "$NVFP4_CFG"
  fi
fi
push_results "engine A nvfp4"

if ! { ok_tasks nvfp4; ok_tasks nvfp4_dense; } | grep -q flf2v; then
  stage "engine A: nvfp4 flf2v failed -> official 4-step distilled bf16 flf2v"
  if python fetch_models.py engine_a_distill; then
    D=$(python -c "import json;m=json.load(open('$RESULTS/models.json'))['engine_a_distill'];print(m['high'],m['low'])")
    read -r D_HIGH D_LOW <<<"$D"
    python engine_a.py --variant distill_bf16 --tasks flf2v --model-path "$A_BASE" --high "$D_HIGH" --low "$D_LOW" --base-config "$FLF_CFG"
  fi
  push_results "engine A distill"
fi

# Free disk before Engine B.
rm -rf /workspace/models/lightx2v/Wan2.2-Distill-Models

# ---------------- Engine B ----------------
stage "engine B: ComfyUI install"
git clone -q --depth 1 https://github.com/comfyanonymous/ComfyUI.git /workspace/ComfyUI
python -m venv --system-site-packages /workspace/comfy-venv
grep -viE '^(torch|torchvision|torchaudio)([<>=~ ]|$)' /workspace/ComfyUI/requirements.txt > /tmp/comfy-req.txt
/workspace/comfy-venv/bin/pip install -q -r /tmp/comfy-req.txt
(cd /workspace/ComfyUI && echo "ComfyUI $(git rev-parse --short HEAD)") | tee -a "$RESULTS/lightx2v_version.txt"

stage "engine B: download"
python fetch_models.py engine_b /workspace/ComfyUI
B_MODELS=$(python -c "import json;print(json.dumps(json.load(open('$RESULTS/models.json'))['engine_b']))")

stage "engine B: render"
PATH=/workspace/comfy-venv/bin:$PATH python engine_b.py --comfy-dir /workspace/ComfyUI --models "$B_MODELS"

stage "done"
# EXIT trap pushes results and destroys the instance.
