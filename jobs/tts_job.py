"""Generate the voiceover for every shot with Kokoro (open weights, Apache 2.0), one WAV per line per voice.

Writes $OUT/vo/<voice>/<shot>.wav and $OUT/vo/<voice>/timing.json with per-line duration and,
when Kokoro provides them, per-word start/end times (used for word-by-word captions).

Usage: python tts_job.py JOB_JSON OUT_DIR
"""

from __future__ import annotations

import json
import sys
import time
from pathlib import Path

import numpy as np
import soundfile as sf
from kokoro import KPipeline

SR = 24000


def main() -> None:
    job = json.loads(Path(sys.argv[1]).read_text())
    out = Path(sys.argv[2]) / "vo"
    voice_cfg = job["voice"]
    pipe = KPipeline(lang_code=voice_cfg.get("lang_code", "b"))
    for voice in voice_cfg["candidates"]:
        vdir = out / voice
        vdir.mkdir(parents=True, exist_ok=True)
        timing = {"voice": voice, "sample_rate": SR, "lines": []}
        t0 = time.perf_counter()
        for shot in job["shots"]:
            audio, words, offset = [], [], 0.0
            for result in pipe(shot["vo_line"], voice=voice, speed=float(voice_cfg.get("speed", 1.0))):
                a = result.audio.cpu().numpy() if hasattr(result.audio, "cpu") else np.asarray(result.audio)
                for tok in getattr(result, "tokens", None) or []:
                    if getattr(tok, "start_ts", None) is not None and tok.text.strip():
                        words.append({"w": tok.text, "start": round(offset + tok.start_ts, 3),
                                      "end": round(offset + (tok.end_ts or tok.start_ts), 3)})
                audio.append(a)
                offset += len(a) / SR
            wav = np.concatenate(audio) if audio else np.zeros(1, dtype=np.float32)
            sf.write(vdir / f"{shot['id']}.wav", wav, SR)
            timing["lines"].append({"id": shot["id"], "text": shot["vo_line"],
                                    "duration_s": round(len(wav) / SR, 3), "words": words})
        timing["seconds"] = round(time.perf_counter() - t0, 1)
        timing["total_vo_s"] = round(sum(l["duration_s"] for l in timing["lines"]), 2)
        (vdir / "timing.json").write_text(json.dumps(timing, indent=2))
        print(f"{voice}: {timing['total_vo_s']} s of VO in {timing['seconds']} s", flush=True)


if __name__ == "__main__":
    main()
