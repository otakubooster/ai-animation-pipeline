"""Editor v2: performance editing for finished ads (paid social).

What it adds over edit_ad.py (same inputs: job.json, clips, VO, word timings):
  * Text never covers faces: faces are detected per shot (assembly/layout.py) and captions / on-screen texts take
    the free band nearest their home position, inside each platform's safe zone, separately for 9:16, 1:1, 16:9
    (16:9 puts on-screen texts on the blurred side panels).
  * On-screen texts ("overlay") are typed and designed: day badges (TAG 3 / WOCHE 2 ...), offer stickers, CTA pill,
    testimonial name tags, study citations, versus labels, headlines, and lists revealed item by item when spoken.
  * Captions: 1-3 words, pop-in, spoken word highlighted.
  * Pacing: shots longer than ~3.4 s are cut at word boundaries into punch-ins (alternating framing, centred on
    the face), the standard jump-cut look; no shot holds one framing for more than ~3.3 s.
  * Transitions where the story needs them (scene change: slide/whip, time jump: white flash, packshot: zoom), hard
    cuts everywhere else; every transition keeps the VO in sync (segments carry handles).
  * Sound design: whoosh / shutter / pop / ching / swipe effects (assembly/sfx, ElevenLabs SFX) under the VO.
  * No reverse (boomerang) or heavy slow-motion: a short clip is slowed at most x1.15, then held (flagged in the
    report as needing a re-render).

  python assembly/edit_v2.py JOB_DIR --clips DIR --vo VO --words WORDS --out OUT --name NAME
"""

from __future__ import annotations

import argparse
import json
import re
import shutil
import subprocess
import sys
from pathlib import Path

from PIL import Image, ImageFont

sys.path.insert(0, str(Path(__file__).resolve().parent))
import layout  # noqa: E402
from edit_ad import (HEAD_TRIM, EMOJI_RE, align_shots, ass_time, find_clip, freeze_region, hook_png, norm,  # noqa: E402
                     probe_duration, run, share_plan, spoken_words)

FPS = 30
W, H = 1080, 1920
HERE = Path(__file__).resolve().parent
SFX_DIR = HERE / "sfx"
FONT_BLACK = "/usr/share/fonts/opentype/inter/InterDisplay-Black.otf"
FONT_BOLD = "/usr/share/fonts/opentype/inter/InterDisplay-ExtraBold.otf"   # fontconfig family "Inter Display ExtraBold" ("Inter Display Bold" is not a family: libass fell back to a wide font)
FAM_BLACK, FAM_BOLD, FONT_BOLD_FLAG, ENCODING = "Inter Display Black", "Inter Display ExtraBold", 0, 1
ASS_EM = 1.21            # ASS font size = ascent + descent; Inter's is 1.21 em
PUNCT = "[,.!?:;…،؟]"    # sentence/comma marks (Latin + Arabic)


def use_arabic() -> None:
    """Darija/Arabic jobs: Noto Kufi Arabic Bold everywhere (libass shapes it; Encoding -1 = base direction from the
    text, so lines run right to left)."""
    global FONT_BLACK, FONT_BOLD, FAM_BLACK, FAM_BOLD, FONT_BOLD_FLAG, ENCODING, ASS_EM
    FONT_BLACK = FONT_BOLD = "/usr/share/fonts/truetype/noto/NotoKufiArabic-Bold.ttf"
    FAM_BLACK = FAM_BOLD = "Noto Kufi Arabic"
    FONT_BOLD_FLAG, ENCODING = -1, -1
    ASS_EM = 1.6         # Kufi's ascent + descent is ~1.6 em
MAX_SLOW = 1.15
PIECE_MAX = 3.4          # longer shots get punch-in cuts
TRANS_GAP = 7.0          # seconds between two transitions at least
SEG_VERSION = 2          # bump when segment rendering changes (invalidates the segment cache)
BLUE, YELLOW, RED, GREEN, INK = "0B5ED7", "FFD60A", "D7263D", "1A9E4B", "111111"
SFX_PEAK = {"whoosh": 0.33, "whoosh_fast": 0.10, "shutter": 0.05, "pop": 0.0, "ching": 0.02, "swipe": 0.08}


def F(t: float) -> int:
    return int(round(t * FPS))


def bgr(hexrgb: str, alpha: int = 0) -> str:
    return f"&H{alpha:02X}{hexrgb[4:6]}{hexrgb[2:4]}{hexrgb[0:2]}&"


# ----------------------------------------------------------------------------------------------- on-screen text types

BADGE = re.compile(r"^(TAG|WOCHE|MONAT)\s*\d|^\d+\s*(MINUTE|MINUTEN|STUNDE|STUNDEN|TAGE|MONATE?)\b", re.I)
OFFER = re.compile(r"GRATIS|RABATT|%|AKTION|ZAHLEN SIE NICHTS|SIE ZAHLEN NICHTS|Pakete|→ ?40", re.I)
CTA = re.compile(r"festbiss\.de|Link unten|JETZT KAUFEN", re.I)
CITE = re.compile(r"Studie|Neurology|Prosthet|Etikett|Tallgren|et al\.", re.I)
VERSUS = re.compile(r"\bvs\.?\b|^links\b.*\brechts\b", re.I)
TRUST = re.compile(r"Versand|Geld-zur|Garantie|risikofrei|testen", re.I)


AR_OFFER = re.compile(r"درهم|التوصيل|الدفع|فابور|تخفيض|عرض")
AR_CTA = re.compile(r"اطلب|اطلبي|كليكي|الرابط")


def kind_of(text: str) -> str:
    if AR_CTA.search(text):
        return "cta"
    if AR_OFFER.search(text):
        return "offer"
    if "\\N" in text:
        return "list"
    if "★" in text and (re.search(r"[A-Z][a-z]+ [A-Z]\.", text) or "bewertung" in text.lower()):
        return "nametag"
    if CITE.search(text):
        return "cite"
    if VERSUS.search(text):
        return "versus"
    if text.strip().upper() == "WARNUNG":
        return "alert"
    if CTA.search(text):
        return "cta"
    if OFFER.search(text) or TRUST.search(text):
        return "offer"
    if BADGE.search(text) and len(text) <= 22:
        return "badge"
    return "headline"


# ------------------------------------------------------------------------------------------------------- ASS writer

class Ass:
    """One subtitle document per format; everything positioned with \\pos, shapes drawn as vector rounded boxes."""

    def __init__(self, res: tuple[int, int]):
        self.res = res
        self.ev: list[str] = []
        self.fonts = {}

    def font(self, path: str, size: float) -> ImageFont.FreeTypeFont:
        key = (path, int(size))
        if key not in self.fonts:
            self.fonts[key] = ImageFont.truetype(path, max(int(size / ASS_EM), 8))
        return self.fonts[key]

    def width(self, text: str, size: float, bold: bool = False) -> float:
        f = self.font(FONT_BOLD if bold else FONT_BLACK, size)
        return f.getlength(re.sub(r"\{[^}]*\}", "", text))

    def wrap(self, text: str, size: float, max_w: float, bold: bool = False, max_lines: int = 3) -> list[str]:
        if " · " in text and (ENCODING == -1 or self.width(text, size, bold) > max_w):    # break at the separators first (Arabic: always, Kufi has no "·")
            out = []
            for part in text.split(" · "):
                out += self.wrap(part, size, max_w, bold, max_lines)
            if len(out) <= max_lines:
                return out
        lines, cur = [], ""
        for w in text.split():
            nxt = (cur + " " + w).strip()
            if cur and self.width(nxt, size, bold) > max_w:
                lines.append(cur)
                cur = w
            else:
                cur = nxt
        lines.append(cur)
        if len(lines) > max_lines:
            lines = lines[:max_lines - 1] + [" ".join(lines[max_lines - 1:])]
        return lines

    def add(self, t0: float, t1: float, style: str, text: str, layer: int = 1) -> None:
        self.ev.append(f"Dialogue: {layer},{ass_time(t0)},{ass_time(t1)},{style},,0,0,0,,{text}")

    @staticmethod
    def rrect(w: float, h: float, r: float) -> str:
        w, h, r = round(w), round(h), round(min(r, h / 2, w / 2))
        return (f"m {r} 0 l {w - r} 0 b {w} 0 {w} 0 {w} {r} l {w} {h - r} b {w} {h} {w} {h} {w - r} {h} "
                f"l {r} {h} b 0 {h} 0 {h} 0 {h - r} l 0 {r} b 0 0 0 0 {r} 0")

    def pill(self, t0, t1, cx, cy, lines, size, fg, bg, bg_alpha=0, bold=False, pad=(0.55, 0.30), radius=None,
             rot=0.0, anim="pop", layer=5, outline=False, style=None, font=None):
        """A rounded box with centred text lines; returns its (x0, y0, x1, y1) in pixels."""
        em = size / ASS_EM
        if font:
            fnt = ImageFont.truetype(font, max(int(size / ASS_EM), 8))
            tw = max(fnt.getlength(re.sub(r"\{[^}]*\}", "", l)) for l in lines)
        else:
            tw = max(self.width(l, size, bold) for l in lines)
        bw, bh = tw + 2 * pad[0] * em, len(lines) * size + 2 * pad[1] * em
        rad = radius if radius is not None else min(bh / 2, em * 0.55)
        a = self.anim(anim, cx, cy)
        r = f"\\frz{rot:.1f}" if rot else ""
        if bg:
            self.add(t0, t1, "Shp", f"{{\\an5{a}{r}\\c{bgr(bg)}\\1a&H{bg_alpha:02X}&\\p1}}{self.rrect(bw, bh, rad)}", layer)
        style = style or ("TxtB" if bold else "Txt")
        ol = "\\bord5\\3c&H000000&" if outline else ""
        self.add(t0, t1, style, f"{{\\an5{a}{r}\\fs{size:.0f}\\c{bgr(fg)}{ol}}}" + "\\N".join(lines), layer + 1)
        return cx - bw / 2, cy - bh / 2, cx + bw / 2, cy + bh / 2

    @staticmethod
    def anim(kind: str, cx: float, cy: float) -> str:
        if kind == "pop":
            return f"\\pos({cx:.0f},{cy:.0f})\\fscx55\\fscy55\\t(0,110,\\fscx108\\fscy108)\\t(110,190,\\fscx100\\fscy100)\\fad(40,120)"
        if kind == "slide":
            return f"\\move({cx - 70:.0f},{cy:.0f},{cx:.0f},{cy:.0f},0,170)\\fad(90,120)"
        if kind == "soft":
            return f"\\pos({cx:.0f},{cy:.0f})\\fscx92\\fscy92\\t(0,80,\\fscx100\\fscy100)\\fad(40,60)"
        if kind == "none":
            return f"\\pos({cx:.0f},{cy:.0f})"
        return f"\\pos({cx:.0f},{cy:.0f})\\fad(160,160)"

    def write(self, path: Path, cap_size: int, txt_size: int) -> None:
        head = ["[Script Info]", "ScriptType: v4.00+", f"PlayResX: {self.res[0]}", f"PlayResY: {self.res[1]}",
                "WrapStyle: 2", "ScaledBorderAndShadow: yes", "", "[V4+ Styles]",
                "Format: Name, Fontname, Fontsize, PrimaryColour, SecondaryColour, OutlineColour, BackColour, Bold, Italic, "
                "Underline, StrikeOut, ScaleX, ScaleY, Spacing, Angle, BorderStyle, Outline, Shadow, Alignment, MarginL, "
                "MarginR, MarginV, Encoding",
                f"Style: Cap,{FAM_BLACK},{cap_size},&H00FFFFFF,&H00FFFFFF,&H00000000,&H78000000,{FONT_BOLD_FLAG},0,0,0,100,100,0,0,1,"
                f"{max(cap_size // 13, 4)},2,5,0,0,0,{ENCODING}",
                f"Style: Txt,{FAM_BLACK},{txt_size},&H00FFFFFF,&H00FFFFFF,&H00000000,&H00000000,{FONT_BOLD_FLAG},0,0,0,100,100,0,0,1,0,0,5,0,0,0,{ENCODING}",
                f"Style: TxtB,{FAM_BOLD},{txt_size},&H00FFFFFF,&H00FFFFFF,&H00000000,&H00000000,{FONT_BOLD_FLAG},0,0,0,100,100,0,0,1,0,0,5,0,0,0,{ENCODING}",
                "Style: Shp,Inter Display Black,20,&H00FFFFFF,&H00FFFFFF,&H00000000,&HA0000000,0,0,0,0,100,100,0,0,1,0,4,5,0,0,0,1",
                f"Style: CapBox,Inter Display SemiBold,{cap_size},&H00111111,&H00111111,&H00000000,&H00000000,0,0,0,0,100,100,0,0,1,0,0,5,0,0,0,{ENCODING}",
                f"Style: Hdr,Inter Display SemiBold,{txt_size},&H00FFFFFF,&H00FFFFFF,&H00000000,&H64000000,0,0,0,0,100,100,0,0,1,0,3,5,0,0,0,{ENCODING}",
                f"Style: Cmt,Inter Display SemiBold,{txt_size},&H00111111,&H00111111,&H00000000,&H00000000,0,0,0,0,100,100,0,0,1,0,0,7,0,0,0,{ENCODING}",
                "", "[Events]", "Format: Layer, Start, End, Style, Name, MarginL, MarginR, MarginV, Effect, Text"]
        path.write_text("\n".join(head + self.ev) + "\n")


# ------------------------------------------------------------------------------------------------------- formats

class Fmt:
    """An export format: resolution, how 9:16-normalised points map into it, where text may go, text sizes."""

    def __init__(self, name):
        self.name = name
        if name == "9x16":
            self.res, self.cap, self.txt = (1080, 1920), 74, 54
            self.safe = (0.10, 0.79)
            self.cap_c, self.ov_c = [0.70, 0.66, 0.74, 0.62, 0.58, 0.36, 0.30], [0.19, 0.25, 0.31, 0.15, 0.45, 0.52]
            self.vf = "[0:v]null[base]"
            self.panel = None
        elif name == "1x1":
            self.res, self.cap, self.txt = (1080, 1080), 60, 44
            self.safe = (0.06, 0.94)
            self.cap_c, self.ov_c = [0.84, 0.78, 0.88, 0.72, 0.30, 0.22], [0.13, 0.20, 0.27, 0.60, 0.66]
            self.vf = "[0:v]crop=1080:1080:0:380[base]"
            self.panel = None
        else:
            self.res, self.cap, self.txt = (1920, 1080), 58, 44
            self.safe = (0.06, 0.94)
            self.cap_c, self.ov_c = [0.86, 0.80, 0.90, 0.74, 0.16], [0.5, 0.32, 0.68, 0.2, 0.8]
            self.vf = ("[0:v]split[a][b];[a]scale=1920:-2,crop=1920:1080,boxblur=30:3,eq=brightness=-0.08[bg];"
                       "[b]scale=-2:1080[fg];[bg][fg]overlay=(W-w)/2:0[base]")
            self.panel = (0.03, 0.315)       # left panel x range (normalised) for on-screen texts

        if ENCODING == -1:
            self.cap, self.txt = int(self.cap * 1.35), int(self.txt * 1.45)

    def boxes(self, faces916: list[list[float]]) -> list[list[float]]:
        """Face boxes in this format's normalised coords."""
        out = []
        for b in faces916:
            if self.name == "1x1":
                y0, y1 = (b[1] * 1920 - 380) / 1080, (b[3] * 1920 - 380) / 1080
                if y1 > 0 and y0 < 1:
                    out.append([b[0], max(0, y0), b[2], min(1, y1)])
            elif self.name == "16x9":
                out.append([0.342 + 0.316 * b[0], b[1], 0.342 + 0.316 * b[2], b[3]])
            else:
                out.append(list(b))
        return out


# --------------------------------------------------------------------------------------------- shots, pieces, render

def plan_pieces(t: dict, no_split: bool, forced: list[float] = ()) -> list[tuple[float, float]]:
    """Cut points inside a shot (absolute timeline seconds) at word starts, preferring sentence/comma breaks.
    `forced` cuts (e.g. the join between two renders inside a lip-synced clip) are always cut points."""
    forced = sorted(c for c in forced if t["start"] + 0.3 < c < t["end"] - 0.3)
    if forced:
        out, a = [], t["start"]
        for c in forced + [t["end"]]:
            out += plan_pieces({**t, "start": a, "end": c, "words": [w for w in t.get("words") or [] if a <= w["start"] < c]}, no_split)
            a = c
        return out
    a0, b0 = t["start"], t["end"]
    if no_split or b0 - a0 <= PIECE_MAX:
        return [(a0, b0)]
    ws = t.get("words") or []
    starts = [(w["start"] - 0.04, bool(re.search(PUNCT + "$", p["text"]))) for p, w in zip(ws, ws[1:])]
    cuts, cur = [], a0
    while b0 - cur > PIECE_MAX:
        target = cur + 2.5
        cands = [(s, p) for s, p in starts if cur + 1.5 <= s <= min(cur + 3.3, b0 - 1.2)]
        if cands:
            c = min(cands, key=lambda sp: abs(sp[0] - target) - (0.6 if sp[1] else 0))[0]
        elif b0 - (cur + 2.5) >= 1.2:
            c = cur + 2.5
        else:
            break
        cuts.append(c)
        cur = c
    return list(zip([a0] + cuts, cuts + [b0]))


ZOOMS_FACE = [1.0, 1.18, 1.06, 1.22]
ZOOMS_BROLL = [1.0, 1.12]


def piece_vf(zoom: float, rect: list[float]) -> str:
    wz, hz = int(round(W * zoom / 2)) * 2, int(round(H * zoom / 2)) * 2
    x, y = int(rect[0] * wz), int(rect[1] * hz)
    x, y = min(max(x, 0), wz - W), min(max(y, 0), hz - H)
    return (f"scale={wz}:{hz}:force_original_aspect_ratio=increase:flags=lanczos,crop={wz}:{hz},crop={W}:{H}:{x}:{y},"
            f"setsar=1,unsharp=5:5:0.5:5:5:0.0")


def enc(dst: Path) -> list[str]:
    return ["-c:v", "libx264", "-preset", "fast", "-crf", "16", "-pix_fmt", "yuv420p", "-r", str(FPS), "-an", str(dst)]


def render_piece(src: Path, src_t: float, slow: float, frames: int, dst: Path, zoom: float, rect: list[float],
                 src_end: float, reverse: bool = False) -> float:
    """`frames` output frames of `src` from source time src_t, slowed by `slow`, punched in to `rect`; frames past
    src_end freeze. Returns the held seconds."""
    pre = max(0.0, -src_t)
    a = max(src_t, 0.0)
    need = frames / FPS / slow
    held = min(max(0.0, (a + need) - src_end) * slow, frames / FPS)
    a = min(a, max(0.0, src_end - 0.1))         # a piece starting past the usable end freezes its last frame
    rev = ",reverse" if reverse else ""         # a short object motion that runs the wrong way for the line
    vf = (f"trim=start={a:.3f}:end={src_end:.3f},setpts=PTS-STARTPTS{rev},setpts=(PTS-STARTPTS)*{slow:.4f},fps={FPS},{piece_vf(zoom, rect)},"
          f"tpad=start_mode=clone:start_duration={pre * slow:.3f}:stop_mode=clone:stop_duration={frames / FPS + 1:.3f}")
    run(["ffmpeg", "-v", "error", "-y", "-i", str(src), "-vf", vf, "-frames:v", str(frames), *enc(dst)])
    got = subprocess.run(["ffprobe", "-v", "error", "-select_streams", "v:0", "-show_entries", "stream=nb_frames",
                          "-of", "csv=p=0", str(dst)], capture_output=True, text=True).stdout.strip()
    if got.isdigit() and int(got) < frames - 1:  # a short piece would shift every later shot off the voice
        raise RuntimeError(f"{dst.name}: {got} of {frames} frames (src {src.name} t={src_t:.2f} end={src_end:.2f})")
    return held


def zoom_seq(mode: str, n: int, faces: list, rot: int = 0) -> list[float]:
    """Nominal punch-in zoom of each of a shot's n pieces; rot shifts the pattern by one step (used so a shot that
    continues the previous shot's scene opens on another framing)."""
    if mode == "still":
        if n == 1:
            return [1.0] if not rot else [1.14]
        base = (1.0, 1.16, 1.06, 1.2) if faces else (1.0, 1.12, 1.06)
    elif mode == "parts":
        base = (1.0, 1.15) if faces else (1.0, 1.1)
    else:
        if n == 1 and not rot:
            return [1.0]
        base = ZOOMS_FACE if faces else ZOOMS_BROLL
    return [base[(j + rot) % len(base)] for j in range(n)]


def render_still(img: Path, frames: int, dst: Path, z0: float = 1.0, z1: float = 1.07, cx: float = 0.5, cy: float = 0.5) -> None:
    """Slow push from zoom z0 to z1 on the point (cx, cy) of the image (normalised), clamped to the frame."""
    z = f"({z0}+{z1 - z0:.4f}*on/{max(frames - 1, 1)})"
    zoom = (f"zoompan=z='{z}':x='max(0,min(iw-iw/zoom,iw*{cx:.3f}-iw/zoom/2))':y='max(0,min(ih-ih/zoom,ih*{cy:.3f}-ih/zoom/2))'"
            f":d={frames}:s={W}x{H}:fps={FPS}")
    run(["ffmpeg", "-v", "error", "-y", "-loop", "1", "-i", str(img), "-vf",
         f"scale={W * 2}:{H * 2}:force_original_aspect_ratio=increase:flags=lanczos,crop={W * 2}:{H * 2},{zoom},setsar=1",
         "-frames:v", str(frames), *enc(dst)])


def concat(files: list[Path], dst: Path) -> None:
    lst = dst.with_suffix(".txt")
    lst.write_text("".join(f"file '{p.resolve()}'\n" for p in files))
    run(["ffmpeg", "-v", "error", "-y", "-f", "concat", "-safe", "0", "-i", str(lst), "-c", "copy", str(dst)])


def similarity(a: Path, b: Path) -> float:
    """Colour-histogram correlation of two images (1 = same look, < ~0.5 = different scene)."""
    if a == b:
        return 1.0
    import cv2
    hs = []
    for p in (a, b):
        im = Image.open(p).convert("RGB").resize((128, 228))
        hsv = cv2.cvtColor(__import__("numpy").asarray(im), cv2.COLOR_RGB2HSV)
        h = cv2.calcHist([hsv], [0, 1], None, [24, 16], [0, 180, 0, 256])
        hs.append(cv2.normalize(h, h).flatten())
    return float(cv2.compareHist(hs[0], hs[1], cv2.HISTCMP_CORREL))


# --------------------------------------------------------------------------------------------- text timing helpers

def match_time(part: str, words: list[dict], after: float) -> float | None:
    """Start time of the first spoken word (after `after`) that carries the part's key word."""
    keys = [k for k in re.findall(r"[a-zäöüß]{4,}|\d+", part.lower()) if k not in {"kein", "mehr", "sind", "nicht"}]
    for w in words:
        if w["start"] < after - 0.01:
            continue
        nw = norm(w["text"])
        if any(k in nw for k in keys[:2]):
            return w["start"]
    return None


def part_spans(text: str, t: dict, seg_end: float) -> list[tuple[float, float, str]]:
    """' / ' separated parts of an overlay, each timed to its spoken words where possible, else spread evenly."""
    parts = [p.strip() for p in text.split(" / ") if p.strip()]
    if len(parts) == 1:
        return [(t["start"], seg_end, parts[0])]
    starts, after = [], t["start"]
    for i, p in enumerate(parts):
        m = match_time(p, t.get("words") or [], after) if i else t["start"]
        starts.append(m)
        if m is not None:
            after = m + 0.3
    n, i = len(parts), 0
    while i < n:                                # unmatched parts: spread evenly between their matched neighbours
        if starts[i] is None:
            j = i
            while j < n and starts[j] is None:
                j += 1
            prev = starts[i - 1] if i else t["start"]
            nxt = starts[j] if j < n else seg_end
            step = (nxt - prev) / (j - i + 1)
            for m in range(i, j):
                starts[m] = prev + step * (m - i + 1)
            i = j
        else:
            i += 1
    starts = [max(s, t["start"]) for s in starts]
    for i in range(1, n):                       # every part stays on screen at least 0.9 s
        starts[i] = max(starts[i], starts[i - 1] + 0.9)
    ends = starts[1:] + [seg_end]
    return [(a, max(b, a + 0.5), p) for a, b, p in zip(starts, ends, parts)]


# ------------------------------------------------------------------------------------------------------------ main

def main() -> None:
    ap = argparse.ArgumentParser()
    ap.add_argument("job_dir", type=Path)
    ap.add_argument("--clips", type=Path, required=True)
    ap.add_argument("--vo", type=Path, required=True)
    ap.add_argument("--words", type=Path, required=True)
    ap.add_argument("--out", type=Path, required=True)
    ap.add_argument("--name", default="ad")
    ap.add_argument("--crf", type=int, default=20)
    ap.add_argument("--keep", action="store_true", help="keep the work folder")
    ap.add_argument("--formats", default="9x16,1x1,16x9")
    a = ap.parse_args()

    job = json.loads((a.job_dir / "job.json").read_text())
    shots = job["shots"]
    if str(job.get("language", "")).startswith("ar"):
        use_arabic()
    tmp = a.out / "work"
    tmp.mkdir(parents=True, exist_ok=True)
    words = spoken_words(a.words)
    timeline = align_shots(shots, words)
    total = timeline[-1]["end"]
    frames_dir = a.job_dir / "frames"

    # shots rendered elsewhere (skip_render + a copied clip, e.g. a hook variant reusing another ad's clips) edit like
    # their own clips, so their QA windows apply
    plan = share_plan([s for s in shots if not s.get("skip_render") or find_clip(a.clips, s["id"])], timeline,
                      lambda sid: find_clip(a.clips, sid), cycle=bool(job.get("cycle_long")))
    lips = a.clips / "lipsync"
    ls_handles, ls_cuts = {}, {}
    if (lips / "manifest.json").exists():
        items = [i for i in json.loads((lips / "manifest.json").read_text())["items"] if i.get("slug") == job.get("slug")]
        ls_handles = {i["id"]: i["handle"] for i in items}
        ls_cuts = {i["id"]: i.get("cuts", []) for i in items}

    # ---- sources and faces per shot
    info = []
    face_cache = tmp / "faces.json"
    fc = json.loads(face_cache.read_text()) if face_cache.exists() else {}
    for t, sh in zip(timeline, shots):
        d = {"id": sh["id"], "start": t["start"], "end": t["end"], "dur": t["dur"]}
        ls = lips / f"{sh['id']}.mp4"
        if sh.get("still"):          # still_frame: another frame of the ad stands in (e.g. while a frame is redone)
            d |= {"mode": "still", "src": frames_dir / (sh.get("still_frame") or sh["frame"])}
        elif ls.exists() and ls.stat().st_size > 1024:
            hd = ls_handles.get(sh["id"], 0.4)       # the lip-synced window starts `handle` s before the shot
            d |= {"mode": "clip", "src": ls, "head": hd, "end": probe_duration(ls), "lipsync": True}
        elif sh.get("parts"):     # clean windows of several renders of this shot, played one after the other
            parts = [(find_clip(a.clips, q["clip"]), q["start"], q["end"]) for q in sh["parts"]]
            parts = [(src, st, min(en, probe_duration(src))) for src, st, en in parts if src]
            d["reverse"] = [bool(q.get("reverse")) for q in sh["parts"]]
            d |= {"mode": "parts", "parts": parts, "src": parts[0][0], "head": parts[0][1], "end": parts[0][2]}
        else:
            src_id, head, end = plan.get(sh["id"], (sh["id"], HEAD_TRIM, None))
            src = find_clip(a.clips, src_id)
            if src is None:
                d |= {"mode": "still", "src": frames_dir / sh["frame"], "missing_clip": True}
            else:
                d |= {"mode": "clip", "src": src, "head": head, "end": min(end or 1e9, probe_duration(src))}
        key = f"{d['src']}:{d.get('head')}:{d.get('end')}"
        if key not in fc:
            if d["mode"] == "still":
                fc[key] = layout.shot_faces(d["src"], None)
            else:
                h0, e0 = d["head"], d["end"]
                fc[key] = layout.shot_faces(d["src"], [h0 + 0.1, (h0 + e0) / 2, max(h0, e0 - 0.3)])
        d["faces"] = fc[key]
        info.append(d)
    face_cache.write_text(json.dumps(fc))

    # ---- transitions (boundary k = between shot k and k+1): (xfade name, seconds, sfx). Accents, not every cut:
    # candidates by priority (first jump of a new time unit: white flash; cut to a packshot: zoom; strong scene change:
    # slide), then at most one every TRANS_GAP s. Other time-badge cuts stay hard cuts with a shutter click.
    kinds = [kind_of(sh["overlay"].split(" / ")[0]) if sh.get("overlay") else None for sh in shots]
    unit = lambda sh: (re.search(r"(MINUTE|STUNDE|TAG|WOCHE|MONAT)", sh.get("overlay", "").upper()) or [None])[0]
    cands = []
    seen_units = set()
    for k in range(len(shots) - 1):
        c = timeline[k + 1]["start"]
        if c < 2.5 or shots[k + 1].get("freeze_region") or shots[k].get("freeze_region"):
            continue
        sim = similarity(frames_dir / shots[k]["frame"], frames_dir / shots[k + 1]["frame"])
        nb = info[k + 1]
        if kinds[k + 1] == "badge":
            u = unit(shots[k + 1])
            if u not in seen_units:
                cands.append((3, k, ("fadewhite", 0.26, "shutter")))
            seen_units.add(u)
        elif not nb["faces"] and sim < 0.6 and kinds[k + 1] in ("offer", "cta", None) and info[k]["faces"]:
            cands.append((2, k, ("smoothup", 0.26, "whoosh_fast")))
        elif sim < 0.35:
            cands.append((1, k, ("slideleft", 0.22, "whoosh")))
    gap = TRANS_GAP
    if job.get("transitions") == "scenes":
        # every change of scene (first frame) gets a short transition: slides alternate, into the product world /
        # offer cards it rises (smoothup), the turn to the happy ending flashes white
        gap, cands, n = 0.75, [], 0
        happy = set(job.get("transition_flash_shots", []))
        for k in range(len(shots) - 1):
            if timeline[k + 1]["start"] < 1.5:
                continue
            if shots[k]["frame"] == shots[k + 1]["frame"]:
                # same scene, another render: the characters' pose restarts; a short dissolve (plus the new framing
                # below) blends it instead of a visible snap. Lowest priority, silent.
                cands.append((0, k, ("fade", 0.24, None)))
                continue
            if shots[k + 1]["id"] in happy:
                tr = ("fadewhite", 0.30, "shutter")
            elif not info[k + 1]["faces"] and kinds[k + 1] in ("offer", "cta") or shots[k + 1]["frame"].startswith(("SH", "END")):
                tr = ("smoothup", 0.30, "whoosh_fast")
            else:
                tr = (("slideleft", 0.28, "whoosh"), ("smoothleft", 0.30, "whoosh_fast"))[n % 2]
                n += 1
            cands.append((1, k, tr))
    if job.get("transitions") == "dtc":
        # DTC references cut hard on the words almost everywhere; accents only where the story turns. Every cut
        # between two renders of the same frame dissolves (no pose snap); a shot can ask for an accent with
        # trans_in: flash | slide | whip | rise | dissolve.
        TR = {"flash": ("fadewhite", 0.30, "shutter"), "slide": ("slideleft", 0.26, "whoosh"),
              "whip": ("smoothleft", 0.22, "whoosh_fast"), "rise": ("smoothup", 0.30, "whoosh_fast"),
              "dissolve": ("fade", 0.36, None), "zoom": ("zoomin", 0.30, "whoosh_fast")}
        gap, cands = 0.6, []
        for k in range(len(shots) - 1):
            if timeline[k + 1]["start"] < 0.8:
                continue
            want = shots[k + 1].get("trans_in")
            if want in TR:
                cands.append((2, k, TR[want]))
            elif shots[k]["frame"] == shots[k + 1]["frame"]:
                cands.append((1, k, ("fade", 0.24, None)))
    trans: dict[int, tuple[str, float, str]] = {}
    for pr, k, tr in sorted(cands, key=lambda x: (-x[0], x[1])):
        c = timeline[k + 1]["start"]
        if all(abs(c - timeline[j + 1]["start"]) >= gap for j in trans):
            trans[k] = tr
    badge_cuts = [k for k in range(len(shots) - 1) if kinds[k + 1] == "badge" and k not in trans]

    # ---- render segments (with handles for transitions) as punch-in pieces
    segs, report = [], []
    prev_zoom = None
    for k, (t, sh, d) in enumerate(zip(timeline, shots, info)):
        hpre = F(trans[k - 1][1] / 2) if k - 1 in trans else 0
        hpost = F(trans[k][1] / 2) if k in trans else 0
        f0, f1 = F(t["start"]), F(t["end"])
        # framing plan: a hard cut between two renders of the same first frame restarts the characters' pose; when
        # both sides would also share the framing it reads as a glitch (jump cut), so the new shot opens on another
        # framing (wide <-> punch-in) and the cut reads as a cut-in
        if d["mode"] == "still":
            n_pieces = len(plan_pieces(t, False))
        elif d["mode"] == "parts":
            n_pieces = len(d["parts"])
        else:
            n_pieces = len(plan_pieces(t, bool(sh.get("freeze_region")),
                                       [t["start"] + c for c in ls_cuts.get(sh["id"], [])] if d.get("lipsync") else []))
        cap = lambda zs: [min(z, sh.get("max_zoom", 9.0)) for z in zs]  # noqa: E731
        seq = cap(zoom_seq(d["mode"], n_pieces, d["faces"]))
        rot = 0
        same = k > 0 and shots[k - 1]["frame"] == sh["frame"]
        if same and prev_zoom is not None and abs(seq[0] - prev_zoom) < 0.08 and not sh.get("freeze_region"):
            rot = 1
            seq = cap(zoom_seq(d["mode"], n_pieces, d["faces"], 1))
        prev_zoom = seq[-1]
        seg = tmp / f"seg_{sh['id']}.mp4"
        rep = {"id": sh["id"], "start": round(t["start"], 2), "dur": round(t["dur"], 2), "mode": d["mode"],
               "trans_in": trans[k - 1][0] if k - 1 in trans else "cut", "faces": len(d["faces"])}
        srcs = [q[0] for q in d.get("parts", [])] or [d["src"]]
        key = json.dumps([f0, f1, hpre, hpost, d["mode"], d.get("head"), d.get("end"), [(str(q), q.stat().st_mtime) for q in srcs],
                          [(str(q[0]), q[1], q[2]) for q in d.get("parts", [])], sh.get("freeze_region"), [w["start"] for w in t.get("words") or []],
                          SEG_VERSION] + (["still_pieces"] if d["mode"] == "still" else []) + ([sh["max_zoom"]] if sh.get("max_zoom") else [])
                         + ([f"rot{rot}"] if rot else []), default=str)
        kf = seg.with_suffix(".key")
        if seg.exists() and kf.exists() and kf.read_text() == key and (tmp / f"seg_{sh['id']}.rep").exists():
            segs.append(seg)
            report.append(json.loads((tmp / f"seg_{sh['id']}.rep").read_text()))
            continue
        if d["mode"] == "still":
            # a long still is cut at word starts into pieces, each a slow push from another framing (face punch-in,
            # off-centre drift), so it reads as edited footage rather than a frozen frame
            pieces = plan_pieces(t, False)
            bounds = [F(p[0]) for p in pieces] + [f1]
            bounds[0] -= hpre
            bounds[-1] += hpost
            files = []
            zs = zoom_seq("still", len(pieces), d["faces"], rot)
            for j in range(len(pieces)):
                z0 = zs[j]
                if len(pieces) == 1:
                    z1, (cx, cy) = z0 + 0.07, ((0.5, 0.5) if not rot else (0.5, 0.46))
                    if rot and d["faces"]:
                        r = layout.zoom_rect(z0 + 0.05, d["faces"])
                        cx, cy = (r[0] + r[2]) / 2, (r[1] + r[3]) / 2
                elif d["faces"]:
                    r = layout.zoom_rect(z0 + 0.05, d["faces"])
                    z1, cx, cy = z0 + 0.06, (r[0] + r[2]) / 2, (r[1] + r[3]) / 2
                else:
                    z1 = z0 + 0.06
                    cx, cy = {1.0: (0.5, 0.5), 1.12: (0.42, 0.46), 1.06: (0.58, 0.54)}.get(z0, (0.5, 0.46))
                if sh.get("max_zoom"):
                    z1 = min(z1, sh["max_zoom"])
                    z0 = min(z0, z1 - 0.03)
                pf = tmp / f"seg_{sh['id']}_{j}.mp4"
                render_still(d["src"], bounds[j + 1] - bounds[j], pf, z0, z1, cx, cy)
                files.append(pf)
            if len(files) == 1:
                files[0].replace(seg)
            else:
                concat(files, seg)
                for q in files:
                    q.unlink(missing_ok=True)
            rep["pieces"] = len(files)
        elif d["mode"] == "parts":
            parts = d["parts"]
            usable = sum(e - s_ for _, s_, e in parts)
            slow = min(max((t["dur"] + (hpre + hpost) / FPS) / max(usable, 0.1), 1.0), MAX_SLOW)
            bounds, cum = [f0 - hpre], f0 - hpre
            for _, s_, e in parts[:-1]:
                cum += int(round((e - s_) * slow * FPS))
                bounds.append(cum)
            bounds.append(f1 + hpost)
            ws = [F(w["start"] - 0.04) for w in t.get("words") or []]
            for i in range(1, len(bounds) - 1):          # cut between parts on the nearest word start
                near = [x for x in ws if abs(x - bounds[i]) <= 12 and bounds[i - 1] + 15 < x < bounds[i + 1] - 15]
                if near:
                    bounds[i] = min(near, key=lambda x: abs(x - bounds[i]))
            files, held = [], 0.0
            for k2, (src, s_, e) in enumerate(parts):
                z = seq[k2]
                rect = layout.zoom_rect(z, d["faces"]) if z > 1 else [0, 0, 1, 1]
                pf = tmp / f"seg_{sh['id']}_p{k2}.mp4"
                held += render_piece(src, s_, slow, bounds[k2 + 1] - bounds[k2], pf, z, rect, e, reverse=d["reverse"][k2])
                files.append(pf)
            concat(files, seg)
            for q in files:
                q.unlink(missing_ok=True)
            rep |= {"pieces": len(parts), "parts": [[q.stem, s_, e] for q, s_, e in parts], "slow": round(slow, 3), "held_s": round(held, 2)}
            if held > 0.4:
                rep["needs_rerender"] = "parts too short"
        else:
            no_split = bool(sh.get("freeze_region"))
            pieces = plan_pieces(t, no_split, [t["start"] + c for c in ls_cuts.get(sh["id"], [])] if d.get("lipsync") else [])
            usable = d["end"] - d["head"]
            slow = min(max((t["dur"] + hpost / FPS) / max(usable, 0.1), 1.0), MAX_SLOW)
            # cycle_long: a line much longer than its footage replays the clip from the start at a piece boundary
            # (word boundary + framing change = a natural jump cut) instead of slowing and freezing it
            cyc = bool(job.get("cycle_long")) and (t["dur"] + hpost / FPS) > usable * MAX_SLOW + 0.3 and len(pieces) > 1
            if cyc:
                slow = 1.0
            zooms = ZOOMS_FACE if d["faces"] else ZOOMS_BROLL
            files, held = [], 0.0
            bounds = [F(p[0]) for p in pieces] + [f1]
            bounds[0] -= hpre
            bounds[-1] += hpost
            ptr = (bounds[0] - f0) / FPS
            for j in range(len(pieces)):
                z = seq[j] if j < len(seq) else min(zooms[j % len(zooms)], sh.get("max_zoom", 9.0))
                rect = layout.zoom_rect(z, d["faces"]) if z > 1 else [0, 0, 1, 1]
                if cyc:
                    plen = (bounds[j + 1] - bounds[j]) / FPS
                    if ptr + plen > usable and ptr > 0:
                        ptr = 0.0
                    src_t = d["head"] + ptr
                    ptr += plen
                else:
                    src_t = d["head"] + (bounds[j] - f0) / FPS / slow
                pf = tmp / f"seg_{sh['id']}_{j}.mp4"
                held += render_piece(d["src"], src_t, slow, bounds[j + 1] - bounds[j], pf, z, rect, d["end"])
                files.append(pf)
            concat(files, seg)
            for p in files:
                p.unlink(missing_ok=True)
            rep |= {"pieces": len(pieces), "slow": round(slow, 3), "held_s": round(held, 2)}
            if cyc:
                rep["cycled"] = True
            if held > 0.4:
                rep["needs_rerender"] = "clip too short"
            if d.get("lipsync"):
                rep["lipsync"] = True
        if rot:
            rep["reframed"] = True
        if sh.get("freeze_region") and d["mode"] != "still":
            freeze_region(seg, sh["freeze_region"], source=frames_dir / sh["frame"])
            rep["freeze_region"] = sh["freeze_region"]
        segs.append(seg)
        report.append(rep)
        kf.write_text(key)
        (tmp / f"seg_{sh['id']}.rep").write_text(json.dumps(rep))

    # ---- join: runs of hard cuts are concatenated, runs are joined by xfade
    runs, cur = [], [segs[0]]
    for k in range(1, len(segs)):
        if k - 1 in trans:
            runs.append(cur)
            cur = []
        cur.append(segs[k])
    runs.append(cur)
    run_files = []
    for i, r in enumerate(runs):
        rf = tmp / f"run_{i}.mp4"
        concat(r, rf)
        run_files.append(rf)
    clean = tmp / "clean.mp4"
    if len(run_files) == 1:
        shutil.copy(run_files[0], clean)
    else:
        inputs, fcx, length = [], [], None
        tlist = [trans[k] for k in sorted(trans)]
        for i, rf in enumerate(run_files):
            inputs += ["-i", str(rf)]
            fcx.append(f"[{i}:v]settb=AVTB,fps={FPS},format=yuv420p[r{i}]")
        prev, length = "r0", probe_frames(run_files[0])
        for i, (name, sec, _) in enumerate(tlist):
            tf = 2 * F(sec / 2)
            off = (length - tf) / FPS
            out = f"x{i}"
            fcx.append(f"[{prev}][r{i + 1}]xfade=transition={name}:duration={tf / FPS:.4f}:offset={off:.4f}[{out}]")
            length = length + probe_frames(run_files[i + 1]) - tf
            prev = out
        run(["ffmpeg", "-v", "error", "-y", *inputs, "-filter_complex", ";".join(fcx), "-map", f"[{prev}]",
             "-c:v", "libx264", "-preset", "fast", "-crf", "16", "-pix_fmt", "yuv420p", str(clean)])

    # ---- sound: VO + effects
    sfx_events = []
    for k, (name, sec, fx) in trans.items():
        if fx is None:
            continue
        sfx_events.append((timeline[k + 1]["start"] - SFX_PEAK[fx], fx, 0.45 if fx != "shutter" else 0.5))
    for k in badge_cuts:                       # time jumps without a flash still get the camera click
        sfx_events.append((timeline[k + 1]["start"] - SFX_PEAK["shutter"], "shutter", 0.4))

    # ---- text layers per format
    hook = job.get("hook_card")
    outputs = []
    texts_report = {}
    vo = tmp / "vo_sfx.m4a"
    for fname in a.formats.split(","):
        fm = Fmt(fname)
        doc = Ass(fm.res)
        RW, RH = fm.res
        texts_report[fname] = []
        hook_span = None
        if hook:
            hs = [(t["start"], t["end"]) for t, sh in zip(timeline, shots) if sh["id"] in set(hook["shots"])]
            hook_span = (min(x for x, _ in hs), max(y for _, y in hs))
            # a hook headline is a scroll stopper, not a caption: at most max_s (default 6 s), so it never sits over
            # the speaker for a whole monologue
            hook_span = (hook_span[0], min(hook_span[1], hook_span[0] + float(hook.get("max_s", 6.0))))
        hdr_style = job.get("overlay_style")
        card = job.get("comment_card")
        if card:
            cs = [(t["start"], t["end"]) for t, sh in zip(timeline, shots) if sh["id"] in set(card["shots"])]
            add_comment_card(doc, fm, card, min(x for x, _ in cs), max(y for _, y in cs))
        if job.get("progress_pills"):
            add_progress_pills(doc, fm, job["progress_pills"], timeline, shots)
        if job.get("tier_list"):
            add_tier_list(doc, fm, job["tier_list"], timeline, shots)
        for k, (t, sh, d) in enumerate(zip(timeline, shots, info)):
            boxes = fm.boxes(d["faces"])
            seg_end = t["end"]
            taken = []
            if card and sh["id"] in set(card["shots"]):
                taken.append((0.0, 0.2))
            if sh.get("marks"):
                add_marks(doc, fm, sh["marks"], t["start"], seg_end, sfx_events if fname == "9x16" else None, t.get("words"))
            # hook card zone (top) is reserved while it shows
            if hook_span and hook_span[0] <= t["start"] < hook_span[1]:
                hy = layout.free_slot([0.20, 0.27, 0.14, 0.34, 0.46, 0.53] if fname == "9x16" else ([0.15, 0.24, 0.62] if fname == "1x1" else [0.5]), 0.17, boxes,
                                      *((fm.panel) if fm.panel else (0.08, 0.92)))
                taken.append((hy - 0.09, hy + 0.09))
                d.setdefault("hook_y", {})[fname] = hy
            # on-screen text; with no face in the shot the middle is the product (packshot): keep it clear
            ov_boxes = boxes if d["faces"] or fm.panel else boxes + [[0.15, 0.27, 0.85, 0.72] if fname == "9x16" else [0.15, 0.22, 0.85, 0.78]]
            hst = sh.get("overlay_style") or hdr_style
            if sh.get("overlay") and hst in ("header-shadow", "header-box", "header-red", "header-white", "header-white-caps"):
                # one header per run of shots with the same text (no re-pop at a cut inside the run)
                same_prev = k > 0 and shots[k - 1].get("overlay") == sh["overlay"]
                if not same_prev:
                    k2 = k
                    while k2 + 1 < len(shots) and shots[k2 + 1].get("overlay") == sh["overlay"]:
                        k2 += 1
                    hy = add_header(doc, fm, sh["overlay"], t["start"], timeline[k2]["end"], hst)
                    texts_report[fname].append({"shot": sh["id"], "text": sh["overlay"], "kind": hst, "y": round(hy, 3)})
                taken.append((0.0, 0.2))
            elif sh.get("overlay"):       # parts show one after the other, so each may use the same free slot
                part_slots = []
                for (p0, p1, part) in part_spans(sh["overlay"], t, seg_end):
                    tk = list(taken)
                    placed = place_text(doc, fm, part, p0, p1, t, ov_boxes, tk, sfx_events if fname == "9x16" else None)
                    part_slots += tk[len(taken):]
                    texts_report[fname].append({"shot": sh["id"], "text": part, **placed})
                taken += part_slots
            # captions
            cap_h = fm.cap * 1.25 / RH
            if job.get("captions_y"):                   # preferred caption line for the whole ad (9:16 position)
                y9 = float(job["captions_y"])
                cy = {"9x16": y9, "1x1": min(max((y9 * 1920 - 380) / 1080, 0.12), 0.88), "16x9": 0.88}[fname]
                if fname == "9x16" and not job.get("captions_fixed"):
                    # the line moves off a face (box grown towards the chin: punch-ins enlarge faces) and, on product
                    # shots without a face, off the pack in the middle; nearest free line, lower ones first
                    fb = [[b[0], b[1], b[2], min(1.0, b[3] + 0.25 * (b[3] - b[1]))] for b in boxes]
                    if not d["faces"] and sh.get("product"):
                        fb.append([0.15, 0.27, 0.85, 0.72])
                    cands = [y9] + [min(max(y9 + dy, 0.14), 0.82) for dy in (0.06, 0.12, 0.18, 0.24, 0.30, -0.06, -0.12, -0.18, -0.24)]
                    cy = layout.free_slot(cands, cap_h, fb, 0.08, 0.92, taken)
            elif job.get("captions_pos") == "bottom":     # fixed bottom band (no face search), above the UI safe zone
                cy = {"9x16": 0.785, "1x1": 0.88, "16x9": 0.89}[fname]
            elif fm.panel:
                cy = layout.free_slot(fm.cap_c, cap_h, boxes, 0.05, 0.95, taken)
            else:
                cy = layout.free_slot(fm.cap_c, cap_h, boxes, 0.08, 0.92, taken)
            cstyle = job.get("caption_style", "karaoke")
            cw = list(t.get("words") or [])
            if k > 0 and cw and (pw := timeline[k - 1].get("words")) and re.search(r"\w-$", pw[-1]["text"]):
                cw = cw[1:]                             # shown whole at the end of the previous shot
            if cw and k + 1 < len(timeline) and re.search(r"\w-$", cw[-1]["text"]) and (nw := timeline[k + 1].get("words")):
                cw[-1] = {**cw[-1], "text": cw[-1]["text"] + nw[0]["text"]}   # "Geld-zurück-" | "Garantie." across a cut
            if sh.get("captions_y") and fname == "9x16":
                cy = float(sh["captions_y"])           # per-shot line (e.g. the emptiest band of a whiteboard frame)
            if job.get("tier_list") and sh["id"] in job["tier_list"].get("shots", {}) and fname == "9x16":
                cy = 0.53 - cap_h / 2 - 0.012           # just above the tier panel (it fills 0.53-0.90)
            if sh.get("no_captions"):
                pass
            elif cstyle in ("box", "box-small"):
                add_box_captions(doc, fm, cw, cy, seg_end, small=cstyle == "box-small")
            elif cstyle == "shadow":
                add_shadow_captions(doc, fm, cw, cy, seg_end)
            elif cstyle == "redbox":
                add_redbox_captions(doc, fm, cw, cy, seg_end)
            else:
                add_captions(doc, fm, cw, cy, seg_end)
        ass = tmp / f"text_{fname}.ass"
        doc.write(ass, fm.cap, fm.txt)

        if fname == a.formats.split(",")[0]:
            mix_audio(a.vo, sfx_events, total, vo, tmp)
        # hook png
        hpng = tmp / f"hook_{fname}.png"
        overlay = "[base]null[b]"
        if hook:
            hw = int(RW * 0.80) if not fm.panel else int(RW * 0.29)
            hook_png(hook["text"], hook.get("red", ""), hpng, hw, hook.get("bg", (255, 255, 255)), hook.get("fg", (20, 20, 20)))
            hi = Image.open(hpng)
            hy = next((d["hook_y"][fname] for d in info if d.get("hook_y", {}).get(fname)), 0.2)
            hx = (RW - hi.width) // 2 if not fm.panel else int(RW * (fm.panel[0] + fm.panel[1]) / 2 - hi.width / 2)
            y = int(hy * RH - hi.height / 2)
            h0, h1 = hook_span
            overlay = (f"[1:v]format=rgba,fade=t=in:st={h0:.2f}:d=0.18:alpha=1,fade=t=out:st={h1 - 0.2:.2f}:d=0.2:alpha=1[hk];"
                       f"[base][hk]overlay={hx}:{y}:enable='between(t,{h0:.2f},{h1:.2f})'[b]")
        else:
            Image.new("RGBA", (2, 2), (0, 0, 0, 0)).save(hpng)
        dst = a.out / f"{a.name}_{fname}.mp4"
        fcx = f"{fm.vf};{overlay};[b]ass={ass}[v]"
        run(["ffmpeg", "-v", "error", "-y", "-i", str(clean), "-loop", "1", "-i", str(hpng), "-i", str(vo),
             "-filter_complex", fcx, "-map", "[v]", "-map", "2:a", "-c:v", "libx264", "-crf", str(a.crf), "-preset", "medium",
             "-pix_fmt", "yuv420p", "-c:a", "copy", "-t", f"{total:.2f}", "-movflags", "+faststart", str(dst)])
        outputs.append(str(dst))
    rep = {"total_s": round(total, 2), "transitions": {shots[k + 1]["id"]: v[0] for k, v in trans.items()},
           "shots": report, "texts": texts_report.get("9x16"), "sfx": [(round(x, 2), n) for x, n, _ in sfx_events],
           "outputs": outputs}
    (a.out / f"{a.name}_edit_report.json").write_text(json.dumps(rep, indent=1, ensure_ascii=False))
    print(json.dumps({"total_s": rep["total_s"], "transitions": len(trans), "outputs": outputs}, indent=1))
    if not a.keep:
        shutil.rmtree(tmp, ignore_errors=True)


def probe_frames(p: Path) -> int:
    out = subprocess.run(["ffprobe", "-v", "error", "-count_packets", "-select_streams", "v:0", "-show_entries",
                          "stream=nb_read_packets", "-of", "csv=p=0", str(p)], capture_output=True, text=True).stdout
    return int(out.strip())


FONT_SEMI = "/usr/share/fonts/opentype/inter/InterDisplay-SemiBold.otf"


def glue_hyphens(words: list[dict]) -> list[dict]:
    """A split compound written with a space ("Geld-zurück- Garantie") shows as one caption word."""
    out = []
    for w in words:
        if out and re.search(r"\w-$", out[-1]["text"]):
            out[-1] = {**out[-1], "text": out[-1]["text"] + w["text"].lstrip(), "end": w["end"]}
        else:
            out.append(w)
    return out


def add_box_captions(doc: Ass, fm: Fmt, words: list[dict], cy: float, seg_end: float, small: bool = False) -> None:
    """DTC 'box' captions (references: explainer + testimonial ads): sentence-case phrases of up to 4 words in black
    semi-bold on a white rounded box, one box per phrase, a quick soft pop; no per-word highlight."""
    RW, RH = fm.res
    words = glue_hyphens(words)
    size = int(fm.cap * (0.66 if small else 0.86))
    max_w = RW * (0.60 if fm.name != "16x9" else 0.46)      # per line; a phrase may take two lines
    fnt = ImageFont.truetype(FONT_SEMI, max(int(size / ASS_EM), 8))
    groups, cur = [], []
    for w in words:
        txt = re.sub(r'["“”„„«»]|\s*[—–]+\s*', "", w["text"])
        if not txt:
            continue
        if cur and fnt.getlength(" ".join(x[1] for x in cur + [(w, txt)])) > max_w * (1.0 if small else 1.9):
            groups.append(cur)
            cur = []
        cur.append((w, txt))
        if len(cur) >= (4 if small else 6) or re.search(PUNCT + "$", w["text"]):
            groups.append(cur)
            cur = []
    if cur:
        groups.append(cur)
    for gi, g in enumerate(groups):
        t0 = g[0][0]["start"]
        t1 = groups[gi + 1][0][0]["start"] if gi + 1 < len(groups) else g[-1][0]["end"] + 0.4
        t1 = min(t1, g[-1][0]["end"] + 0.45, seg_end)
        if t1 - t0 < 0.08:
            continue
        line = re.sub(PUNCT + "+$", "", " ".join(x[1] for x in g))
        lines, cur2 = [], ""
        for wd in line.split():                 # balanced wrap into at most two lines
            nxt = (cur2 + " " + wd).strip()
            if cur2 and fnt.getlength(nxt) > max_w:
                lines.append(cur2)
                cur2 = wd
            else:
                cur2 = nxt
        lines.append(cur2)
        if len(lines) == 2 and fnt.getlength(lines[1]) < fnt.getlength(lines[0]) * 0.45:
            ws_ = line.split()
            best = min(range(1, len(ws_)), key=lambda i: abs(fnt.getlength(" ".join(ws_[:i])) - fnt.getlength(" ".join(ws_[i:]))))
            lines = [" ".join(ws_[:best]), " ".join(ws_[best:])]
        em = size / ASS_EM
        doc.pill(t0, t1, RW / 2, cy * RH, lines, size, "111111", "FFFFFF", pad=(0.42, 0.20), radius=em * 0.32,
                 anim="soft", layer=20, style="CapBox", font=FONT_SEMI)


def caption_groups(doc: Ass, words: list[dict], size: float, max_w: float, n: int, lower: bool = False) -> list:
    groups, cur = [], []
    for w in words:
        txt = re.sub(r'["“”„«»]|\s*[—–]+\s*', "", w["text"])
        txt = txt.lower() if lower else txt
        if not txt:
            continue
        if cur and doc.width(" ".join(x[1] for x in cur + [(w, txt)]), size) > max_w:
            groups.append(cur)
            cur = []
        cur.append((w, txt))
        if len(cur) >= n or re.search(PUNCT + "$", w["text"]):
            groups.append(cur)
            cur = []
    if cur:
        groups.append(cur)
    return groups


def add_shadow_captions(doc: Ass, fm: Fmt, words: list[dict], cy: float, seg_end: float) -> None:
    """White bold sans with a soft black shadow, 2-4 words, no box, no highlight (testimonial/UGC references)."""
    RW, RH = fm.res
    words = glue_hyphens(words)
    size = int(fm.cap * 0.9)
    groups = caption_groups(doc, words, size, RW * (0.82 if fm.name != "16x9" else 0.58), 4)
    for gi, g in enumerate(groups):
        t0 = g[0][0]["start"]
        t1 = groups[gi + 1][0][0]["start"] if gi + 1 < len(groups) else g[-1][0]["end"] + 0.4
        t1 = min(t1, g[-1][0]["end"] + 0.45, seg_end)
        if t1 - t0 < 0.08:
            continue
        line = re.sub(PUNCT + "+$", "", " ".join(x[1] for x in g))
        doc.add(t0, t1, "Cap", f"{{\\an5\\pos({RW / 2:.0f},{cy * RH:.0f})\\fs{size}\\bord0\\shad4\\4a&H48&\\blur0.8"
                               f"\\fscx94\\fscy94\\t(0,80,\\fscx100\\fscy100)}}{line}", 20)


def add_redbox_captions(doc: Ass, fm: Fmt, words: list[dict], cy: float, seg_end: float) -> None:
    """Karaoke in white lowercase bold; the word being spoken sits on a red rounded box (creator/explainer refs)."""
    RW, RH = fm.res
    words = glue_hyphens(words)
    size = int(fm.cap * 0.9)
    groups = caption_groups(doc, words, size, RW * (0.80 if fm.name != "16x9" else 0.56), 3, lower=True)
    clean = lambda x: re.sub(PUNCT + "+$", "", x)
    sp = doc.width("a a", size) - doc.width("aa", size)
    em = size / ASS_EM
    y = cy * RH
    for gi, g in enumerate(groups):
        g_end = groups[gi + 1][0][0]["start"] if gi + 1 < len(groups) else min(g[-1][0]["end"] + 0.5, seg_end)
        g_end = min(g_end, g[-1][0]["end"] + 0.5, seg_end)
        if g_end - g[0][0]["start"] < 0.08:
            continue
        toks = [clean(tx) for _, tx in g]
        widths = [doc.width(tx, size) for tx in toks]
        total = sum(widths) + sp * (len(toks) - 1)
        x = RW / 2 - total / 2
        lefts = []
        for wd in widths:
            lefts.append(x)
            x += wd + sp
        pop = "\\fscx90\\fscy90\\t(0,70,\\fscx100\\fscy100)"
        doc.add(g[0][0]["start"], g_end, "Cap", f"{{\\an5\\pos({RW / 2:.0f},{y:.0f}){pop}\\fs{size}\\bord{max(size // 22, 2)}\\shad2}}" + " ".join(toks), 21)
        for wi, (w, _) in enumerate(g):
            end = g[wi + 1][0]["start"] if wi + 1 < len(g) else g_end
            if end <= w["start"]:
                continue
            bw, bh = widths[wi] + em * 0.42, size * 1.06
            cxw = lefts[wi] + widths[wi] / 2
            doc.add(w["start"], end, "Shp", f"{{\\an5\\pos({cxw:.0f},{y + size * 0.04:.0f})\\c&H2F25E0&\\bord0\\shad0\\p1}}{Ass.rrect(bw, bh, em * 0.22)}", 20)


def add_captions(doc: Ass, fm: Fmt, words: list[dict], cy: float, seg_end: float) -> None:
    """Spoken words in groups of up to 3 (never wider than the frame), the current word in yellow, each group pops."""
    RW, RH = fm.res
    words = glue_hyphens(words)
    size = fm.cap
    max_w = RW * (0.84 if fm.name != "16x9" else 0.60)
    groups, cur = [], []
    for w in words:
        txt = re.sub(r'["“”„„«»]|\s*[—–]+\s*', "", w["text"]).upper()      # quotes and dashes never show in captions
        if cur and doc.width(" ".join(x[1] for x in cur + [(w, txt)]), size) > max_w:
            groups.append(cur)
            cur = []
        cur.append((w, txt))
        if len(cur) >= 3 or re.search(PUNCT + "$", w["text"]):
            groups.append(cur)
            cur = []
    if cur:
        groups.append(cur)
    clean = lambda s: re.sub(PUNCT + "+$", "", s)
    x = RW / 2
    y = cy * RH
    for gi, g in enumerate(groups):
        g_end = groups[gi + 1][0][0]["start"] if gi + 1 < len(groups) else min(g[-1][0]["end"] + 0.5, seg_end)
        g_end = min(g_end, g[-1][0]["end"] + 0.5, seg_end)
        for wi, (w, _) in enumerate(g):
            end = g[wi + 1][0]["start"] if wi + 1 < len(g) else g_end
            if end <= w["start"]:
                continue
            body = " ".join(("{\\c&H00E1FF&}" + clean(tx) + "{\\c&HFFFFFF&}") if ww is w else clean(tx) for ww, tx in g)
            pop = "\\fscx82\\fscy82\\t(0,90,\\fscx100\\fscy100)" if wi == 0 else ""
            doc.add(w["start"], end, "Cap", f"{{\\an5\\pos({x:.0f},{y:.0f}){pop}}}{body}", 20)


def add_header(doc: Ass, fm: Fmt, text: str, t0: float, t1: float, style: str, fade_in: bool = True) -> float:
    """Top header of a shot. header-shadow (UGC creator ads): white semi-bold sentence with a soft shadow, no box;
    header-box (testimonial ads): white bold capitals on a dark translucent box. Returns its centre y (normalised)."""
    RW, RH = fm.res
    text = EMOJI_RE.sub("", text).strip()
    x0n, x1n = fm.panel if fm.panel else (0.06, 0.94)
    cx = RW * (x0n + x1n) / 2
    maxw = RW * (x1n - x0n) * 0.94
    if style in ("header-red", "header-white", "header-white-caps"):
        size = int(fm.txt * 0.74)
        txt = text.upper() if style == "header-white-caps" else text
        lines = doc.wrap(txt, size, maxw * 0.84, bold=True, max_lines=3)
        y = RH * (0.085 if fm.name == "9x16" else 0.08) + (len(lines) - 1) * size * 0.55
        fg, bg = ("FFFFFF", "E0252F") if style == "header-red" else ("111111", "FFFFFF")
        doc.pill(t0, t1, cx, y, lines, size, fg, bg, bold=True, pad=(0.6, 0.34), radius=size / ASS_EM * 0.32,
                 anim="soft" if fade_in else "none", layer=16)
        return (y + len(lines) * size * 0.5) / RH
    if style == "header-box":
        size = int(fm.txt * 0.82)
        lines = doc.wrap(text.upper(), size, maxw * 0.9, bold=True, max_lines=2)
        y = RH * (0.115 if fm.name == "9x16" else 0.10)
        doc.pill(t0, t1, cx, y, lines, size, "FFFFFF", "000000", bg_alpha=0x58, bold=True, pad=(0.5, 0.25),
                 radius=size / ASS_EM * 0.25, anim="soft" if fade_in else "none")
        return y / RH
    size = int(fm.txt * 0.92)
    fnt = ImageFont.truetype(FONT_SEMI, max(int(size / ASS_EM), 8))
    words, lines, cur = text.split(), [], ""
    for w in words:
        nxt = (cur + " " + w).strip()
        if cur and fnt.getlength(nxt) > maxw:
            lines.append(cur)
            cur = w
        else:
            cur = nxt
    lines.append(cur)
    y = RH * (0.105 if fm.name == "9x16" else 0.09) + (len(lines) - 1) * size * 0.5
    fad = "\\fad(120,100)" if fade_in else "\\fad(0,100)"
    # soft dark scrim behind the sentence so white text reads on bright walls (blurred, no hard box edge)
    em = size / ASS_EM
    sw = max(fnt.getlength(ln) for ln in lines) + em * 1.6
    sh_ = len(lines) * size * 1.18 + em * 0.9
    doc.add(t0, t1, "Hdr", f"{{\\an5\\pos({cx:.0f},{y:.0f}){fad}\\p1\\bord0\\shad0\\1c&H000000&\\1a&HA8&\\blur{em * 0.9:.0f}}}"
            f"m 0 0 l {sw:.0f} 0 {sw:.0f} {sh_:.0f} 0 {sh_:.0f}{{\\p0}}", 14)
    doc.add(t0, t1, "Hdr", f"{{\\an5\\pos({cx:.0f},{y:.0f}){fad}\\bord1.2\\3c&H000000&\\3a&HB0&\\shad3\\4a&H60&}}" + "\\N".join(lines), 15)
    return y / RH


def word_time(t: dict, key: str | None, default: float) -> float:
    if not key:
        return default
    k = norm(key)
    hit = next((w for w in (t.get("words") or []) if norm(w["text"]).startswith(k)), None)
    return max(t["start"], hit["start"] - 0.05) if hit else default


def add_progress_pills(doc: Ass, fm: Fmt, spec: dict, timeline: list, shots: list) -> None:
    """'5 reasons' progress row at the top: one pill per chapter, the current one red (listicle references).
    spec = {labels: [...], starts: [[shot_id, at_word|None], ...], end: shot_id}"""
    RW, RH = fm.res
    ids = [s["id"] for s in shots]
    tl = {s["id"]: t for s, t in zip(shots, timeline)}
    starts = [word_time(tl[sid], w, tl[sid]["start"]) for sid, w in spec["starts"]]
    end = tl[spec["end"]]["end"] if spec.get("end") in tl else timeline[-1]["end"]
    n = len(spec["labels"])
    x0n, x1n = fm.panel if fm.panel else (0.04, 0.96)
    gap = RW * 0.012
    pw = (RW * (x1n - x0n) - gap * (n - 1)) / n
    size = int(fm.txt * (0.56 if fm.name == "9x16" else 0.34))
    y = RH * (0.055 if fm.name == "9x16" else 0.05)
    while size > 10 and max(doc.width(lab, size, True) for lab in spec["labels"]) > pw * 0.9:
        size -= 2                                   # labels stay inside their pill
    for i in range(n):
        a, b = starts[i], (starts[i + 1] if i + 1 < n else end)
        if b - a < 0.1:
            continue
        for j, lab in enumerate(spec["labels"]):
            cx = RW * x0n + pw / 2 + j * (pw + gap)
            act = j == i
            doc.add(a, b, "Shp", f"{{\\an5\\pos({cx:.0f},{y:.0f})\\c{bgr('E0252F' if act else '000000')}\\1a&H{0 if act else 0x70:02X}&\\bord0\\shad0\\p1}}{Ass.rrect(pw, size * 1.5, size * 0.5)}", 28)
            doc.add(a, b, "TxtB", f"{{\\an5\\pos({cx:.0f},{y:.0f})\\fs{size}\\c&HFFFFFF&\\bord0\\shad0}}{lab}", 29)


def add_tier_list(doc: Ass, fm: Fmt, spec: dict, timeline: list, shots: list) -> None:
    """Tier list on a dark panel in the lower half (ranking references): rows S..D in colour, items land in their
    row on a spoken word and stay for the later tier shots. spec = {rows, colors, shots: {id: [{row, text, at_word}]}}"""
    if fm.name != "9x16":
        return
    RW, RH = fm.res
    rows, cols = spec.get("rows", ["S", "A", "B", "C", "D"]), spec.get("colors", ["E0252F", "F28C28", "F2C230", "4CAF50", "1FA3A3"])
    py0, py1 = 0.53, 0.90
    rh = RH * (py1 - py0) / len(rows)
    px0, px1 = RW * 0.04, RW * 0.96
    # item text as large as the row allows: per row, the size at which all of its items (the final set) fit
    sq0 = rh * 0.82
    avail = px1 - px0 - 14 - sq0 - 18 - 10
    sizes = {}
    for r in rows:
        txts = [it["text"] for its in spec["shots"].values() for it in its if it["row"] == r]
        sz = int(fm.txt * 0.78)
        while sz > fm.txt * 0.40 and sum(doc.width(x, sz, True) + sz * 0.9 + 14 for x in txts) > avail:
            sz -= 2
        sizes[r] = sz
    placed = {r: [] for r in rows}                     # row -> [(text, t_appear)]
    for sh, t in zip(shots, timeline):
        items = spec["shots"].get(sh["id"])
        if items is None:
            continue
        a, b = t["start"], t["end"]
        doc.add(a, b, "Shp", f"{{\\an7\\pos({px0:.0f},{RH * py0:.0f})\\c&H141414&\\1a&H30&\\bord0\\shad0\\fad(120,0)\\p1}}{Ass.rrect(px1 - px0, RH * (py1 - py0), 24)}", 22)
        spread = [a + 0.4 + (b - a - 0.6) * (i + 0.5) / max(len(items), 1) for i in range(len(items))]
        new = [(it, word_time(t, it.get("at_word"), spread[i])) for i, it in enumerate(items)]
        for ri, r in enumerate(rows):
            ry = RH * py0 + rh * ri + rh / 2
            sq = rh * 0.82
            doc.add(a, b, "Shp", f"{{\\an5\\pos({px0 + 14 + sq / 2:.0f},{ry:.0f})\\c{bgr(cols[ri])}\\bord0\\shad0\\p1}}{Ass.rrect(sq, sq, 12)}", 23)
            doc.add(a, b, "TxtB", f"{{\\an5\\pos({px0 + 14 + sq / 2:.0f},{ry:.0f})\\fs{int(fm.txt * 0.75)}\\c&HFFFFFF&\\bord0\\shad0}}{r}", 24)
            x = px0 + 14 + sq + 18
            size = sizes[r]
            for txt, ta in placed[r] + [(it["text"], tt) for it, tt in new if it["row"] == r]:
                w = doc.width(txt, size, True) + size * 0.9
                appear = max(a, ta)
                anim = "\\fscx40\\fscy40\\t(0,140,\\fscx108\\fscy108)\\t(140,220,\\fscx100\\fscy100)" if ta >= a + 0.05 else ""
                if appear < b - 0.05:
                    doc.add(appear, b, "Shp", f"{{\\an5\\pos({x + w / 2:.0f},{ry:.0f}){anim}\\c&HFFFFFF&\\bord0\\shad2\\p1}}{Ass.rrect(w, rh * 0.62, 14)}", 25)
                    doc.add(appear, b, "TxtB", f"{{\\an5\\pos({x + w / 2:.0f},{ry:.0f}){anim}\\fs{size}\\c&H111111&\\bord0\\shad0}}{txt}", 26)
                x += w + 14
        for it, tt in new:
            placed[it["row"]].append((it["text"], -1.0))


def add_comment_card(doc: Ass, fm: Fmt, card: dict, t0: float, t1: float) -> None:
    """'Reply to comment' card (UGC reply ads): white rounded card at the top left with a grey avatar dot, the
    viewer's comment in black and a grey 'Antwort an @...' line."""
    RW, RH = fm.res
    x0n = fm.panel[0] if fm.panel else 0.05
    wn = (fm.panel[1] - fm.panel[0]) if fm.panel else 0.72
    size = int(fm.txt * (0.80 if not fm.panel else 0.62))
    fnt = ImageFont.truetype(FONT_SEMI, max(int(size / ASS_EM), 8))
    av = size * 1.5
    maxw = RW * wn - av - size * 1.6
    lines, cur = [], ""
    for w in card["text"].split():
        nxt = (cur + " " + w).strip()
        if cur and fnt.getlength(nxt) > maxw:
            lines.append(cur)
            cur = w
        else:
            cur = nxt
    lines.append(cur)
    pad = size * 0.55
    bh = len(lines) * size * 1.05 + size * 0.85 + 2 * pad
    bw = RW * wn
    x, y = RW * x0n, RH * float(card.get("y", 0.045 if fm.name == "9x16" else 0.05))
    fade = "\\fad(150,120)"
    doc.add(t0, t1, "Shp", f"{{\\an7\\pos({x:.0f},{y:.0f}){fade}\\c&HFFFFFF&\\shad5\\4a&HB0&\\p1}}{Ass.rrect(bw, bh, size * 0.6)}", 30)
    r = av / 2
    k = 0.5523 * r
    circ = (f"m {r} 0 b {r + k} 0 {2 * r} {r - k} {2 * r} {r} b {2 * r} {r + k} {r + k} {2 * r} {r} {2 * r} "
            f"b {r - k} {2 * r} 0 {r + k} 0 {r} b 0 {r - k} {r - k} 0 {r} 0")
    doc.add(t0, t1, "Shp", f"{{\\an7\\pos({x + pad:.0f},{y + pad:.0f}){fade}\\c&HB4B4B4&\\shad0\\p1}}{circ}", 31)
    tx = x + pad + av + size * 0.5
    doc.add(t0, t1, "Cmt", f"{{\\an7\\pos({tx:.0f},{y + pad:.0f}){fade}\\fs{size}\\c&H111111&}}" + "\\N".join(lines), 32)
    doc.add(t0, t1, "Cmt", f"{{\\an7\\pos({tx:.0f},{y + pad + len(lines) * size * 1.05 + size * 0.15:.0f}){fade}"
                           f"\\fs{int(size * 0.72)}\\c&H8A8A8A&}}{card.get('reply', 'Antwort an @nutzerin')}", 32)


def add_marks(doc: Ass, fm: Fmt, marks: list[dict], t0: float, t1: float, sfx, words=None) -> None:
    """Editor graphics on a shot (9:16 coordinates, mapped into 1:1/16:9): red X, red circle, red arrow, coloured
    labels, a number counting up. Each mark: {type, x, y, size?, at? (s after shot start), text?, color?, to?, angle?}."""
    RW, RH = fm.res

    def pt(x, y):
        if fm.name == "1x1":
            return x * RW, (y * 1920 - 380)
        if fm.name == "16x9":
            return RW * (0.342 + 0.316 * x), y * RH
        return x * RW, y * RH

    scale = 1.0 if fm.name == "9x16" else (1.0 if fm.name == "1x1" else 0.5625)
    for m in marks:
        a = t0 + float(m.get("at", 0.15))
        if m.get("at_word"):                  # appear on a spoken word (first word of the shot starting with it)
            key = norm(m["at_word"])
            hit = next((w for w in (words or []) if norm(w["text"]).startswith(key)), None)
            if hit:
                a = max(t0, hit["start"] - 0.05)
        b = t0 + float(m["until"]) if m.get("until") is not None else t1
        if b - a < 0.15:
            continue
        cx, cy = pt(m["x"], m["y"])
        sz = float(m.get("size", 0.3)) * 1080 * scale
        col = bgr(m.get("color", "E0252F"))
        pop = "\\fscx40\\fscy40\\t(0,120,\\fscx110\\fscy110)\\t(120,200,\\fscx100\\fscy100)\\fad(0,120)"
        typ = m["type"]
        if typ == "x":
            bar_w, bar_h = sz, sz * 0.16
            bar = Ass.rrect(bar_w, bar_h, bar_h / 2)
            for rot in (45, -45):
                doc.add(a, b, "Shp", f"{{\\an5\\pos({cx:.0f},{cy:.0f}){pop}\\frz{rot}\\c{col}\\bord3\\3c&HFFFFFF&\\shad2\\p1}}{bar}", 26)
        elif typ == "circle":
            r = sz / 2
            k = 0.5523 * r
            circ = (f"m {r} 0 b {r + k} 0 {2 * r} {r - k} {2 * r} {r} b {2 * r} {r + k} {r + k} {2 * r} {r} {2 * r} "
                    f"b {r - k} {2 * r} 0 {r + k} 0 {r} b 0 {r - k} {r - k} 0 {r} 0")
            doc.add(a, b, "Shp", f"{{\\an5\\pos({cx:.0f},{cy:.0f}){pop}\\1a&HFF&\\bord{max(sz * 0.045, 5):.0f}\\3c{col}\\shad0\\p1}}{circ}", 26)
        elif typ == "arrow":
            L, w = sz, sz * 0.13
            arrow = (f"m 0 {-w / 2} l {L * 0.68} {-w / 2} l {L * 0.68} {-w * 1.6} l {L} 0 l {L * 0.68} {w * 1.6} "
                     f"l {L * 0.68} {w / 2} l 0 {w / 2}")
            ang = float(m.get("angle", 0))          # direction the arrow points (degrees, 0 = right, 90 = up)
            mv = f"\\move({cx - L * 0.25:.0f},{cy:.0f},{cx:.0f},{cy:.0f},0,180)"
            doc.add(a, b, "Shp", f"{{\\an6{mv}\\fad(80,120)\\frz{ang}\\org({cx:.0f},{cy:.0f})\\c{col}\\bord3\\3c&HFFFFFF&\\shad2\\p1}}{arrow}", 26)
        elif typ == "label":
            size = int(fm.txt * float(m.get("scale", 0.75)))
            doc.pill(a, b, cx, cy, [m["text"]], size, m.get("fg", "FFFFFF"), m.get("color", "E0252F"), bold=True, pad=(0.45, 0.2),
                     radius=size / ASS_EM * 0.3, anim=m.get("anim", "pop"), layer=27)
        elif typ == "badge":                     # round seal, e.g. 60 TAGE GELD-ZURUECK-GARANTIE
            r = sz / 2
            k = 0.5523 * r
            circ = (f"m {r} 0 b {r + k} 0 {2 * r} {r - k} {2 * r} {r} b {2 * r} {r + k} {r + k} {2 * r} {r} {2 * r} "
                    f"b {r - k} {2 * r} 0 {r + k} 0 {r} b 0 {r - k} {r - k} 0 {r} 0")
            rot = float(m.get("rot", -8))
            doc.add(a, b, "Shp", f"{{\\an5\\pos({cx:.0f},{cy:.0f}){pop}\\frz{rot}\\c{col}\\bord{max(sz * 0.03, 3):.0f}\\3c&HFFFFFF&\\shad3\\4a&H90&\\p1}}{circ}", 26)
            lines = m.get("lines", ["60 TAGE", "GELD-ZURÜCK-", "GARANTIE"])
            fs = sz * float(m.get("text_scale", 0.15))
            doc.add(a, b, "TxtB", f"{{\\an5\\pos({cx:.0f},{cy:.0f}){pop}\\frz{rot}\\fs{fs:.0f}\\c&HFFFFFF&\\bord0\\shad0}}" + "\\N".join(lines), 27)
        elif typ == "button":                    # CTA button, gently pulsing
            size = int(fm.txt * float(m.get("scale", 0.72)))
            bx = doc.pill(a, b, cx, cy, [m["text"]], size, m.get("fg", "111111"), m.get("color", "9BE22E"), bold=True,
                          pad=(0.8, 0.42), radius=size / ASS_EM * 0.6, anim="pop", layer=27)
        elif typ == "big":                       # big centred word/number with a soft shadow
            size = int(fm.txt * float(m.get("scale", 2.4)))
            doc.add(a, b, "Txt", f"{{\\an5\\pos({cx:.0f},{cy:.0f}){pop}\\fs{size}\\c{bgr(m.get('fg', 'FFFFFF'))}\\bord0\\shad5\\4a&H70&}}{m['text']}", 26)
        elif typ == "counter":
            to, dur = int(m["to"]), float(m.get("count_s", 1.2))
            size = int(fm.txt * float(m.get("scale", 2.6)))
            steps = 24
            for i in range(steps + 1):
                v = round(to * (1 - (1 - i / steps) ** 3))
                ta = a + dur * i / steps
                tb = a + dur * (i + 1) / steps if i < steps else b
                txt = f"{v:,}".replace(",", ".") + m.get("suffix", "")
                doc.add(ta, tb, "Txt", f"{{\\an5\\pos({cx:.0f},{cy:.0f})\\fs{size}\\c&HFFFFFF&\\bord0\\shad4\\4a&H80&}}{txt}", 26)
        if sfx is not None and (typ in ("x", "circle", "badge", "big", "button") or (typ == "label" and m.get("anim", "pop") == "pop")):
            sfx.append((a, "pop", 0.3))


def place_text(doc: Ass, fm: Fmt, text: str, t0: float, t1: float, t: dict, boxes, taken, sfx) -> dict:
    """Draw one on-screen text in the style of its kind at a face-free spot; returns what was placed where."""
    RW, RH = fm.res
    kind = kind_of(text)
    s = fm.txt
    panel = fm.panel
    x0n, x1n = panel if panel else (0.08, 0.92)
    cxp = RW * (x0n + x1n) / 2
    maxw = RW * (x1n - x0n) * 0.92
    t1 = max(t1, t0 + 0.6)

    def slot(h_px, cands=None):
        y = layout.free_slot(cands or fm.ov_c, h_px / RH, boxes, x0n, x1n, taken)
        taken.append((y - h_px / RH / 2 - 0.01, y + h_px / RH / 2 + 0.01))
        return y * RH

    if kind == "badge":
        size = int(s * 1.25)
        label = text.upper()
        em = size / ASS_EM
        bw = doc.width(label, size) + 1.1 * em
        bh = size + 0.6 * em
        if panel:
            cx, cy = RW * x0n + bw / 2 + 20, RH * 0.10 + bh / 2
        else:
            cx, cy = RW * 0.06 + bw / 2, RH * fm.safe[0] + bh / 2 + 10
            fb = [cx / RW - bw / RW / 2, cy / RH - bh / RH / 2, cx / RW + bw / RW / 2, cy / RH + bh / RH / 2]
            if layout.overlap(tuple(fb), boxes) > 0:
                cx = RW * 0.94 - bw / 2
        doc.pill(t0, t1, cx, cy, [label], size, "FFFFFF", BLUE, anim="slide", radius=em * 0.35)
        return {"kind": kind, "y": round(cy / RH, 3)}
    if kind == "list":
        items = [i.strip() for i in text.split("\\N") if i.strip()]
        good = False
        after, out = t0, []
        for i, it in enumerate(items):
            if it.endswith(":"):
                good = True
                continue
            m = match_time(it, t.get("words") or [], after)
            out.append([m, it, good])
            if m:
                after = m + 0.2
        known = [o[0] for o in out if o[0]]
        if len(known) < max(2, len(out) // 2):      # the VO does not name the items: one after the other, evenly
            step = (t1 - t0 - 0.3) / max(len(out), 1)
            for i, o in enumerate(out):
                o[0] = t0 + 0.3 + i * step
        for i, o in enumerate(out):                 # unspoken items: between their spoken neighbours
            if o[0] is None:
                prev = out[i - 1][0] if i else t0
                nxt = next((x[0] for x in out[i + 1:] if x[0]), t1)
                o[0] = prev + (nxt - prev) / 2 if prev else t0
        size = int(s * 1.4)
        h_px = size * 2.2
        y = slot(h_px)
        for i, (st, it, good) in enumerate(out):
            en = out[i + 1][0] if i + 1 < len(out) else t1
            if en - st < 0.25:
                continue
            lines = doc.wrap(("✓ " if good else "✕ ") + it, size, maxw, max_lines=2)
            doc.pill(st, en, cxp, y, lines, size, "FFFFFF", GREEN if good else RED, anim="pop")
            if sfx is not None:
                sfx.append((st - SFX_PEAK["swipe"], "pop" if good else "swipe", 0.35))
        return {"kind": kind, "y": round(y / RH, 3), "items": len(out), "spoken": len(known)}
    if kind == "versus":
        m = re.match(r"^links\s+(.+?)\s*·\s*rechts\s+(.+)$", text, re.I)
        left, right = (m.group(1), m.group(2)) if m else [x.strip() for x in re.split(r"\bvs\.?", text, flags=re.I)][:2]
        size = int(s * 0.95)
        y = slot(size * 1.8)
        if panel:
            doc.pill(t0, t1, RW * 0.17, RH * 0.45, [left.upper()], size, "FFFFFF", "555555", anim="pop")
            doc.pill(t0, t1, RW * 0.83, RH * 0.45, [right.upper()], size, "FFFFFF", BLUE, anim="pop")
        else:
            doc.pill(t0, t1, RW * 0.27, y, [left.upper()], size, "FFFFFF", "555555", anim="pop")
            doc.pill(t0, t1, RW * 0.73, y, [right.upper()], size, "FFFFFF", BLUE, anim="pop")
        if sfx is not None:
            sfx.append((t0, "pop", 0.35))
        return {"kind": kind, "y": round(y / RH, 3)}
    if kind == "cite":
        size = int(s * 0.75)
        lines = doc.wrap(text, size, maxw * 0.9, bold=True, max_lines=3)
        h_px = len(lines) * size + size * 0.5
        y = slot(h_px, [0.86, 0.80] if fm.name == "1x1" else (fm.ov_c if not panel else [0.78]))
        doc.pill(t0, t1, cxp, y, lines, size, "FFFFFF", "000000", bg_alpha=0x50, bold=True, anim="fade", radius=10)
        return {"kind": kind, "y": round(y / RH, 3)}
    if kind == "nametag":
        size = int(s * 1.05)
        stars = re.findall(r"★+", text)
        name = re.sub(r"★+", "", text).strip(" ,·")
        label = "{\\c&H0AD6FF&}" + (stars[0] if stars else "★★★★★") + "{\\c&HFFFFFF&}  " + name
        y = slot(size * 1.7, [0.62, 0.56, 0.66, 0.50, 0.30] if not panel else [0.6])
        em = size / ASS_EM
        bw = doc.width(name + "  ★★★★★", size) + 1.1 * em
        cx = (RW * x0n + bw / 2 + (0 if panel else RW * 0.0)) if not panel else cxp
        doc.pill(t0, t1, cx, y, [label], size, "FFFFFF", "000000", bg_alpha=0x40, anim="slide")
        if sfx is not None:
            sfx.append((t0, "pop", 0.3))
        return {"kind": kind, "y": round(y / RH, 3)}
    # headline / offer / cta / alert
    style = {"offer": (YELLOW, INK, -3.0, 1.08), "cta": ("FFFFFF", BLUE, 0.0, 1.3), "alert": (RED, "FFFFFF", 0.0, 1.1),
             "headline": ("FFFFFF", INK, 0.0, 1.0)}[kind]
    bg, fg, rot, scale = style
    size = int(s * scale)
    label = EMOJI_RE.sub("", text).strip()
    label = label.upper() if kind in ("offer", "alert") else label
    label = re.sub(r"\s*·\s*", " · ", label)
    lines = doc.wrap(label, size, maxw, max_lines=3)
    lines = [re.sub(r"~~(.+?)~~", r"{\\s1\\alpha&H28&\\fscx88\\fscy88}\1{\\s0\\alpha&H00&\\fscx100\\fscy100}", l) for l in lines]
    h_px = len(lines) * size + size * 0.75
    cands = None
    if kind == "cta" and not t.get("words"):
        cands = [0.62, 0.55] + fm.ov_c
    y = slot(h_px, cands)
    doc.pill(t0, t1, cxp, y, lines, size, fg, bg, rot=rot, anim="pop")
    if sfx is not None:
        sfx.append((t0, "ching" if kind == "offer" else "pop", 0.4 if kind == "offer" else 0.3))
    return {"kind": kind, "y": round(y / RH, 3), "lines": len(lines)}


def mix_audio(vo: Path, events: list, total: float, dst: Path, tmp: Path) -> None:
    """VO (loudness-normalised) with the sound effects laid under it."""
    events = sorted(e for e in events if 0 <= e[0] < total)
    keep, last = [], -9.0
    for e in events:                      # never two effects within 0.35 s
        if e[0] - last >= 0.35:
            keep.append(e)
            last = e[0]
    inputs, fc = ["-i", str(vo)], ["[0:a]loudnorm=I=-16:TP=-1.5:LRA=11,aformat=sample_rates=48000:channel_layouts=stereo[v]"]
    for i, (st, name, gain) in enumerate(keep):
        inputs += ["-i", str(SFX_DIR / f"{name}.wav")]
        ms = int(max(st, 0) * 1000)
        fc.append(f"[{i + 1}:a]volume={gain},adelay={ms}|{ms}[s{i}]")
    mix = "[v]" + "".join(f"[s{i}]" for i in range(len(keep)))
    fc.append(f"{mix}amix=inputs={len(keep) + 1}:normalize=0:duration=first[a]")
    run(["ffmpeg", "-v", "error", "-y", *inputs, "-filter_complex", ";".join(fc), "-map", "[a]", "-c:a", "aac", "-b:a", "192k",
         "-t", f"{total:.2f}", str(dst)])


if __name__ == "__main__":
    main()
