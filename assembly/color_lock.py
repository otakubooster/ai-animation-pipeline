"""Pull a clip's colours back to its source frame's palette, frame by frame (H3 colour drift: a pink scene that turns
teal or green over a few seconds).

Each frame's Lab statistics are matched to the reference image's (mean and std of a/b fully, of L by `l_weight`), so a
gradual cast is removed while motion, light pulses and shading stay. Use it only on shots whose content is fine and
whose colour drifts; it cannot fix objects that change colour on purpose.

  python assembly/color_lock.py CLIP REFERENCE_IMAGE OUT [--l-weight 0.5]
"""
from __future__ import annotations

import argparse
import subprocess
from pathlib import Path

import numpy as np


def stats(lab: np.ndarray) -> tuple[np.ndarray, np.ndarray]:
    flat = lab.reshape(-1, 3)
    return flat.mean(0), flat.std(0) + 1e-6


def lock_frame(frame_bgr: np.ndarray, ref_mean: np.ndarray, ref_std: np.ndarray, l_weight: float) -> np.ndarray:
    import cv2

    lab = cv2.cvtColor(frame_bgr, cv2.COLOR_BGR2LAB).astype(np.float32)
    m, s = stats(lab)
    w = np.array([l_weight, 1.0, 1.0], np.float32)
    tm, ts = m + (ref_mean - m) * w, s + (ref_std - s) * w
    out = (lab - m) / s * ts + tm
    return cv2.cvtColor(np.clip(out, 0, 255).astype(np.uint8), cv2.COLOR_LAB2BGR)


def main() -> None:
    import cv2

    ap = argparse.ArgumentParser()
    ap.add_argument("clip", type=Path)
    ap.add_argument("reference", type=Path)
    ap.add_argument("out", type=Path)
    ap.add_argument("--l-weight", type=float, default=0.5)
    a = ap.parse_args()
    cap = cv2.VideoCapture(str(a.clip))
    fps = cap.get(cv2.CAP_PROP_FPS) or 24
    w, h = int(cap.get(cv2.CAP_PROP_FRAME_WIDTH)), int(cap.get(cv2.CAP_PROP_FRAME_HEIGHT))
    ref = cv2.resize(cv2.imread(str(a.reference)), (w, h))
    rm, rs = stats(cv2.cvtColor(ref, cv2.COLOR_BGR2LAB).astype(np.float32))
    enc = subprocess.Popen(["ffmpeg", "-v", "error", "-y", "-f", "rawvideo", "-pix_fmt", "bgr24", "-s", f"{w}x{h}", "-r", str(fps),
                            "-i", "-", "-i", str(a.clip), "-map", "0:v", "-map", "1:a?", "-c:v", "libx264", "-crf", "16",
                            "-pix_fmt", "yuv420p", "-c:a", "copy", str(a.out)], stdin=subprocess.PIPE)
    n = 0
    while True:
        ok, fr = cap.read()
        if not ok:
            break
        enc.stdin.write(lock_frame(fr, rm, rs, a.l_weight).tobytes())
        n += 1
    enc.stdin.close()
    enc.wait()
    print(a.out, n, "frames")


if __name__ == "__main__":
    main()
