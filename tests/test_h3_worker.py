"""H3 batch worker: clip lengths, shard split, render size, ComfyUI graph (no GPU, no server)."""
import importlib.util
import sys
from pathlib import Path

from PIL import Image

ROOT = Path(__file__).resolve().parents[1]
spec = importlib.util.spec_from_file_location("render_job_h3", ROOT / "jobs" / "render_job_h3.py")
rj = importlib.util.module_from_spec(spec)
sys.modules["render_job_h3"] = rj
spec.loader.exec_module(rj)


def test_clip_seconds_follows_vo_and_h3_limits():
    assert rj.clip_seconds({"vo_s": 6.1}) == 7.0
    assert rj.clip_seconds({"vo_s": 1.0}) == 4.0
    assert rj.clip_seconds({"vo_s": 20}) == 15.0
    assert rj.clip_seconds({"h3_seconds": 9.5, "vo_s": 3}) == 9.5
    assert rj.clip_seconds({"num_frames": 81}) == 5.06
    assert rj.clip_seconds({}) == 5.0


def test_assign_balances_and_is_deterministic():
    items = [("a", f"s{i}", s) for i, s in enumerate([15, 4, 4, 7, 7, 10, 5, 6])]
    one, two = rj.assign(items, 3), rj.assign(list(reversed(items)), 3)
    assert one == two
    load = [sum(s for j, i, s in items if one[(j, i)] == b) for b in range(3)]
    assert max(load) - min(load) <= 4
    assert set(rj.assign(items, 1).values()) == {0}


def test_size_keeps_frame_aspect(tmp_path):
    for (w, h), want in [((1080, 1920), [576, 1024]), ((1024, 1024), [768, 768]), ((1920, 1080), [1024, 576])]:
        p = tmp_path / f"{w}x{h}.png"
        Image.new("RGB", (w, h)).save(p)
        assert rj.size_for(p) == want


def test_graph_uses_first_and_last_frame():
    g = rj.api_graph({"id": "x", "mode": "fl2va", "size": [576, 1024], "seconds": 7, "prompt": "p",
                      "first_frame": "first", "last_frame": "last"}, {"first": "a.png", "last": "b.png"}, "")
    cond = g["cond"]["inputs"]
    assert g["cond"]["class_type"] == "MiniMaxH3ImageToVideo"
    assert cond["first_frame"] == ["img_first_frame", 0] and cond["last_frame"] == ["img_last_frame", 0]
    assert cond["length"] % 17 == 5 and cond["length"] >= 7 * 24
    g2 = rj.api_graph({"id": "y", "mode": "fl2va", "prompt": "p", "first_frame": "first", "last_frame": None},
                      {"first": "a.png"}, "")
    assert "last_frame" not in g2["cond"]["inputs"]
