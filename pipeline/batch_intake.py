"""Build one job.json per ad folder from a batch zip: frames Shot_NNN.* in order + the numbered UK script lines.

Each script has a '## UK script' section with numbered lines, one line per shot (shot numbers = line numbers).
Speaker headers inside that section (e.g. '**The tooth**', '**Meow Mouth**') are kept per line.

  python pipeline/batch_intake.py EXTRACTED_DIR RELEASE_TAG ASSET_NAME OUT_JOBS_DIR
"""

from __future__ import annotations

import json
import re
import sys
from pathlib import Path

LINE = re.compile(r"^\s*(\d+)\.\s+(.*\S)\s*$")
SPEAKER = re.compile(r"^\s*\*\*([^*]+)\*\*\s*$")
SHOT = re.compile(r"shot[_\- ]?0*(\d+)", re.I)


def slugify(name: str) -> str:
    return re.sub(r"[^a-z0-9]+", "-", name.lower()).strip("-")


def script_lines(md: str) -> list[dict]:
    start = md.index("## UK script")
    end = md.find("\n## ", start + 5)
    lines, speaker = [], None
    for raw in md[start:end if end > 0 else None].splitlines():
        if m := SPEAKER.match(raw):
            speaker = m.group(1).strip()
        elif m := LINE.match(raw):
            lines.append({"n": int(m.group(1)), "text": m.group(2), "speaker": speaker})
    return lines


def main(src: str, tag: str, asset: str, out: str) -> None:
    for folder in sorted(p for p in Path(src).iterdir() if p.is_dir()):
        mds = list(folder.glob("*.md"))
        frames = sorted((p for p in folder.iterdir() if SHOT.search(p.stem) and p.suffix.lower() in {".png", ".jpg"}),
                        key=lambda p: int(SHOT.search(p.stem).group(1)))
        lines = script_lines(mds[0].read_text()) if mds else []
        slug = slugify(folder.name)
        ok = len(lines) == len(frames) and [l["n"] for l in lines] == list(range(1, len(frames) + 1))
        shots = [{"id": f"s{l['n']:03d}", "frame": f.name, "vo_line": l["text"], "speaker": l["speaker"],
                  "motion_prompt": "", "seed": 1000 + l["n"]} for l, f in zip(lines, frames)]
        job = {"job_id": f"{slug}-v1", "slug": slug, "folder": folder.name, "script_file": mds[0].name if mds else None,
               "aspect_ratio": "9:16", "language": "en-GB",
               "render": {"width": 720, "height": 1280, "fps": 16, "interpolate_fps": 32},
               "frames_release": {"repo": "otakubooster/ai-animation-pipeline", "tag": tag, "asset": asset,
                                  "subdir": folder.name},
               "shots": shots}
        d = Path(out) / slug
        d.mkdir(parents=True, exist_ok=True)
        (d / "job.json").write_text(json.dumps(job, indent=2, ensure_ascii=False))
        speakers = sorted({str(l["speaker"]) for l in lines})
        print(f"{slug:24} frames={len(frames):3} lines={len(lines):3} match={ok} speakers={speakers}")


if __name__ == "__main__":
    main(*sys.argv[1:5])
