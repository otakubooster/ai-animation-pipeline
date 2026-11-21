"""assembly/color_lock.py: a colour cast is pulled back to the reference palette."""
import sys
from pathlib import Path

import numpy as np

sys.path.insert(0, str(Path(__file__).resolve().parents[1] / "assembly"))
import color_lock  # noqa: E402


def test_cast_removed():
    import cv2
    rng = np.random.default_rng(0)
    ref = np.clip(rng.normal([180, 160, 230], 20, (64, 36, 3)), 0, 255).astype(np.uint8)        # pink-ish (BGR)
    teal = np.clip(ref.astype(int) + [40, 30, -70], 0, 255).astype(np.uint8)                     # drifted to teal
    rm, rs = color_lock.stats(cv2.cvtColor(ref, cv2.COLOR_BGR2LAB).astype(np.float32))
    out = color_lock.lock_frame(teal, rm, rs, 0.5)
    m_out = cv2.cvtColor(out, cv2.COLOR_BGR2LAB).reshape(-1, 3).mean(0)
    m_teal = cv2.cvtColor(teal, cv2.COLOR_BGR2LAB).reshape(-1, 3).mean(0)
    assert np.abs(m_out[1:] - rm[1:]).max() < 2.0          # a/b back on the reference
    assert np.abs(m_teal[1:] - rm[1:]).max() > 10.0        # (the cast was real)
