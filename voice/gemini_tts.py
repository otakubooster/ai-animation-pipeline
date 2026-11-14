"""Speech from Google AI Studio (Gemini TTS) — used when the user picks Gemini voices instead of ElevenLabs.

The API key comes from the GEMINI_API_KEY environment variable (never stored in the repo). Gemini returns raw
24 kHz 16-bit mono PCM; it is written as a WAV file. Style, accent and emotion are steered by a natural-language
instruction in front of the line (e.g. "Say in Moroccan Darija, softly and emotionally:").

  GEMINI_API_KEY=... python voice/gemini_tts.py OUT.wav VOICE "INSTRUCTION" "LINE" [--model gemini-3.8-flash-tts]
"""

from __future__ import annotations

import argparse
import base64
import json
import os
import time
import urllib.error
import urllib.request
import wave
from pathlib import Path

API = "https://generativelanguage.googleapis.com/v1beta/models/{model}:generateContent"
DEFAULT_MODEL = "gemini-3.8-flash-tts"
SR = 24000


def request_body(voice: str, instruction: str, line: str) -> dict:
    text = f"{instruction.strip()}\n{line.strip()}" if instruction else line.strip()
    return {"contents": [{"parts": [{"text": text}]}],
            "generationConfig": {"responseModalities": ["AUDIO"],
                                 "speechConfig": {"voiceConfig": {"prebuiltVoiceConfig": {"voiceName": voice}}}}}


def pcm_from_response(resp: dict) -> bytes:
    for cand in resp.get("candidates", []):
        for part in cand.get("content", {}).get("parts", []):
            data = part.get("inlineData", {}).get("data")
            if data:
                return base64.b64decode(data)
    raise ValueError(f"no audio in response: {json.dumps(resp)[:400]}")


def write_wav(path: Path, pcm: bytes, sr: int = SR) -> float:
    with wave.open(str(path), "wb") as w:
        w.setnchannels(1)
        w.setsampwidth(2)
        w.setframerate(sr)
        w.writeframes(pcm)
    return len(pcm) / 2 / sr


def synth(out: Path, voice: str, instruction: str, line: str, model: str = DEFAULT_MODEL, key: str | None = None,
          tries: int = 4) -> float:
    key = key or os.environ["GEMINI_API_KEY"]
    req = urllib.request.Request(API.format(model=model), data=json.dumps(request_body(voice, instruction, line)).encode(),
                                 headers={"Content-Type": "application/json", "x-goog-api-key": key})
    for k in range(tries):
        try:
            with urllib.request.urlopen(req, timeout=180) as r:
                return write_wav(out, pcm_from_response(json.loads(r.read())))
        except urllib.error.HTTPError as e:            # 429 / 5xx: back off and retry; anything else is fatal
            if e.code not in (429, 500, 502, 503, 504) or k == tries - 1:
                raise RuntimeError(f"Gemini TTS HTTP {e.code}: {e.read()[:300]!r}") from None
            time.sleep(8 * (k + 1))
        except (urllib.error.URLError, ConnectionError, TimeoutError) as e:   # dropped connection: retry
            if k == tries - 1:
                raise RuntimeError(f"Gemini TTS network error: {e}") from None
            time.sleep(8 * (k + 1))
    raise RuntimeError("unreachable")


if __name__ == "__main__":
    ap = argparse.ArgumentParser()
    ap.add_argument("out")
    ap.add_argument("voice")
    ap.add_argument("instruction")
    ap.add_argument("line")
    ap.add_argument("--model", default=DEFAULT_MODEL)
    a = ap.parse_args()
    print(f"{synth(Path(a.out), a.voice, a.instruction, a.line, a.model):.2f} s")
