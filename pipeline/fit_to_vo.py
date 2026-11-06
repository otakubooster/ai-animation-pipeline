"""Size every shot's render length from the real voiceover timing (VO is made before rendering).

Each shot needs: its VO line's duration (from this line's first word to the next line's first word)
+ the 0.5 s head trim + 0.5 s spare, at 16 fps, rounded up to Wan's 4n+1 frame rule, capped at 121 frames.
Lines longer than ~7 s still render 121 frames; the editor slows them up to 1.5x, then holds.

  python pipeline/fit_to_vo.py jobs/<slug>/job.json jobs/<slug>/vo/words.json
"""

from __future__ import annotations

import json
import math
import sys
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parents[1] / "assembly"))
from edit_ad import align_shots, spoken_words  # noqa: E402

FPS = 16
MIN_FRAMES, MAX_FRAMES = 49, 121
PAD_S = 1.0  # 0.5 s head trim + 0.5 s spare


def frames_for(seconds: float) -> int:
    n = 4 * math.ceil((seconds * FPS - 1) / 4) + 1
    return max(MIN_FRAMES, min(MAX_FRAMES, n))


def main(job_path: str, words_path: str) -> None:
    jp = Path(job_path)
    job = json.loads(jp.read_text())
    timeline = align_shots(job["shots"], spoken_words(Path(words_path)))
    for shot, t in zip(job["shots"], timeline):
        shot["vo_s"] = t["dur"]
    by_id = {s["id"]: s for s in job["shots"]}
    for shot in job["shots"]:
        need = shot["vo_s"]
        # A shot whose damaged neighbour borrows its clip must cover both lines.
        need += sum(o["vo_s"] for o in job["shots"] if o.get("fill_from") == shot["id"])
        shot["num_frames"] = frames_for(need + PAD_S)
    jp.write_text(json.dumps(job, indent=2, ensure_ascii=False))
    total = sum(s["num_frames"] for s in job["shots"]) / FPS
    print(f"{len(job['shots'])} shots, VO {timeline[-1]['end']:.1f} s, render footage {total:.0f} s")


if __name__ == "__main__":
    main(*sys.argv[1:3])
