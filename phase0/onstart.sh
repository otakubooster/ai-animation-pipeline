#!/bin/bash
# Vast.ai onstart script for the Phase 0 spike (launch.py sends this file as the instance's onstart).
# Needs GH_TOKEN (Vast account environment variable) and GH_REPO / RUN_ID / SPIKE_DPH / SPIKE_MAX_SECONDS
# (instance env set by launch.py). Clones the repo and starts run_spike.sh in the background.
[ -f /etc/environment ] && set -a && . /etc/environment && set +a
mkdir -p /workspace && cd /workspace
command -v git >/dev/null || (apt-get update -qq && apt-get install -y -qq git >/dev/null)
if ! git clone -q --depth 1 "https://x-access-token:${GH_TOKEN}@github.com/${GH_REPO}.git" /workspace/repo; then
  echo "clone failed (GH_TOKEN missing or wrong?) - destroying instance"
  curl -sS -X DELETE "https://console.vast.ai/api/v0/instances/${CONTAINER_ID}/" -H "Authorization: Bearer ${CONTAINER_API_KEY}"
  exit 1
fi
nohup bash "/workspace/repo/${RUN_SCRIPT:-phase0/run_spike.sh}" > /workspace/spike-boot.log 2>&1 &
