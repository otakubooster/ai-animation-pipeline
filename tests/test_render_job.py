"""Batch renderer logic with a fake LightX2V pipeline (no GPU)."""

import json
import sys
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parents[1] / "jobs"))
import render_job  # noqa: E402


class FakePipe:
    def __init__(self, fail_first_for=()):
        self.calls, self.fail = [], set(fail_first_for)

    def generate(self, **kw):
        self.calls.append(kw)
        shot = Path(kw["save_result_path"]).stem
        if shot in self.fail:
            self.fail.discard(shot)
            raise RuntimeError("CUDA out of memory")
        Path(kw["save_result_path"]).write_bytes(b"mp4")


def make_job(tmp, job_id, n):
    jp = tmp / f"{job_id}.json"
    jp.write_text(json.dumps({"job_id": job_id, "render": {"width": 720, "height": 1280},
                              "shots": [{"id": f"s{i:02d}", "frame": f"Shot_{i:02d}.png", "motion_prompt": "m",
                                         "seed": i, "num_frames": 121} for i in range(1, n + 1)]}))
    return jp


def test_batch_retries_resumes_and_pushes(tmp_path, monkeypatch):
    pushes = []
    monkeypatch.setattr(render_job.subprocess, "run", lambda cmd, shell: pushes.append(cmd))
    pipe, counter = FakePipe(fail_first_for={"s02"}), [0]
    out_a = tmp_path / "out_a"
    s = render_job.render_job(pipe, make_job(tmp_path, "a", 3), tmp_path, out_a, 0.6, "push", 2, counter)
    assert s["clips_ok"] == 3 and s["clips_failed"] == 0
    s02 = [c for c in pipe.calls if c["save_result_path"].endswith("s02.mp4")]
    assert [c["num_frames"] for c in s02] == [121, 81]               # retried with fewer frames after OOM
    assert pushes == ["push"]                                        # every 2 clips -> once for 3 clips
    calls_before = len(pipe.calls)
    render_job.render_job(pipe, make_job(tmp_path, "a", 3), tmp_path, out_a, 0.6, "", 2, counter)
    assert len(pipe.calls) == calls_before                           # resume: nothing re-rendered


def test_shot_that_always_fails_does_not_stop_the_batch(tmp_path):
    class Broken(FakePipe):
        def generate(self, **kw):
            raise RuntimeError("bad frame")
    s = render_job.render_job(Broken(), make_job(tmp_path, "b", 2), tmp_path, tmp_path / "out_b", 0.6, "", 10, [0])
    assert s["clips_failed"] == 2
    report = json.loads((tmp_path / "out_b" / "render.json").read_text())
    assert len(report["runs"][0]["attempts"]) == 3                   # full, shorter, 480p


def test_shot_render_size_overrides_job_size():
    import render_job
    a = render_job.attempts_for({"num_frames": 97, "render_size": [720, 960]}, 720, 1280)
    assert a[0] == (960, 720, 97)
