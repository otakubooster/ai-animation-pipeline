"""QA contact sheets: start / middle / end frame of every rendered shot, 10 shots per sheet.

The reviewing Claude session looks at these to catch subjects leaving the frame, warped faces or labels,
and character drift. Fixes: set `clip_start` on the shot (use a clean part of the clip) or re-render it.

  python pipeline/qa_sheet.py CLIPS_DIR OUT_DIR
"""

from __future__ import annotations

import subprocess
import sys
from pathlib import Path

from PIL import Image, ImageDraw


def frame_count(f: Path) -> int:
    out = subprocess.run(["ffprobe", "-v", "error", "-count_frames", "-select_streams", "v:0", "-show_entries",
                          "stream=nb_read_frames", "-of", "csv=p=0", str(f)], capture_output=True, text=True).stdout
    return int(out.strip())


def main(clips: str, out: str, per_sheet: int = 10, thumb_w: int = 180) -> None:
    src, dst = Path(clips), Path(out)
    dst.mkdir(parents=True, exist_ok=True)
    files = sorted(src.glob("s*.mp4"))
    rows = []
    for f in files:
        n = frame_count(f)
        thumbs = []
        for k, fr in enumerate([8, n // 2, n - 2]):
            p = dst / f"_{f.stem}_{k}.png"
            subprocess.run(["ffmpeg", "-v", "error", "-y", "-i", str(f), "-vf", f"select=eq(n\\,{fr}),scale={thumb_w}:-2",
                            "-frames:v", "1", str(p)], check=True)
            thumbs.append(Image.open(p))
        rows.append((f.stem, thumbs))
    for part in range(0, len(rows), per_sheet):
        chunk = rows[part:part + per_sheet]
        w, h = chunk[0][1][0].size
        sheet = Image.new("RGB", (3 * w + 70, len(chunk) * (h + 6)), "white")
        d = ImageDraw.Draw(sheet)
        for i, (name, thumbs) in enumerate(chunk):
            y = i * (h + 6)
            d.text((6, y + h // 2), name, fill="black")
            for k, im in enumerate(thumbs):
                sheet.paste(im, (64 + k * w, y))
        sheet.save(dst / f"qa_{part // per_sheet + 1}.jpg", quality=82)
    for p in dst.glob("_*.png"):
        p.unlink()
    print(f"{len(rows)} shots -> {dst}")


if __name__ == "__main__":
    main(sys.argv[1], sys.argv[2])
