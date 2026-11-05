"""Render every shot of a job with Engine A (LightX2V, Wan 2.2 I2V A14B NVFP4, 4 steps) on the GPU box.

Loads the model once, renders shots in order, writes clips + timing to $OUT/clips and $OUT/render.json.
A shot that fails is retried once with fewer frames, then at 480p, so one bad shot never stops the job.
Calls `push_hook` (a shell command) every few clips so partial results reach GitHub.

Usage: python render_job.py JOB_JSON FRAMES_DIR OUT_DIR MODEL_PATH HIGH LOW BASE_CONFIG
"""

from __future__ import annotations

import json
import os
import subprocess
import sys
import time
import traceback
from pathlib import Path


def write(path: Path, data) -> None:
    tmp = path.with_suffix(".tmp")
    tmp.write_text(json.dumps(data, indent=2))
    tmp.replace(path)


def main() -> None:
    job_json, frames_dir, out_dir, model_path, high, low, base_cfg = sys.argv[1:8]
    job = json.loads(Path(job_json).read_text())
    out = Path(out_dir)
    (out / "clips").mkdir(parents=True, exist_ok=True)
    report_path = out / "render.json"
    report = json.loads(report_path.read_text()) if report_path.exists() else {"runs": []}
    done = {r["id"] for r in report["runs"] if r.get("ok")}
    dph = float(os.environ.get("SPIKE_DPH", "0") or 0)
    push_every = int(os.environ.get("PUSH_EVERY", "5"))
    push_hook = os.environ.get("PUSH_HOOK", "")

    cfg = json.loads(Path(base_cfg).read_text())
    cfg.update({"high_noise_quantized_ckpt": high, "low_noise_quantized_ckpt": low,
                "high_noise_original_ckpt": None, "low_noise_original_ckpt": None})
    cfg_path = out / "engine_a_config.json"
    cfg_path.write_text(json.dumps(cfg, indent=2))

    t0 = time.perf_counter()
    from lightx2v import LightX2VPipeline

    pipe = LightX2VPipeline(model_path=model_path, model_cls="wan2.2_moe", task="i2v", distill_method="dmd2")
    pipe.create_generator(config_json=str(cfg_path))
    report["load_s"] = round(time.perf_counter() - t0, 2)
    write(report_path, report)

    w, h = job["render"]["width"], job["render"]["height"]
    since_push = 0
    for shot in job["shots"]:
        if shot["id"] in done:
            continue
        attempts = [(h, w, shot["num_frames"]), (h, w, min(shot["num_frames"], 81)), (832, 480, 81)]
        run = {"id": shot["id"], "file": f"clips/{shot['id']}.mp4", "attempts": []}
        for (hh, ww, nf) in attempts:
            t = time.perf_counter()
            try:
                pipe.generate(task="i2v", seed=shot["seed"], prompt=shot["motion_prompt"],
                              image_path=str(Path(frames_dir) / shot["frame"]), size=(hh, ww),
                              num_frames=nf, save_result_path=str(out / run["file"]))
                secs = round(time.perf_counter() - t, 2)
                run.update(ok=True, seconds=secs, usd=round(secs * dph / 3600, 4), size=[ww, hh], num_frames=nf)
                break
            except Exception:
                run["attempts"].append({"size": [ww, hh], "num_frames": nf,
                                        "seconds": round(time.perf_counter() - t, 2),
                                        "error": traceback.format_exc()[-1500:]})
                try:
                    import torch
                    torch.cuda.empty_cache()
                except Exception:
                    pass
        else:
            run["ok"] = False
        report["runs"] = [r for r in report["runs"] if r["id"] != shot["id"]] + [run]
        write(report_path, report)
        print(json.dumps({k: v for k, v in run.items() if k != "attempts"}), flush=True)
        since_push += 1
        if push_hook and since_push >= push_every:
            subprocess.run(push_hook, shell=True)
            since_push = 0

    ok = [r for r in report["runs"] if r.get("ok")]
    report["summary"] = {"clips_ok": len(ok), "clips_failed": len(report["runs"]) - len(ok),
                         "render_s_total": round(sum(r["seconds"] for r in ok), 1),
                         "usd_total": round(sum(r["seconds"] for r in ok) * dph / 3600, 4),
                         "median_s_per_clip": sorted(r["seconds"] for r in ok)[len(ok) // 2] if ok else None}
    write(report_path, report)


if __name__ == "__main__":
    main()
