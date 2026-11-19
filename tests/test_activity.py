"""assembly/activity.py: idle-tail detection (synthetic activity curves and a rendered clip)."""
import subprocess
import sys
from pathlib import Path

import numpy as np

sys.path.insert(0, str(Path(__file__).resolve().parents[1] / "assembly"))
import activity  # noqa: E402

FPS = activity.FPS


def test_active_then_idle_tail_is_found():
    act = np.r_[np.full(3 * FPS, 4.0), np.full(3 * FPS, 0.4)]       # 3 s of motion, then 3 s frozen
    t = activity.idle_from(act)
    assert t is not None and 2.5 <= t <= 3.5


def test_steady_motion_has_no_idle_tail():
    rng = np.random.default_rng(1)
    act = 3.0 + rng.normal(0, 0.4, 6 * FPS)
    assert activity.idle_from(act) is None


def test_short_idle_tail_is_ignored():
    act = np.r_[np.full(5 * FPS, 4.0), np.full(int(0.6 * FPS), 0.3)]  # 0.6 s pause at the end: not a freeze
    assert activity.idle_from(act) is None


def test_quiet_but_alive_clip_is_not_idle():
    act = np.full(6 * FPS, 0.9)                                       # calm talking head: low but even activity
    assert activity.idle_from(act) is None


def test_rendered_clip(tmp_path):
    from PIL import Image, ImageFilter
    clip, bg = tmp_path / "c.mp4", tmp_path / "bg.png"
    rng = np.random.default_rng(0)        # a static textured background (real footage has corners to track)
    Image.fromarray(rng.integers(0, 255, (512, 288), dtype=np.uint8)).filter(ImageFilter.GaussianBlur(2)).save(bg)
    # a box moving for 2 s, then standing still for 2.5 s
    subprocess.run(["ffmpeg", "-v", "error", "-y", "-loop", "1", "-r", "24", "-i", str(bg), "-f", "lavfi", "-i",
                    "color=c=white:s=90x90:r=24", "-filter_complex", "[0:v][1:v]overlay=x='min(t,2)*80':y=200",
                    "-t", "4.5", "-pix_fmt", "yuv420p", str(clip)], check=True)
    end = activity.active_end(clip, tmp_path / "act.json")
    assert end is not None and 1.8 <= end <= 3.0
    assert (tmp_path / "act.json").exists()
