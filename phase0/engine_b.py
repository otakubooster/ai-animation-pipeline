"""Engine B spike: ComfyUI + Wan 2.2 I2V A14B fp8 + lightx2v 4-step LoRAs, headless via the ComfyUI API.

Starts ComfyUI, renders every case with WanImageToVideo (i2v) and WanFirstLastFrameToVideo (flf2v),
and records cold-start and per-clip timings to results/engine_b.json.
Model file names are passed in by run_spike.sh after discovery on Hugging Face.
"""

from __future__ import annotations

import argparse
import json
import shutil
import subprocess
import time
import traceback
import urllib.request
import uuid
from pathlib import Path

from common import CASES_FILE, RESULTS, gpu_info, load_cases, now, write_json

API = "http://127.0.0.1:8188"
NEG = "blurry, low quality, distorted, deformed, extra limbs, watermark, text, static, jittery"


def http(method: str, path: str, body: dict | None = None):
    data = json.dumps(body).encode() if body is not None else None
    req = urllib.request.Request(API + path, data=data, method=method,
                                 headers={"Content-Type": "application/json"})
    with urllib.request.urlopen(req, timeout=30) as r:
        return json.loads(r.read() or b"{}")


def wait_ready(timeout: int = 600) -> None:
    end = time.time() + timeout
    while time.time() < end:
        try:
            http("GET", "/system_stats")
            return
        except Exception:
            time.sleep(2)
    raise RuntimeError("ComfyUI did not start")


def workflow(m: dict, case: dict, task: str, prefix: str) -> dict:
    w, h = case["width"], case["height"]
    g = {
        "unet_hi": {"class_type": "UNETLoader", "inputs": {"unet_name": m["high"], "weight_dtype": "default"}},
        "unet_lo": {"class_type": "UNETLoader", "inputs": {"unet_name": m["low"], "weight_dtype": "default"}},
        "lora_hi": {"class_type": "LoraLoaderModelOnly", "inputs": {"model": ["unet_hi", 0], "lora_name": m["lora_high"], "strength_model": 1.0}},
        "lora_lo": {"class_type": "LoraLoaderModelOnly", "inputs": {"model": ["unet_lo", 0], "lora_name": m["lora_low"], "strength_model": 1.0}},
        "ms_hi": {"class_type": "ModelSamplingSD3", "inputs": {"model": ["lora_hi", 0], "shift": 5.0}},
        "ms_lo": {"class_type": "ModelSamplingSD3", "inputs": {"model": ["lora_lo", 0], "shift": 5.0}},
        "clip": {"class_type": "CLIPLoader", "inputs": {"clip_name": m["t5"], "type": "wan", "device": "default"}},
        "pos": {"class_type": "CLIPTextEncode", "inputs": {"text": case["motion_prompt"], "clip": ["clip", 0]}},
        "neg": {"class_type": "CLIPTextEncode", "inputs": {"text": NEG, "clip": ["clip", 0]}},
        "vae": {"class_type": "VAELoader", "inputs": {"vae_name": m["vae"]}},
        "img1": {"class_type": "LoadImage", "inputs": {"image": Path(case["first"]).name}},
    }
    common = {"positive": ["pos", 0], "negative": ["neg", 0], "vae": ["vae", 0],
              "width": w, "height": h, "length": 81, "batch_size": 1, "start_image": ["img1", 0]}
    if task == "flf2v":
        g["img2"] = {"class_type": "LoadImage", "inputs": {"image": Path(case["last"]).name}}
        g["cond"] = {"class_type": "WanFirstLastFrameToVideo", "inputs": {**common, "end_image": ["img2", 0]}}
    else:
        g["cond"] = {"class_type": "WanImageToVideo", "inputs": common}
    seed = case.get("seed", 42)
    g["ks_hi"] = {"class_type": "KSamplerAdvanced", "inputs": {
        "model": ["ms_hi", 0], "add_noise": "enable", "noise_seed": seed, "steps": 4, "cfg": 1.0,
        "sampler_name": "euler", "scheduler": "simple", "positive": ["cond", 0], "negative": ["cond", 1],
        "latent_image": ["cond", 2], "start_at_step": 0, "end_at_step": 2, "return_with_leftover_noise": "enable"}}
    g["ks_lo"] = {"class_type": "KSamplerAdvanced", "inputs": {
        "model": ["ms_lo", 0], "add_noise": "disable", "noise_seed": seed, "steps": 4, "cfg": 1.0,
        "sampler_name": "euler", "scheduler": "simple", "positive": ["cond", 0], "negative": ["cond", 1],
        "latent_image": ["ks_hi", 0], "start_at_step": 2, "end_at_step": 10000, "return_with_leftover_noise": "disable"}}
    g["dec"] = {"class_type": "VAEDecode", "inputs": {"samples": ["ks_lo", 0], "vae": ["vae", 0]}}
    g["save"] = {"class_type": "SaveImage", "inputs": {"images": ["dec", 0], "filename_prefix": prefix}}
    return g


def run_one(graph: dict, timeout: int = 1800) -> dict:
    pid = http("POST", "/prompt", {"prompt": graph, "client_id": str(uuid.uuid4())})["prompt_id"]
    end = time.time() + timeout
    while time.time() < end:
        hist = http("GET", f"/history/{pid}")
        if pid in hist:
            entry = hist[pid]
            status = entry.get("status", {})
            if status.get("status_str") == "error":
                raise RuntimeError(json.dumps(status)[-3000:])
            return entry
        time.sleep(1)
    raise TimeoutError("render timed out")


def frames_to_mp4(comfy_out: Path, prefix: str, mp4: Path) -> None:
    frames = sorted(comfy_out.glob(f"{prefix}_*.png"))
    if not frames:
        raise RuntimeError("no frames saved")
    tmp = comfy_out / f"{prefix}_seq"
    tmp.mkdir(exist_ok=True)
    for i, f in enumerate(frames):
        shutil.move(str(f), tmp / f"{i:05d}.png")
    subprocess.run(["ffmpeg", "-y", "-loglevel", "error", "-framerate", "16", "-i", str(tmp / "%05d.png"),
                    "-c:v", "libx264", "-pix_fmt", "yuv420p", "-crf", "18", str(mp4)], check=True)
    shutil.rmtree(tmp)


def main() -> None:
    ap = argparse.ArgumentParser()
    ap.add_argument("--comfy-dir", required=True)
    ap.add_argument("--models", required=True, help="JSON: high, low, lora_high, lora_low, t5, vae (file names)")
    ap.add_argument("--tasks", default="i2v,flf2v")
    args = ap.parse_args()
    comfy = Path(args.comfy_dir)
    models = json.loads(args.models)
    report = {"engine": "B", "variant": "comfy_fp8_lightx2v_lora", "gpu": gpu_info(), "models": models,
              "started": now(), "runs": []}
    report_path = RESULTS / "engine_b.json"
    out_dir = RESULTS / "clips"
    out_dir.mkdir(parents=True, exist_ok=True)

    cases = load_cases(CASES_FILE)
    for c in cases:
        for key in ("first", "last"):
            if c.get(key):
                shutil.copy(c[key], comfy / "input" / Path(c[key]).name)

    t0 = time.perf_counter()
    log = open(RESULTS / "comfyui.log", "w")
    proc = subprocess.Popen(["python", "main.py", "--listen", "127.0.0.1", "--port", "8188",
                             "--disable-auto-launch"], cwd=comfy, stdout=log, stderr=subprocess.STDOUT)
    try:
        wait_ready()
        report["server_start_s"] = round(time.perf_counter() - t0, 2)
        first = True
        for case in cases:
            for task in args.tasks.split(","):
                if task == "flf2v" and not case.get("last"):
                    continue
                prefix = f"B_{task}_{case['id']}"
                name = f"{prefix}.mp4"
                run = {"case": case["id"], "task": task, "file": f"clips/{name}", "cold": first}
                t = time.perf_counter()
                try:
                    run_one(workflow(models, case, task, prefix))
                    run["render_s"] = round(time.perf_counter() - t, 2)
                    frames_to_mp4(comfy / "output", prefix, out_dir / name)
                    run["seconds"] = round(time.perf_counter() - t, 2)
                    run["ok"] = True
                except Exception:
                    run["seconds"] = round(time.perf_counter() - t, 2)
                    run["ok"] = False
                    run["error"] = traceback.format_exc()[-3000:]
                first = False
                report["runs"].append(run)
                write_json(report_path, report)
                print(json.dumps(run), flush=True)
    finally:
        proc.terminate()
        report["finished"] = now()
        report["total_s"] = round(time.perf_counter() - t0, 2)
        write_json(report_path, report)


if __name__ == "__main__":
    main()
