"""Join one ElevenLabs take per script line into an ad's voiceover, with word timings for the editor.

Used when every line is generated on its own (several characters/voices, or a language Scribe cannot transcribe
reliably, e.g. Moroccan Darija): each line's leading/trailing silence is trimmed, lines are joined with short
gaps, and word timings are spread over each line's span in proportion to word length. The script text is
the caption text, so captions always match the script exactly.

  python voice/lines_vo.py JOB_DIR      # job.json shots carry vo_file (path) and vo_line; writes vo/take.mp3,
                                        # vo/words.json and each shot's vo_s / vo_span
"""

from __future__ import annotations

import json
import re
import subprocess
import sys
import tempfile
from pathlib import Path

GAP_S = 0.3          # pause between two lines
PRE_LAST_S = 0.5     # extra breath before the end-card line
SR = 44100


def run(cmd: list[str]) -> str:
    return subprocess.run(cmd, check=True, capture_output=True, text=True).stdout


def duration(p: Path) -> float:
    return float(run(["ffprobe", "-v", "error", "-show_entries", "format=duration", "-of", "csv=p=0", str(p)]))


def trim(src: Path, dst: Path) -> float:
    """Mono wav without leading/trailing silence (keeps 60 ms of air at both ends)."""
    sil = "silenceremove=start_periods=1:start_threshold=-45dB:start_silence=0.06"
    run(["ffmpeg", "-v", "error", "-y", "-i", str(src), "-af", f"{sil},areverse,{sil},areverse", "-ac", "1",
         "-ar", str(SR), str(dst)])
    return duration(dst)


def spread_words(text: str, t0: float, t1: float) -> list[dict]:
    """Word timings over [t0, t1], each word's share proportional to its letters (+1 for the space)."""
    toks = text.split()
    weights = [len(re.sub(r"[^\w]", "", t)) + 1 for t in toks]
    total, t, out = sum(weights), t0, []
    for tok, w in zip(toks, weights):
        d = (t1 - t0) * w / total
        out.append({"text": tok, "type": "word", "start": round(t, 3), "end": round(t + d * 0.92, 3)})
        t += d
    return out


def join(files: list[Path], texts: list[str], out_mp3: Path, out_words: Path, last_is_end: bool = True) -> list[tuple[float, float]]:
    spans, words, parts, t = [], [], [], 0.0
    with tempfile.TemporaryDirectory() as td:
        td = Path(td)
        for i, (f, txt) in enumerate(zip(files, texts)):
            if i:
                gap = GAP_S + (PRE_LAST_S if last_is_end and i == len(files) - 1 else 0.0)
                g = td / f"gap{i}.wav"
                run(["ffmpeg", "-v", "error", "-y", "-f", "lavfi", "-i", f"anullsrc=r={SR}:cl=mono", "-t", f"{gap:.3f}", str(g)])
                parts.append(g)
                t += duration(g)
            w = td / f"line{i}.wav"
            d = trim(f, w)
            parts.append(w)
            spans.append((round(t, 3), round(t + d, 3)))
            words += spread_words(txt, t + 0.04, t + d - 0.04)
            t += d
        lst = td / "list.txt"
        lst.write_text("".join(f"file '{p}'\n" for p in parts))
        run(["ffmpeg", "-v", "error", "-y", "-f", "concat", "-safe", "0", "-i", str(lst), "-c:a", "libmp3lame", "-b:a", "192k", str(out_mp3)])
    out_words.write_text(json.dumps({"words": words, "source": "lines_vo (script text, proportional timing)"}, ensure_ascii=False, indent=1))
    return spans


def main(job_dir: str) -> None:
    jd = Path(job_dir)
    job = json.loads((jd / "job.json").read_text())
    shots = job["shots"]
    (jd / "vo").mkdir(exist_ok=True)
    spans = join([Path(s["vo_file"]) for s in shots], [s["vo_line"] for s in shots], jd / "vo" / "take.mp3",
                 jd / "vo" / "words.json", last_is_end=bool(job.get("end_card_shot")))
    for k, (s, (a, b)) in enumerate(zip(shots, spans)):
        nxt = spans[k + 1][0] if k + 1 < len(spans) else b + 0.6
        s["vo_span"] = [a, b]
        s["vo_s"] = round(nxt - a, 3)          # the shot runs until the next line starts
    (jd / "job.json").write_text(json.dumps(job, indent=2, ensure_ascii=False))
    print(f"{jd.name}: {len(shots)} lines, {spans[-1][1]:.1f} s")


if __name__ == "__main__":
    main(sys.argv[1])
