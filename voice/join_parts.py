"""Join per-character voice parts into one take: each part loudness-matched, 0.4 s gap, word timings shifted.

  python voice/join_parts.py jobs/<slug>/vo tooth gum kidney meow   # part_<name>.mp3 + words_<name>.json -> take.mp3, words.json
"""

from __future__ import annotations

import json
import subprocess
import sys
from pathlib import Path

GAP = 0.4


def dur(p: Path) -> float:
    return float(subprocess.run(["ffprobe", "-v", "error", "-show_entries", "format=duration", "-of", "csv=p=0", str(p)],
                                capture_output=True, text=True, check=True).stdout)


def main(vo: str, *names: str) -> None:
    d = Path(vo)
    words, t, inputs, filt = [], 0.0, [], []
    for i, n in enumerate(names):
        part = d / f"part_{n}.mp3"
        for w in json.loads((d / f"words_{n}.json").read_text())["words"]:
            words.append(w | {"start": round(w["start"] + t, 3), "end": round(w["end"] + t, 3), "speaker": n})
        inputs += ["-i", str(part)]
        filt.append(f"[{i}:a]aresample=44100,loudnorm=I=-16:TP=-1.5:LRA=11,apad=pad_dur={GAP}[p{i}]")
        t += dur(part) + GAP
    fc = ";".join(filt) + ";" + "".join(f"[p{i}]" for i in range(len(names))) + f"concat=n={len(names)}:v=0:a=1[a]"
    subprocess.run(["ffmpeg", "-nostdin", "-v", "error", "-y", *inputs, "-filter_complex", fc, "-map", "[a]",
                    "-ar", "44100", "-b:a", "192k", str(d / "take.mp3")], check=True)
    (d / "words.json").write_text(json.dumps({"words": words}, ensure_ascii=False))
    print(d / "take.mp3", f"{dur(d / 'take.mp3'):.1f}s", len(words), "words")


if __name__ == "__main__":
    main(*sys.argv[1:])
