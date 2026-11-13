#!/usr/bin/env bash
# Push every job's output folder to the job-results branch: $OUT_ROOT/<job_id>/ -> jobs/<job_id>/results/$RUN_ID/
# Usage: push_results.sh "<message>". Needs OUT_ROOT, RUN_ID, GH_REPO, GH_TOKEN. Never fails the caller.
# A lock keeps concurrent pushes (render loop + background job finishers) from colliding.
export GIT_TERMINAL_PROMPT=0
exec 9>/tmp/push_results.lock
flock 9
echo "$(date -u +%FT%TZ) === push ($1)"
dst=/workspace/results-repo
# Pushes are serialized by the flock above, so a leftover git lock can only come from a crashed git process.
rm -f $dst/.git/index.lock $dst/.git/HEAD.lock $dst/.git/refs/heads/*.lock 2>/dev/null
if [ ! -d $dst/.git ]; then
  # Blobless + sparse: only this run's folders are checked out, so earlier batches' clips are never downloaded.
  url="https://x-access-token:${GH_TOKEN}@github.com/${GH_REPO}.git"
  if timeout 300 git clone -q --depth 1 --filter=blob:none --no-checkout -b job-results "$url" $dst; then
    (cd $dst && git sparse-checkout set --no-cone "/batches/$RUN_ID/" && git checkout -q job-results)
  else
    rm -rf $dst; timeout 300 git clone -q --depth 1 --filter=blob:none --no-checkout "$url" $dst || { echo "PUSH: clone failed"; exit 0; }
    (cd $dst && git sparse-checkout set --no-cone "/batches/$RUN_ID/" && git checkout -q --orphan job-results && git rm -rqf --cached . 2>/dev/null)
  fi
fi
for d in "$OUT_ROOT"/*/; do
  id=$(basename "$d")
  (cd $dst && git sparse-checkout add "/jobs/$id/results/$RUN_ID/")
  mkdir -p "$dst/jobs/$id/results/$RUN_ID"
  rsync -a --exclude '*.tmp' "$d" "$dst/jobs/$id/results/$RUN_ID/"
done
mkdir -p "$dst/batches/$RUN_ID" && cp "$OUT_ROOT"/*.log "$OUT_ROOT"/*.jsonl "$OUT_ROOT"/*.txt "$dst/batches/$RUN_ID/" 2>/dev/null
cd $dst && git add --sparse --ignore-removal jobs batches && git -c user.name=vast-render -c user.email=render@localhost commit -qm "$RUN_ID: $1"
# If the branch moved (another box pushed), rebuild our commit on top of the new tip: our files live only under
# this run's own folders, so re-adding them on the fresh tip can never conflict. (A rebase on a shallow clone
# got stuck on 2026-11-07 and blocked every later push.) Up to 3 tries.
for try in 1 2 3; do
  if out=$(timeout 300 git push -q origin HEAD:job-results 2>&1); then echo "PUSH ok"; break; fi
  echo "$out" | sed 's/x-access-token:[^@]*@/***@/' | tail -2
  rm -rf .git/rebase-merge .git/rebase-apply
  timeout 120 git fetch -q --depth 1 origin job-results || continue
  git reset -q --mixed FETCH_HEAD
  git add --sparse --ignore-removal jobs batches && git -c user.name=vast-render -c user.email=render@localhost commit -qm "$RUN_ID: $1" || true
done
echo "PUSH done"
exit 0
