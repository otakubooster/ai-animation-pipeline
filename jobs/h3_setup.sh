#!/usr/bin/env bash
# Install ComfyUI + the Comfy-Org MiniMax H3 files on the GPU box and start a headless server on 127.0.0.1:8188.
#   H3_FAMILIES="fl2va" (default) or "fl2va ref2va"   h3_setup.sh LOG_DIR
# Files (Comfy-Org/MiniMax-H3): pruned int8 transformer(s) 21 GB each, qwen3vl 32b nvfp4 text encoder 15.7 GB,
# int8 video VAE, fp32 audio VAE, turbo LoRA(s). Needs ~120 GB host RAM.
set -uo pipefail
LOG_DIR=${1:-/workspace/job/out}
FAMILIES=${H3_FAMILIES:-fl2va}
echo "=== h3 setup ($FAMILIES)"
TV=$(python3 -c "import torch;print(torch.__version__.split('+')[0])"); CU=$(python3 -c "import torch;print('cu'+torch.version.cuda.replace('.',''))")
python3 -c "import torchaudio" 2>/dev/null || pip install -q "torchaudio==$TV" --index-url "https://download.pytorch.org/whl/$CU" \
  || pip install -q torchaudio --no-deps
[ -d /workspace/ComfyUI ] || git clone -q --depth 1 https://github.com/comfyanonymous/ComfyUI.git /workspace/ComfyUI
(cd /workspace/ComfyUI && echo "ComfyUI $(git rev-parse --short HEAD)" > "$LOG_DIR/versions.txt" \
  && grep -v -E '^(torch|torchvision|torchaudio)([<>=~ ]|$)' requirements.txt > /tmp/req.txt && pip install -q -r /tmp/req.txt)
python3 -c "import torch,torchaudio;print('torch',torch.__version__,'torchaudio',torchaudio.__version__)" >> "$LOG_DIR/versions.txt"

H3_FAMILIES="$FAMILIES" LOG_DIR="$LOG_DIR" timeout 3600 python3 - <<'PY' || { echo "H3 download failed"; exit 1; }
import json, os, time
from huggingface_hub import hf_hub_download
fam = os.environ["H3_FAMILIES"].split()
lora = {"fl2va": "minimax_h3_fl2v_turbo_8step_v1.0_comfyui_bf16.safetensors", "ref2va": "minimax_h3_ref2v_turbo_4step_v0.1_comfyui_bf16.safetensors"}
files = {"diffusion_models": [f"minimax_h3_{f}_pruned_int8_convrot.safetensors" for f in fam],
         "text_encoders": ["qwen3vl_32b_minimax_h3_nvfp4_awq.safetensors"],
         "vae": ["minimax_h3_video_vae_int8_convrot.safetensors", "minimax_h3_audio_vae_fp32.safetensors"],
         "loras": [lora[f] for f in fam]}
log = []
for sub, names in files.items():
    for n in names:
        t = time.perf_counter()
        p = hf_hub_download("Comfy-Org/MiniMax-H3", f"{sub}/{n}", local_dir="/workspace/h3")
        os.makedirs(f"/workspace/ComfyUI/models/{sub}", exist_ok=True)
        dst = f"/workspace/ComfyUI/models/{sub}/{n}"
        if not os.path.exists(dst):
            os.symlink(p, dst)
        log.append({"file": n, "seconds": round(time.perf_counter() - t, 1), "gb": round(os.path.getsize(p) / 1e9, 2)})
        print(log[-1], flush=True)
json.dump(log, open(os.path.join(os.environ["LOG_DIR"], "h3_downloads.json"), "w"), indent=1)
PY
(cd /workspace/ComfyUI && nohup python3 main.py --listen 127.0.0.1 --port 8188 --disable-auto-launch > comfy.log 2>&1 &)
for i in $(seq 1 120); do curl -s http://127.0.0.1:8188/system_stats >/dev/null && break; sleep 5; done
curl -sf http://127.0.0.1:8188/object_info/MiniMaxH3ImageToVideo >/dev/null || { echo "ComfyUI has no MiniMaxH3ImageToVideo node"; tail -30 /workspace/ComfyUI/comfy.log; exit 1; }
echo "=== h3 ready"
