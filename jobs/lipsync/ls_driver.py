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
GUIDANCE = float(os.environ.get("LS_GUIDANCE", 2.0))


def neutral_reference(pipe) -> None:
    """LatentSync conditions the UNet on each frame's own unmasked face, so a source clip whose mouth already moves
    (H3 talking shots speak their own gibberish) leaks its mouth shapes into the result (measured 2026-11-16: the
    output mouth followed the H3 mouth, not the voice). Here the UNet gets ONE reference for the whole clip, the face
    with the most closed mouth; the per-frame faces are still pasted back around the mouth (eyes, brows, head motion)."""
    import numpy as np
    from latentsync.utils.face_detector import FaceDetector
    det = FaceDetector()
    orig_loop, orig_prep = pipe.loop_video, pipe.prepare_image_latents

    def to_hwc(f):
        a = f.cpu().numpy() if hasattr(f, "cpu") else np.asarray(f)
        return a.transpose(1, 2, 0) if a.shape[0] == 3 else a

    def loop_video(whisper_chunks, video_frames):
        vf, faces, boxes, mats = orig_loop(whisper_chunks, video_frames)
        best, best_open = 0, 1e9
        mp, vision = det.mp, None
        for k in range(0, len(faces), max(1, len(faces) // 60)):
            img = np.ascontiguousarray(to_hwc(faces[k]).astype(np.uint8))
            res = det.det.detect(mp.Image(image_format=mp.ImageFormat.SRGB, data=img))
            if res.face_landmarks:
                L = res.face_landmarks[0]
                o = abs(L[14].y - L[13].y) / max(abs(L[152].y - L[10].y), 1e-3)
                if o < best_open:
                    best, best_open = k, o
        ref, _, _ = pipe.image_processor.prepare_masks_and_masked_images(faces[best:best + 1], affine_transform=False)
        pipe._neutral_ref = ref[0]
        print(f"neutral reference: frame {best} (mouth open {best_open:.3f})", flush=True)
        return vf, faces, boxes, mats

    def prepare_image_latents(images, device, dtype, generator, cfg):
        ref = pipe._neutral_ref.to(images.device, images.dtype)
        return orig_prep(ref.unsqueeze(0).expand_as(images).contiguous(), device, dtype, generator, cfg)

    pipe.loop_video = loop_video
    pipe.prepare_image_latents = prepare_image_latents


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
    neutral_reference(pipe)
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
