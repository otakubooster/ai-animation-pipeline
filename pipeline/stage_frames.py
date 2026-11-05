"""Stage an ad's frames for the GPU box without the user touching GitHub.

Unzips the user's upload, keeps the shot frames (Shot_01.png ...), converts them to high-quality JPEG at full
resolution (about 10x smaller, no visible loss at render size) and commits them to the `frames-store` branch
under frames/<slug>/ using a separate worktree, then pushes. Prints the frame list and any script file found.

  python pipeline/stage_frames.py UPLOAD.zip SLUG [--no-push]
"""

from __future__ import annotations

import argparse
import json
import re
import shutil
import subprocess
import tempfile
import zipfile
from pathlib import Path

from PIL import Image

REPO = Path(__file__).resolve().parents[1]
BRANCH = "frames-store"
SHOT_RE = re.compile(r"shot[_\- ]?0*(\d+)", re.I)
SCRIPT_EXT = {".md", ".txt", ".docx", ".pdf"}


def git(*args, cwd=REPO, check=True):
    return subprocess.run(["git", *args], cwd=cwd, check=check, capture_output=True, text=True)


def extract(zip_path: Path, dst: Path) -> tuple[list[Path], list[Path]]:
    with zipfile.ZipFile(zip_path) as z:
        z.extractall(dst)
    files = [p for p in dst.rglob("*") if p.is_file() and not p.name.startswith(("._", "."))]
    shots = sorted((p for p in files if p.suffix.lower() in {".png", ".jpg", ".jpeg", ".webp"} and SHOT_RE.search(p.stem)),
                   key=lambda p: int(SHOT_RE.search(p.stem).group(1)))
    scripts = [p for p in files if p.suffix.lower() in SCRIPT_EXT]
    return shots, scripts


def main() -> None:
    ap = argparse.ArgumentParser()
    ap.add_argument("zip", type=Path)
    ap.add_argument("slug")
    ap.add_argument("--no-push", action="store_true")
    a = ap.parse_args()

    tmp = Path(tempfile.mkdtemp(prefix=f"stage-{a.slug}-"))
    shots, scripts = extract(a.zip, tmp / "unzipped")
    if not shots:
        raise SystemExit("No shot frames found (expected names like Shot_01.png).")

    wt = tmp / "worktree"
    git("fetch", "-q", "origin", BRANCH, check=False)
    if git("rev-parse", "--verify", "-q", f"origin/{BRANCH}", check=False).returncode == 0:
        git("worktree", "add", "-q", "-B", BRANCH, str(wt), f"origin/{BRANCH}")
    elif git("rev-parse", "--verify", "-q", BRANCH, check=False).returncode == 0:
        git("worktree", "add", "-q", str(wt), BRANCH)
    else:
        git("worktree", "add", "-q", "--detach", str(wt))
        git("checkout", "-q", "--orphan", BRANCH, cwd=wt)
        git("rm", "-rq", "--cached", ".", cwd=wt, check=False)
        for p in wt.iterdir():
            if p.name != ".git":
                shutil.rmtree(p) if p.is_dir() else p.unlink()
    out = wt / "frames" / a.slug
    out.mkdir(parents=True, exist_ok=True)
    names = []
    for p in shots:
        n = int(SHOT_RE.search(p.stem).group(1))
        name = f"Shot_{n:02d}.jpg"
        Image.open(p).convert("RGB").save(out / name, quality=95, subsampling=0)
        names.append(name)
    git("add", "-A", "frames", cwd=wt)
    git("-c", "user.name=ad-factory", "-c", "user.email=ad-factory@localhost", "commit", "-qm", f"frames: {a.slug}",
        cwd=wt, check=False)
    size_mb = round(sum((out / n).stat().st_size for n in names) / 1e6, 1)
    pushed = None
    if not a.no_push:
        pushed = git("push", "-q", "origin", f"HEAD:{BRANCH}", cwd=wt, check=False).returncode == 0
    git("worktree", "remove", "--force", str(wt), check=False)
    print(json.dumps({"slug": a.slug, "frames": names, "frames_dir": str(tmp / "unzipped"),
                      "scripts": [str(p) for p in scripts], "pushed": pushed, "size_mb": size_mb},
                     indent=2))


if __name__ == "__main__":
    main()
