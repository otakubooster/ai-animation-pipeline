"""Check and repair an ad's frames before rendering, writing a clean 9:16 JPEG set.

Problems seen in real uploads: PNGs truncated during export (bottom of the image is black) and frames that are
not 9:16. Rules (cheapest fix that keeps the ad complete):
  - damage <= MAX_CROP of the height: keep the intact top part and cover-crop it back to 9:16 (slight zoom)
  - shot that reuses another shot's frame by design (`reuse_map`): copy the intact source frame
  - damage > MAX_CROP: not rendered; the shot gets `fill_from` = previous shot, so the editor continues that clip
  - wrong aspect ratio: centre cover-crop to 9:16
Updates job.json in place (frame names -> Shot_NNN.jpg, skip_render / fill_from, frame_repair notes).

  python pipeline/repair_frames.py JOB_JSON SRC_DIR OUT_DIR [--reuse 25:20,36:20]
"""

from __future__ import annotations

import argparse
import json
from pathlib import Path

from PIL import Image, ImageFile

TARGET = 9 / 16
MAX_CROP = 0.25
OUT_W, OUT_H = 941, 1672


def load(path: Path) -> tuple[Image.Image, bool]:
    try:
        im = Image.open(path)
        im.load()
        return im.convert("RGB"), False
    except OSError:
        ImageFile.LOAD_TRUNCATED_IMAGES = True
        try:
            im = Image.open(path)
            im.load()
            return im.convert("RGB"), True
        finally:
            ImageFile.LOAD_TRUNCATED_IMAGES = False


def black_bottom(im: Image.Image) -> float:
    g = im.convert("L")
    w, h = g.size
    y = h - 1
    while y >= 0 and g.crop((0, y, w, y + 1)).getextrema()[1] <= 3:
        y -= 1
    return (h - 1 - y) / h


def cover_916(im: Image.Image) -> Image.Image:
    w, h = im.size
    if w / h > TARGET:
        nw = round(h * TARGET)
        im = im.crop(((w - nw) // 2, 0, (w - nw) // 2 + nw, h))
    else:
        nh = round(w / TARGET)
        im = im.crop((0, (h - nh) // 2, w, (h - nh) // 2 + nh))
    return im.resize((OUT_W, OUT_H), Image.LANCZOS)


def main() -> None:
    ap = argparse.ArgumentParser()
    ap.add_argument("job_json", type=Path)
    ap.add_argument("src", type=Path)
    ap.add_argument("out", type=Path)
    ap.add_argument("--reuse", default="", help="shot:source pairs, e.g. 25:20,36:20")
    a = ap.parse_args()
    reuse = {int(k): int(v) for k, v in (p.split(":") for p in a.reuse.split(",") if p)}
    job = json.loads(a.job_json.read_text())
    a.out.mkdir(parents=True, exist_ok=True)
    by_num = {int(s["id"][1:]): s for s in job["shots"]}
    orig = {n: s["frame"] for n, s in by_num.items()}   # source names before renaming to .jpg
    report = []
    for num, shot in sorted(by_num.items()):
        src = a.src / orig[num]
        im, truncated = load(src)
        dmg = black_bottom(im) if truncated else 0.0
        note = None
        if dmg > 0.01 and num in reuse:
            im, _ = load(a.src / orig[reuse[num]])
            note = f"damaged {dmg:.0%}: used shot {reuse[num]}'s frame (reused by design)"
        elif dmg > MAX_CROP:
            prev = max((n for n in by_num if n < num and not by_num[n].get("skip_render")), default=None)
            nxt = min((n for n in by_num if n > num), default=None)
            shot["skip_render"] = True
            shot["fill_from"] = by_num[prev]["id"] if prev else by_num[nxt]["id"]
            note = f"damaged {dmg:.0%}: not rendered, {shot['fill_from']} continues through this line"
        elif dmg > 0.01:
            im = im.crop((0, 0, im.width, int(im.height * (1 - dmg)) - 2))
            note = f"damaged {dmg:.0%}: cropped to the intact part"
        if im.size != (OUT_W, OUT_H) or note:
            if not note:
                note = f"{im.width}x{im.height}: cover-cropped to 9:16"
            im = cover_916(im)
        name = f"Shot_{num:03d}.jpg"
        if not shot.get("skip_render"):
            im.save(a.out / name, quality=95, subsampling=0)
        shot["frame"] = name
        if note:
            shot["frame_repair"] = note
            report.append(f"{shot['id']}: {note}")
    a.job_json.write_text(json.dumps(job, indent=2, ensure_ascii=False))
    print(f"{job['job_id']}: {len(report)} frames fixed")
    for r in report:
        print("  " + r)


if __name__ == "__main__":
    main()
