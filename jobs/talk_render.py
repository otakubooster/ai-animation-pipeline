"""Lip-sync talking clips with InfiniteTalk (LightX2V, 4-step distilled) on the GPU box. The model loads once.

  python talk_render.py --models talk_models.json --base-config CFG --size 720 ITEMS.json
ITEMS.json: [{"id": "...", "image": "...png", "audio": "...wav", "out": "...mp4", "prompt": "..."}]
Writes <out>.json next to each clip with seconds, audio length and $; skips items whose mp4 exists (resume).
"""

from __future__ import annotations

import argparse
import json
import os
import subprocess
import time
import traceback
from pathlib import Path

SIZES = {"480": ([480, 832], "infinitetalk-480"), "720": ([720, 1280], "infinitetalk-720")}


def build_config(models: dict, base_cfg: Path, size: str, dst: Path) -> Path:
    cfg = json.loads(base_cfg.read_text())
    cfg["size"], cfg["infinitetalk_size"] = SIZES[size]
    if models.get("dit_fp8"):
        cfg.update(dit_quantized_ckpt=models["dit"], dit_quantized=True)
        cfg.pop("dit_original_ckpt", None)
    else:
        cfg.update(dit_original_ckpt=models["dit"], dit_quantized=False)
        # LightX2V picks the matmul kernel from dit_quant_scheme even when dit_quantized is false.
        for k in ("dit_quantized_ckpt", "dit_quant_scheme"):
            cfg.pop(k, None)
    cfg["adapter_model_path"] = models["adapter"]
    cfg["adapter_quantized"] = bool(models.get("adapter_fp8"))
    if not cfg["adapter_quantized"]:
        cfg.pop("adapter_quant_scheme", None)
    cfg["audio_encoder_path"] = models["audio_encoder"]
    # Base Wan2.1 repo ships bf16 T5/CLIP only: no quantized encoders; keep T5 on the CPU to leave VRAM for the DiT.
    cfg.update(t5_quantized=False, clip_quantized=False, t5_cpu_offload=True)
    for k in ("t5_quant_scheme", "clip_quant_scheme"):
        cfg.pop(k, None)
    if models.get("dit_fallback"):
        cfg["sample_shift"] = 5   # LightX2V's distilled Wan2.1 I2V setting (infinitetalk takes no distill_method)
    cfg["video_duration"] = 300
    dst.write_text(json.dumps(cfg, indent=1))
    return dst


def audio_seconds(path: str) -> float:
    return float(subprocess.run(["ffprobe", "-v", "error", "-show_entries", "format=duration", "-of", "csv=p=0", path],
                                capture_output=True, text=True, check=True).stdout)


def main() -> None:
    ap = argparse.ArgumentParser()
    ap.add_argument("--models", required=True)
    ap.add_argument("--base-config", required=True)
    ap.add_argument("--size", default="720", choices=list(SIZES))
    ap.add_argument("items")
    a = ap.parse_args()
    dph = float(os.environ.get("SPIKE_DPH", "0") or 0)
    models = json.loads(Path(a.models).read_text())
    items = json.loads(Path(a.items).read_text())
    cfg = build_config(models, Path(a.base_config), a.size, Path(f"/workspace/job/infinitetalk_{a.size}.json"))

    t0 = time.perf_counter()
    from lightx2v import LightX2VPipeline

    pipe = LightX2VPipeline(model_path=models["model_path"], model_cls="infinitetalk", task="s2v")
    pipe.create_generator(config_json=str(cfg))
    print(json.dumps({"talk_model_load_s": round(time.perf_counter() - t0, 1), "size": a.size}), flush=True)

    for it in items:
        out = Path(it["out"])
        if out.exists() and out.with_suffix(".json").exists():
            continue
        out.parent.mkdir(parents=True, exist_ok=True)
        rec = {"id": it["id"], "size": a.size, "audio_s": round(audio_seconds(it["audio"]), 2)}
        t = time.perf_counter()
        try:
            pipe.generate(seed=it.get("seed", 42), prompt=it.get("prompt") or "The person talks naturally to the camera.",
                          image_path=it["image"], audio_path=it["audio"], save_result_path=str(out))
            secs = time.perf_counter() - t
            rec.update(ok=True, seconds=round(secs, 1), s_per_video_s=round(secs / max(rec["audio_s"], 0.1), 2),
                       usd=round(secs * dph / 3600, 4))
        except Exception:
            rec.update(ok=False, seconds=round(time.perf_counter() - t, 1), error=traceback.format_exc()[-2000:])
            try:
                import torch
                torch.cuda.empty_cache()
            except Exception:
                pass
        out.with_suffix(".json").write_text(json.dumps(rec, indent=1))
        print(json.dumps({k: v for k, v in rec.items() if k != "error"}), flush=True)
        if os.environ.get("PUSH_HOOK"):
            subprocess.run(os.environ["PUSH_HOOK"], shell=True)


if __name__ == "__main__":
    main()
