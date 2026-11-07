"""Plan the lip-sync (talking presenter) segments of a job and cut their audio.

A segment = one run of consecutive talking shots that use the same presenter frame, lip-synced in one go from
that frame + the matching slice of the voiceover. Ads that keep the presenter on screen under other shots
(picture-in-picture box over the talker, or a small inset of the talker in a corner) get one extra segment
covering the whole voiceover from the main presenter frame.

  python pipeline/talk_plan.py jobs/<slug> OUT_AUDIO_DIR     # writes job["talk"] and OUT_AUDIO_DIR/<seg>.wav
"""

from __future__ import annotations

import json
import subprocess
import sys
from collections import Counter
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parents[1] / "assembly"))
from edit_ad import align_shots, spoken_words  # noqa: E402

PAD = 0.25   # seconds of audio context around a segment (the editor cuts inside it)


def plan_segments(shots: list[dict], timeline: list[dict], total: float, full_from: str | None = None) -> list[dict]:
    segs: list[dict] = []
    for sh, t in zip(shots, timeline):
        if not sh.get("talking"):
            continue
        if full_from and sh["frame"] == full_from:
            continue          # covered by the full-length segment
        if segs and segs[-1]["frame"] == sh["frame"] and abs(segs[-1]["_end"] - t["start"]) < 0.01:
            segs[-1]["_end"] = t["end"]
            segs[-1]["shots"].append(sh["id"])
        else:
            segs.append({"frame": sh["frame"], "_start": t["start"], "_end": t["end"], "shots": [sh["id"]]})
    out = []
    if full_from:
        out.append({"id": "full", "frame": full_from, "start": 0.0, "end": round(total, 3), "shots": "*"})
    for i, s in enumerate(segs, 1):
        out.append({"id": f"t{i:02d}", "frame": s["frame"], "start": round(max(0.0, s["_start"] - PAD), 3),
                    "end": round(min(total, s["_end"] + PAD), 3), "shots": s["shots"]})
    return out


def main_frame(shots: list[dict]) -> str | None:
    c = Counter(s["frame"] for s in shots if s.get("talking"))
    return c.most_common(1)[0][0] if c else None


def main(job_dir: str, audio_out: str) -> None:
    jd, ao = Path(job_dir), Path(audio_out)
    p = jd / "job.json"
    job = json.loads(p.read_text())
    if not any(s.get("talking") for s in job["shots"]):
        print(f"{job['slug']}: no talking shots")
        return
    words = spoken_words(jd / "vo" / "words.json")
    tl = align_shots(job["shots"], words)
    total = tl[-1]["end"]
    needs_full = any(s.get("pip") == "box" for s in job["shots"]) or job.get("talk_inset")
    segs = plan_segments(job["shots"], tl, total, main_frame(job["shots"]) if needs_full else None)
    ao.mkdir(parents=True, exist_ok=True)
    for s in segs:
        subprocess.run(["ffmpeg", "-nostdin", "-v", "error", "-y", "-ss", f"{s['start']:.3f}", "-to", f"{s['end']:.3f}",
                        "-i", str(jd / "vo" / "take.mp3"), "-ac", "1", "-ar", "16000", str(ao / f"{s['id']}.wav")], check=True)
    job["talk"] = {"segments": segs, "audio_dir": "talk"}
    p.write_text(json.dumps(job, indent=2, ensure_ascii=False))
    print(f"{job['slug']}: {len(segs)} segments, {sum(s['end'] - s['start'] for s in segs):.1f} s of lip-sync")


if __name__ == "__main__":
    main(*sys.argv[1:3])
