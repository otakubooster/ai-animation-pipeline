"""Split one long narration take into its script segments at the pauses between them.

The narration is generated as one take with a [pause] tag between segments (sounds more natural than one take per
line and needs far fewer generations). The cut points are the silences closest to where each boundary is expected
from the segments' text lengths, chosen in order by dynamic programming, so a long pause inside a segment does not
steal a boundary.

  python voice/split_take.py TAKE.mp3 SEGMENTS.json OUT_DIR    # SEGMENTS.json = ["text", ...]; writes OUT_DIR/seg_NN.wav
"""

from __future__ import annotations

import json
import re
import subprocess
import sys
from pathlib import Path


def silences(path: Path, noise_db: int = -32, min_s: float = 0.12) -> list[tuple[float, float]]:
    err = subprocess.run(["ffmpeg", "-nostdin", "-i", str(path), "-af", f"silencedetect=noise={noise_db}dB:d={min_s}", "-f", "null", "-"],
                         capture_output=True, text=True).stderr
    starts = [float(x) for x in re.findall(r"silence_start: ([\d.]+)", err)]
    ends = [float(x) for x in re.findall(r"silence_end: ([\d.]+)", err)]
    return list(zip(starts, ends))


def duration(path: Path) -> float:
    return float(subprocess.run(["ffprobe", "-v", "error", "-show_entries", "format=duration", "-of", "csv=p=0", str(path)],
                                capture_output=True, text=True, check=True).stdout)


def weight(text: str) -> int:
    return len(re.sub(r"\[[^\]]*\]|\W", "", text)) + 2


def choose_cuts(texts: list[str], sil: list[tuple[float, float]], total: float) -> list[tuple[float, float]]:
    """Pick len(texts)-1 silences (in order) minimising distance to the text-proportional boundary times;
    longer silences are preferred (the [pause] gaps)."""
    w = [weight(t) for t in texts]
    speech = total - sum(b - a for a, b in sil) * 0.0
    acc, exp = 0, []
    for x in w[:-1]:
        acc += x
        exp.append(speech * acc / sum(w))
    k, n = len(exp), len(sil)
    if n < k:
        raise ValueError(f"only {n} pauses for {k} boundaries")
    INF = float("inf")
    cost = [[INF] * (n + 1) for _ in range(k + 1)]
    back = [[-1] * (n + 1) for _ in range(k + 1)]
    for j in range(n + 1):
        cost[0][j] = 0.0
    for i in range(1, k + 1):
        for j in range(i, n + 1):
            a, b = sil[j - 1]
            mid = (a + b) / 2
            c = (mid - exp[i - 1]) ** 2 - 0.5 * (b - a)       # close to the expected time first, longer pause second
            best, arg = INF, -1
            for jj in range(i - 1, j):
                if cost[i - 1][jj] < best:
                    best, arg = cost[i - 1][jj], jj
            cost[i][j] = best + c
            back[i][j] = arg
    j = min(range(k, n + 1), key=lambda jj: cost[k][jj])
    picks = []
    for i in range(k, 0, -1):
        picks.append(sil[j - 1])
        j = back[i][j]
    return picks[::-1]


def split(take: Path, texts: list[str], out_dir: Path, cut_times: list[float] | None = None) -> list[dict]:
    """cut_times: exact boundaries (e.g. the [pause] markers of a Scribe transcript of the take); otherwise the
    pauses are found from the audio."""
    out_dir.mkdir(parents=True, exist_ok=True)
    total = duration(take)
    if cut_times:
        assert len(cut_times) == len(texts) - 1, (len(cut_times), len(texts))
        bounds = [0.0] + list(cut_times) + [total]
    else:
        cuts = choose_cuts(texts, silences(take), total)
        bounds = [0.0] + [(a + b) / 2 for a, b in cuts] + [total]
    report = []
    for i, t in enumerate(texts):
        a, b = bounds[i], bounds[i + 1]
        dst = out_dir / f"seg_{i + 1:02d}.wav"
        subprocess.run(["ffmpeg", "-nostdin", "-v", "error", "-y", "-ss", f"{a:.3f}", "-to", f"{b:.3f}", "-i", str(take), "-ac", "1", "-ar", "44100", str(dst)], check=True)
        rate = (b - a) / weight(t)
        report.append({"check": "OK" if 0.06 <= rate <= 0.13 else "CHECK", "seg": i + 1, "start": round(a, 2), "end": round(b, 2), "dur": round(b - a, 2), "chars": weight(t),
                       "s_per_char": round((b - a) / weight(t), 3), "text": t[:40]})
    return report


if __name__ == "__main__":
    cuts = json.loads(Path(sys.argv[4]).read_text()) if len(sys.argv) > 4 else None
    rep = split(Path(sys.argv[1]), json.loads(Path(sys.argv[2]).read_text()), Path(sys.argv[3]), cuts)
    for r in rep:
        print(r)
