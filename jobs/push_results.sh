#!/usr/bin/env bash
# Push the job's output folder to the job-results branch. Usage: push_results.sh "<message>"
# Needs OUT, JOB_ID, RUN_ID, GH_REPO, GH_TOKEN in the environment. Never fails the caller.
export GIT_TERMINAL_PROMPT=0
echo "$(date -u +%FT%TZ) === push ($1)"
dst=/workspace/results-repo
if [ ! -d $dst/.git ]; then
  timeout 300 git clone -q --depth 1 "https://x-access-token:${GH_TOKEN}@github.com/${GH_REPO}.git" $dst || { echo "PUSH: clone failed"; exit 0; }
  (cd $dst && { timeout 120 git fetch -q --depth 1 origin job-results && git checkout -q -B job-results FETCH_HEAD; } || git checkout -q -b job-results)
fi
tgt=$dst/jobs/$JOB_ID/results/$RUN_ID
mkdir -p "$tgt"
rsync -a --exclude '*.tmp' "$OUT/" "$tgt/"
cd $dst && git add -A jobs && git -c user.name=vast-render -c user.email=render@localhost commit -qm "$JOB_ID $RUN_ID: $1"
timeout 300 git push origin HEAD:job-results 2>&1 | sed 's/x-access-token:[^@]*@/***@/'
echo "PUSH exit ${PIPESTATUS[0]}"
exit 0
