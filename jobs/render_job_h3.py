"""Render the shots of one or many ad jobs with MiniMax H3 (open weights, self-hosted ComfyUI) on the GPU box.

Same job.json as the Wan worker (render_job.py): each shot's first frame (`frame`) is animated with H3's FL2VA
mode, optionally ending on `last_frame` (another frame file, e.g. the next shot's frame for a continuous scene).
H3 renders 4-15 s at 24 fps with native audio, so a clip is made exactly as long as its VO line needs
(`h3_seconds`, else vo_s + 0.9 s, else num_frames/16) - no slow-down or looping in the edit.

Several boxes can split one batch: SHARD="i/N" gives this box every shot the shared longest-first assignment
puts on box i (all boxes compute the same assignment). Clips: OUT_DIR/clips/<id>.mp4; timing:
OUT_DIR/render_<i>.json; a finished shard writes OUT_DIR/READY.<i> (plain READY when N == 1).
A failed shot is retried with a new seed, then shorter, so one bad shot never stops the batch; shots already
rendered are skipped (resume).

  python render_job_h3.py a.json:frames/a:out/a  b.json:frames/b:out/b ...   [--server http://127.0.0.1:8188]
Licence: MiniMax H3 Community License - no use or display of the model or its outputs in the EU, UK, US or
South Korea; the GPU host is chosen accordingly (launch.py --territory h3).
"""

from __future__ import annotations

import argparse
import json
import os
import shutil
import subprocess
import sys
import time
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parents[1] / "phase0"))
from h3_render import COMFY, api_graph, frames_for, post, wait  # noqa: E402

SOUND = " Sound: quiet natural ambient sound of the scene only; no speech, no voices, no music."
SOUND_TALK = " Sound: only the character's voice and quiet room tone; no music."   # talking shots: real mouth movement
DEFAULT_AREA = 576 * 1024


def clip_seconds(shot: dict) -> float:
    if shot.get("h3_seconds"):
        s = float(shot["h3_seconds"])
    elif shot.get("vo_s"):
        s = float(shot["vo_s"]) + 0.9          # 0.5 s head trim in the edit + a short tail
    elif shot.get("num_frames"):
        s = shot["num_frames"] / 16
    else:
        s = 5.0
    return round(min(max(s, 4.0), 15.0), 2)


def size_for(frame: Path, area: int = DEFAULT_AREA) -> list[int]:
    """Render size with the frame's own aspect ratio (no crop), ~area pixels, sides multiples of 32."""
    from PIL import Image

    w, h = Image.open(frame).size
    k = (area / (w * h)) ** 0.5
    return [max(256, round(w * k / 32) * 32), max(256, round(h * k / 32) * 32)]


def assign(items: list[tuple[str, str, float]], n: int) -> dict[tuple[str, str], int]:
    """Longest-first greedy split of (job_id, shot_id, seconds) over n boxes; deterministic."""
    load = [0.0] * n
    out = {}
    for job_id, sid, s in sorted(items, key=lambda x: (-x[2], x[0], x[1])):
        i = min(range(n), key=lambda b: (load[b], b))
        load[i] += s
        out[(job_id, sid)] = i
    return out


def write(path: Path, data) -> None:
    tmp = path.with_suffix(".tmp")
    tmp.write_text(json.dumps(data, indent=2))
    tmp.replace(path)


def stage_image(src: Path, job_id: str) -> str:
    dst = COMFY / "input" / f"{job_id}__{src.name}"
    dst.parent.mkdir(parents=True, exist_ok=True)
    if not dst.exists():
        shutil.copy(src, dst)
    return dst.name


def render_shot(server: str, job: dict, shot: dict, frames_dir: Path, out: Path, area: int) -> dict:
    first = frames_dir / shot["frame"]
    size = shot.get("h3_size") or size_for(first, area)
    secs = clip_seconds(shot)
    images = {"first": stage_image(first, job["job_id"])}
    if shot.get("last_frame"):
        images["last"] = stage_image(frames_dir / shot["last_frame"], job["job_id"])
    prompt = (shot.get("h3_prompt") or shot.get("motion_prompt", "")).strip() + job.get("h3_prompt_suffix", "") + (SOUND_TALK if shot.get("talking") else SOUND)
    run = {"id": shot["id"], "file": f"clips/{shot['id']}.mp4", "size": size, "attempts": []}
    seed = int(shot.get("seed") or 1)
    for k, (sd, s) in enumerate([(seed, secs), (seed + 1000, secs), (seed + 2000, min(secs, 6.0))]):
        spec = {"id": f"{job['job_id']}__{shot['id']}_{k}", "mode": "fl2va", "turbo": True, "size": size, "seed": sd,
                "seconds": s, "prompt": prompt, "first_frame": "first", "last_frame": "last" if "last" in images else None}
        t = time.perf_counter()
        res = post(server, "/prompt", {"prompt": api_graph(spec, images, "")})
        err = res.get("body") if res.get("http_error") else None
        if not err:
            h = wait(server, res["prompt_id"], timeout_s=1800)
            vids = sorted((COMFY / "output" / "h3").glob(f"{spec['id']}_*.mp4"), key=lambda p: p.stat().st_mtime)
            if vids:
                shutil.move(str(vids[-1]), out / run["file"])
                run.update(ok=True, seconds=round(time.perf_counter() - t, 1), seed=sd, clip_s=s,
                           num_frames=frames_for(s), fps=24)
                return run
            err = json.dumps(h.get("status", h))[:1500]
        run["attempts"].append({"seed": sd, "clip_s": s, "seconds": round(time.perf_counter() - t, 1), "error": str(err)[:1500]})
    run["ok"] = False
    return run


def main() -> None:
    ap = argparse.ArgumentParser()
    ap.add_argument("specs", nargs="+", help="JOB_JSON:FRAMES_DIR:OUT_DIR")
    ap.add_argument("--server", default="http://127.0.0.1:8188")
    a = ap.parse_args()
    dph = float(os.environ.get("SPIKE_DPH", "0") or 0)
    hook, push_every = os.environ.get("PUSH_HOOK", ""), int(os.environ.get("PUSH_EVERY", "3"))
    me, n = (int(x) for x in os.environ.get("SHARD", "0/1").split("/"))
    tag = str(me) if n > 1 else ""

    jobs = []
    for spec in a.specs:
        job_json, frames_dir, out = (Path(p) for p in spec.split(":"))
        jobs.append((json.loads(job_json.read_text()), frames_dir, out))
    todo = [(j["job_id"], s["id"], clip_seconds(s)) for j, _, _ in jobs for s in j["shots"] if not s.get("skip_render")]
    owner = assign(todo, n)
    mine = sum(1 for v in owner.values() if v == me)
    print(json.dumps({"shard": f"{me}/{n}", "shots": mine, "clip_seconds": round(sum(s for (j, i, s) in todo if owner[(j, i)] == me), 1)}), flush=True)

    counter = 0
    for job, frames_dir, out in jobs:
        (out / "clips").mkdir(parents=True, exist_ok=True)
        rp = out / (f"render_{tag}.json" if tag else "render.json")
        report = json.loads(rp.read_text()) if rp.exists() else {"job_id": job["job_id"], "engine": "minimax-h3-fl2va", "runs": []}
        done = {r["id"] for r in report["runs"] if r.get("ok") and (out / r["file"]).exists()}
        area = int(job.get("render_h3", {}).get("area", DEFAULT_AREA))
        for shot in job["shots"]:
            if shot.get("skip_render") or owner.get((job["job_id"], shot["id"])) != me or shot["id"] in done:
                continue
            try:
                run = render_shot(a.server, job, shot, frames_dir, out, area)
            except Exception as e:          # e.g. a missing or unreadable frame: log it, keep the batch going
                run = {"id": shot["id"], "file": f"clips/{shot['id']}.mp4", "ok": False, "error": repr(e)[:800], "seconds": 0}
            if run.get("ok"):
                run["usd"] = round(run["seconds"] * dph / 3600, 4)
            report["runs"] = [r for r in report["runs"] if r["id"] != shot["id"]] + [run]
            ok = [r for r in report["runs"] if r.get("ok")]
            report["summary"] = {"clips_ok": len(ok), "clips_failed": len(report["runs"]) - len(ok),
                                 "render_s_total": round(sum(r["seconds"] for r in ok), 1),
                                 "usd_total": round(sum(r["seconds"] for r in ok) * dph / 3600, 4)}
            write(rp, report)
            print(json.dumps({"job": job["job_id"], **{k: v for k, v in run.items() if k != "attempts"}}), flush=True)
            counter += 1
            if hook and counter % push_every == 0:
                subprocess.run(f"{hook} clips", shell=True)
        (out / (f"READY.{tag}" if tag else "READY")).touch()
        if hook:
            subprocess.run(f"{hook} '{job['job_id']} ready{(' shard ' + tag) if tag else ''}'", shell=True)


if __name__ == "__main__":
    main()
