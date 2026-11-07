#!/usr/bin/env bash
# Lip-sync debug matrix: several InfiniteTalk configs on a 5 s English sample at 480p, each reporting speed and
# whether frames come out black. Results -> job-results jobs/talk-matrix/results/<RUN_ID>/. Self-destroys.
set -uo pipefail
REPO_DIR=/workspace/repo
WORK=/workspace/job
OUT_ROOT=$WORK/out
OUT=$OUT_ROOT/talk-matrix
mkdir -p "$OUT"
RUN_ID=${RUN_ID:-$(date -u +%Y%m%d-%H%M%S)}
MAX_SECONDS=${SPIKE_MAX_SECONDS:-3600}
exec > >(tee -a "$OUT_ROOT/batch.log" /proc/1/fd/1) 2>&1
export GIT_TERMINAL_PROMPT=0 OUT_ROOT RUN_ID GH_REPO
push() { bash $REPO_DIR/jobs/push_results.sh "$1"; }
finish() { push "final"; curl -sS -X DELETE "https://console.vast.ai/api/v0/instances/${CONTAINER_ID}/" -H "Authorization: Bearer ${CONTAINER_API_KEY}" || true; }
trap finish EXIT
trap 'exit 1' TERM INT
( sleep "$MAX_SECONDS"; echo "HARD TIMEOUT"; pkill -f 'talk_render.py|fetch_talk.py'; sleep 5; kill -TERM $$ ) &
command -v ffmpeg >/dev/null && command -v rsync >/dev/null || (apt-get update -qq && apt-get install -y -qq ffmpeg rsync >/dev/null)
pip install -q -U "huggingface_hub[hf_xet]" pillow
LX_DIR=$(python -c "import lightx2v,os;print(os.path.dirname(os.path.dirname(lightx2v.__file__)))" 2>/dev/null || true)
if [ -z "$LX_DIR" ] || [ ! -d "$LX_DIR/configs/infinitetalk" ]; then
  [ -d /workspace/LightX2V ] || git clone -q --depth 1 https://github.com/ModelTC/LightX2V.git /workspace/LightX2V
  pip install -q -e /workspace/LightX2V; LX_DIR=/workspace/LightX2V
fi
timeout 3000 python $REPO_DIR/phase0/fetch_talk.py
cp /workspace/spike/results/talk_models.json "$OUT/"
FP8=$(python -c "
from huggingface_hub import hf_hub_download
print(hf_hub_download('lightx2v/Wan2.1-Distill-Models','wan2.1_i2v_480p_scaled_fp8_e4m3_lightx2v_4step.safetensors',local_dir='/workspace/models/talk'))")
ffmpeg -v error -y -t 5 -i $REPO_DIR/phase0/talk/sample_en.mp3 -ac 1 -ar 16000 $WORK/sample5.wav
CFG=$LX_DIR/configs/infinitetalk/5090/infinitetalk_single_distilled.json
SDPA='{"self_attn_1_type":"torch_sdpa","cross_attn_1_type":"torch_sdpa","cross_attn_2_type":"torch_sdpa","audio_cross_attn_type":"torch_sdpa"}'
FA2='{"self_attn_1_type":"flash_attn2","cross_attn_1_type":"flash_attn2","cross_attn_2_type":"flash_attn2","audio_cross_attn_type":"flash_attn2"}'
run() {  # name dit override
  echo "[{\"id\": \"$1\", \"image\": \"$REPO_DIR/phase0/talk/presenter.jpg\", \"audio\": \"$WORK/sample5.wav\", \"out\": \"$OUT/$1.mp4\", \"prompt\": \"A woman vet talks warmly and naturally to the camera at her desk.\"}]" > $WORK/items_$1.json
  timeout 900 python $REPO_DIR/jobs/talk_render.py --models $OUT/talk_models.json --base-config $CFG --size 480 \
    ${2:+--dit "$2"} --override "$3" $WORK/items_$1.json 2>&1 | tail -n 300 > "$OUT/$1.log"
  grep -E '^\{"id"' "$OUT/$1.log" | tail -1
  push "matrix $1"
}
run bf16_sdpa "" "$SDPA"
run fp8_sdpa "$FP8" "$SDPA"
run bf16_fa2 "" "$FA2"
run bf16_noaudio_norm "" "$(python -c "import json;d=json.loads('$SDPA');d['norm_output_audio']=False;print(json.dumps(d))")"
