#!/usr/bin/env bash
# MiniMax H3 spike on one rented RTX 5090 (host outside the EU/UK/US/KR - H3 licence): install ComfyUI, fetch the
# Comfy-Org H3 files (pruned int8 transformers, nvfp4 text encoder, turbo LoRAs), render the shots in
# $H3_SHOTS (default jobs/festbiss-h3-spike/shots.json), push results to job-results, self-destroy.
set -uo pipefail
REPO_DIR=/workspace/repo
WORK=/workspace/job
OUT_ROOT=$WORK/out
SHOTS=${H3_SHOTS:-$REPO_DIR/jobs/festbiss-h3-spike/shots.json}
JOB_ID=$(python3 -c "import json;print(json.load(open('$SHOTS'))['job_id'])")
OUT=$OUT_ROOT/$JOB_ID
mkdir -p "$OUT"
RUN_ID=${RUN_ID:-$(date -u +%Y%m%d-%H%M%S)}
MAX_SECONDS=${SPIKE_MAX_SECONDS:-5400}
exec > >(tee -a "$OUT_ROOT/batch.log" /proc/1/fd/1) 2>&1
export GIT_TERMINAL_PROMPT=0 OUT_ROOT RUN_ID GH_REPO
START=$(date +%s)
stage() { echo "$(date -u +%FT%TZ) [+$(( $(date +%s) - START ))s] === $*"; echo "{\"t\": $(( $(date +%s) - START )), \"stage\": \"$*\"}" >> "$OUT_ROOT/timeline.jsonl"; }
push() { bash $REPO_DIR/jobs/push_results.sh "$1"; }
finish() { cp /workspace/ComfyUI/comfy.log "$OUT/comfy.log" 2>/dev/null; df -h /workspace > "$OUT/disk.txt"; free -g > "$OUT/ram.txt"
  push "final"; stage "self-destroy"
  curl -sS -X DELETE "https://console.vast.ai/api/v0/instances/${CONTAINER_ID}/" -H "Authorization: Bearer ${CONTAINER_API_KEY}" || true; }
trap finish EXIT
trap 'exit 1' TERM INT
( sleep "$MAX_SECONDS"; echo "HARD TIMEOUT"; pkill -f 'h3_render.py|main.py'; sleep 5; kill -TERM $$ ) &

stage "setup"
nvidia-smi --query-gpu=name,memory.total,driver_version --format=csv,noheader | tee "$OUT/gpu.txt"
curl -s "https://ipinfo.io/country" | tee "$OUT/host_country.txt"; echo
push "box alive"
command -v ffmpeg >/dev/null && command -v rsync >/dev/null || (apt-get update -qq && apt-get install -y -qq ffmpeg rsync >/dev/null)
pip install -q -U "huggingface_hub[hf_xet]" pillow
TV=$(python3 -c "import torch;print(torch.__version__.split('+')[0])"); CU=$(python3 -c "import torch;print('cu'+torch.version.cuda.replace('.',''))")
python3 -c "import torchaudio" 2>/dev/null || pip install -q "torchaudio==$TV" --index-url "https://download.pytorch.org/whl/$CU" \
  || pip install -q torchaudio --no-deps
python3 -c "import torch,torchaudio;print('torch',torch.__version__,'torchaudio',torchaudio.__version__)" | tee "$OUT/versions.txt"

stage "comfyui"
[ -d /workspace/ComfyUI ] || git clone -q --depth 1 https://github.com/comfyanonymous/ComfyUI.git /workspace/ComfyUI
(cd /workspace/ComfyUI && echo "ComfyUI $(git rev-parse --short HEAD)" | tee -a "$OUT/versions.txt" \
  && grep -v -E '^(torch|torchvision|torchaudio)([<>=~ ]|$)' requirements.txt > /tmp/req.txt && pip install -q -r /tmp/req.txt)

stage "h3 models"
python3 - <<'PY'
import time, json
from huggingface_hub import hf_hub_download
files = {"diffusion_models": ["minimax_h3_fl2va_pruned_int8_convrot.safetensors", "minimax_h3_ref2va_pruned_int8_convrot.safetensors"],
         "text_encoders": ["qwen3vl_32b_minimax_h3_nvfp4_awq.safetensors"],
         "vae": ["minimax_h3_video_vae_int8_convrot.safetensors", "minimax_h3_audio_vae_fp32.safetensors"],
         "loras": ["minimax_h3_fl2v_turbo_8step_v1.0_comfyui_bf16.safetensors", "minimax_h3_ref2v_turbo_4step_v0.1_comfyui_bf16.safetensors"]}
log = []
for sub, names in files.items():
    for n in names:
        t = time.perf_counter()
        p = hf_hub_download("Comfy-Org/MiniMax-H3", f"{sub}/{n}", local_dir="/workspace/h3")
        import os
        os.makedirs(f"/workspace/ComfyUI/models/{sub}", exist_ok=True)
        dst = f"/workspace/ComfyUI/models/{sub}/{n}"
        if not os.path.exists(dst):
            os.symlink(p, dst)
        log.append({"file": n, "seconds": round(time.perf_counter() - t, 1), "gb": round(os.path.getsize(p) / 1e9, 2)})
        print(log[-1], flush=True)
json.dump(log, open("/workspace/job/h3_downloads.json", "w"), indent=1)
PY
cp /workspace/job/h3_downloads.json "$OUT/" 2>/dev/null
push "models downloaded"

stage "comfyui server"
(cd /workspace/ComfyUI && nohup python3 main.py --listen 127.0.0.1 --port 8188 --disable-auto-launch > comfy.log 2>&1 &)
for i in $(seq 1 120); do curl -s http://127.0.0.1:8188/system_stats >/dev/null && break; sleep 5; done
curl -s http://127.0.0.1:8188/system_stats | tee "$OUT/system_stats.json"; echo
curl -s http://127.0.0.1:8188/object_info/MiniMaxH3ImageToVideo | head -c 300; echo

stage "render shots"
[ -f "$(dirname "$SHOTS")/fetch_images" ] && python3 "$REPO_DIR/$(cat "$(dirname "$SHOTS")/fetch_images")" "$(dirname "$SHOTS")"
python3 $REPO_DIR/phase0/h3_render.py "$SHOTS" "$OUT"
stage "done"
