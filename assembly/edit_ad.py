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
BANNER_FONT_AR = "/usr/share/fonts/truetype/noto/NotoKufiArabic-Bold.ttf"
EMOJI_FONT = "/usr/share/fonts/truetype/noto/NotoColorEmoji.ttf"
HEAD_TRIM = 0.5
MAX_SLOW = 1.5


def run(cmd: list[str]) -> None:
    subprocess.run(cmd, check=True)


def probe_duration(path: Path) -> float:
    out = subprocess.run(["ffprobe", "-v", "error", "-show_entries", "format=duration", "-of", "csv=p=0", str(path)],
                         capture_output=True, text=True, check=True).stdout
    return float(out.strip())


AR_MARKS = re.compile(r"[\u0640\u064B-\u065F\u0670]")       # tatweel + Arabic diacritics


def norm(tok: str) -> str:
    """Comparable form of a word: lower case, letters/digits only (any script), Arabic diacritics dropped."""
    return re.sub(r"[^\w']|_", "", AR_MARKS.sub("", tok.lower().replace("’", "'")))


def is_arabic(text: str) -> bool:
    return bool(re.search(r"[\u0600-\u06FF]", text))


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


SILENT_SHOT_S = 3.0  # shots whose line is only a stage direction, e.g. "(music only)"


def spoken_text(line: str) -> str:
    """The words actually spoken: drops stage directions in parentheses and quote marks."""
    return re.sub(r"\([^)]*\)", " ", line).replace('"', " ").strip()


def align_shots(shots: list[dict], words: list[dict]) -> list[dict]:
    """Assign transcript words to shots in order; returns per-shot start/end and its words.
    A shot with no spoken words gets SILENT_SHOT_S seconds after the previous shot."""
    i, out = 0, []
    for s in shots:
        toks = [norm(t) for t in spoken_text(s["vo_line"]).split() if norm(t)]
        if not toks:
            out.append({"id": s["id"], "words": [], "silent": True})
            continue
        seg = words[i:i + len(toks)]
        got = [norm(w["text"]) for w in seg]
        if got != toks:
            raise ValueError(f"{s['id']}: transcript mismatch\n  script: {toks}\n  audio:  {got}")
        out.append({"id": s["id"], "words": seg})
        i += len(toks)
    # Starts: first spoken word (or right after the previous shot for silent shots).
    for k, o in enumerate(out):
        if o.get("silent"):
            continue
        o["start"] = 0.0 if k == 0 else o["words"][0]["start"] - 0.05
    for k, o in enumerate(out):
        if not o.get("silent"):
            continue
        prev = out[k - 1] if k else None
        o["start"] = (prev["words"][-1]["end"] + 0.4) if prev and prev.get("words") else (prev["start"] + 1 if prev else 0.0)
    for k, o in enumerate(out):
        if o.get("silent"):
            nxt = out[k + 1]["start"] if k + 1 < len(out) else None
            o["end"] = nxt if nxt else o["start"] + SILENT_SHOT_S
        else:
            nxt = out[k + 1]["start"] if k + 1 < len(out) else None
            o["end"] = nxt if nxt is not None else o["words"][-1]["end"] + 1.0
        o["dur"] = round(o["end"] - o["start"], 3)
    return out


def render_segment(src: Path, dur: float, dst: Path, still: bool, head: float = HEAD_TRIM, end: float | None = None,
                   boomerang: bool = True) -> dict:
    """Cut one shot to exactly `dur` seconds at 1080x1920/30 fps, starting `head` seconds into the clip."""
    scale = f"scale={W}:{H}:force_original_aspect_ratio=increase:flags=lanczos,crop={W}:{H},setsar=1"
    if still:
        frames = int(round(dur * FPS))
        zoom = f"zoompan=z='min(1+0.08*on/{frames},1.08)':x='iw/2-(iw/zoom/2)':y='ih/2-(ih/zoom/2)':d={frames}:s={W}x{H}:fps={FPS}"
        run(["ffmpeg", "-v", "error", "-y", "-loop", "1", "-i", str(src), "-vf",
             f"scale={W*2}:{H*2}:flags=lanczos,{zoom},setsar=1", "-frames:v", str(frames),
             "-c:v", "libx264", "-crf", "18", "-pix_fmt", "yuv420p", "-an", str(dst)])
        return {"mode": "still"}
    usable = max(min(probe_duration(src), end or 1e9) - head, 0.1)
    slow = min(max(dur / usable, 1.0), MAX_SLOW)
    trim = f"trim=start={head}" + (f":end={end}" if end else "")
    held = max(dur - usable * slow, 0)
    if held > 0.3 and boomerang:
        # Too short even slowed: play it forward then backward (ping-pong) instead of freezing on the last frame.
        pp = dst.with_name(dst.stem + "_pp.mp4")
        run(["ffmpeg", "-v", "error", "-y", "-i", str(src), "-filter_complex",
             f"[0:v]{trim},setpts=(PTS-STARTPTS)*{slow:.4f},fps={FPS},{scale},split[a][b];[b]reverse[r];[a][r]concat=n=2:v=1[v]",
             "-map", "[v]", "-c:v", "libx264", "-crf", "16", "-pix_fmt", "yuv420p", "-an", str(pp)])
        run(["ffmpeg", "-v", "error", "-y", "-stream_loop", "-1", "-i", str(pp), "-t", f"{dur:.3f}",
             "-c:v", "libx264", "-crf", "18", "-pix_fmt", "yuv420p", "-an", str(dst)])
        pp.unlink(missing_ok=True)
        return {"mode": "clip", "clip_start": head, "source_s": round(usable + head, 2), "slowdown": round(slow, 3),
                "held_s": 0.0, "boomerang": True}
    vf = f"{trim},setpts=(PTS-STARTPTS)*{slow:.4f},fps={FPS},{scale},tpad=stop_mode=clone:stop_duration={dur}"
    run(["ffmpeg", "-v", "error", "-y", "-i", str(src), "-vf", vf, "-t", f"{dur:.3f}",
         "-c:v", "libx264", "-crf", "18", "-pix_fmt", "yuv420p", "-an", str(dst)])
    return {"mode": "clip", "clip_start": head, "source_s": round(usable + head, 2), "slowdown": round(slow, 3),
            "held_s": round(held, 2)}


def overlay_box(base: Path, inner: Path, dst: Path, dur: float) -> None:
    """Picture in picture: `inner` (already cut to dur) in a white-framed box over the middle of `base`."""
    bw = int(W * 0.74)
    run(["ffmpeg", "-v", "error", "-y", "-i", str(base), "-i", str(inner), "-filter_complex",
         f"[1:v]scale={bw}:-2:force_original_aspect_ratio=decrease,pad=iw+20:ih+20:10:10:white[p];"
         f"[0:v][p]overlay=(W-w)/2:(H-h)/2-40:shortest=1[v]", "-map", "[v]", "-t", f"{dur:.3f}",
         "-c:v", "libx264", "-crf", "18", "-pix_fmt", "yuv420p", "-an", str(dst)])


def overlay_inset(base: Path, talker: Path, dst: Path, dur: float, mask: Path) -> None:
    """Small round inset of the talking presenter in the lower left (inside the 1:1 crop's safe area)."""
    d = 330
    if not mask.exists():
        m = Image.new("L", (d, d), 0)
        ImageDraw.Draw(m).ellipse([0, 0, d - 1, d - 1], fill=255)
        m.save(mask)
    run(["ffmpeg", "-v", "error", "-y", "-i", str(base), "-i", str(talker), "-loop", "1", "-i", str(mask), "-filter_complex",
         f"[1:v]crop=iw:iw:0:ih*0.08,scale={d}:{d}[t];[2:v]format=gray,scale={d}:{d}[m];[t][m]alphamerge[c];"
         f"[c]pad={d + 12}:{d + 12}:6:6:color=white@0[c2];"
         f"[0:v]drawbox=x=34:y={1460 - d - 52}:w={d + 12}:h={d + 12}:color=white@0:t=0[b];"
         f"[b][c2]overlay=34:{1460 - d - 52}:shortest=1[v]",
         "-map", "[v]", "-t", f"{dur:.3f}", "-c:v", "libx264", "-crf", "18", "-pix_fmt", "yuv420p", "-an", str(dst)])


def ass_time(t: float) -> str:
    t = max(t, 0)
    return f"{int(t // 3600)}:{int(t % 3600 // 60):02d}:{t % 60:05.2f}"


CAPTION_PRESETS = {
    # name: font, size factor, upper, outline, shadow, box, highlight colour (ASS BGR) or None, words per group, place
    "word-highlight":  ("Inter Display Black", 1.0, False, None, 2, False, "&H00E5FF&", 3, "low"),
    "subtitle":        ("Inter Display Bold", 0.78, False, 2.6, 2.5, False, None, 4, "middle"),
    "caps-outline":    ("Inter Display Black", 0.95, True, None, 2, False, None, 3, "low"),
    "caps-outline-mid": ("Inter Display Black", 0.95, True, None, 2, False, None, 3, "middle"),
    "caps-key-yellow": ("Inter Display Black", 1.0, True, None, 2, False, "&H00E5FF&", 3, "middle"),
    "caps-key-yellow-low": ("Inter Display Black", 1.0, True, None, 2, False, "&H00E5FF&", 3, "low"),
    "phrase-key-red":  ("Inter Display Black", 0.85, False, None, 2, False, "&H2E10C8&", 5, "low"),
    "one-word":        ("Inter Display Black", 1.25, False, None, 2, False, None, 1, "low"),
    "boxed":           ("Inter Display Bold", 0.7, False, 0, 0, True, None, 5, "low"),
    # plain white sentence-case phrases with a soft shadow (no highlight), low or centred
    "sentence-low":    ("Inter Display Black", 0.88, False, None, 2, False, None, 4, "low"),
    "sentence-mid":    ("Inter Display Black", 0.88, False, None, 2, False, None, 3, "middle"),
    # Arabic (Darija) ads: right-to-left, shaped by libass; frames keep the top 20% free for captions
    "ar-key-yellow":   ("Noto Kufi Arabic", 0.9, False, None, 2, False, "&H00E5FF&", 3, "top"),
    "ar-plain-top":    ("Noto Kufi Arabic", 0.9, False, None, 2, False, None, 4, "top"),
}
PUNCT_END = r"[,.!?،؟…:]"


def captions_ass(words: list[dict], path: Path, res: tuple[int, int], size: int, margin_v: int, max_words: int = 3,
                 high_spans: list[tuple[float, float]] = (), margin_high: int = 0, style: str = "word-highlight",
                 low_spans: list[tuple[float, float]] = ()) -> None:
    """Captions timed to the VO words in one of CAPTION_PRESETS (word groups; the spoken word highlighted when the
    preset has a highlight colour). Inside `high_spans` (product shots) captions move up to `margin_high`."""
    font, fac, upper, outline, shadow, box, hl, per, place = CAPTION_PRESETS.get(style, CAPTION_PRESETS["word-highlight"])
    per = max_words if style == "word-highlight" else per
    # Lines never wrap (WrapStyle 2): a group also ends before it gets wider than the frame (Inter Black measured on
    # 1080-wide renders: ~0.41 em per lower-case character, ~0.53 em per capital), so long words cannot run off the edge.
    max_chars = max(8, int((res[0] - 100) / (size * fac * (0.55 if upper else 0.42))))
    groups, cur = [], []
    for w in words:
        if cur and len(" ".join(x["text"] for x in cur + [w])) > max_chars:
            groups.append(cur)
            cur = []
        cur.append(w)
        if len(cur) >= per or re.search(PUNCT_END + "$", w["text"]):
            groups.append(cur)
            cur = []
    if cur:
        groups.append(cur)
    # quote marks go: a quoted line split over two caption groups would otherwise show a lone stray quote
    clean = lambda t: re.sub(PUNCT_END + "+$", "", re.sub(r'["\u201c\u201d]', "", t))
    up = (lambda t: t.upper()) if upper else (lambda t: t)
    fs = int(size * fac)
    ol = max(fs // 13, 4) if outline is None else outline
    align, mv0 = {"low": (2, margin_v), "top": (8, int(res[1] * (0.11 if res[1] > res[0] else 0.06)))}.get(place, (5, 0))
    rtl = "Arabic" in font                 # bold Arabic face + libass base direction auto-detected (Encoding -1)
    border, back = (3, "&H96000000") if box else (1, "&H64000000")
    lines = ["[Script Info]", "ScriptType: v4.00+", f"PlayResX: {res[0]}", f"PlayResY: {res[1]}", "WrapStyle: 2", "",
             "[V4+ Styles]",
             "Format: Name, Fontname, Fontsize, PrimaryColour, SecondaryColour, OutlineColour, BackColour, Bold, Italic, "
             "Underline, StrikeOut, ScaleX, ScaleY, Spacing, Angle, BorderStyle, Outline, Shadow, Alignment, MarginL, MarginR, MarginV, Encoding",
             f"Style: Cap,{font},{fs},&H00FFFFFF,&H00FFFFFF,{'&H28000000' if style == 'subtitle' else '&H00000000'},{back},"
             f"{-1 if rtl else 0},0,0,0,100,100,0,0,{border},{ol if not box else 12},{shadow},{align},80,80,{mv0},{-1 if rtl else 1}",
             "", "[Events]",
             "Format: Layer, Start, End, Style, Name, MarginL, MarginR, MarginV, Effect, Text"]
    for gi, g in enumerate(groups):
        g_end = groups[gi + 1][0]["start"] if gi + 1 < len(groups) else g[-1]["end"] + 0.6
        g_end = min(g_end, g[-1]["end"] + 0.6)
        mv = margin_high if place == "low" and any(a <= g[0]["start"] < b for a, b in high_spans) else 0
        pre = ""
        if place == "top" and any(a <= g[0]["start"] < b for a, b in low_spans):   # product at the top of the frame
            pre, mv = "{\\an2}", int(res[1] * 0.10)
        if not hl:            # one event per group, plain text
            text = up(" ".join(x["text"] for x in g).rstrip(",")) if style == "subtitle" else up(" ".join(clean(x["text"]) for x in g))
            lines.append(f"Dialogue: 0,{ass_time(g[0]['start'])},{ass_time(g_end)},Cap,,0,0,{mv},,{pre}{text}")
            continue
        for wi, w in enumerate(g):
            end = g[wi + 1]["start"] if wi + 1 < len(g) else g_end
            mv = margin_high if place == "low" and any(a <= w["start"] < b for a, b in high_spans) else mv
            text = pre + " ".join(("{\\c" + hl + "}" + up(clean(x["text"])) + "{\\c&HFFFFFF&}") if x is w else up(clean(x["text"])) for x in g)
            lines.append(f"Dialogue: 0,{ass_time(w['start'])},{ass_time(end)},Cap,,0,0,{mv},,{text}")
    path.write_text("\n".join(lines) + "\n")


EMOJI_WORDS = {"🌿": "الأكاسيا", "🦠": "بروبيوتيك", "🌵": "الصبار", "🌱": "النعناع"}   # ingredient icons -> names


def emoji_free(text: str) -> str:
    """Caption text without colour emoji; a caption made only of ingredient icons gets their names instead."""
    plain = re.sub(r"\s{2,}", " ", EMOJI_RE.sub("", text)).strip(" ·")
    if not re.search(r"\w", plain) and any(k in text for k in EMOJI_WORDS):
        named = "".join(f" {EMOJI_WORDS[ch]} " if ch in EMOJI_WORDS else ch for ch in text)
        plain = re.sub(r"\s{2,}", " ", EMOJI_RE.sub("", named)).strip(" ·")
        plain = re.sub(r"(?<=\w) (?=\w)", " · ", plain) if "+" not in plain else plain
    return plain


EMOJI_RE = re.compile("[\U0001F000-\U0001FAFF\u2600-\u27BF\uFE0F\u200D\u2B50\u2B55\u2705\u274C]")


def on_screen_ass(cards: list[tuple[float, float, str]], path: Path, res: tuple[int, int], size: int, box: str | None = None) -> None:
    """Each shot's on-screen text (parts split by ' / ' shown one after the other). Default: white sentence case with
    a soft shadow in the middle of the frame (music-only ads). box="RRGGBB": white bold text on an opaque box of that
    colour in the top fifth (Arabic gets Noto Kufi + RTL); colour emoji are dropped (libass can't draw them)."""
    style = f"Style: Txt,Inter Display Bold,{size},&H00FFFFFF,&H00FFFFFF,&H50000000,&H78000000,0,0,0,0,100,100,0,0,1,2.5,3,5,90,90,0,1"
    if box:   # "RRGGBB" or "RRGGBB:TEXTRRGGBB:OPACITY%" (e.g. "23022E:EFF8E2:85" = plum box at 85 %, mint text)
        bg, fg, op = (box.split(":") + ["FFFFFF", "100"])[:3]
        alpha = f"{round(255 * (1 - int(op) / 100)):02X}"
        bgr = f"&H{alpha}{bg[4:6]}{bg[2:4]}{bg[0:2]}&"
        fgr = f"&H00{fg[4:6]}{fg[2:4]}{fg[0:2]}&"
        ar = any(is_arabic(c[2]) for c in cards)
        top = int(res[1] * (0.10 if res[1] > res[0] else 0.06))
        style = (f"Style: Txt,{'Noto Kufi Arabic' if ar else 'Inter Display Bold'},{size},{fgr},{fgr},{bgr},{bgr},"
                 f"-1,0,0,0,100,100,0,0,4,{max(size // 4, 10)},0,8,90,90,{top},{-1 if ar else 1}")   # 4 = one box per line
    lines = ["[Script Info]", "ScriptType: v4.00+", f"PlayResX: {res[0]}", f"PlayResY: {res[1]}", "WrapStyle: 0", "",
             "[V4+ Styles]",
             "Format: Name, Fontname, Fontsize, PrimaryColour, SecondaryColour, OutlineColour, BackColour, Bold, Italic, "
             "Underline, StrikeOut, ScaleX, ScaleY, Spacing, Angle, BorderStyle, Outline, Shadow, Alignment, MarginL, MarginR, MarginV, Encoding",
             style, "", "[Events]", "Format: Layer, Start, End, Style, Name, MarginL, MarginR, MarginV, Effect, Text"]
    if box:
        cards = [(c[0], c[1], ("{\\an2}" if len(c) > 3 and c[3] == "bottom" else "") + emoji_free(c[2])) for c in cards]
    low_mv = int(res[1] * 0.10)
    for a, b, text, *_ in cards:
        pre = "{\\an2}" if text.startswith("{\\an2}") else ""
        text = text[len(pre):]
        text = re.sub(r"~~(.+?)~~", r"{\\s1}\1{\\s0}", text)          # ~~458~~ = old price, struck through
        parts = [x.strip() for x in text.split(" / ") if x.strip()]
        if not parts:
            continue
        step = (b - a) / len(parts)
        for i, part in enumerate(parts):
            lines.append(f"Dialogue: 0,{ass_time(a + i * step + 0.05)},{ass_time(a + (i + 1) * step - 0.05)},Txt,,0,0,{low_mv if pre else 0},,{pre}{part}")
    path.write_text("\n".join(lines) + "\n")


def banner_png(text: str, path: Path, width: int, bg=(255, 255, 255, 255), fg=(200, 16, 46, 255)) -> None:
    """Rounded white pill with bold dark text and a colour emoji (rendered separately, Noto is bitmap-only)."""
    emoji = "".join(ch for ch in text if ord(ch) > 0x2000)
    label = text.replace(emoji, "").strip()
    fsize = int(width * 0.06)
    font = ImageFont.truetype(BANNER_FONT_AR if is_arabic(label) else BANNER_FONT, fsize)
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
    d.rounded_rectangle([6, 6, bw + 6, bh + 6], radius=bh // 2, fill=bg)
    ty = 6 + (bh - fsize) // 2 - int(fsize * 0.12)
    d.text((6 + pad_x, ty), label, font=font, fill=fg)
    if em:
        img.alpha_composite(em, (6 + pad_x + tw + int(fsize * 0.3), 6 + (bh - em.height) // 2))
    img.save(path)


def end_overlay_png(lines: list[dict], path: Path, res: tuple[int, int]) -> None:
    """Full-frame transparent PNG with offer pills, e.g. [{"text": "...", "y_frac": 0.08, "scale": 1.0,
    "bg": "14532D", "fg": "FFFFFF"}]; each pill is centred at its y_frac of the frame height."""
    canvas = Image.new("RGBA", res, (0, 0, 0, 0))
    for ln in lines:
        p = path.with_name(path.stem + f"_{len(ln['text'])}_{int(ln['y_frac'] * 1000)}.png")
        hx = lambda c: tuple(int(c[i:i + 2], 16) for i in (0, 2, 4)) + (255,)
        banner_png(ln["text"], p, int(min(res) * ln.get("scale", 1.0)), bg=hx(ln.get("bg", "FFFFFF")), fg=hx(ln.get("fg", "C8102E")))
        pill = Image.open(p)
        if pill.width > res[0] - 40:
            pill = pill.resize((res[0] - 40, int(pill.height * (res[0] - 40) / pill.width)), Image.LANCZOS)
        canvas.alpha_composite(pill, ((res[0] - pill.width) // 2, int(res[1] * ln["y_frac"]) - pill.height // 2))
    canvas.save(path)


def emoji_img(chars: str, size: int) -> Image.Image:
    """Colour emoji (Noto Color Emoji is bitmap-only at 109 px) scaled to about `size` px high."""
    ef = ImageFont.truetype(EMOJI_FONT, 109)
    em = Image.new("RGBA", (136 * max(1, len(chars)), 128), (0, 0, 0, 0))
    ImageDraw.Draw(em).text((0, 0), chars, font=ef, embedded_color=True)
    em = em.crop(em.getbbox())
    return em.resize((max(1, int(em.width * size / em.height)), size), Image.LANCZOS)


def hook_png(text: str, red: str, path: Path, width: int, bg=(255, 255, 255), fg=(20, 20, 20)) -> None:
    """Hook title card: white rounded box with bold black text; the words of `red` are drawn in red. Emoji words
    are drawn in colour from the emoji font (the text font has no emoji glyphs and would show empty boxes)."""
    fsize = int(width * 0.068)
    font = ImageFont.truetype(BANNER_FONT, fsize)
    red_words = {norm(w) for w in red.split()}
    is_em = lambda w: bool(EMOJI_RE.search(w)) and not EMOJI_RE.sub("", w).strip()
    ems = {}
    def wid(w):
        if is_em(w):
            ems.setdefault(w, emoji_img(EMOJI_RE.sub(lambda m: m.group(0) if m.group(0) not in "\ufe0f\u200d" else "", w), int(fsize * 1.05)))
            return ems[w].width
        return font.getbbox(w)[2]
    words, maxw, space = text.split(), int(width * 0.80), font.getbbox(" ")[2] or fsize // 3
    lw_of = lambda l: sum(wid(w) for w in l) + space * (len(l) - 1)
    lines, cur = [], []
    for w in words:
        if cur and lw_of(cur + [w]) > maxw:
            lines.append(cur); cur = [w]
        else:
            cur.append(w)
    lines.append(cur)
    lh = int(fsize * 1.22)
    widths = [lw_of(l) for l in lines]
    pad_x, pad_y = int(fsize * 0.65), int(fsize * 0.5)
    bw, bh = max(widths) + 2 * pad_x, lh * len(lines) + 2 * pad_y
    img = Image.new("RGBA", (bw + 12, bh + 14), (0, 0, 0, 0))
    d = ImageDraw.Draw(img)
    d.rounded_rectangle([6, 11, bw + 6, bh + 11], radius=int(fsize * 0.6), fill=(0, 0, 0, 70))
    d.rounded_rectangle([6, 6, bw + 6, bh + 6], radius=int(fsize * 0.6), fill=tuple(bg) + (255,))
    for i, (l, lw) in enumerate(zip(lines, widths)):
        x, y = 6 + (bw - lw) // 2, 6 + pad_y + i * lh - int(fsize * 0.1)
        for w in l:
            if is_em(w):
                img.alpha_composite(ems[w], (x, y + int(fsize * 0.12)))
            else:
                d.text((x, y), w, font=font, fill=(200, 16, 46, 255) if norm(w) in red_words else tuple(fg) + (255,))
            x += wid(w) + space
    img.save(path)


def playable(p: Path) -> bool:
    """A clip that exists and decodes (interpolation on the box can leave empty or truncated files)."""
    if not p.exists() or p.stat().st_size < 1024:
        return False
    try:
        return probe_duration(p) > 0.1
    except (ValueError, subprocess.CalledProcessError):
        return False


def find_clip(clips: Path, sid: str) -> Path | None:
    return next((p for p in [clips / "clips_32fps" / f"{sid}.mp4", clips / "clips" / f"{sid}.mp4", clips / f"{sid}.mp4"]
                 if playable(p)), None)


def clip_source(shot: dict) -> str:
    """Which rendered clip a shot shows: its own, or a neighbour's when its frame was damaged or off-story."""
    return shot.get("fill_from") or shot.get("clip_from") or shot["id"]


def share_plan(shots: list[dict], timeline: list[dict], locate, length=None) -> dict:
    """{shot id: (source clip id, head, end)}. Shots that share one clip get consecutive pieces of it, in timeline
    order; when the clip is too short for all of them, each gets a piece proportional to its duration (the editor
    then slows it, up to MAX_SLOW, and holds the last frame)."""
    length = length or (lambda path: probe_duration(path))
    dur = {t["id"]: t["dur"] for t in timeline}
    groups: dict[str, list[dict]] = {}
    for sh in shots:
        groups.setdefault(clip_source(sh), []).append(sh)
    plan = {}
    for src_id, members in groups.items():
        owner = next((m for m in shots if m["id"] == src_id), {})
        start = owner.get("clip_start", HEAD_TRIM)
        path = locate(src_id)
        stop = min(length(path), owner.get("clip_end") or 1e9) if path else None
        if stop is None or len(members) == 1:
            plan[members[0]["id"]] = (src_id, start, owner.get("clip_end"))
            for m in members[1:]:
                plan[m["id"]] = (src_id, start, None)
            continue
        need = sum(dur[m["id"]] for m in members)
        usable = max(stop - start, 0.1)
        scale = min(1.0, usable / need)
        cur = start
        for m in members:
            piece = dur[m["id"]] * scale
            plan[m["id"]] = (src_id, round(cur, 3), round(cur + piece, 3))
            cur += piece
    return plan


def main() -> None:
    ap = argparse.ArgumentParser()
    ap.add_argument("job_dir", type=Path)
    ap.add_argument("--clips", type=Path)
    ap.add_argument("--stills", type=Path, help="folder of first frames: build an animatic instead")
    ap.add_argument("--vo", type=Path, help="voiceover (omit for a music-only on-screen-text ad)")
    ap.add_argument("--words", type=Path)
    ap.add_argument("--out", type=Path, required=True)
    ap.add_argument("--name", default="ad")
    ap.add_argument("--crf", type=int, default=19, help="final export quality (higher = smaller file)")
    a = ap.parse_args()

    job = json.loads((a.job_dir / "job.json").read_text())
    out, tmp = a.out, a.out / "work"
    tmp.mkdir(parents=True, exist_ok=True)
    if a.words:
        words = spoken_words(a.words)
        timeline = align_shots(job["shots"], words)
    else:                      # music-only ad: shot lengths come from job.json (vo_s), text from on_screen
        words, timeline, t0 = [], [], 0.0
        for sh in job["shots"]:
            timeline.append({"id": sh["id"], "start": t0, "end": t0 + sh["vo_s"], "dur": sh["vo_s"], "words": []})
            t0 += sh["vo_s"]

    talk = {g["id"]: g for g in job.get("talk", {}).get("segments", [])}
    full = talk.get("full")

    def talk_slice(shot: dict, t: dict) -> tuple[Path, float] | None:
        """The lip-synced clip covering this shot's time, and where in it the shot starts."""
        cands = ([full] if full and shot["frame"] == full["frame"] else []) + \
                [g for g in talk.values() if g["id"] != "full" and shot["id"] in g["shots"]]
        for g in cands:
            src = a.clips / "talk" / f"{g['id']}.mp4"
            if src.exists() and src.stat().st_size > 0:
                return src, max(0.0, t["start"] - g["start"])
        return None

    def full_slice(t: dict) -> tuple[Path, float, bool] | None:
        """Talker footage under a PiP box / inset: the full-length lip-sync clip, else (no lip-sync) the plain
        Wan talking clip of the same presenter nearest in time, looped (third value = exact/in sync)."""
        if not full:
            return None
        src = a.clips / "talk" / "full.mp4"
        if src.exists() and src.stat().st_size > 0:
            return src, max(0.0, t["start"] - full["start"]), True
        near = sorted((abs(tt["start"] - t["start"]), sh["id"]) for tt, sh in zip(timeline, job["shots"])
                      if sh.get("talking") and sh["frame"] == full["frame"])
        for _, sid in near:
            if (c := find_clip(a.clips, sid)):
                return c, HEAD_TRIM, False
        return None

    plan = {} if a.stills else share_plan([s_ for s_ in job["shots"] if not s_.get("skip_render")], timeline, lambda sid: find_clip(a.clips, sid))
    seg_files, report = [], []
    for t, shot in zip(timeline, job["shots"]):
        dst = tmp / f"{shot['id']}.mp4"
        if a.stills:
            info = render_segment(a.stills / shot["frame"], t["dur"], dst, still=True)
        elif shot.get("talking") and (ts := talk_slice(shot, t)):
            info = render_segment(ts[0], t["dur"], dst, still=False, head=ts[1], boomerang=False) | {"talk": ts[0].stem}
        else:
            src_id, head, end = plan.get(shot["id"], (shot["id"], HEAD_TRIM, None))
            src = find_clip(a.clips, src_id)
            if shot.get("pip") == "box" and (fs := full_slice(t)) and src:
                inner, base = tmp / f"{shot['id']}_in.mp4", tmp / f"{shot['id']}_base.mp4"
                info = render_segment(src, t["dur"], inner, still=False, head=head, end=end)
                render_segment(fs[0], t["dur"], base, still=False, head=fs[1], boomerang=not fs[2])
                overlay_box(base, inner, dst, t["dur"])
                info |= {"pip": "box"}
            else:
                info = render_segment(src, t["dur"], dst, still=False, head=head, end=end) if src else \
                    render_segment(a.job_dir / "frames" / shot["frame"], t["dur"], dst, still=True) | {"missing_clip": True}
                if job.get("talk_inset") and not shot.get("talking") and (fs := full_slice(t)):
                    base, talker = dst.with_name(dst.stem + "_b.mp4"), tmp / f"{shot['id']}_talker.mp4"
                    dst.rename(base)
                    render_segment(fs[0], t["dur"], talker, still=False, head=fs[1], boomerang=not fs[2])
                    overlay_inset(base, talker, dst, t["dur"], tmp / "inset_mask.png")
                    info |= {"inset": True}
            if src_id != shot["id"]:
                info |= {"clip_of": src_id}
        seg_files.append(dst)
        report.append({"id": shot["id"], "start": round(t["start"], 2), "dur": t["dur"], **info})
    (tmp / "concat.txt").write_text("".join(f"file '{p.resolve()}'\n" for p in seg_files))
    clean = tmp / "clean.mp4"
    run(["ffmpeg", "-v", "error", "-y", "-f", "concat", "-safe", "0", "-i", str(tmp / "concat.txt"), "-c", "copy", str(clean)])

    vo = tmp / "vo.m4a"
    if a.vo:
        run(["ffmpeg", "-v", "error", "-y", "-i", str(a.vo), "-af", "loudnorm=I=-16:TP=-1.5:LRA=11", "-c:a", "aac", "-b:a", "192k", str(vo)])
    else:                      # silent track so the music bed can be mixed in afterwards
        run(["ffmpeg", "-v", "error", "-y", "-f", "lavfi", "-i", "anullsrc=r=48000:cl=stereo", "-t", f"{timeline[-1]['end']:.2f}",
             "-c:a", "aac", "-b:a", "128k", str(vo)])

    banner_t = next((t["start"] for t in timeline if job.get("banner") and t["id"] == job["banner"]["from_shot"]), 0.0)
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
    low_ids = set(job.get("captions", {}).get("low_on_shots", []))
    low_spans = [(t["start"], t["end"]) for t in timeline if t["id"] in low_ids]
    outputs = []
    for name, f in formats.items():
        ass = tmp / f"captions_{name}.ass"
        cstyle = job.get("captions", {}).get("style", "word-highlight")
        if cstyle.startswith("on-screen-box"):        # e.g. "on-screen-box:14532D" = the shot's on_screen text on a box
            on_screen_ass([(t["start"], t["end"], sh.get("on_screen", ""), sh.get("caption_pos", "top")) for t, sh in zip(timeline, job["shots"])],
                          ass, f["res"], int(f["cap"][0] * 0.8), box=cstyle.split(":", 1)[1] if ":" in cstyle else "14532D")
        elif words:
            captions_ass(words, ass, f["res"], f["cap"][0], f["cap"][1], max_words=4 if cstyle == "subtitle" else 3,
                         high_spans=high_spans, margin_high=f["cap_high"], style=cstyle, low_spans=low_spans)
        else:
            on_screen_ass([(t["start"], t["end"], sh.get("on_screen", "")) for t, sh in zip(timeline, job["shots"])],
                          ass, f["res"], int(f["cap"][0] * 0.8))
        png = tmp / f"banner_{name}.png"
        if job.get("banner"):
            banner_png(job["banner"]["text"], png, f["banner_w"])
            by = int(f["res"][1] * job["banner"]["y_frac"]) if job["banner"].get("y_frac") else f["banner_y"]
            overlay = f"[base][1:v]overlay=(W-w)/2:{by}:enable='gte(t,{banner_t:.2f})'[b]"
        else:
            Image.new("RGBA", (2, 2), (0, 0, 0, 0)).save(png)
            overlay = "[base]null[b]"
        if job.get("end_lines"):
            epng = tmp / f"end_{name}.png"
            end_overlay_png(job["end_lines"], epng, f["res"])
            e0 = next(t["start"] for t in timeline if t["id"] == job["end_card_shot"])
            overlay = overlay.replace("[b]", "[b0]") + f";[4:v]format=rgba[eo];[b0][eo]overlay=0:0:enable='gte(t,{e0:.2f})'[b]"
        hook = job.get("hook_card")
        hpng = tmp / f"hook_{name}.png"
        if hook:
            hook_png(hook["text"], hook.get("red", ""), hpng, f["banner_w"], hook.get("bg", (255, 255, 255)), hook.get("fg", (20, 20, 20)))
            spans = [(t["start"], t["end"]) for t in timeline if t["id"] in set(hook["shots"])]
            h0, h1 = min(a for a, _ in spans), max(b for _, b in spans)
            overlay += f";[b][3:v]overlay=(W-w)/2:{f['banner_y']}:enable='between(t,{h0:.2f},{h1:.2f})'[b2]"
        else:
            Image.new("RGBA", (2, 2), (0, 0, 0, 0)).save(hpng)
            overlay += ";[b]null[b2]"
        dst = out / f"{a.name}_{name}.mp4"
        fc = f"{f['vf']};{overlay};[b2]ass={ass}[v]"
        extra = ["-loop", "1", "-i", str(tmp / f"end_{name}.png")] if job.get("end_lines") else []
        run(["ffmpeg", "-v", "error", "-y", "-i", str(clean), "-i", str(png), "-i", str(vo), "-i", str(hpng), *extra, "-filter_complex", fc,
             "-map", "[v]", "-map", "2:a", "-c:v", "libx264", "-crf", str(a.crf), "-preset", "medium", "-pix_fmt", "yuv420p",
             "-c:a", "copy", "-t", f"{total:.2f}", "-movflags", "+faststart", str(dst)])
        outputs.append(str(dst))
    (out / f"{a.name}_edit_report.json").write_text(json.dumps({"total_s": round(total, 2), "banner_from_s": round(banner_t, 2),
                                                                "shots": report, "outputs": outputs}, indent=2))
    print(json.dumps({"total_s": round(total, 2), "outputs": outputs}, indent=2))
    if not a.stills:
        shutil.rmtree(tmp, ignore_errors=True)


if __name__ == "__main__":
    main()
