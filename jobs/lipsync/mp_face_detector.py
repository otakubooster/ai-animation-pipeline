"""Drop-in replacement for LatentSync's latentsync/utils/face_detector.py.

LatentSync ships InsightFace's detector, whose pretrained models are licensed for non-commercial research only.
These ads are commercial, so the detector is swapped for MediaPipe's FaceLandmarker (Apache-2.0 model).
LatentSync's ImageProcessor reads only three points from the 106-point layout it expects:
  left brow  = mean of indices [43, 48, 49, 51, 50]   (image-left: the template's first point has the smaller x)
  right brow = mean of indices 101..105
  nose       = mean of indices [74, 77, 83, 86]
so those indices are filled from MediaPipe's 478-point mesh (brow arcs; nose 195/197, whose drop below the brows
matches the template's 20/41 ratio). A frame where no face is found reuses the last good detection, so a blink of the
detector does not abort the clip (LatentSync raises "Face not detected" on None).
"""

import os

import numpy as np

MODEL = os.environ.get("MP_FACE_MODEL", "checkpoints/auxiliary/face_landmarker.task")
BROW_A = [70, 63, 105, 66, 107, 46, 53, 52, 65, 55]
BROW_B = [300, 293, 334, 296, 336, 276, 283, 282, 295, 285]
NOSE = [195, 197]


class FaceDetector:
    def __init__(self, device="cuda", **_):
        import mediapipe as mp
        from mediapipe.tasks import python as mpt
        from mediapipe.tasks.python import vision
        self.mp = mp
        self.det = vision.FaceLandmarker.create_from_options(vision.FaceLandmarkerOptions(
            base_options=mpt.BaseOptions(model_asset_path=MODEL), num_faces=3,
            min_face_detection_confidence=0.3, min_face_presence_confidence=0.3))
        self.last = (None, None)
        self.misses = 0

    def __call__(self, frame):
        img = np.ascontiguousarray(frame[..., :3]).astype(np.uint8)
        h, w = img.shape[:2]
        faces = []
        # full frame first; small faces in wide/full-body shots are often only found in a crop
        for (x0, y0, x1, y1) in ((0, 0, w, h), (0, 0, w, h // 2), (w // 5, 0, w - w // 5, int(h * 0.6)), (0, h // 4, w, h * 3 // 4)):
            crop = np.ascontiguousarray(img[y0:y1, x0:x1])
            res = self.det.detect(self.mp.Image(image_format=self.mp.ImageFormat.SRGB, data=crop))
            if res.face_landmarks:
                cw, ch = x1 - x0, y1 - y0
                faces = [np.array([[x0 + p.x * cw, y0 + p.y * ch] for p in lm]) for lm in res.face_landmarks]
                break
        if not faces:
            self.misses += 1
            return self.last
        # the largest face is the speaker
        pts = max(faces, key=lambda L: np.ptp(L[:, 0]) * np.ptp(L[:, 1]))
        a, b = pts[BROW_A].mean(0), pts[BROW_B].mean(0)
        left, right = (a, b) if a[0] < b[0] else (b, a)
        nose = pts[NOSE].mean(0)
        lm = np.zeros((106, 2), dtype=np.float32)
        lm[[43, 48, 49, 51, 50]] = left
        lm[101:106] = right
        lm[[74, 77, 83, 86]] = nose
        x0, y0 = pts.min(0)
        x1, y1 = pts.max(0)
        bbox = np.array([max(0, x0), max(0, y0), min(w, x1), min(h, y1)], dtype=np.float32)
        self.last = (bbox, lm)
        return self.last
