"""Lip-sync a list of clips with LatentSync 1.6, loading the model once (run from the LatentSync checkout).

  python ls_driver.py MANIFEST.json IN_DIR OUT_ROOT
MANIFEST: {"items": [{"job_id": "...", "id": "s003", "video": "<job_id>/s003.mp4", "audio": "<job_id>/s003.wav"}, ...]}
Writes OUT_ROOT/<job_id>/lipsync/<id>.mp4 and OUT_ROOT/<job_id>/lipsync/report.json; calls $PUSH_HOOK every few clips.
"""

import json
import os
import subprocess
import sys
import time
import traceback
from pathlib import Path

import torch
from omegaconf import OmegaConf
from diffusers import AutoencoderKL, DDIMScheduler
from accelerate.utils import set_seed

sys.path.insert(0, os.getcwd())
from latentsync.models.unet import UNet3DConditionModel  # noqa: E402
from latentsync.pipelines.lipsync_pipeline import LipsyncPipeline  # noqa: E402
from latentsync.whisper.audio2feature import Audio2Feature  # noqa: E402

STEPS = int(os.environ.get("LS_STEPS", 20))
GUIDANCE = float(os.environ.get("LS_GUIDANCE", 1.5))


def main():
    manifest, in_dir, out_root = json.load(open(sys.argv[1])), Path(sys.argv[2]), Path(sys.argv[3])
    config = OmegaConf.load("configs/unet/stage2_512.yaml")
    dtype = torch.float16
    scheduler = DDIMScheduler.from_pretrained("configs")
    audio_encoder = Audio2Feature(model_path="checkpoints/whisper/tiny.pt", device="cuda",
                                  num_frames=config.data.num_frames, audio_feat_length=config.data.audio_feat_length)
    vae = AutoencoderKL.from_pretrained("stabilityai/sd-vae-ft-mse", torch_dtype=dtype)
    vae.config.scaling_factor = 0.18215
    vae.config.shift_factor = 0
    unet, _ = UNet3DConditionModel.from_pretrained(OmegaConf.to_container(config.model), "checkpoints/latentsync_unet.pt",
                                                   device="cpu")
    unet = unet.to(dtype=dtype)
    pipe = LipsyncPipeline(vae=vae, audio_encoder=audio_encoder, unet=unet, scheduler=scheduler).to("cuda")
    try:
        from DeepCache import DeepCacheSDHelper
        helper = DeepCacheSDHelper(pipe=pipe)
        helper.set_params(cache_interval=3, cache_branch_id=0)
        helper.enable()
    except Exception as e:  # noqa: BLE001
        print("DeepCache off:", e)
    reports: dict[str, list] = {}
    done = 0
    for it in manifest["items"]:
        out_dir = out_root / it["job_id"] / "lipsync"
        out_dir.mkdir(parents=True, exist_ok=True)
        dst = out_dir / f"{it['id']}.mp4"
        rep = {"id": it["id"]}
        if dst.exists() and dst.stat().st_size > 1024:
            continue
        t0 = time.time()
        try:
            set_seed(1247)
            tmp = Path(f"/workspace/ls_tmp/{it['job_id']}_{it['id']}")
            tmp.mkdir(parents=True, exist_ok=True)
            pipe(video_path=str(in_dir / it["video"]), audio_path=str(in_dir / it["audio"]), video_out_path=str(dst),
                 num_frames=config.data.num_frames, num_inference_steps=STEPS, guidance_scale=GUIDANCE, weight_dtype=dtype,
                 width=config.data.resolution, height=config.data.resolution, mask_image_path=config.data.mask_image_path,
                 temp_dir=str(tmp))
            rep |= {"ok": dst.exists() and dst.stat().st_size > 1024, "seconds": round(time.time() - t0, 1)}
        except Exception as e:  # noqa: BLE001
            traceback.print_exc()
            rep |= {"ok": False, "error": str(e)[:300], "seconds": round(time.time() - t0, 1)}
        print(json.dumps(rep), flush=True)
        reports.setdefault(it["job_id"], []).append(rep)
        (out_dir / "report.json").write_text(json.dumps(reports[it["job_id"]], indent=1))
        done += 1
        if os.environ.get("PUSH_HOOK") and done % int(os.environ.get("PUSH_EVERY", 6)) == 0:
            subprocess.run(os.environ["PUSH_HOOK"] + f" 'lipsync {done}'", shell=True)


if __name__ == "__main__":
    main()
