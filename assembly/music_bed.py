"""Compose an original background-music bed for an ad on the CPU (no GPU, no licensing): two moods with a
crossfade at the story's turn (e.g. sad -> uplifting at the product reveal), then mix it under the voiceover
with ducking and swap it into the finished MP4s without re-encoding the video.

  python assembly/music_bed.py --videos a_9x16.mp4 a_1x1.mp4 a_16x9.mp4 --turn 38.3 --style cat --out-suffix _music

Styles (mood A -> mood B):
  cat    gentle minor pad + sparse piano  ->  warm major piano arpeggio, soft bass and light shaker
  tooth  tense minor pizzicato plucks      ->  bright, bouncy major plucks with bass and claps
  theo   slow melancholic piano + pad      ->  hopeful major piano with warm strings
"""

from __future__ import annotations

import argparse
import subprocess
import wave
from pathlib import Path

import numpy as np

SR = 44100
A4 = 440.0
NOTE = {n: i for i, n in enumerate(["C", "C#", "D", "D#", "E", "F", "F#", "G", "G#", "A", "A#", "B"])}


def hz(name: str) -> float:
    """'A3' -> frequency."""
    pitch, octave = name[:-1], int(name[-1])
    return A4 * 2 ** ((NOTE[pitch] + 12 * (octave + 1) - 69) / 12)


def env(n: int, attack: float, release: float) -> np.ndarray:
    e = np.ones(n)
    a, r = min(int(attack * SR), n // 2), min(int(release * SR), n // 2)
    if a:
        e[:a] = np.linspace(0, 1, a)
    if r:
        e[-r:] *= np.linspace(1, 0, r)
    return e


def piano(f: float, dur: float, amp: float, bright: float = 1.0, decay: float = 1.6) -> np.ndarray:
    n = int(dur * SR)
    t = np.arange(n) / SR
    out = np.zeros(n)
    for k in range(1, 9):
        fk = f * k * (1 + 0.0004 * k * k)           # slight inharmonicity
        if fk > 8000:
            break
        out += (1 / k ** (1.6 / bright)) * np.exp(-t * k ** 0.8 / decay) * np.sin(2 * np.pi * fk * t)
    return amp * out * env(n, 0.004, 0.08)


def pluck(f: float, dur: float, amp: float) -> np.ndarray:
    return piano(f, dur, amp, bright=1.3, decay=0.22)


def pad(freqs: list[float], dur: float, amp: float, warmth: float = 1.6) -> np.ndarray:
    n = int(dur * SR)
    t = np.arange(n) / SR
    out = np.zeros(n)
    rng = np.random.default_rng(int(sum(freqs)))
    for f in freqs:
        for det in (-0.003, 0.0, 0.003):
            ph = rng.uniform(0, 2 * np.pi)
            for k in range(1, 9):
                if f * k > 5000:
                    break
                out += (1 / k ** warmth) * np.sin(2 * np.pi * f * (1 + det) * k * t + ph * k)
    out /= max(1, len(freqs) * 3)
    lfo = 0.85 + 0.15 * np.sin(2 * np.pi * 0.15 * t)
    return amp * out * lfo * env(n, 0.9, 0.9)


def bass(f: float, dur: float, amp: float) -> np.ndarray:
    n = int(dur * SR)
    t = np.arange(n) / SR
    return amp * (np.sin(2 * np.pi * f * t) + 0.25 * np.sin(4 * np.pi * f * t)) * np.exp(-t / 1.2) * env(n, 0.01, 0.1)


def noise_hit(dur: float, amp: float, tone: float = 0.6) -> np.ndarray:
    n = int(dur * SR)
    t = np.arange(n) / SR
    x = np.random.default_rng(n).standard_normal(n)
    x = np.diff(x, prepend=0) * tone + x * (1 - tone)     # crude high-pass
    return amp * x * np.exp(-t / 0.03)


def place(buf: np.ndarray, sig: np.ndarray, at: float) -> None:
    i = int(at * SR)
    if i >= len(buf):
        return
    j = min(len(buf), i + len(sig))
    buf[i:j] += sig[: j - i]


def chord_notes(root: str, minor: bool, octave: int) -> list[str]:
    r = NOTE[root]
    third = 3 if minor else 4
    names = list(NOTE)
    return [f"{names[(r + s) % 12]}{octave + (r + s) // 12}" for s in (0, third, 7, 12)]


def section(style: str, mood: str, length: float, seed: int) -> np.ndarray:
    """Render one mood section of `length` seconds."""
    rng = np.random.default_rng(seed)
    buf = np.zeros(int((length + 4) * SR))
    plans = {
        ("cat", "A"):   dict(bpm=66, prog=[("A", 1), ("F", 0), ("C", 0), ("G", 0)], pad=0.20, keys="sparse"),
        ("cat", "B"):   dict(bpm=96, prog=[("C", 0), ("G", 0), ("A", 1), ("F", 0)], pad=0.13, keys="arp", bass=True, shaker=True),
        ("tooth", "A"): dict(bpm=84, prog=[("D", 1), ("A#", 0), ("G", 1), ("A", 0)], pad=0.12, keys="pizz", bass=True),
        ("tooth", "B"): dict(bpm=112, prog=[("F", 0), ("C", 0), ("D", 1), ("A#", 0)], pad=0.08, keys="bounce", bass=True, clap=True),
        ("theo", "A"):  dict(bpm=58, prog=[("E", 1), ("C", 0), ("G", 0), ("D", 0)], pad=0.16, keys="sparse"),
        ("theo", "B"):  dict(bpm=70, prog=[("G", 0), ("D", 0), ("E", 1), ("C", 0)], pad=0.16, keys="arp_soft", bass=True),
    }
    p = plans[(style, mood)]
    beat = 60 / p["bpm"]
    bar = 4 * beat
    t, i = 0.0, 0
    while t < length + 2:
        root, minor = p["prog"][i % len(p["prog"])]
        notes = chord_notes(root, bool(minor), 3)
        place(buf, pad([hz(n) for n in notes[:3]], bar + 1.0, p["pad"]), t)
        if p.get("bass"):
            for b in range(4 if style == "tooth" else 2):
                place(buf, bass(hz(f"{root}2"), beat * (1 if style == "tooth" else 2), 0.16), t + b * beat * (1 if style == "tooth" else 2))
        top = chord_notes(root, bool(minor), 4)
        k = p["keys"]
        if k == "sparse":                      # a few soft piano notes per bar
            for b in rng.choice(4, size=2, replace=False):
                place(buf, piano(hz(top[rng.integers(0, 3)]), beat * 2.5, 0.10), t + b * beat)
        elif k in ("arp", "arp_soft"):         # 8th-note arpeggio
            seq = [top[0], top[1], top[2], top[3], top[2], top[1], top[0], top[1]]
            for s, n in enumerate(seq):
                place(buf, piano(hz(n), beat * 1.5, 0.075 if k == "arp" else 0.06), t + s * beat / 2)
        elif k == "pizz":                      # staccato plucks, slightly uneasy rhythm
            for s in (0, 1.5, 2, 3):
                place(buf, pluck(hz(top[rng.integers(0, 3)]), 0.5, 0.11), t + s * beat)
        elif k == "bounce":                    # bouncy off-beat plucks
            for s in range(8):
                if s % 2 or rng.random() < 0.4:
                    place(buf, pluck(hz(top[s % 3]), 0.4, 0.10), t + s * beat / 2)
        if p.get("shaker"):
            for s in range(8):
                place(buf, noise_hit(0.08, 0.025 if s % 2 else 0.015), t + s * beat / 2)
        if p.get("clap"):
            for s in (1, 3):
                place(buf, noise_hit(0.12, 0.05, tone=0.3), t + s * beat)
        t += bar
        i += 1
    return buf[: int(length * SR)]


def compose(total: float, turn: float, style: str, xfade: float = 2.0) -> np.ndarray:
    a = section(style, "A", turn + xfade, seed=1)
    b = section(style, "B", total - turn + xfade, seed=2)
    out = np.zeros(int((total + 1) * SR))
    na = len(a)
    fade_out = np.ones(na)
    fade_out[-int(xfade * SR):] = np.linspace(1, 0, int(xfade * SR)) ** 0.7
    place(out, a * fade_out, 0)
    fade_in = np.ones(len(b))
    fade_in[: int(xfade * SR)] = np.linspace(0, 1, int(xfade * SR)) ** 0.7
    place(out, b * fade_in, turn)
    out = out[: int(total * SR)]
    tail = int(min(3.0, total / 4) * SR)
    out[-tail:] *= np.linspace(1, 0, tail)                  # gentle fade at the end
    out /= max(1e-6, np.max(np.abs(out)))
    return out


def write_wav(path: Path, mono: np.ndarray) -> None:
    left = mono
    right = np.roll(mono, int(0.012 * SR))                   # small Haas offset for width
    st = np.stack([left, right], 1)
    pcm = (np.clip(st, -1, 1) * 32000).astype("<i2")
    with wave.open(str(path), "wb") as w:
        w.setnchannels(2)
        w.setsampwidth(2)
        w.setframerate(SR)
        w.writeframes(pcm.tobytes())


def duration(path: Path) -> float:
    return float(subprocess.run(["ffprobe", "-v", "error", "-show_entries", "format=duration", "-of", "csv=p=0", str(path)],
                                capture_output=True, text=True, check=True).stdout)


def main() -> None:
    ap = argparse.ArgumentParser()
    ap.add_argument("--videos", nargs="+", type=Path, required=True, help="finished MP4s of one ad (same audio)")
    ap.add_argument("--turn", type=float, required=True, help="seconds where mood A turns into mood B")
    ap.add_argument("--style", choices=["cat", "tooth", "theo"], required=True)
    ap.add_argument("--music-level", type=float, default=-27.0, help="music loudness (LUFS) before ducking")
    ap.add_argument("--out-suffix", default="_music")
    a = ap.parse_args()

    total = duration(a.videos[0])
    work = a.videos[0].parent / "music_work"
    work.mkdir(exist_ok=True)
    raw = work / "bed_raw.wav"
    write_wav(raw, compose(total, a.turn, a.style))
    bed = work / "bed.wav"
    # Room reverb + gentle top cut, then set the music level.
    subprocess.run(["ffmpeg", "-nostdin", "-v", "error", "-y", "-i", str(raw), "-af",
                    f"aecho=0.8:0.55:70|140|230:0.32|0.22|0.14,lowpass=f=7500,highpass=f=60,"
                    f"loudnorm=I={a.music_level}:TP=-6:LRA=7", "-ar", "44100", str(bed)], check=True)
    for v in a.videos:
        dst = v.with_name(v.stem + a.out_suffix + v.suffix)
        # Duck the music under the voice (sidechain), mix, limit; copy the video stream untouched.
        fc = ("[0:a]asplit=2[vo][key];[1:a]aresample=48000[m];"
              "[m][key]sidechaincompress=threshold=0.02:ratio=5:attack=30:release=500:makeup=1[duck];"
              "[vo][duck]amix=inputs=2:duration=first:normalize=0,alimiter=limit=0.95[a]")
        subprocess.run(["ffmpeg", "-nostdin", "-v", "error", "-y", "-i", str(v), "-i", str(bed), "-filter_complex", fc,
                        "-map", "0:v", "-map", "[a]", "-c:v", "copy", "-c:a", "aac", "-b:a", "192k",
                        "-movflags", "+faststart", str(dst)], check=True)
        print(dst)


if __name__ == "__main__":
    main()
