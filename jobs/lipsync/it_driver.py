"""Talking shots with InfiniteTalk (Apache-2.0): the whole shot is generated from its first frame + the voice, so jaw,
teeth, cheeks and head move together with the speech (mouth-only inpainting looked patched and fake).
Run from the InfiniteTalk checkout, model loaded once:

  python it_driver.py MANIFEST.json IN_DIR OUT_ROOT
MANIFEST: {"items": [{"job_id", "id", "image": "<job_id>/<id>.png", "audio": "<job_id>/<id>.wav", "prompt"}, ...]}
Writes OUT_ROOT/<job_id>/lipsync/<id>.mp4 (25 fps, audio muxed) + report.json; $PUSH_HOOK every few clips.
"""

import json
import os
import subprocess
import sys
import time
import traceback
from pathlib import Path

import collections  # noqa: E402
import inspect  # noqa: E402
if not hasattr(inspect, "ArgSpec"):     # removed in Python 3.11; still used by an old dependency of InfiniteTalk
    inspect.ArgSpec = collections.namedtuple("ArgSpec", "args varargs keywords defaults")
    inspect.getargspec = lambda f: inspect.ArgSpec(*inspect.getfullargspec(f)[:4])
sys.path.insert(0, os.environ.get("IT_SHIMS", ""))
sys.path.insert(0, os.getcwd())
import soundfile as sf  # noqa: E402
import torch  # noqa: E402
import generate_infinitetalk as g  # noqa: E402
import wan  # noqa: E402
from wan.configs import WAN_CONFIGS  # noqa: E402

W = Path(os.environ.get("IT_WEIGHTS", "weights"))
STEPS = int(os.environ.get("IT_STEPS", 8))
AUDIO_CFG = float(os.environ.get("IT_AUDIO_CFG", 2.0))
TEXT_CFG = float(os.environ.get("IT_TEXT_CFG", 1.0))
SHIFT = float(os.environ.get("IT_SHIFT", 2.0))
SIZE = os.environ.get("IT_SIZE", "infinitetalk-480")


def main():
    manifest, in_dir, out_root = json.load(open(sys.argv[1])), Path(sys.argv[2]), Path(sys.argv[3])
    sys.argv = [sys.argv[0], "--ckpt_dir", str(W / "Wan2.1-I2V-14B-480P"), "--wav2vec_dir", str(W / "chinese-wav2vec2-base"),
                "--infinitetalk_dir", str(W / "InfiniteTalk/single/infinitetalk.safetensors"),
                "--quant", os.environ.get("IT_QUANT", "int8"),
                "--quant_dir", str(W / os.environ.get("IT_QUANT_FILE", "InfiniteTalk/quant_models/infinitetalk_single_int8_lora.safetensors")),
                "--input_json", "unused.json", "--size", SIZE, "--sample_steps", str(STEPS), "--mode", "streaming",
                "--motion_frame", "9", "--sample_shift", str(SHIFT), "--sample_text_guide_scale", str(TEXT_CFG),
                "--sample_audio_guide_scale", str(AUDIO_CFG), "--num_persistent_param_in_dit", os.environ.get("IT_PERSIST", "0")]
    args = g._parse_args()
    g._validate_args(args)
    cfg = WAN_CONFIGS[args.task]
    pipe = wan.InfiniteTalkPipeline(config=cfg, checkpoint_dir=args.ckpt_dir, quant_dir=args.quant_dir, device_id=0, rank=0,
                                    t5_fsdp=False, dit_fsdp=False, use_usp=False, t5_cpu=args.t5_cpu, lora_dir=args.lora_dir,
                                    lora_scales=args.lora_scale, quant=args.quant, dit_path=args.dit_path,
                                    infinitetalk_dir=args.infinitetalk_dir)
    if args.num_persistent_param_in_dit is not None:
        pipe.vram_management = True
        pipe.enable_vram_management(num_persistent_param_in_dit=args.num_persistent_param_in_dit)
    fe, enc = g.custom_init("cpu", args.wav2vec_dir)
    reports, done = {}, 0
    for it in manifest["items"]:
        out_dir = out_root / it["job_id"] / "lipsync"
        out_dir.mkdir(parents=True, exist_ok=True)
        dst = out_dir / f"{it['id']}.mp4"
        if dst.exists() and dst.stat().st_size > 1024:
            continue
        rep, t0 = {"id": it["id"]}, time.time()
        try:
            tmp = Path(f"/workspace/it_tmp/{it['job_id']}_{it['id']}")
            tmp.mkdir(parents=True, exist_ok=True)
            speech = g.audio_prepare_single(str(in_dir / it["audio"]))
            emb = g.get_embedding(speech, fe, enc)
            torch.save(emb, tmp / "1.pt")
            sf.write(tmp / "sum.wav", speech, 16000)
            clip = {"prompt": it["prompt"], "cond_video": str(in_dir / it["image"]),
                    "cond_audio": {"person1": str(tmp / "1.pt")}, "video_audio": str(tmp / "sum.wav")}
            video = pipe.generate_infinitetalk(clip, size_buckget=args.size, motion_frame=args.motion_frame,
                                               frame_num=args.frame_num, shift=args.sample_shift, sampling_steps=args.sample_steps,
                                               text_guide_scale=args.sample_text_guide_scale,
                                               audio_guide_scale=args.sample_audio_guide_scale, seed=int(it.get("seed", 42)),
                                               offload_model=True, max_frames_num=args.max_frame_num,
                                               color_correction_strength=args.color_correction_strength, extra_args=args)
            g.save_video_ffmpeg(video, str(dst.with_suffix("")), [str(tmp / "sum.wav")], high_quality_save=True)
            rep |= {"ok": dst.exists() and dst.stat().st_size > 1024, "seconds": round(time.time() - t0, 1)}
        except Exception as e:  # noqa: BLE001
            traceback.print_exc()
            rep |= {"ok": False, "error": str(e)[:300], "seconds": round(time.time() - t0, 1)}
        print(json.dumps(rep), flush=True)
        reports.setdefault(it["job_id"], []).append(rep)
        (out_dir / "report.json").write_text(json.dumps(reports[it["job_id"]], indent=1))
        done += 1
        if os.environ.get("PUSH_HOOK") and done % int(os.environ.get("PUSH_EVERY", 4)) == 0:
            subprocess.run(os.environ["PUSH_HOOK"] + f" 'infinitetalk {done}'", shell=True)


if __name__ == "__main__":
    main()
