#!/usr/bin/env bash
# Push every job's output folder to the job-results branch: $OUT_ROOT/<job_id>/ -> jobs/<job_id>/results/$RUN_ID/
# Usage: push_results.sh "<message>". Needs OUT_ROOT, RUN_ID, GH_REPO, GH_TOKEN. Never fails the caller.
# A lock keeps concurrent pushes (render loop + background job finishers) from colliding.
export GIT_TERMINAL_PROMPT=0
exec 9>/tmp/push_results.lock
flock 9
echo "$(date -u +%FT%TZ) === push ($1)"
dst=/workspace/results-repo
if [ ! -d $dst/.git ]; then
  timeout 300 git clone -q --depth 1 "https://x-access-token:${GH_TOKEN}@github.com/${GH_REPO}.git" $dst || { echo "PUSH: clone failed"; exit 0; }
  (cd $dst && { timeout 120 git fetch -q --depth 1 origin job-results && git checkout -q -B job-results FETCH_HEAD; } || git checkout -q -b job-results)
fi
for d in "$OUT_ROOT"/*/; do
  id=$(basename "$d")
  mkdir -p "$dst/jobs/$id/results/$RUN_ID"
  rsync -a --exclude '*.tmp' "$d" "$dst/jobs/$id/results/$RUN_ID/"
done
mkdir -p "$dst/batches/$RUN_ID" && cp "$OUT_ROOT"/*.log "$OUT_ROOT"/*.jsonl "$OUT_ROOT"/*.txt "$dst/batches/$RUN_ID/" 2>/dev/null
cd $dst && git add -A jobs batches && git -c user.name=vast-render -c user.email=render@localhost commit -qm "$RUN_ID: $1"
# If the branch moved (a concurrent push), rebase once and retry.
if ! out=$(timeout 300 git push -q origin HEAD:job-results 2>&1); then
  echo "$out" | sed 's/x-access-token:[^@]*@/***@/'
  timeout 120 git pull -q --rebase origin job-results && timeout 300 git push -q origin HEAD:job-results && echo "PUSH ok after rebase"
fi
echo "PUSH done"
exit 0
