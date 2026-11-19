"""Expert inset over a finished 9:16 ad while b-roll plays (testimonial/explainer/green-screen-reply references).
Spans come from the editor's report (shot ids -> times); the talking clip loops.
  round  : the clip in a white-ringed circle in the lower left (default)
  cutout : a green-screen clip keyed out (chromakey + despill), bottom-anchored, lower middle or lower left

  python assembly/pip_inset.py VIDEO CLIP REPORT.json SHOT_IDS(comma) OUT [--size 0.30] [--y 0.745]
  python assembly/pip_inset.py ... --mode cutout --height 0.46 --x center [--keep 1.0]
"""
from __future__ import annotations

import argparse
import json
import subprocess
from pathlib import Path


def spans_for(report: dict, ids: set[str], merge_gap: float = 0.25) -> list[tuple[float, float]]:
    out = []
    for s in report["shots"]:
        if s["id"] in ids:
            a, b = float(s["start"]), float(s["start"]) + float(s["dur"])
            if out and a - out[-1][1] <= merge_gap:
                out[-1] = (out[-1][0], b)
            else:
                out.append((a, b))
    return [(a, b) for a, b in out if b - a >= 0.8]


def probe_size(clip: Path) -> tuple[int, int]:
    out = subprocess.run(["ffprobe", "-v", "error", "-select_streams", "v:0", "-show_entries", "stream=width,height",
                          "-of", "csv=p=0", str(clip)], capture_output=True, text=True, check=True).stdout.strip()
    w, h = out.split(",")[:2]
    return int(w), int(h)


def key_colour(clip: Path) -> str:
    """The backdrop's green, sampled from the clip's top corners (median of two 24x24 patches)."""
    import io
    from PIL import Image
    raw = subprocess.run(["ffmpeg", "-v", "error", "-ss", "0.5", "-i", str(clip), "-frames:v", "1", "-f", "image2pipe",
                          "-vcodec", "png", "-"], capture_output=True, check=True).stdout
    im = Image.open(io.BytesIO(raw)).convert("RGB")
    px = [im.getpixel((x, y)) for x0 in (4, im.width - 28) for x in range(x0, x0 + 24, 4) for y in range(4, 28, 4)]
    med = [sorted(c[i] for c in px)[len(px) // 2] for i in range(3)]
    return "".join(f"{v:02X}" for v in med)


def main() -> None:
    ap = argparse.ArgumentParser()
    ap.add_argument("video", type=Path)
    ap.add_argument("clip", type=Path)
    ap.add_argument("report", type=Path)
    ap.add_argument("ids")
    ap.add_argument("out", type=Path)
    ap.add_argument("--size", type=float, default=0.30)
    ap.add_argument("--y", type=float, default=0.745)
    ap.add_argument("--face_y", type=float, default=0.10, help="top of the square crop in the clip (fraction of height)")
    ap.add_argument("--mode", choices=["round", "cutout"], default="round")
    ap.add_argument("--height", type=float, default=0.46, help="cutout: height as a fraction of the frame")
    ap.add_argument("--x", choices=["center", "left", "right"], default="center", help="cutout: horizontal position")
    ap.add_argument("--keep", type=float, default=1.0, help="cutout: top fraction of the clip kept (drop a desk)")
    a = ap.parse_args()
    rep = json.loads(a.report.read_text())
    spans = spans_for(rep, set(a.ids.split(",")))
    if not spans:
        subprocess.run(["cp", str(a.video), str(a.out)], check=True)
        print("no spans")
        return
    W, H = 1080, 1920
    total = subprocess.run(["ffprobe", "-v", "error", "-show_entries", "format=duration", "-of", "csv=p=0", str(a.video)],
                           capture_output=True, text=True, check=True).stdout.strip()
    en = "+".join(f"between(t,{s:.2f},{e:.2f})" for s, e in spans)
    if a.mode == "cutout":
        key = key_colour(a.clip)
        h = int(H * a.height) // 2 * 2
        cw, ch = probe_size(a.clip)
        keep_h = int(ch * a.keep) // 2 * 2
        w = int(round(cw * h / keep_h / 2)) * 2
        x = {"center": (W - w) // 2, "left": 24, "right": W - w - 24}[a.x]
        fc = (f"[1:v]crop={cw}:{keep_h}:0:0,chromakey=color=0x{key}:similarity=0.14:blend=0.07,despill=type=green,"
              f"scale={w}:{h},format=yuva420p[cut];[0:v][cut]overlay=x={x}:y={H - h}:enable='{en}'[v]")
        subprocess.run(["ffmpeg", "-v", "error", "-y", "-i", str(a.video), "-stream_loop", "-1", "-i", str(a.clip),
                        "-filter_complex", fc, "-map", "[v]", "-map", "0:a?", "-c:v", "libx264", "-preset", "medium", "-crf", "20",
                        "-pix_fmt", "yuv420p", "-c:a", "copy", "-t", total, str(a.out)], check=True)
        print("cutout spans", len(spans), round(sum(e - s for s, e in spans), 1), "s", "key", key)
        return
    d = int(W * a.size) // 2 * 2
    ring = d + 12
    x, y = 46, int(H * a.y)
    fade = "+".join(f"between(t,{s:.2f},{e:.2f})" for s, e in spans)
    fc = (f"[1:v]crop=iw:iw:0:ih*{a.face_y},scale={d}:{d},format=yuva420p,"
          f"geq=lum='p(X,Y)':cb='p(X,Y)':cr='p(X,Y)':a='if(lte(hypot(X-{d / 2},Y-{d / 2}),{d / 2 - 1}),255,0)'[pip];"
          f"color=c=white:s={ring}x{ring},format=yuva420p,geq=lum='255':cb='128':cr='128':"
          f"a='if(lte(hypot(X-{ring / 2},Y-{ring / 2}),{ring / 2 - 1}),255,0)'[ring];"
          f"[0:v][ring]overlay=x={x - 6}:y={y - 6}:enable='{en}'[b1];[b1][pip]overlay=x={x}:y={y}:enable='{fade}'[v]")
    # the output is cut at the ad's own length: -shortest ended it ~0.7 s early (looped clip + endless colour source)
    subprocess.run(["ffmpeg", "-v", "error", "-y", "-i", str(a.video), "-stream_loop", "-1", "-i", str(a.clip),
                    "-filter_complex", fc, "-map", "[v]", "-map", "0:a?", "-c:v", "libx264", "-preset", "medium", "-crf", "20",
                    "-pix_fmt", "yuv420p", "-c:a", "copy", "-t", total, str(a.out)], check=True)
    print("pip spans", len(spans), round(sum(e - s for s, e in spans), 1), "s")


if __name__ == "__main__":
    main()
