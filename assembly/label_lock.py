"""Lock the real label onto a moving bottle: track the label region of the SOURCE frame through the H3 clip (ECC
homography, frame to frame) and paste the crisp source label back over the clip's garbled one.
  python label_lock.py SRC_FRAME CLIP OUT [--hsv h0,h1] [--debug sheet.jpg]
Output is 1080x1920 (cover-scaled like the editor), same fps as the clip."""
import sys, subprocess, cv2, numpy as np

W, H = 1080, 1920


def cover(img, w=W, h=H):
    ih, iw = img.shape[:2]
    k = max(w / iw, h / ih)
    r = cv2.resize(img, (round(iw * k), round(ih * k)), interpolation=cv2.INTER_LANCZOS4 if k > 1 else cv2.INTER_AREA)
    y0, x0 = (r.shape[0] - h) // 2, (r.shape[1] - w) // 2
    return r[y0:y0 + h, x0:x0 + w]


def label_box(img):
    hsv = cv2.cvtColor(img, cv2.COLOR_BGR2HSV)
    m = ((hsv[:, :, 0] >= 135) & (hsv[:, :, 0] <= 178) & (hsv[:, :, 1] > 60) & (hsv[:, :, 2] > 35) & (hsv[:, :, 2] < 170)).astype(np.uint8) * 255
    m = cv2.morphologyEx(m, cv2.MORPH_CLOSE, np.ones((15, 15), np.uint8))
    n, lab, st, _ = cv2.connectedComponentsWithStats(m)
    i = 1 + int(np.argmax(st[1:, cv2.CC_STAT_AREA]))
    x, y, w, h = st[i][:4]
    return int(x), int(y), int(w), int(h)


def main():
    src_p, clip_p, out_p = sys.argv[1:4]
    dbg = sys.argv[sys.argv.index("--debug") + 1] if "--debug" in sys.argv else None
    src = cover(cv2.imread(src_p))
    x, y, w, h = label_box(src)
    pad = 6
    x0, y0, x1, y1 = x + pad, y + pad, x + w - pad, y + h - pad          # stay inside the label edge
    tpl = src[y0:y1, x0:x1].copy()
    tg = cv2.GaussianBlur(cv2.cvtColor(tpl, cv2.COLOR_BGR2GRAY), (5, 5), 0)
    th, tw = tpl.shape[:2]
    mask = np.zeros((th, tw), np.float32); f = 10
    mask[f:-f, f:-f] = 1.0
    mask = cv2.GaussianBlur(mask, (0, 0), f / 2)
    fps = subprocess.run(["ffprobe", "-v", "error", "-select_streams", "v:0", "-show_entries", "stream=r_frame_rate", "-of", "csv=p=0", clip_p],
                         capture_output=True, text=True).stdout.strip()
    cap = cv2.VideoCapture(clip_p)
    enc = subprocess.Popen(["ffmpeg", "-v", "error", "-y", "-f", "rawvideo", "-pix_fmt", "bgr24", "-s", f"{W}x{H}", "-r", fps, "-i", "-",
                            "-c:v", "libx264", "-crf", "14", "-preset", "medium", "-pix_fmt", "yuv420p", out_p], stdin=subprocess.PIPE)
    warp = np.array([[1, 0, x0], [0, 1, y0], [0, 0, 1]], np.float32)
    crit = (cv2.TERM_CRITERIA_EPS | cv2.TERM_CRITERIA_COUNT, 80, 1e-5)
    n = fails = 0; shots = []
    while True:
        ok, fr = cap.read()
        if not ok:
            break
        fr = cover(fr)
        fg = cv2.GaussianBlur(cv2.cvtColor(fr, cv2.COLOR_BGR2GRAY), (5, 5), 0)
        try:
            cc, wnew = cv2.findTransformECC(tg, fg, warp.copy(), cv2.MOTION_HOMOGRAPHY, crit, None, 5)
            # reject wild jumps
            if cc > 0.55 and np.abs(wnew[:2, 2] - warp[:2, 2]).max() < 60:
                warp = wnew
            else:
                fails += 1
        except cv2.error:
            fails += 1
        # brightness match (glints / light changes on the bottle)
        reg = cv2.warpPerspective(fr, np.linalg.inv(warp), (tw, th))
        g = float(np.clip(reg.mean() / (tpl.mean() + 1e-6), 0.9, 1.12))
        patch = np.clip(tpl.astype(np.float32) * g, 0, 255)
        pw = np.clip(cv2.warpPerspective(patch, warp, (W, H), flags=cv2.INTER_LINEAR), 0, 255)
        mw = cv2.warpPerspective(mask, warp, (W, H), flags=cv2.INTER_LINEAR)[:, :, None]
        out = (pw * mw + fr.astype(np.float32) * (1 - mw)).astype(np.uint8)
        enc.stdin.write(out.tobytes())
        if dbg and n % 24 == 0:
            shots.append((fr[y - 40:y + h + 40, x - 60:x + w + 60].copy(), out[y - 40:y + h + 40, x - 60:x + w + 60].copy()))
        n += 1
    enc.stdin.close(); enc.wait()
    if dbg and shots:
        top = np.hstack([cv2.resize(a, (220, int(a.shape[0] * 220 / a.shape[1]))) for a, _ in shots])
        bot = np.hstack([cv2.resize(b, (220, int(b.shape[0] * 220 / b.shape[1]))) for _, b in shots])
        cv2.imwrite(dbg, np.vstack([top, bot]))
    print(f"{n} frames, ECC fallbacks {fails}, label box {x},{y},{w},{h}")


if __name__ == "__main__":
    main()
