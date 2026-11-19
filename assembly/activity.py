"""Subject activity over a clip, with camera motion compensated, to find an idle tail.

H3 clips often perform their one action in the first seconds and then hold the pose (sometimes under a slow camera
push-in): played to the end, the shot reads as "moves, then freezes and zooms". `active_end` returns the time after
which the clip stays idle, so the editor can end the usable window there instead of playing the frozen tail.

Activity = mean absolute difference of consecutive frames (8 fps, 144x256 grey) after aligning them with the global
similarity transform (LK-tracked corners + RANSAC), so pans, sway and push-ins count far less than people moving.
"""
from __future__ import annotations

import json
import subprocess
from pathlib import Path

import numpy as np

W, H, FPS = 144, 256, 8


def activity(src: Path) -> np.ndarray:
    """Per-step activity (len = frames - 1 at FPS)."""
    import cv2

    raw = subprocess.run(["ffmpeg", "-v", "error", "-i", str(src), "-vf", f"fps={FPS},scale={W}:{H},format=gray",
                          "-f", "rawvideo", "-"], capture_output=True, check=True).stdout
    fr = np.frombuffer(raw, np.uint8).reshape(-1, H, W)
    inner = (slice(int(H * .06), int(H * .94)), slice(int(W * .06), int(W * .94)))
    out = []
    for a, b in zip(fr[:-1], fr[1:]):
        m = None
        pts = cv2.goodFeaturesToTrack(a, 150, 0.01, 6)
        if pts is not None and len(pts) >= 8:
            nxt, st, _ = cv2.calcOpticalFlowPyrLK(a, b, pts, None)
            ok = st.ravel() == 1
            if ok.sum() >= 8:
                m, _ = cv2.estimateAffinePartial2D(pts[ok], nxt[ok], method=cv2.RANSAC, ransacReprojThreshold=1.0)
        aw = cv2.warpAffine(a, m, (W, H), borderMode=cv2.BORDER_REPLICATE) if m is not None else a
        out.append(float(np.abs(aw[inner].astype(np.float32) - b[inner].astype(np.float32)).mean()))
    return np.array(out)


def idle_from(act: np.ndarray, rel: float = 0.6, floor: float = 2.2, min_tail: float = 1.2) -> float | None:
    """Start time (s) of an idle tail, or None. Idle = smoothed activity under min(rel x the clip's 85th percentile,
    floor) from that time to the end, for at least min_tail seconds."""
    if len(act) < FPS * 2:
        return None
    sm = np.convolve(act, np.ones(4) / 4, mode="same")
    peak = float(np.percentile(sm[2:], 85))
    thr = min(rel * peak, floor)
    k = len(sm)
    while k > 0 and sm[k - 1] < thr:
        k -= 1
    tail = (len(sm) - k) / FPS
    return k / FPS if tail >= min_tail and k > FPS else None


def active_end(src: Path, cache: Path | None = None, pad: float = 0.4) -> float | None:
    """End of the active part of `src` (idle tail start + pad), None when the clip stays active. Cached per file."""
    store = json.loads(cache.read_text()) if cache and cache.exists() else {}
    key = f"{src}:{src.stat().st_mtime}"
    if key not in store:
        t = idle_from(activity(src))
        store[key] = None if t is None else round(t + pad, 2)
        if cache:
            cache.write_text(json.dumps(store))
    return store[key]
