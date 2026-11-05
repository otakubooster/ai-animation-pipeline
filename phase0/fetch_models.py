"""Discover and download model files from Hugging Face on the GPU box, timing each download.

File names are discovered at runtime (and logged) instead of hard-coded, because this session
could not reach huggingface.co to confirm them. Writes results/models.json with chosen paths.

Usage: python fetch_models.py engine_a|engine_a_distill|engine_b
"""

from __future__ import annotations

import json
import sys
import time
from pathlib import Path

from huggingface_hub import HfApi, hf_hub_download, snapshot_download

from common import RESULTS, write_json

MODELS = Path("/workspace/models")
api = HfApi()
MANIFEST = RESULTS / "models.json"


def manifest() -> dict:
    return json.loads(MANIFEST.read_text()) if MANIFEST.exists() else {"downloads": [], "repo_files": {}}


def files(repo: str, m: dict) -> list[str]:
    fs = api.list_repo_files(repo)
    m["repo_files"][repo] = fs
    return fs


def pick(fs: list[str], must: list[str], prefer: list[str] = (), avoid: list[str] = ()) -> str:
    cands = [f for f in fs if f.endswith((".safetensors", ".pth"))
             and all(k in f.lower() for k in must) and not any(a in f.lower() for a in avoid)]
    if not cands:
        raise FileNotFoundError(f"no file matching {must} (avoid {avoid})")
    cands.sort(key=lambda f: (-sum(p in f.lower() for p in prefer), len(f)))
    return cands[0]


def get(repo: str, filename: str, m: dict, dest: Path) -> str:
    t = time.perf_counter()
    p = hf_hub_download(repo, filename, local_dir=dest)
    m["downloads"].append({"repo": repo, "file": filename, "seconds": round(time.perf_counter() - t, 1),
                           "gb": round(Path(p).stat().st_size / 1e9, 2)})
    write_json(MANIFEST, m)
    return p


def engine_a(m: dict) -> None:
    base = MODELS / "Wan2.2-I2V-A14B"
    t = time.perf_counter()
    snapshot_download("Wan-AI/Wan2.2-I2V-A14B", local_dir=base,
                      allow_patterns=["*.json", "*.pth", "google/*", "*.txt", "*.model"])
    m["downloads"].append({"repo": "Wan-AI/Wan2.2-I2V-A14B", "file": "base (VAE, T5, tokenizer, configs)",
                           "seconds": round(time.perf_counter() - t, 1)})
    sel = {"model_path": str(base)}
    for repo in ("lightx2v/LightWan2.2-A14B", "lightx2v/Wan2.2-NVFP4-Sparse"):
        try:
            fs = files(repo, m)
            hi = pick(fs, ["i2v", "high"], prefer=["nvfp4", "fp4"], avoid=["t2v"])
            lo = pick(fs, ["i2v", "low"], prefer=["nvfp4", "fp4"], avoid=["t2v"])
            sel["high"], sel["low"] = get(repo, hi, m, MODELS / repo), get(repo, lo, m, MODELS / repo)
            sel["repo"] = repo
            break
        except Exception as exc:
            m.setdefault("errors", []).append(f"{repo}: {exc}")
    m["engine_a"] = sel
    write_json(MANIFEST, m)


def engine_a_distill(m: dict) -> None:
    repo = "lightx2v/Wan2.2-Distill-Models"
    fs = files(repo, m)
    hi = pick(fs, ["i2v", "high", "4step"], avoid=["t2v", "fp8", "int8", "nvfp4"])
    lo = pick(fs, ["i2v", "low", "4step"], avoid=["t2v", "fp8", "int8", "nvfp4"])
    m["engine_a_distill"] = {"high": get(repo, hi, m, MODELS / repo), "low": get(repo, lo, m, MODELS / repo)}
    write_json(MANIFEST, m)


def engine_b(m: dict, comfy: Path) -> None:
    sel = {}
    targets = {
        "high": ("diffusion_models", ["i2v", "high_noise", "fp8"], [], ["t2v"]),
        "low": ("diffusion_models", ["i2v", "low_noise", "fp8"], [], ["t2v"]),
        "lora_high": ("loras", ["i2v", "lightx2v", "high"], ["4step"], ["t2v"]),
        "lora_low": ("loras", ["i2v", "lightx2v", "low"], ["4step"], ["t2v"]),
        "t5": ("text_encoders", ["umt5", "fp8"], [], []),
        "vae": ("vae", ["wan_2.1_vae"], [], []),
    }
    repos = ["Comfy-Org/Wan_2.2_ComfyUI_Repackaged", "Comfy-Org/Wan_2.1_ComfyUI_repackaged"]
    listing = {r: files(r, m) for r in repos}
    for key, (folder, must, prefer, avoid) in targets.items():
        for repo in repos:
            try:
                f = pick([x for x in listing[repo] if f"/{folder}/" in f"/{x}"], must, prefer, avoid)
            except FileNotFoundError:
                continue
            p = Path(get(repo, f, m, MODELS / repo))
            link = comfy / "models" / folder / p.name
            link.parent.mkdir(parents=True, exist_ok=True)
            if not link.exists():
                link.symlink_to(p)
            sel[key] = p.name
            break
        else:
            raise FileNotFoundError(f"engine B: nothing found for {key}")
    m["engine_b"] = sel
    write_json(MANIFEST, m)


if __name__ == "__main__":
    which = sys.argv[1]
    m = manifest()
    if which == "engine_a":
        engine_a(m)
    elif which == "engine_a_distill":
        engine_a_distill(m)
    elif which == "engine_b":
        engine_b(m, Path(sys.argv[2]))
    print(json.dumps({k: v for k, v in m.items() if k != "repo_files"}, indent=2))
