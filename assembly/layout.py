"""Where the faces are, so text never covers them and punch-ins stay framed on them.

Faces come from MediaPipe's BlazeFace full-range detector (works on photoreal and on 3D Pixar-style faces), sampled
on a few frames of the part of the clip a shot actually shows. Boxes are normalised [x0, y0, x1, y1] in the
1080x1920 output frame and padded to cover hair and chin. `free_slot` picks a text position (centre y) from a list
of candidates that keeps a text band clear of every face box.
"""

from __future__ import annotations

import io
import subprocess
from pathlib import Path

import numpy as np
from PIL import Image

MODEL = Path(__file__).resolve().parent / "models" / "blaze_face_full_range.tflite"
_DET = None


def _detector():
    global _DET
    if _DET is None:
        import mediapipe as mp  # noqa: F401
        from mediapipe.tasks import python as mpt
        from mediapipe.tasks.python import vision
        _DET = vision.FaceDetector.create_from_options(vision.FaceDetectorOptions(
            base_options=mpt.BaseOptions(model_asset_path=str(MODEL)), min_detection_confidence=0.5))
    return _DET


def faces_in(img: Image.Image) -> list[list[float]]:
    """Face boxes in `img`, normalised, padded: +45 % of the face height above (hair), +18 % below (chin), +20 % sides."""
    import mediapipe as mp
    img = img.convert("RGB")
    w, h = img.size
    res = _detector().detect(mp.Image(image_format=mp.ImageFormat.SRGB, data=np.asarray(img)))
    out = []
    for d in res.detections:
        b = d.bounding_box
        x0, y0, bw, bh = b.origin_x / w, b.origin_y / h, b.width / w, b.height / h
        if bw * bh < 0.002:          # tiny background faces do not matter for text
            continue
        out.append([max(0.0, x0 - 0.20 * bw), max(0.0, y0 - 0.45 * bh), min(1.0, x0 + 1.20 * bw), min(1.0, y0 + 1.18 * bh)])
    return out


def frame_at(src: Path, t: float | None) -> Image.Image | None:
    """One frame of a clip (t seconds in) or the image itself (t None)."""
    cmd = ["ffmpeg", "-v", "error"] + (["-ss", f"{t:.2f}"] if t is not None else []) + ["-i", str(src), "-frames:v", "1",
                                                                                       "-f", "image2pipe", "-vcodec", "png", "-"]
    data = subprocess.run(cmd, capture_output=True).stdout
    return Image.open(io.BytesIO(data)) if data else None


def cover_map(sw: int, sh: int, W: int = 1080, H: int = 1920):
    """Map normalised source coords to normalised output coords for scale-to-cover + centre crop."""
    s = max(W / sw, H / sh)
    ox, oy = (sw * s - W) / 2, (sh * s - H) / 2
    return lambda x, y: ((x * sw * s - ox) / W, (y * sh * s - oy) / H)


def shot_faces(src: Path, times: list[float] | None) -> list[list[float]]:
    """Face boxes (output-normalised) over the given clip times (None = src is an image); boxes that overlap across
    samples are merged into their union, so a moving head keeps one box covering its whole path."""
    boxes = []
    for t in (times or [None]):
        im = frame_at(src, t)
        if im is None:
            continue
        m = cover_map(*im.size)
        for b in faces_in(im):
            (x0, y0), (x1, y1) = m(b[0], b[1]), m(b[2], b[3])
            boxes.append([max(0, x0), max(0, y0), min(1, x1), min(1, y1)])
    merged: list[list[float]] = []
    for b in boxes:
        for m_ in merged:
            if not (b[2] < m_[0] or b[0] > m_[2] or b[3] < m_[1] or b[1] > m_[3]):
                m_[:] = [min(m_[0], b[0]), min(m_[1], b[1]), max(m_[2], b[2]), max(m_[3], b[3])]
                break
        else:
            merged.append(list(b))
    return merged


def zoom_rect(zoom: float, faces: list[list[float]], face_y: float = 0.40) -> list[float]:
    """Visible rect [x0, y0, x1, y1] (normalised) of a punch-in by `zoom`, centred on the main face (the face centre
    lands at `face_y` of the frame height) or on the frame centre when there is no face."""
    span = 1 / zoom
    if faces:
        f = max(faces, key=lambda b: (b[2] - b[0]) * (b[3] - b[1]))
        cx, cy = (f[0] + f[2]) / 2, (f[1] + f[3]) / 2
        x0, y0 = cx - span / 2, cy - face_y * span
    else:
        x0, y0 = 0.5 - span / 2, 0.45 - span / 2
    x0, y0 = min(max(x0, 0), 1 - span), min(max(y0, 0), 1 - span)
    return [x0, y0, x0 + span, y0 + span]


def in_rect(boxes: list[list[float]], rect: list[float]) -> list[list[float]]:
    """Face boxes seen through a crop rect, renormalised to it (boxes outside are dropped)."""
    x0, y0, x1, y1 = rect
    sx, sy = x1 - x0, y1 - y0
    out = []
    for b in boxes:
        nb = [(b[0] - x0) / sx, (b[1] - y0) / sy, (b[2] - x0) / sx, (b[3] - y0) / sy]
        if nb[2] > 0 and nb[0] < 1 and nb[3] > 0 and nb[1] < 1:
            out.append([max(0, nb[0]), max(0, nb[1]), min(1, nb[2]), min(1, nb[3])])
    return out


def overlap(band: tuple[float, float, float, float], boxes: list[list[float]]) -> float:
    """Area of a text band [x0, y0, x1, y1] that covers face boxes."""
    tot = 0.0
    for b in boxes:
        w = min(band[2], b[2]) - max(band[0], b[0])
        h = min(band[3], b[3]) - max(band[1], b[1])
        if w > 0 and h > 0:
            tot += w * h
    return tot


def free_slot(cands: list[float], height: float, boxes: list[list[float]], x0: float = 0.1, x1: float = 0.9,
              taken: list[tuple[float, float]] = (), margin: float = 0.012) -> float:
    """First candidate centre y whose band (height + margins, x0..x1) covers no face and no taken band; else the one
    that covers the least."""
    def cost(y):
        band = (x0, y - height / 2 - margin, x1, y + height / 2 + margin)
        c = overlap(band, boxes)
        c += sum(max(0, min(band[3], b) - max(band[1], a)) for a, b in taken) * 10
        return c
    for y in cands:
        if cost(y) == 0:
            return y
    return min(cands, key=cost)
