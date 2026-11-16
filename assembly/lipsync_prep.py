"""Inputs for a lip-sync run (jobs/run_lipsync.sh): per talking shot the clip window it plays in the edit and the
voiceover it carries, each with HANDLE seconds either side, plus a manifest.

  python assembly/lipsync_prep.py OUT_DIR --clips-root DIR --shots SLUG:s002,s004 SLUG2:s003 ...
The editor (edit_v2.py) uses clips/lipsync/<id>.mp4 when present, starting HANDLE seconds in.
"""

from __future__ import annotations

import argparse
import json
import sys
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parent))
from edit_ad import HEAD_TRIM, align_shots, find_clip, probe_duration, run, share_plan, spoken_words  # noqa: E402

HANDLE = 0.4
REPO = Path(__file__).resolve().parent.parent


def main() -> None:
    ap = argparse.ArgumentParser()
    ap.add_argument("out", type=Path)
    ap.add_argument("--clips-root", type=Path, required=True, help="folder with <slug>/clips/<id>.mp4")
    ap.add_argument("--shots", nargs="+", required=True, help="SLUG:id,id,...")
    a = ap.parse_args()
    items = []
    for spec in a.shots:
        slug, ids = spec.split(":")
        jd = REPO / "jobs" / slug
        job = json.loads((jd / "job.json").read_text())
        words = spoken_words(jd / "vo" / "words.json")
        tl = {t["id"]: t for t in align_shots(job["shots"], words)}
        clips = a.clips_root / slug
        plan = share_plan([s for s in job["shots"] if not s.get("skip_render")], list(tl.values()),
                          lambda sid: find_clip(clips, sid))
        out = a.out / job["job_id"]
        out.mkdir(parents=True, exist_ok=True)
        for sid in ids.split(","):
            t = tl[sid]
            src_id, head, end = plan.get(sid, (sid, HEAD_TRIM, None))
            src = find_clip(clips, src_id)
            if src is None:
                print("no clip", slug, sid)
                continue
            hd = min(HANDLE, head, t["start"])          # same handle on video and audio keeps them in sync
            v0 = head - hd
            vlen = t["dur"] + hd + HANDLE
            vend = min(end or 1e9, probe_duration(src))
            run(["ffmpeg", "-v", "error", "-y", "-i", str(src), "-vf",
                 f"trim=start={v0:.3f}:end={vend:.3f},setpts=PTS-STARTPTS,fps=25", "-an", "-c:v", "libx264", "-crf", "16",
                 "-pix_fmt", "yuv420p", str(out / f"{sid}.mp4")])
            a0 = t["start"] - hd
            run(["ffmpeg", "-v", "error", "-y", "-ss", f"{a0:.3f}", "-t", f"{vlen:.3f}", "-i", str(jd / "vo" / "take.mp3"),
                 "-ac", "1", "-ar", "16000", str(out / f"{sid}.wav")])
            items.append({"job_id": job["job_id"], "slug": slug, "id": sid, "video": f"{job['job_id']}/{sid}.mp4",
                          "audio": f"{job['job_id']}/{sid}.wav", "src": str(src.name), "head": head,
                          "video_s": round(min(vend - v0, 1e9), 2), "audio_s": round(vlen, 2), "handle": round(hd, 3)})
    (a.out / "manifest.json").write_text(json.dumps({"items": items}, indent=1))
    print(len(items), "items")


if __name__ == "__main__":
    main()
