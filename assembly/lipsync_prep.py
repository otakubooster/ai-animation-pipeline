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
            shot = next(s for s in job["shots"] if s["id"] == sid)
            cuts = []
            if shot.get("parts"):       # several renders of the shot: join their windows; the editor cuts at the joins
                parts = [(find_clip(clips, q["clip"]), q["start"], q["end"]) for q in shot["parts"]]
                head = parts[0][1]
                hd = min(HANDLE, head, t["start"])
                vlen = t["dur"] + hd + HANDLE
                pieces, cum = [], 0.0
                for k, (psrc, ps, pe) in enumerate(parts):
                    pe = min(pe, probe_duration(psrc))
                    ps0 = ps - hd if k == 0 else ps
                    pf = out / f"{sid}_part{k}.mp4"
                    run(["ffmpeg", "-v", "error", "-y", "-i", str(psrc), "-vf",
                         f"trim=start={ps0:.3f}:end={pe:.3f},setpts=PTS-STARTPTS,fps=25,scale=576:1024:force_original_aspect_ratio=increase,crop=576:1024,setsar=1",
                         "-an", "-c:v", "libx264", "-crf", "16", "-pix_fmt", "yuv420p", str(pf)])
                    if k:
                        cuts.append(round(cum - hd, 3))
                    cum += pe - ps0
                    pieces.append(pf)
                lst = out / f"{sid}_parts.txt"
                lst.write_text("".join(f"file '{q.resolve()}'\n" for q in pieces))
                run(["ffmpeg", "-v", "error", "-y", "-f", "concat", "-safe", "0", "-i", str(lst), "-c", "copy", str(out / f"{sid}.mp4")])
                for q in pieces:
                    q.unlink()
                lst.unlink()
                src, v0, vend = parts[0][0], 0.0, cum
            else:
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
                          "video_s": round(min(vend - v0, 1e9), 2), "audio_s": round(vlen, 2), "handle": round(hd, 3),
                          **({"cuts": cuts} if cuts else {})})
    (a.out / "manifest.json").write_text(json.dumps({"items": items}, indent=1))
    print(len(items), "items")


if __name__ == "__main__":
    main()
