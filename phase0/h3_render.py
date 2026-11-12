"""Render shots with MiniMax H3 (open weights, Comfy-Org repackage) through a headless ComfyUI server.

One shots file, one ComfyUI process, model loaded once. Each shot picks a mode:
  t2va  - text only                         (MiniMaxH3ImageToVideo, no images)
  fl2va - first frame (and/or last frame)   (MiniMaxH3ImageToVideo, first_frame/last_frame)
  ref2va- reference images (product, person) (MiniMaxH3ReferenceToVideo, prompt refers to <Picture N>)
Output: OUT/<id>.mp4 (video + H3's native stereo audio) and OUT/render.json with seconds per shot.

  python phase0/h3_render.py SHOTS.json OUT_DIR [--server http://127.0.0.1:8188]

Licence: MiniMax H3 Community License - no use or display of the model or its outputs in the EU, UK, US or
South Korea; the GPU host is chosen accordingly (launch.py --territory h3).
"""

from __future__ import annotations

import argparse
import json
import shutil
import time
import urllib.request
from pathlib import Path

COMFY = Path("/workspace/ComfyUI")
MODELS = {
    "fl2va": "minimax_h3_fl2va_pruned_int8_convrot.safetensors",
    "ref2va": "minimax_h3_ref2va_pruned_int8_convrot.safetensors",
    "clip": "qwen3vl_32b_minimax_h3_nvfp4_awq.safetensors",
    "vae": "minimax_h3_video_vae_int8_convrot.safetensors",
    "audio_vae": "minimax_h3_audio_vae_fp32.safetensors",
    "lora_fl2va": "minimax_h3_fl2v_turbo_8step_v1.0_comfyui_bf16.safetensors",
    "lora_ref2va": "minimax_h3_ref2v_turbo_4step_v0.1_comfyui_bf16.safetensors",
}
TURBO_STEPS = {"fl2va": 8, "ref2va": 4}
FULL_STEPS = 20


def frames_for(seconds: float) -> int:
    """24 fps frame count on H3's 17k+5 grid (124 = ~5 s), as in the official ComfyUI template."""
    n = max(5, round(seconds * 24))
    return n + (5 - n % 17) % 17


def api_graph(shot: dict, images: dict[str, str], ref_key: str) -> dict:
    """ComfyUI API-format prompt for one shot. images: logical name -> filename in ComfyUI/input."""
    mode = shot.get("mode", "t2va")
    family = "ref2va" if mode == "ref2va" else "fl2va"
    turbo = shot.get("turbo", True)
    w, h = shot.get("size", [576, 1024])
    g = {
        "unet": {"class_type": "UNETLoader", "inputs": {"unet_name": MODELS[family], "weight_dtype": "default"}},
        "lora": {"class_type": "LoraLoaderModelOnly", "inputs": {"model": ["unet", 0], "lora_name": MODELS["lora_" + family], "strength_model": 1.0}},
        "clip": {"class_type": "CLIPLoader", "inputs": {"clip_name": MODELS["clip"], "type": "minimax", "device": "default"}},
        "vae": {"class_type": "VAELoader", "inputs": {"vae_name": MODELS["vae"]}},
        "avae": {"class_type": "VAELoader", "inputs": {"vae_name": MODELS["audio_vae"]}},
        "noise": {"class_type": "RandomNoise", "inputs": {"noise_seed": int(shot.get("seed", 1))}},
        "sampler": {"class_type": "KSamplerSelect", "inputs": {"sampler_name": "res_multistep"}},
        "sched": {"class_type": "BasicScheduler", "inputs": {"model": ["lora" if turbo else "unet", 0], "scheduler": "simple",
                                                             "steps": TURBO_STEPS[family] if turbo else FULL_STEPS, "denoise": 1.0}},
        "guider": {"class_type": "BasicGuider", "inputs": {"model": ["lora" if turbo else "unet", 0], "conditioning": ["cond", 0]}},
        "sample": {"class_type": "SamplerCustomAdvanced", "inputs": {"noise": ["noise", 0], "guider": ["guider", 0], "sampler": ["sampler", 0],
                                                                     "sigmas": ["sched", 0], "latent_image": ["cond", 1]}},
        "dec": {"class_type": "VAEDecode", "inputs": {"samples": ["sample", 0], "vae": ["vae", 0]}},
        "adec": {"class_type": "VAEDecodeAudio", "inputs": {"samples": ["sample", 0], "vae": ["avae", 0]}},
        "video": {"class_type": "CreateVideo", "inputs": {"images": ["dec", 0], "audio": ["adec", 0], "fps": 24}},
        "save": {"class_type": "SaveVideo", "inputs": {"video": ["video", 0], "filename_prefix": f"h3/{shot['id']}", "format": "auto", "codec": "auto"}},
    }
    length = frames_for(shot.get("seconds", 5))
    if family == "fl2va":
        cond = {"clip": ["clip", 0], "vae": ["vae", 0], "prompt": shot["prompt"], "width": w, "height": h, "length": length}
        for slot in ("first_frame", "last_frame"):
            if shot.get(slot):
                g["img_" + slot] = {"class_type": "LoadImage", "inputs": {"image": images[shot[slot]]}}
                cond[slot] = ["img_" + slot, 0]
        g["cond"] = {"class_type": "MiniMaxH3ImageToVideo", "inputs": cond}
    else:
        cond = {"clip": ["clip", 0], "vae": ["vae", 0], "audio_vae": ["avae", 0], "prompt": shot["prompt"], "width": w,
                "height": h, "length": length, "ref_image_size": "match"}
        for i, name in enumerate(shot.get("ref_images", [])):
            g[f"ref{i}"] = {"class_type": "LoadImage", "inputs": {"image": images[name]}}
            cond[ref_key.format(i=i)] = [f"ref{i}", 0]
        g["cond"] = {"class_type": "MiniMaxH3ReferenceToVideo", "inputs": cond}
    return g


def post(server: str, path: str, body: dict | None = None) -> dict:
    req = urllib.request.Request(server + path, data=json.dumps(body).encode() if body is not None else None,
                                 headers={"Content-Type": "application/json"})
    try:
        with urllib.request.urlopen(req, timeout=60) as r:
            return json.loads(r.read() or b"{}")
    except urllib.error.HTTPError as e:
        return {"http_error": e.code, "body": e.read().decode()[:4000]}


def wait(server: str, pid: str, timeout_s: int = 3600) -> dict:
    t0 = time.time()
    while time.time() - t0 < timeout_s:
        h = post(server, f"/history/{pid}")
        if pid in h and h[pid].get("status", {}).get("completed") is not None:
            return h[pid]
        if pid in h and h[pid].get("status", {}).get("status_str") == "error":
            return h[pid]
        time.sleep(3)
    return {"timeout": True}


def main() -> None:
    ap = argparse.ArgumentParser()
    ap.add_argument("shots", type=Path)
    ap.add_argument("out", type=Path)
    ap.add_argument("--server", default="http://127.0.0.1:8188")
    a = ap.parse_args()
    spec = json.loads(a.shots.read_text())
    a.out.mkdir(parents=True, exist_ok=True)
    # Reference/first-frame images go to ComfyUI/input under their own names.
    images = {}
    for name, path in spec.get("images", {}).items():
        dst = COMFY / "input" / Path(path).name
        shutil.copy(a.shots.parent / path if not Path(path).is_absolute() else path, dst)
        images[name] = dst.name
    info = post(a.server, "/object_info/MiniMaxH3ReferenceToVideo")
    (a.out / "object_info_ref2va.json").write_text(json.dumps(info, indent=1)[:20000])
    ref_key = "ref_images.ref_image_{i}"
    runs = []
    for shot in spec["shots"]:
        t = time.perf_counter()
        run = {"id": shot["id"], "mode": shot.get("mode", "t2va"), "size": shot.get("size", [576, 1024]),
               "seconds_video": shot.get("seconds", 5), "turbo": shot.get("turbo", True)}
        res = post(a.server, "/prompt", {"prompt": api_graph(shot, images, ref_key)})
        if res.get("http_error") and shot.get("mode") == "ref2va" and ref_key != "ref_image_{i}":
            run["first_error"] = res["body"][:800]
            ref_key = "ref_image_{i}"                       # older/newer naming of the dynamic reference inputs
            res = post(a.server, "/prompt", {"prompt": api_graph(shot, images, ref_key)})
        if res.get("http_error"):
            run.update(ok=False, error=res["body"][:2000])
        else:
            h = wait(a.server, res["prompt_id"])
            vids = sorted((COMFY / "output" / "h3").glob(f"{shot['id']}_*.mp4"), key=lambda p: p.stat().st_mtime)
            if vids:
                shutil.copy(vids[-1], a.out / f"{shot['id']}.mp4")
                run["ok"] = True
            else:
                run.update(ok=False, error=json.dumps(h.get("status", h))[:2000])
        run["render_s"] = round(time.perf_counter() - t, 1)
        runs.append(run)
        (a.out / "render.json").write_text(json.dumps(runs, indent=1))
        print(json.dumps(run), flush=True)


if __name__ == "__main__":
    main()
