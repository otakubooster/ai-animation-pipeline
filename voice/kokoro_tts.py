"""Free local voiceover with Kokoro-82M (Apache 2.0) on the CPU, via kokoro-onnx. Fallback when ElevenLabs credits
run out. ElevenLabs audio tags ([sad], [excited] ...) are dropped; Kokoro takes its delivery from punctuation.

  pip install kokoro-onnx soundfile
  # model files (GitHub release, ~350 MB): kokoro-v1.0.onnx + voices-v1.0.bin from
  # https://github.com/thewh1teagle/kokoro-onnx/releases/tag/model-files-v1.0
  python voice/kokoro_tts.py --model-dir DIR --voice bf_emma --speed 0.85 --text-file part.txt --out part.mp3

British voices: bf_emma (best), bf_isabella, bf_alice, bf_lily, bm_george, bm_fable, bm_lewis, bm_daniel.
"""

from __future__ import annotations

import argparse
import json
import re
import subprocess
from pathlib import Path

SR = 24000


def clean(text: str) -> str:
    """Drop [audio tags], keep the words and punctuation."""
    return re.sub(r"\s+", " ", re.sub(r"\[[^\]]*\]", " ", text)).strip()


def sentences(text: str) -> list[str]:
    return [s for s in re.split(r"(?<=[.!?…])\s+", clean(text)) if s]


def synth(text: str, out: Path, model_dir: Path, voice: str, speed: float = 0.85, pause: float = 0.28) -> float:
    """Speak `text` sentence by sentence (steadier prosody than one long call) with short pauses; write MP3.
    Returns the duration in seconds. Sentence start/end times go to <out>.sentences.json (for voice/align.py)."""
    import numpy as np
    import soundfile as sf
    from kokoro_onnx import Kokoro

    k = Kokoro(str(model_dir / "kokoro-v1.0.onnx"), str(model_dir / "voices-v1.0.bin"))
    gap = np.zeros(int(pause * SR), dtype=np.float32)
    parts = [np.zeros(int(0.15 * SR), dtype=np.float32)]
    spans, t = [], 0.15
    for s in sentences(text):
        audio, sr = k.create(s, voice=voice, speed=speed, lang="en-gb")
        assert sr == SR
        parts += [audio.astype(np.float32), gap]
        spans.append({"text": s, "start": round(t, 3), "end": round(t + len(audio) / SR, 3)})
        t += (len(audio) + len(gap)) / SR
    wav = out.with_suffix(".wav")
    sf.write(wav, np.concatenate(parts), SR)
    subprocess.run(["ffmpeg", "-nostdin", "-v", "error", "-y", "-i", str(wav), "-ar", "44100", "-b:a", "128k", str(out)],
                   check=True)
    wav.unlink()
    out.with_suffix(".sentences.json").write_text(json.dumps(spans, indent=1, ensure_ascii=False))
    return sum(len(p) for p in parts) / SR


def main() -> None:
    ap = argparse.ArgumentParser()
    ap.add_argument("--model-dir", type=Path, required=True)
    ap.add_argument("--voice", default="bf_emma")
    ap.add_argument("--speed", type=float, default=0.85)
    ap.add_argument("--text-file", type=Path, required=True)
    ap.add_argument("--out", type=Path, required=True)
    a = ap.parse_args()
    print(f"{a.out} {synth(a.text_file.read_text(), a.out, a.model_dir, a.voice, a.speed):.1f}s")


if __name__ == "__main__":
    main()
