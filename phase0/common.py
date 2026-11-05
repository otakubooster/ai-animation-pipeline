"""Shared helpers for the Phase 0 spike (runs on the GPU box)."""

from __future__ import annotations

import json
import subprocess
import time
from pathlib import Path

HERE = Path(__file__).resolve().parent
RESULTS = Path("/workspace/spike/results")
CASES_FILE = Path("/workspace/spike/cases.json")


def now() -> str:
    return time.strftime("%Y-%m-%dT%H:%M:%SZ", time.gmtime())


def write_json(path: Path, data) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    tmp = path.with_suffix(".tmp")
    tmp.write_text(json.dumps(data, indent=2))
    tmp.replace(path)


def load_cases(path: Path) -> list[dict]:
    return json.loads(path.read_text())


def gpu_info() -> str:
    try:
        return subprocess.run(
            ["nvidia-smi", "--query-gpu=name,memory.total,driver_version", "--format=csv,noheader"],
            capture_output=True, text=True, timeout=20,
        ).stdout.strip()
    except Exception as exc:  # pragma: no cover - diagnostic only
        return f"unknown ({exc})"
