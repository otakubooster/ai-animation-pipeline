#!/usr/bin/env bash
# Put one job's first frames in DEST on the GPU box (used by run_job.sh and run_job_h3.sh).
#   fetch_frames.sh JOB_JSON DEST       (needs GH_TOKEN, GH_REPO; WORK defaults to /workspace/job)
# Source: job.json "frames_git" {branch, path} (staged by pipeline/stage_frames.py on branch frames-store), or
# "frames_release" {repo, tag, asset, subdir?} (a zip on a GitHub release; downloaded once per asset, flattened;
# subdir = only that folder of the zip, for zips holding several ads).
set -uo pipefail
JOB_JSON=$1 DEST=$2
WORK=${WORK:-/workspace/job}
mkdir -p "$DEST"
SRC=$(python3 -c "import json;j=json.load(open('$JOB_JSON'));print('git' if 'frames_git' in j else 'release')")
if [ "$SRC" = git ]; then
  FG=$(python3 -c "import json;g=json.load(open('$JOB_JSON'))['frames_git'];print(g['branch'],g['path'])")
  read -r FG_BRANCH FG_PATH <<<"$FG"
  [ -d $WORK/frames-store/.git ] || timeout 600 git clone -q --depth 1 --filter=blob:none --sparse -b "$FG_BRANCH" \
    "https://x-access-token:${GH_TOKEN}@github.com/${GH_REPO}.git" $WORK/frames-store
  (cd $WORK/frames-store && git sparse-checkout add "$FG_PATH")
  cp -r "$WORK/frames-store/$FG_PATH"/. "$DEST/"
else
  REL=$(python3 -c "import json;r=json.load(open('$JOB_JSON'))['frames_release'];print(r['repo'],r['tag'],r['asset'])")
  read -r REL_REPO REL_TAG REL_ASSET <<<"$REL"
  SUBDIR=$(python3 -c "import json;print(json.load(open('$JOB_JSON'))['frames_release'].get('subdir',''))")
  ZIP="$WORK/releases/$REL_TAG/$REL_ASSET"
  if [ ! -s "$ZIP" ]; then
    mkdir -p "$(dirname "$ZIP")"
    # Public repo: the plain release URL needs no token; otherwise fall back to the API with GH_TOKEN.
    curl -sSfL "https://github.com/$REL_REPO/releases/download/$REL_TAG/$REL_ASSET" -o "$ZIP" || {
      ASSET_ID=$(curl -sS -H "Authorization: Bearer $GH_TOKEN" "https://api.github.com/repos/$REL_REPO/releases/tags/$REL_TAG" \
        | python3 -c "import sys,json;print(next(a['id'] for a in json.load(sys.stdin)['assets'] if a['name']=='$REL_ASSET'))")
      curl -sSL -H "Authorization: Bearer $GH_TOKEN" -H "Accept: application/octet-stream" \
        "https://api.github.com/repos/$REL_REPO/releases/assets/$ASSET_ID" -o "$ZIP"; }
  fi
  if [ -n "$SUBDIR" ]; then unzip -q -o -j "$ZIP" "$SUBDIR/*" -d "$DEST"; else unzip -q -o -j "$ZIP" -d "$DEST"; fi
fi
echo "$(basename "$DEST"): $(ls "$DEST" | wc -l) frames"
