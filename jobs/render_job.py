"""Render the shots of one or many jobs with Engine A (LightX2V, Wan 2.2 I2V A14B NVFP4, 4 steps) on the GPU box.

The model loads ONCE for the whole batch. Each job is given as JOB_JSON:FRAMES_DIR:OUT_DIR. Clips and timing go to
OUT_DIR/clips and OUT_DIR/render.json. A shot that fails is retried with fewer frames, then at 480p, so one bad
shot never stops the batch. Already-rendered shots are skipped (resume). After each job, $ON_JOB_DONE OUT_DIR runs
(interpolate + push in the background); every $PUSH_EVERY clips $PUSH_HOOK runs so partial results reach GitHub.

  python render_job.py --model-path BASE --high H --low L --base-config CFG  a.json:frames/a:out/a  b.json:...
"""

from __future__ import annotations

import argparse
import json
import os
import subprocess
import time
import traceback
from pathlib import Path


def write(path: Path, data) -> None:
    tmp = path.with_suffix(".tmp")
    tmp.write_text(json.dumps(data, indent=2))
    tmp.replace(path)


def attempts_for(shot: dict, w: int, h: int) -> list[tuple[int, int, int]]:
    """Full size and length first, then shorter, then 480p as the last resort."""
    return [(h, w, shot["num_frames"]), (h, w, min(shot["num_frames"], 81)), (832, 480, 81)]


def summarise(runs: list[dict], dph: float) -> dict:
    ok = [r for r in runs if r.get("ok")]
    secs = sorted(r["seconds"] for r in ok)
    return {"clips_ok": len(ok), "clips_failed": len(runs) - len(ok), "render_s_total": round(sum(secs), 1),
            "usd_total": round(sum(secs) * dph / 3600, 4), "median_s_per_clip": secs[len(secs) // 2] if secs else None}


def render_job(pipe, job_json: Path, frames_dir: Path, out: Path, dph: float, hook: str, push_every: int,
               counter: list[int]) -> dict:
    job = json.loads(job_json.read_text())
    (out / "clips").mkdir(parents=True, exist_ok=True)
    report_path = out / "render.json"
    report = json.loads(report_path.read_text()) if report_path.exists() else {"job_id": job["job_id"], "runs": []}
    done = {r["id"] for r in report["runs"] if r.get("ok")}
    w, h = job["render"]["width"], job["render"]["height"]
    for shot in job["shots"]:
        if shot["id"] in done or shot.get("skip_render"):
            continue
        run = {"id": shot["id"], "file": f"clips/{shot['id']}.mp4", "attempts": []}
        for (hh, ww, nf) in attempts_for(shot, w, h):
            t = time.perf_counter()
            try:
                pipe.generate(task="i2v", seed=shot["seed"], prompt=shot["motion_prompt"],
                              image_path=str(frames_dir / shot["frame"]), size=(hh, ww), num_frames=nf,
                              save_result_path=str(out / run["file"]))
                secs = round(time.perf_counter() - t, 2)
                run.update(ok=True, seconds=secs, usd=round(secs * dph / 3600, 4), size=[ww, hh], num_frames=nf)
                break
            except Exception:
                run["attempts"].append({"size": [ww, hh], "num_frames": nf, "seconds": round(time.perf_counter() - t, 2),
                                        "error": traceback.format_exc()[-1500:]})
                try:
                    import torch
                    torch.cuda.empty_cache()
                except Exception:
                    pass
        else:
            run["ok"] = False
        report["runs"] = [r for r in report["runs"] if r["id"] != shot["id"]] + [run]
        report["summary"] = summarise(report["runs"], dph)
        write(report_path, report)
        print(json.dumps({"job": job["job_id"], **{k: v for k, v in run.items() if k != "attempts"}}), flush=True)
        counter[0] += 1
        if hook and counter[0] % push_every == 0:
            subprocess.run(hook, shell=True)
    return report["summary"]


def main() -> None:
    ap = argparse.ArgumentParser()
    ap.add_argument("--model-path", required=True)
    ap.add_argument("--high", required=True)
    ap.add_argument("--low", required=True)
    ap.add_argument("--base-config", required=True)
    ap.add_argument("specs", nargs="+", help="JOB_JSON:FRAMES_DIR:OUT_DIR")
    a = ap.parse_args()
    dph = float(os.environ.get("SPIKE_DPH", "0") or 0)
    hook, push_every = os.environ.get("PUSH_HOOK", ""), int(os.environ.get("PUSH_EVERY", "10"))
    on_done = os.environ.get("ON_JOB_DONE", "")

    cfg = json.loads(Path(a.base_config).read_text())
    cfg.update({"high_noise_quantized_ckpt": a.high, "low_noise_quantized_ckpt": a.low,
                "high_noise_original_ckpt": None, "low_noise_original_ckpt": None})
    cfg_path = Path("/workspace/job/engine_a_config.json")
    cfg_path.parent.mkdir(parents=True, exist_ok=True)
    cfg_path.write_text(json.dumps(cfg, indent=2))

    t0 = time.perf_counter()
    from lightx2v import LightX2VPipeline

    pipe = LightX2VPipeline(model_path=a.model_path, model_cls="wan2.2_moe", task="i2v", distill_method="dmd2")
    pipe.create_generator(config_json=str(cfg_path))
    print(json.dumps({"model_load_s": round(time.perf_counter() - t0, 1)}), flush=True)

    counter = [0]
    for spec in a.specs:
        job_json, frames_dir, out = (Path(p) for p in spec.split(":"))
        summary = render_job(pipe, job_json, frames_dir, out, dph, hook, push_every, counter)
        print(json.dumps({"job_done": out.name, **summary}), flush=True)
        if on_done:
            subprocess.run(f"{on_done} {out}", shell=True)


if __name__ == "__main__":
    main()
