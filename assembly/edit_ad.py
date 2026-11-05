"""Assemble a finished ad: shot clips + voiceover + word-by-word captions + offer banner -> 9:16, 1:1, 16:9 MP4s.

Timing comes from the voiceover's word timestamps (ElevenLabs Scribe words.json): each shot runs from the
first word of its VO line to the first word of the next line (hard cuts, SOP). Each clip loses 0.5 s at the
head (SOP: first/last 0.5 s are often unstable); if a clip is shorter than its slot it is slowed down
(up to 1.5x) and then held on its last frame.

  python assembly/edit_ad.py JOB_DIR --clips DIR [--stills] --vo vo/take1.mp3 --words vo/words_take1.json --out OUT

--stills builds an animatic from the first frames (slow push-in) instead of rendered clips.
"""

from __future__ import annotations

import argparse
import json
import re
import shutil
import subprocess
from pathlib import Path

from PIL import Image, ImageDraw, ImageFont

FPS = 30
W, H = 1080, 1920
CAPTION_FONT = "Inter Display Black"
BANNER_FONT = "/usr/share/fonts/opentype/inter/InterDisplay-Black.otf"
EMOJI_FONT = "/usr/share/fonts/truetype/noto/NotoColorEmoji.ttf"
HEAD_TRIM = 0.5
MAX_SLOW = 1.5


def run(cmd: list[str]) -> None:
    subprocess.run(cmd, check=True)


def probe_duration(path: Path) -> float:
    out = subprocess.run(["ffprobe", "-v", "error", "-show_entries", "format=duration", "-of", "csv=p=0", str(path)],
                         capture_output=True, text=True, check=True).stdout
    return float(out.strip())


def norm(tok: str) -> str:
    return re.sub(r"[^a-z0-9']", "", tok.lower().replace("’", "'"))


def spoken_words(words_json: Path) -> list[dict]:
    """Drop ElevenLabs audio tags such as '[pained,' 'quietly]' '[sad]' that Scribe transcribes as words."""
    words, in_tag = [], False
    for w in json.loads(words_json.read_text())["words"]:
        if w.get("type", "word") != "word":
            continue
        t = w["text"]
        if t.startswith("["):
            in_tag = True
        if not in_tag:
            words.append(w)
        if t.endswith("]"):
            in_tag = False
    return words


def align_shots(shots: list[dict], words: list[dict]) -> list[dict]:
    """Assign transcript words to shots in order; returns per-shot start/end and its words."""
    i, out = 0, []
    for s in shots:
        toks = [norm(t) for t in s["vo_line"].split() if norm(t)]
        seg = words[i:i + len(toks)]
        got = [norm(w["text"]) for w in seg]
        if got != toks:
            raise ValueError(f"{s['id']}: transcript mismatch\n  script: {toks}\n  audio:  {got}")
        out.append({"id": s["id"], "words": seg})
        i += len(toks)
    for k, o in enumerate(out):
        o["start"] = 0.0 if k == 0 else o["words"][0]["start"] - 0.05
    for k, o in enumerate(out):
        o["end"] = out[k + 1]["start"] if k + 1 < len(out) else o["words"][-1]["end"] + 1.0
        o["dur"] = round(o["end"] - o["start"], 3)
    return out


def render_segment(src: Path, dur: float, dst: Path, still: bool, head: float = HEAD_TRIM) -> dict:
    """Cut one shot to exactly `dur` seconds at 1080x1920/30 fps, starting `head` seconds into the clip."""
    scale = f"scale={W}:{H}:force_original_aspect_ratio=increase:flags=lanczos,crop={W}:{H},setsar=1"
    if still:
        frames = int(round(dur * FPS))
        zoom = f"zoompan=z='min(1+0.08*on/{frames},1.08)':x='iw/2-(iw/zoom/2)':y='ih/2-(ih/zoom/2)':d={frames}:s={W}x{H}:fps={FPS}"
        run(["ffmpeg", "-v", "error", "-y", "-loop", "1", "-i", str(src), "-vf",
             f"scale={W*2}:{H*2}:flags=lanczos,{zoom},setsar=1", "-frames:v", str(frames),
             "-c:v", "libx264", "-crf", "18", "-pix_fmt", "yuv420p", "-an", str(dst)])
        return {"mode": "still"}
    usable = max(probe_duration(src) - head, 0.1)
    slow = min(max(dur / usable, 1.0), MAX_SLOW)
    vf = f"trim=start={head},setpts=(PTS-STARTPTS)*{slow:.4f},fps={FPS},{scale},tpad=stop_mode=clone:stop_duration={dur}"
    run(["ffmpeg", "-v", "error", "-y", "-i", str(src), "-vf", vf, "-t", f"{dur:.3f}",
         "-c:v", "libx264", "-crf", "18", "-pix_fmt", "yuv420p", "-an", str(dst)])
    return {"mode": "clip", "clip_start": head, "source_s": round(usable + head, 2), "slowdown": round(slow, 3),
            "held_s": round(max(dur - usable * slow, 0), 2)}


def ass_time(t: float) -> str:
    t = max(t, 0)
    return f"{int(t // 3600)}:{int(t % 3600 // 60):02d}:{t % 60:05.2f}"


def captions_ass(words: list[dict], path: Path, res: tuple[int, int], size: int, margin_v: int, max_words: int = 3,
                 high_spans: list[tuple[float, float]] = (), margin_high: int = 0) -> None:
    """Word-by-word captions: 2-3 word groups, white, the word being spoken in yellow.
    Inside `high_spans` (product shots) captions move up to `margin_high` so they never cover the label."""
    groups, cur = [], []
    for w in words:
        cur.append(w)
        if len(cur) >= max_words or re.search(r"[,.!?]$", w["text"]):
            groups.append(cur)
            cur = []
    if cur:
        groups.append(cur)
    clean = lambda t: re.sub(r"[,.!?]+$", "", t)
    lines = ["[Script Info]", "ScriptType: v4.00+", f"PlayResX: {res[0]}", f"PlayResY: {res[1]}", "WrapStyle: 2", "",
             "[V4+ Styles]",
             "Format: Name, Fontname, Fontsize, PrimaryColour, SecondaryColour, OutlineColour, BackColour, Bold, Italic, "
             "Underline, StrikeOut, ScaleX, ScaleY, Spacing, Angle, BorderStyle, Outline, Shadow, Alignment, MarginL, MarginR, MarginV, Encoding",
             f"Style: Cap,{CAPTION_FONT},{size},&H00FFFFFF,&H00FFFFFF,&H00000000,&H64000000,0,0,0,0,100,100,0,0,1,"
             f"{max(size // 13, 4)},2,2,60,60,{margin_v},1", "", "[Events]",
             "Format: Layer, Start, End, Style, Name, MarginL, MarginR, MarginV, Effect, Text"]
    for gi, g in enumerate(groups):
        g_end = groups[gi + 1][0]["start"] if gi + 1 < len(groups) else g[-1]["end"] + 0.6
        g_end = min(g_end, g[-1]["end"] + 0.6)
        for wi, w in enumerate(g):
            start = w["start"]
            end = g[wi + 1]["start"] if wi + 1 < len(g) else g_end
            text = " ".join(("{\\c&H00E5FF&}" + clean(x["text"]) + "{\\c&HFFFFFF&}") if x is w else clean(x["text"]) for x in g)
            mv = margin_high if any(a <= start < b for a, b in high_spans) else 0
            lines.append(f"Dialogue: 0,{ass_time(start)},{ass_time(end)},Cap,,0,0,{mv},,{text}")
    path.write_text("\n".join(lines) + "\n")


def banner_png(text: str, path: Path, width: int) -> None:
    """Rounded white pill with bold dark text and a colour emoji (rendered separately, Noto is bitmap-only)."""
    emoji = "".join(ch for ch in text if ord(ch) > 0x2000)
    label = text.replace(emoji, "").strip()
    fsize = int(width * 0.06)
    font = ImageFont.truetype(BANNER_FONT, fsize)
    tw = font.getbbox(label)[2]
    em = None
    if emoji:
        ef = ImageFont.truetype(EMOJI_FONT, 109)
        em = Image.new("RGBA", (136, 128), (0, 0, 0, 0))
        ImageDraw.Draw(em).text((0, 0), emoji, font=ef, embedded_color=True)
        em = em.crop(em.getbbox()).resize((int(fsize * 1.15), int(fsize * 1.15)), Image.LANCZOS)
    pad_x, pad_y = int(fsize * 0.7), int(fsize * 0.45)
    bw = tw + (em.width + int(fsize * 0.3) if em else 0) + 2 * pad_x
    bh = int(fsize * 1.25) + 2 * pad_y
    img = Image.new("RGBA", (bw + 12, bh + 12), (0, 0, 0, 0))
    d = ImageDraw.Draw(img)
    d.rounded_rectangle([6, 10, bw + 6, bh + 10], radius=bh // 2, fill=(0, 0, 0, 70))       # soft shadow
    d.rounded_rectangle([6, 6, bw + 6, bh + 6], radius=bh // 2, fill=(255, 255, 255, 255))
    ty = 6 + (bh - fsize) // 2 - int(fsize * 0.12)
    d.text((6 + pad_x, ty), label, font=font, fill=(200, 16, 46, 255))
    if em:
        img.alpha_composite(em, (6 + pad_x + tw + int(fsize * 0.3), 6 + (bh - em.height) // 2))
    img.save(path)


def main() -> None:
    ap = argparse.ArgumentParser()
    ap.add_argument("job_dir", type=Path)
    ap.add_argument("--clips", type=Path)
    ap.add_argument("--stills", type=Path, help="folder of first frames: build an animatic instead")
    ap.add_argument("--vo", type=Path, required=True)
    ap.add_argument("--words", type=Path, required=True)
    ap.add_argument("--out", type=Path, required=True)
    ap.add_argument("--name", default="ad")
    a = ap.parse_args()

    job = json.loads((a.job_dir / "job.json").read_text())
    out, tmp = a.out, a.out / "work"
    tmp.mkdir(parents=True, exist_ok=True)
    words = spoken_words(a.words)
    timeline = align_shots(job["shots"], words)

    seg_files, report = [], []
    for t, shot in zip(timeline, job["shots"]):
        dst = tmp / f"{shot['id']}.mp4"
        if a.stills:
            info = render_segment(a.stills / shot["frame"], t["dur"], dst, still=True)
        else:
            src = next((p for p in [a.clips / "clips_32fps" / f"{shot['id']}.mp4", a.clips / "clips" / f"{shot['id']}.mp4",
                                    a.clips / f"{shot['id']}.mp4"] if p.exists()), None)
            info = render_segment(src, t["dur"], dst, still=False, head=shot.get("clip_start", HEAD_TRIM)) if src else \
                render_segment(a.job_dir / "frames" / shot["frame"], t["dur"], dst, still=True) | {"missing_clip": True}
        seg_files.append(dst)
        report.append({"id": shot["id"], "start": round(t["start"], 2), "dur": t["dur"], **info})
    (tmp / "concat.txt").write_text("".join(f"file '{p.resolve()}'\n" for p in seg_files))
    clean = tmp / "clean.mp4"
    run(["ffmpeg", "-v", "error", "-y", "-f", "concat", "-safe", "0", "-i", str(tmp / "concat.txt"), "-c", "copy", str(clean)])

    vo = tmp / "vo.m4a"
    run(["ffmpeg", "-v", "error", "-y", "-i", str(a.vo), "-af", "loudnorm=I=-16:TP=-1.5:LRA=11", "-c:a", "aac", "-b:a", "192k", str(vo)])

    banner_t = next(t["start"] for t in timeline if t["id"] == job["banner"]["from_shot"])
    total = timeline[-1]["end"]
    formats = {
        "9x16": {"res": (1080, 1920), "cap": (84, 470), "cap_high": 1000, "banner_w": 1080, "banner_y": 150,
                 "vf": "[0:v]null[base]"},
        "1x1": {"res": (1080, 1080), "cap": (64, 120), "cap_high": 520, "banner_w": 1080, "banner_y": 60,
                "vf": "[0:v]crop=1080:1080:0:380[base]"},
        "16x9": {"res": (1920, 1080), "cap": (60, 90), "cap_high": 480, "banner_w": 1400, "banner_y": 50,
                 "vf": "[0:v]split[a][b];[a]scale=1920:-2,crop=1920:1080,boxblur=30:3,eq=brightness=-0.08[bg];"
                       "[b]scale=-2:1080[fg];[bg][fg]overlay=(W-w)/2:0[base]"},
    }
    high_ids = set(job.get("captions", {}).get("high_on_shots", []))
    high_spans = [(t["start"], t["end"]) for t in timeline if t["id"] in high_ids]
    outputs = []
    for name, f in formats.items():
        ass = tmp / f"captions_{name}.ass"
        captions_ass(words, ass, f["res"], f["cap"][0], f["cap"][1], high_spans=high_spans, margin_high=f["cap_high"])
        png = tmp / f"banner_{name}.png"
        banner_png(job["banner"]["text"], png, f["banner_w"])
        dst = out / f"{a.name}_{name}.mp4"
        fc = (f"{f['vf']};[base][1:v]overlay=(W-w)/2:{f['banner_y']}:enable='gte(t,{banner_t:.2f})'[b];"
              f"[b]ass={ass}[v]")
        run(["ffmpeg", "-v", "error", "-y", "-i", str(clean), "-i", str(png), "-i", str(vo), "-filter_complex", fc,
             "-map", "[v]", "-map", "2:a", "-c:v", "libx264", "-crf", "19", "-preset", "medium", "-pix_fmt", "yuv420p",
             "-c:a", "copy", "-t", f"{total:.2f}", "-movflags", "+faststart", str(dst)])
        outputs.append(str(dst))
    (out / f"{a.name}_edit_report.json").write_text(json.dumps({"total_s": round(total, 2), "banner_from_s": round(banner_t, 2),
                                                                "shots": report, "outputs": outputs}, indent=2))
    print(json.dumps({"total_s": round(total, 2), "outputs": outputs}, indent=2))
    if not a.stills:
        shutil.rmtree(tmp, ignore_errors=True)


if __name__ == "__main__":
    main()
