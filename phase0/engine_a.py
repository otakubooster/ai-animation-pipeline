"""Engine A spike: LightX2V + LightWan2.2-A14B (NVFP4, 4 steps, sparse attention).

Runs on the rented GPU box. Loads the model once, renders every test case,
and records cold-start and per-clip timings to results/engine_a_<variant>.json.

Variants:
  nvfp4         NVFP4 quantized distilled weights + dynamic sparse attention (vendor-fastest)
  nvfp4_dense   same weights, dense sage_attn2 (used if sparse attention kernels fail)
  distill_bf16  official 4-step distilled bf16 weights (documented FLF2V path; fallback)
"""

from __future__ import annotations

import argparse
import json
import time
import traceback
from pathlib import Path

from common import CASES_FILE, RESULTS, gpu_info, load_cases, now, write_json


def build_config(variant: str, ckpts: dict, base_config: Path, out: Path) -> Path:
    cfg = json.loads(base_config.read_text())
    if variant.startswith("nvfp4"):
        cfg["high_noise_quantized_ckpt"] = ckpts["high"]
        cfg["low_noise_quantized_ckpt"] = ckpts["low"]
        cfg["high_noise_original_ckpt"] = None
        cfg["low_noise_original_ckpt"] = None
        if variant == "nvfp4_dense":
            cfg["self_attn_1_type"] = "sage_attn2"
            cfg.pop("dynamic_sparse_attn_setting", None)
    else:
        cfg["high_noise_original_ckpt"] = ckpts["high"]
        cfg["low_noise_original_ckpt"] = ckpts["low"]
    cfg["num_frames"] = 81
    out.write_text(json.dumps(cfg, indent=2))
    return out


def main() -> None:
    ap = argparse.ArgumentParser()
    ap.add_argument("--variant", required=True, choices=["nvfp4", "nvfp4_dense", "distill_bf16"])
    ap.add_argument("--model-path", required=True, help="Wan2.2-I2V-A14B base dir (VAE, T5, tokenizer)")
    ap.add_argument("--high", required=True)
    ap.add_argument("--low", required=True)
    ap.add_argument("--base-config", required=True)
    ap.add_argument("--tasks", default="i2v,flf2v")
    args = ap.parse_args()

    out_dir = RESULTS / "clips"
    out_dir.mkdir(parents=True, exist_ok=True)
    report = {"engine": "A", "variant": args.variant, "gpu": gpu_info(), "started": now(), "runs": []}
    report_path = RESULTS / f"engine_a_{args.variant}.json"
    tasks = args.tasks.split(",")

    t0 = time.perf_counter()
    try:
        from lightx2v import LightX2VPipeline

        cfg_path = build_config(
            args.variant, {"high": args.high, "low": args.low},
            Path(args.base_config), RESULTS / f"engine_a_{args.variant}_config.json",
        )
        pipe = LightX2VPipeline(
            model_path=args.model_path,
            model_cls="wan2.2_moe",
            task=tasks[0],
            support_tasks=tasks,
            distill_method="dmd2",
        )
        pipe.create_generator(config_json=str(cfg_path))
        report["load_s"] = round(time.perf_counter() - t0, 2)
    except Exception:
        report["load_error"] = traceback.format_exc()
        write_json(report_path, report)
        raise SystemExit(1)
    write_json(report_path, report)

    first = True
    for case in load_cases(CASES_FILE):
        for task in tasks:
            if task == "flf2v" and not case.get("last"):
                continue
            name = f"A_{args.variant}_{task}_{case['id']}.mp4"
            run = {"case": case["id"], "task": task, "file": f"clips/{name}", "cold": first}
            first = False
            t = time.perf_counter()
            try:
                pipe.generate(
                    task=task,
                    seed=case.get("seed", 42),
                    prompt=case["motion_prompt"],
                    image_path=case["first"],
                    last_frame_path=case["last"] if task == "flf2v" else None,
                    size=(case["height"], case["width"]),
                    num_frames=81,
                    save_result_path=str(out_dir / name),
                )
                run["seconds"] = round(time.perf_counter() - t, 2)
                run["ok"] = (out_dir / name).exists()
            except Exception:
                run["seconds"] = round(time.perf_counter() - t, 2)
                run["ok"] = False
                run["error"] = traceback.format_exc()[-3000:]
            report["runs"].append(run)
            write_json(report_path, report)
            print(json.dumps(run), flush=True)

    report["finished"] = now()
    report["total_s"] = round(time.perf_counter() - t0, 2)
    write_json(report_path, report)


if __name__ == "__main__":
    main()
