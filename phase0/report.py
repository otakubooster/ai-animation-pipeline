"""Summarise the spike: per-engine timings, $ per clip, downloads, and the overall instance cost.

Reads results/*.json and writes results/summary.json + results/report.md.
Cost uses SPIKE_DPH (the instance's $/hour, set by launch.py). Clip cost = clip seconds x $/s.
"""

from __future__ import annotations

import json
import os
import statistics
import time
from pathlib import Path

from common import RESULTS, write_json

DPH = float(os.environ.get("SPIKE_DPH", "0") or 0)


def usd(seconds: float) -> float:
    return round(seconds * DPH / 3600, 4)


def summarise(path: Path) -> dict:
    r = json.loads(path.read_text())
    out = {"engine": r.get("engine"), "variant": r.get("variant"), "gpu": r.get("gpu"),
           "load_s": r.get("load_s") or r.get("server_start_s"), "load_error": bool(r.get("load_error")),
           "tasks": {}}
    for task in sorted({x["task"] for x in r.get("runs", [])}):
        runs = [x for x in r["runs"] if x["task"] == task]
        ok = [x for x in runs if x.get("ok")]
        warm = [x["seconds"] for x in ok if not x.get("cold")]
        cold = [x["seconds"] for x in ok if x.get("cold")]
        med = statistics.median(warm or [x["seconds"] for x in ok]) if ok else None
        out["tasks"][task] = {
            "ok": len(ok), "failed": len(runs) - len(ok),
            "median_s_per_clip": med, "cold_first_clip_s": cold[0] if cold else None,
            "usd_per_clip": usd(med) if med else None,
            "first_error": next((x["error"][-600:] for x in runs if not x.get("ok")), None),
        }
    return out


def main() -> None:
    engines = [summarise(p) for p in sorted(RESULTS.glob("engine_*.json")) if not p.name.endswith("_config.json")]
    timeline = [json.loads(line) for line in (RESULTS / "timeline.jsonl").read_text().splitlines()] \
        if (RESULTS / "timeline.jsonl").exists() else []
    models = json.loads((RESULTS / "models.json").read_text()) if (RESULTS / "models.json").exists() else {}
    wall = timeline[-1]["t"] if timeline else 0
    summary = {"generated": time.strftime("%Y-%m-%dT%H:%M:%SZ", time.gmtime()), "dph": DPH,
               "wall_s_so_far": wall, "instance_usd_so_far": usd(wall), "engines": engines,
               "downloads": models.get("downloads", []), "model_errors": models.get("errors", [])}
    write_json(RESULTS / "summary.json", summary)

    lines = [f"# Phase 0 spike report ({summary['generated']})", "",
             f"Instance price: ${DPH:.3f}/h. Wall time so far: {wall/60:.1f} min ≈ ${summary['instance_usd_so_far']:.2f}.", "",
             "| Engine | Variant | Task | OK/fail | Load s | Median s/clip | Cold 1st clip s | $/clip |",
             "|---|---|---|---|---|---|---|---|"]
    for e in engines:
        for task, t in e["tasks"].items():
            lines.append(f"| {e['engine']} | {e['variant']} | {task} | {t['ok']}/{t['failed']} | {e['load_s']} | "
                         f"{t['median_s_per_clip']} | {t['cold_first_clip_s']} | {t['usd_per_clip']} |")
        if e["load_error"]:
            lines.append(f"| {e['engine']} | {e['variant']} | — | load failed | | | | |")
    lines += ["", "## Downloads", ""] + [f"- {d['repo']} / {d['file']}: {d.get('gb', '?')} GB in {d['seconds']} s"
                                       for d in summary["downloads"]]
    errs = [(e["variant"], t, v["first_error"]) for e in engines for t, v in e["tasks"].items() if v["first_error"]]
    if errs or summary["model_errors"]:
        lines += ["", "## Errors", ""] + [f"- {v} {t}: ```{err}```" for v, t, err in errs] + \
                 [f"- {x}" for x in summary["model_errors"]]
    (RESULTS / "report.md").write_text("\n".join(lines) + "\n")
    print("\n".join(lines))


if __name__ == "__main__":
    main()
