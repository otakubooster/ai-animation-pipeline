"""Turn phase0/frames/cases.json (+ images) into /workspace/spike/cases.json with frames
resized to the exact render size (cover-fit, centre crop), so both engines get identical inputs.

Falls back to LightX2V's bundled sample images if no test frames were committed.
"""

from __future__ import annotations

import json
import sys
from pathlib import Path

from PIL import Image

from common import CASES_FILE, HERE

SIZES = {"9:16": (480, 832), "16:9": (832, 480), "1:1": (624, 624)}  # (width, height), multiples of 16
OUT = Path("/workspace/spike/frames")


def fit(src: Path, dst: Path, w: int, h: int) -> str:
    im = Image.open(src).convert("RGB")
    scale = max(w / im.width, h / im.height)
    im = im.resize((round(im.width * scale), round(im.height * scale)), Image.LANCZOS)
    left, top = (im.width - w) // 2, (im.height - h) // 2
    im.crop((left, top, left + w, top + h)).save(dst)
    return str(dst)


def main(lightx2v_dir: str) -> None:
    OUT.mkdir(parents=True, exist_ok=True)
    spec = HERE / "frames" / "cases.json"
    if spec.exists():
        cases, root = json.loads(spec.read_text()), spec.parent
        source = "user frames"
    else:
        root = Path(lightx2v_dir) / "assets" / "inputs" / "imgs"
        source = "LightX2V sample images (no user frames committed)"
        cases = [
            {"id": "s1", "aspect": "16:9", "first": "flf2v_input_first_frame-fs8.png",
             "last": "flf2v_input_last_frame-fs8.png",
             "motion_prompt": "the bird takes off and flies upward, camera follows smoothly"},
            {"id": "s2", "aspect": "16:9", "first": "img_0.jpg", "motion_prompt": "the cat turns its head and looks at the sea, gentle breeze, slow push-in"},
            {"id": "s3", "aspect": "9:16", "first": "girl.png", "motion_prompt": "she smiles and turns her head slightly, hair moves in the wind, slow push-in"},
            {"id": "s4", "aspect": "16:9", "first": "img_1.jpg", "motion_prompt": "slow pan to the right, subtle natural motion"},
            {"id": "s5", "aspect": "16:9", "first": "img_2.jpg", "motion_prompt": "camera slowly orbits left, subject moves naturally"},
        ]
    out = []
    for c in cases:
        w, h = SIZES[c.get("aspect", "9:16")]
        item = {"id": c["id"], "width": w, "height": h, "seed": c.get("seed", 42),
                "motion_prompt": c["motion_prompt"],
                "first": fit(root / c["first"], OUT / f"{c['id']}_first.png", w, h)}
        if c.get("last"):
            item["last"] = fit(root / c["last"], OUT / f"{c['id']}_last.png", w, h)
        out.append(item)
    CASES_FILE.write_text(json.dumps(out, indent=2))
    print(f"{len(out)} cases from {source}")


if __name__ == "__main__":
    main(sys.argv[1])
