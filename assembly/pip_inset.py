"""Round 'expert inset' over a finished 9:16 ad: the speaker's talking clip loops in a circle in the lower left while
b-roll plays (testimonial/explainer references). Spans come from the editor's report (shot ids -> times).

  python assembly/pip_inset.py VIDEO CLIP REPORT.json SHOT_IDS(comma) OUT [--size 0.30] [--y 0.745]
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
    a = ap.parse_args()
    rep = json.loads(a.report.read_text())
    spans = spans_for(rep, set(a.ids.split(",")))
    if not spans:
        subprocess.run(["cp", str(a.video), str(a.out)], check=True)
        print("no spans")
        return
    W, H = 1080, 1920
    d = int(W * a.size) // 2 * 2
    ring = d + 12
    x, y = 46, int(H * a.y)
    en = "+".join(f"between(t,{s:.2f},{e:.2f})" for s, e in spans)
    fade = "+".join(f"between(t,{s:.2f},{e:.2f})" for s, e in spans)
    fc = (f"[1:v]crop=iw:iw:0:ih*{a.face_y},scale={d}:{d},format=yuva420p,"
          f"geq=lum='p(X,Y)':cb='p(X,Y)':cr='p(X,Y)':a='if(lte(hypot(X-{d / 2},Y-{d / 2}),{d / 2 - 1}),255,0)'[pip];"
          f"color=c=white:s={ring}x{ring},format=yuva420p,geq=lum='255':cb='128':cr='128':"
          f"a='if(lte(hypot(X-{ring / 2},Y-{ring / 2}),{ring / 2 - 1}),255,0)'[ring];"
          f"[0:v][ring]overlay=x={x - 6}:y={y - 6}:enable='{en}'[b1];[b1][pip]overlay=x={x}:y={y}:enable='{fade}'[v]")
    # the output is cut at the ad's own length: -shortest ended it ~0.7 s early (looped clip + endless colour source)
    total = subprocess.run(["ffprobe", "-v", "error", "-show_entries", "format=duration", "-of", "csv=p=0", str(a.video)],
                           capture_output=True, text=True, check=True).stdout.strip()
    subprocess.run(["ffmpeg", "-v", "error", "-y", "-i", str(a.video), "-stream_loop", "-1", "-i", str(a.clip),
                    "-filter_complex", fc, "-map", "[v]", "-map", "0:a?", "-c:v", "libx264", "-preset", "medium", "-crf", "20",
                    "-pix_fmt", "yuv420p", "-c:a", "copy", "-t", total, str(a.out)], check=True)
    print("pip spans", len(spans), round(sum(e - s for s, e in spans), 1), "s")


if __name__ == "__main__":
    main()
