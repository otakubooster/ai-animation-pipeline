"""Unit tests for the Phase 0 launcher and report logic (no network, no GPU)."""

import importlib
import json
import sys
from pathlib import Path

import pytest

sys.path.insert(0, str(Path(__file__).resolve().parents[1] / "phase0"))
launch = importlib.import_module("launch")


def offer(**kw):
    base = {"id": 1, "dph_total": 0.6, "inet_down": 800, "inet_down_cost": 0.004}
    base.update(kw)
    return base


def test_est_cost_penalises_expensive_bandwidth():
    cheap_bw = offer(id=1, dph_total=0.65, inet_down_cost=2.67 / 1024)
    pricey_bw = offer(id=2, dph_total=0.60, inet_down_cost=40 / 1024)
    assert launch.est_cost(cheap_bw) < launch.est_cost(pricey_bw)


def test_est_cost_penalises_slow_download():
    fast, slow = offer(inet_down=5000), offer(inet_down=200)
    assert launch.est_cost(fast) < launch.est_cost(slow)


@pytest.mark.parametrize("elapsed,dph,expect", [
    (3600, 1.0, None),                  # $1 after 1 h, under both caps
    (5 * 3600, 1.0, "spend"),           # $5 hits the $5 cap
    (2.6 * 3600, 0.5, "runtime"),       # 2.6 h over the 2.5 h cap
])
def test_over_cap(elapsed, dph, expect):
    reason = launch.over_cap(elapsed, dph, max_usd=5, max_hours=2.5)
    assert (reason is None) if expect is None else (expect in reason)


def test_create_refuses_without_yes():
    with pytest.raises(SystemExit, match="--yes"):
        launch.main(["create", "--offer", "1"])


def test_report_summarise(tmp_path, monkeypatch):
    monkeypatch.setenv("SPIKE_DPH", "0.72")
    report = importlib.import_module("report")
    importlib.reload(report)
    p = tmp_path / "engine_a_nvfp4.json"
    p.write_text(json.dumps({"engine": "A", "variant": "nvfp4", "load_s": 90, "runs": [
        {"task": "i2v", "ok": True, "seconds": 40, "cold": True},
        {"task": "i2v", "ok": True, "seconds": 12},
        {"task": "i2v", "ok": True, "seconds": 14},
        {"task": "flf2v", "ok": False, "seconds": 3, "error": "boom"},
    ]}))
    s = report.summarise(p)
    assert s["tasks"]["i2v"]["median_s_per_clip"] == 13
    assert s["tasks"]["i2v"]["cold_first_clip_s"] == 40
    assert s["tasks"]["i2v"]["usd_per_clip"] == round(13 * 0.72 / 3600, 4)
    assert s["tasks"]["flf2v"]["failed"] == 1 and "boom" in s["tasks"]["flf2v"]["first_error"]


def test_prepare_cases_fit(tmp_path):
    from PIL import Image
    prep = importlib.import_module("prepare_cases")
    src = tmp_path / "in.png"
    Image.new("RGB", (1024, 1536), "red").save(src)
    out = Path(prep.fit(src, tmp_path / "out.png", 480, 832))
    assert Image.open(out).size == (480, 832)
