"""Download the InfiniteTalk lip-sync weights on the GPU box (file names discovered at runtime and logged,
because this session cannot reach huggingface.co). Writes /workspace/spike/results/talk_models.json.

Layout expected by LightX2V's configs/infinitetalk/5090/infinitetalk_single_distilled.json:
  base     Wan-AI/Wan2.1-I2V-14B-480P  (T5, CLIP, VAE, tokenizers)  -> model_path
  dit      InfiniteTalk-4StepDistill-...(fp8|bf16).safetensors     -> dit_quantized_ckpt / dit_original_ckpt
  adapter  single .../infinitetalk(-fp8).safetensors               -> adapter_model_path
  audio    TencentGameMate/chinese-wav2vec2-base                   -> audio_encoder_path
"""

from __future__ import annotations

import json
import sys
import time
from pathlib import Path

from huggingface_hub import HfApi, hf_hub_download, snapshot_download

MODELS = Path("/workspace/models")
OUT = Path("/workspace/spike/results/talk_models.json")
api = HfApi()
m: dict = {"downloads": [], "repo_files": {}, "candidates": []}


def save() -> None:
    OUT.parent.mkdir(parents=True, exist_ok=True)
    OUT.write_text(json.dumps(m, indent=1))


def timed(label: str, fn):
    t = time.perf_counter()
    r = fn()
    m["downloads"].append({"what": label, "seconds": round(time.perf_counter() - t, 1)})
    save()
    return r


def main() -> None:
    repos: set[str] = set()
    for kw in dict(author="lightx2v", search="Talk"), dict(author="lightx2v", search="talk"), dict(author="MeiGen-AI"), \
            dict(search="InfiniteTalk"), dict(author="lightx2v", search="Seko"):
        try:
            repos |= {x.id for x in api.list_models(limit=100, **kw)}
        except Exception as e:  # noqa: BLE001
            m.setdefault("errors", []).append(f"list {kw}: {e}")
    m["candidates"] = sorted(repos)
    dit = adapter = None
    for r in sorted(repos):
        try:
            fs = api.list_repo_files(r)
        except Exception as e:  # noqa: BLE001
            m.setdefault("errors", []).append(f"files {r}: {e}")
            continue
        m["repo_files"][r] = [f for f in fs if f.endswith((".safetensors", ".pth", ".json", ".bin"))][:300]
        for f in fs:
            fl = f.lower()
            if "4stepdistill" in fl and "infinitetalk" in fl and fl.endswith(".safetensors"):
                score = (2 if "fp8" in fl else 1)
                if not dit or score > dit[2]:
                    dit = (r, f, score)
            if fl.endswith(".safetensors") and "infinitetalk" in fl and "single" in fl and "4step" not in fl:
                score = (2 if "fp8" in fl else 1) + (1 if r.startswith("lightx2v") else 0)
                if not adapter or score > adapter[2]:
                    adapter = (r, f, score)
    # The 4-step InfiniteTalk DiT that LightX2V's 5090 config names is not public. Fallback: LightX2V's own
    # 4-step distilled Wan2.1 I2V 480P DiT (the base InfiniteTalk is built on) + MeiGen's single-person adapter.
    if not dit:
        for r in ("lightx2v/Wan2.1-Distill-Models", "lightx2v/Wan2.1-I2V-14B-480P-StepDistill-CfgDistill-Lightx2v"):
            try:
                fs = [f for f in api.list_repo_files(r) if f.endswith(".safetensors")]
            except Exception as e:  # noqa: BLE001
                m.setdefault("errors", []).append(f"files {r}: {e}")
                continue
            m["repo_files"][r] = fs
            c = [f for f in fs if "i2v" in f.lower() and "480" in f.lower() and "lora" not in f.lower()] or \
                [f for f in fs if "lora" not in f.lower()]
            # Unquantized first: the public InfiniteTalk adapter is bf16, and LightX2V needs the adapter in the same
            # quant scheme as the DiT (fp8 DiT + bf16 adapter rendered black frames, 2026-11-07).
            c.sort(key=lambda f: (1 if ("fp8" in f.lower() or "int8" in f.lower() or "comfyui" in f.lower()) else 0, len(f)))
            if c:
                dit = (r, c[0], 0)
                m["dit_fallback"] = True
                break
    adapter_bf16 = "MeiGen-AI/InfiniteTalk" in m["repo_files"] and "single/infinitetalk.safetensors" in m["repo_files"]["MeiGen-AI/InfiniteTalk"]
    if m.get("dit_fallback") and adapter_bf16:
        adapter = ("MeiGen-AI/InfiniteTalk", "single/infinitetalk.safetensors", 0)
    save()
    if not dit or not adapter:
        m["error"] = f"missing dit={dit} adapter={adapter}"
        save()
        sys.exit(2)
    m["dit_repo"], m["dit_file"] = dit[0], dit[1]
    m["adapter_repo"], m["adapter_file"] = adapter[0], adapter[1]
    save()
    base = MODELS / "Wan2.1-I2V-14B-480P"
    timed("base", lambda: snapshot_download("Wan-AI/Wan2.1-I2V-14B-480P", local_dir=base, allow_patterns=[
        "*.json", "models_t5*.pth", "models_clip*.pth", "Wan2.1_VAE.pth", "google/*", "xlm-roberta-large/*"]))
    dp = timed("dit", lambda: hf_hub_download(dit[0], dit[1], local_dir=MODELS / "talk"))
    ap = timed("adapter", lambda: hf_hub_download(adapter[0], adapter[1], local_dir=MODELS / "talk"))
    w2v = MODELS / "chinese-wav2vec2-base"
    timed("wav2vec", lambda: snapshot_download("TencentGameMate/chinese-wav2vec2-base", local_dir=w2v))
    m.update(model_path=str(base), dit=dp, dit_fp8="fp8" in dit[1].lower(), adapter=ap,
             adapter_fp8="fp8" in adapter[1].lower(), audio_encoder=str(w2v), dit_fallback=bool(m.get("dit_fallback")),
             sizes_gb={k: round(Path(v).stat().st_size / 1e9, 2) for k, v in (("dit", dp), ("adapter", ap))})
    save()
    print(json.dumps({k: m[k] for k in ("dit", "adapter", "model_path")}))


if __name__ == "__main__":
    main()
