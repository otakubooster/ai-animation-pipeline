#!/usr/bin/env bash
# Talking shots with InfiniteTalk on one rented RTX 5090 (phase0/onstart.sh, RUN_SCRIPT=jobs/run_infinitetalk.sh).
# Inputs: branch lipsync-in, runs/$LS_RUN/ (manifest.json with image + audio + prompt per shot; assembly/lipsync_prep.py --it).
# Output: branch job-results, jobs/<job_id>/results/$RUN_ID/lipsync/<id>.mp4 (same layout as the LatentSync runs).
# Model: Wan2.1-I2V-14B-480P text encoder / CLIP / VAE + InfiniteTalk int8 DiT with the 4-8 step LoRA merged (19.5 GB);
# the 14B base shards are not needed. xformers / xfuser / kokoro are shimmed (single GPU, plain attention via SDPA).
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
stage() { echo "$(date -u +%FT%TZ) [+$(( $(date +%s) - START ))s] === $*"; }
push() { bash $REPO_DIR/jobs/push_results.sh "$1"; }
finish() { push "infinitetalk final"; stage "self-destroy"
  curl -sS -X DELETE "https://console.vast.ai/api/v0/instances/${CONTAINER_ID}/" -H "Authorization: Bearer ${CONTAINER_API_KEY}" || true; }
trap finish EXIT
trap 'exit 1' TERM INT
( sleep "$MAX_SECONDS"; echo "HARD TIMEOUT"; pkill -f it_driver.py; sleep 5; kill -TERM $$ ) &

if [ "$(curl -s -o /dev/null -w '%{http_code}' -H "Authorization: Bearer ${GH_TOKEN:-}" "https://api.github.com/repos/$GH_REPO")" != 200 ]; then
  echo "FATAL: GH_TOKEN rejected"; exit 1
fi
stage "setup"
{ nvidia-smi --query-gpu=name,memory.total,driver_version --format=csv,noheader; free -g | sed -n 2p; curl -s https://ipinfo.io/country; echo; } | tee "$OUT_ROOT/gpu.txt"
apt-get update -qq && apt-get install -y -qq ffmpeg git libsndfile1 >/dev/null
git clone -q --depth 1 -b lipsync-in "https://x-access-token:${GH_TOKEN}@github.com/${GH_REPO}.git" /workspace/ls_in || { echo "FATAL: no lipsync-in branch"; exit 1; }
IN=/workspace/ls_in/runs/$LS_RUN
[ -f $IN/manifest.json ] || { echo "FATAL: no manifest for $LS_RUN"; exit 1; }
for j in $(python3 -c "import json;print(' '.join(sorted({i['job_id'] for i in json.load(open('$IN/manifest.json'))['items']})))"); do mkdir -p $OUT_ROOT/$j/lipsync; done
push "infinitetalk alive"

stage "install"
git clone -q --depth 1 https://github.com/MeiGen-AI/InfiniteTalk /workspace/InfiniteTalk
cd /workspace/InfiniteTalk
python3 -m venv --system-site-packages /workspace/itenv
. /workspace/itenv/bin/activate
pip install -q diffusers transformers tokenizers accelerate easydict ftfy imageio imageio-ffmpeg scikit-image loguru "numpy<2" \
  pyloudnorm optimum-quanto==0.2.6 scenedetect moviepy==1.0.3 decord librosa soundfile einops opencv-python-headless sentencepiece \
  "huggingface_hub[hf_xet]" 2>&1 | tail -3
python3 -c "import torch;print('torch',torch.__version__,torch.cuda.get_device_name(0))"
# kokoro (TTS demo) is not used; the T5 text encoder loads in bf16 (the repo has no int8 T5 file)
sed -i 's/^from kokoro import KPipeline/try:\n    from kokoro import KPipeline\nexcept Exception:\n    KPipeline = None/' generate_infinitetalk.py
python3 - <<'PY'
from pathlib import Path
p = Path("wan/multitalk.py"); s = p.read_text()
old = "            quant=quant,\n            quant_dir=os.path.dirname(quant_dir) if quant_dir is not None else None,\n        )"
assert old in s, "T5 quant block not found"
p.write_text(s.replace(old, "            quant=None,\n            quant_dir=None,\n        )", 1))
PY
export IT_SHIMS=$REPO_DIR/jobs/lipsync/shims PYTHONPATH=$REPO_DIR/jobs/lipsync/shims

stage "weights"
W=/workspace/InfiniteTalk/weights; mkdir -p $W
hf download Wan-AI/Wan2.1-I2V-14B-480P --local-dir $W/Wan2.1-I2V-14B-480P --include "config.json" "Wan2.1_VAE.pth" \
  "models_t5_umt5-xxl-enc-bf16.pth" "models_clip_open-clip-xlm-roberta-large-vit-huge-14.pth" "google/*" "xlm-roberta-large/*" 2>&1 | tail -1
hf download TencentGameMate/chinese-wav2vec2-base --local-dir $W/chinese-wav2vec2-base 2>&1 | tail -1
hf download TencentGameMate/chinese-wav2vec2-base model.safetensors --revision refs/pr/1 --local-dir $W/chinese-wav2vec2-base 2>&1 | tail -1
hf download MeiGen-AI/InfiniteTalk --local-dir $W/InfiniteTalk --include "quant_models/${IT_QUANT_NAME:-infinitetalk_single_int8_lora}.*" 2>&1 | tail -1
du -sh $W/*; ls $W/InfiniteTalk/quant_models
push "infinitetalk models ready"

stage "generate"
IT_WEIGHTS=$W IT_QUANT_FILE="InfiniteTalk/quant_models/${IT_QUANT_NAME:-infinitetalk_single_int8_lora}.safetensors" \
PUSH_HOOK="bash $REPO_DIR/jobs/push_results.sh" PUSH_EVERY=${PUSH_EVERY:-3} python3 $REPO_DIR/jobs/lipsync/it_driver.py $IN/manifest.json $IN $OUT_ROOT
stage "done"
