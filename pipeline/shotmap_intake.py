"""Intake for the "shot map" ad folders (batch 3 format): <Ad>/Original_Script_and_Shot_Map.md + Shot_NNN.png + References/.

For every ad: parse the shots (VO or on-screen text, reuse instructions, picture-in-picture edits, image prompt),
flag the talking-presenter shots, convert frames to Shot_NNN.jpg for the GPU box, and write jobs/<slug>/job.json.

  python pipeline/shotmap_intake.py ROOT_WITH_AD_FOLDERS FRAMES_OUT --batch batch-3 [--map Folder=slug ...]
"""

from __future__ import annotations

import argparse
import json
import re
from pathlib import Path

from PIL import Image

REPO = Path(__file__).resolve().parents[1]


def parse_shotmap(md: str) -> list[dict]:
    body = md[md.index("## Shots"):]
    parts = re.split(r"\n### Shot (\d+)\s*\n", body)
    shots = []
    for i in range(1, len(parts), 2):
        n, b = int(parts[i]), parts[i + 1]

        def field(k: str) -> str:
            m = re.search(rf"\*\*{k}:\*\*\s*(.*)", b)
            return m.group(1).strip() if m else ""

        frame = field("Frame")
        reuse = re.search(r"reuse shot (\d+)", frame)
        prompt = re.search(r"```\n(.*?)\n```", b, re.S)
        shots.append({"n": n, "vo": field("VO"), "on_screen": field("On screen"), "attach": field("Attach"),
                      "edit": field("Edit"), "reuse": int(reuse.group(1)) if reuse else None,
                      "prompt": prompt.group(1).strip() if prompt else ""})
    return shots


def presenter_ref(md: str, refs: list[str]) -> str | None:
    """The talking presenter's reference id (e.g. K1), if the ad asks for lip-sync."""
    if "lip-sync" not in md.lower():
        return None
    return refs[0].split(".")[0] if refs else None


def is_talking(prompt: str, pres: str | None) -> bool:
    return bool(pres) and bool(re.search(rf"from the {pres} reference", prompt)) \
        and bool(re.search(r"camera|talking|mouth|speak", prompt))


def spoken(vo: str) -> str:
    return "" if re.fullmatch(r"\*?\(?music only\)?\*?", vo.strip(), re.I) else vo


def build(ad_dir: Path, slug: str, frames_out: Path, batch: str) -> dict:
    md = (ad_dir / "Original_Script_and_Shot_Map.md").read_text()
    refs = sorted(p.name for p in (ad_dir / "References").glob("*"))
    pres = presenter_ref(md, refs)
    raw = parse_shotmap(md)
    by_n = {s["n"]: s for s in raw}
    (frames_out / slug).mkdir(parents=True, exist_ok=True)
    shots = []
    for s in raw:
        src = by_n[s["reuse"]] if s["reuse"] else s
        frame = f"Shot_{src['n']:03d}.jpg"
        png = ad_dir / f"Shot_{src['n']:03d}.png"
        dst = frames_out / slug / frame
        if not dst.exists():
            Image.open(png).convert("RGB").save(dst, quality=93)
        w, h = Image.open(dst).size
        shot = {"id": f"s{s['n']:03d}", "frame": frame, "vo_line": spoken(s["vo"]), "seed": 3000 + s["n"],
                "talking": is_talking(src["prompt"], pres), "image_prompt": src["prompt"]}
        if s["on_screen"]:
            shot["on_screen"] = s["on_screen"]
        if s["reuse"]:
            shot["reuse_of"] = f"s{s['reuse']:03d}"
        if "picture in picture" in s["edit"].lower():
            shot["pip"] = "box"
        if abs(w / h - 3 / 4) < 0.05:
            shot["render_size"] = [720, 960]
        shots.append(shot)
    job = {"job_id": f"{slug}-v1", "slug": slug, "folder": ad_dir.name, "batch": batch, "aspect_ratio": "9:16",
           "language": "en-GB", "render": {"width": 720, "height": 1280, "fps": 16, "interpolate_fps": 32},
           "frames_git": {"branch": "frames-store", "path": f"frames/{slug}"},
           "presenter_ref": pres, "shots": shots,
           "voice_notes": re.search(r"## Voice and captions\n(.*?)\n## ", md, re.S).group(1).strip()
           if "## Voice and captions" in md else ""}
    return job


def main() -> None:
    ap = argparse.ArgumentParser()
    ap.add_argument("root", type=Path)
    ap.add_argument("frames_out", type=Path)
    ap.add_argument("--batch", required=True)
    ap.add_argument("--map", nargs="*", default=[], help="Folder=slug")
    a = ap.parse_args()
    names = dict(m.split("=", 1) for m in a.map)
    for md in sorted(a.root.rglob("Original_Script_and_Shot_Map.md")):
        ad = md.parent
        slug = names.get(ad.name) or re.sub(r"[^a-z0-9]+", "-", ad.name.lower().removeprefix("uk_")).strip("-")
        job = build(ad, slug, a.frames_out, a.batch)
        jd = REPO / "jobs" / slug
        jd.mkdir(parents=True, exist_ok=True)
        path = jd / "job.json"
        if path.exists():  # keep edits made after a previous intake (motion prompts, timings, fixes)
            old = {s["id"]: s for s in json.loads(path.read_text())["shots"]}
            for s in job["shots"]:
                for k in ("motion_prompt", "num_frames", "vo_s", "clip_start", "clip_end", "fill_from", "clip_from"):
                    if k in old.get(s["id"], {}):
                        s[k] = old[s["id"]][k]
        path.write_text(json.dumps(job, indent=2, ensure_ascii=False))
        t = sum(s["talking"] for s in job["shots"])
        print(f"{slug}: {len(job['shots'])} shots, {t} talking, presenter {job['presenter_ref']}, "
              f"{len(list((a.frames_out / slug).glob('*.jpg')))} frames")


if __name__ == "__main__":
    main()
