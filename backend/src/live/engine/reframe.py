"""Robust person detection for horizontal -> vertical reframing (OpenCV only, $0, no downloads at runtime).

detect(video) -> dict(layout, face, cam_box, track, src)
  layout  "facecam"  a webcam overlay inside gameplay -> cam panel on top, gameplay below
          "face"     a person fills the frame (IRL / Just Chatting) -> 9:16 crop that follows them
          "vertical" the source is already portrait -> fit + blurred fill
          "gameplay" nobody on screen -> blurred fill + zoomed band
  face    (cx, cy, fw) normalised centre/width of the main face (facecam/face)
  cam_box (x, y, w, h) normalised webcam rectangle when its borders were found, else None
  track   [(t, cx)] smoothed person centre over time for "face"

Detectors, best first:
  1. YuNet DNN face detector (cv2.FaceDetectorYN): handles small, angled and dim faces far better than Haar.
     The model lives next to this file (models/face_detection_yunet_2023mar.onnx); SupoClip ships the same file.
  2. Haar cascades: frontal default + alt2 + profile (both directions), if YuNet is unavailable.
  3. HOG person detector, for frames where nobody's face is visible (back turned, helmet, far away).
A webcam is a face that stays at the SAME place in most samples; game characters move around.
"""
import os
from pathlib import Path

import numpy as np

HERE = Path(__file__).resolve().parent
YUNET_CANDIDATES = [Path(os.environ["CLIPFORGE_YUNET"]) if os.environ.get("CLIPFORGE_YUNET") else None,
                    HERE / "models" / "face_detection_yunet_2023mar.onnx",
                    HERE.parent.parent / "media" / "models" / "face_detection_yunet_2023mar.onnx"]   # SupoClip
SCAN_W = 960                      # analysis width (small facecams stay detectable)


def _yunet_path():
    return next((p for p in YUNET_CANDIDATES if p and p.exists()), None)


class FaceDetector:
    def __init__(self):
        import cv2
        self.cv2, self.yunet, self.size = cv2, None, None
        path = _yunet_path()
        if path is not None and hasattr(cv2, "FaceDetectorYN"):
            try:
                self.yunet = cv2.FaceDetectorYN.create(str(path), "", (320, 320), 0.6, 0.3, 50)
            except Exception:
                self.yunet = None
        if self.yunet is None:
            d = cv2.data.haarcascades
            self.haar = [cv2.CascadeClassifier(d + n) for n in
                         ("haarcascade_frontalface_default.xml", "haarcascade_frontalface_alt2.xml")]
            self.profile = cv2.CascadeClassifier(d + "haarcascade_profileface.xml")
        self.hog = cv2.HOGDescriptor()
        self.hog.setSVMDetector(cv2.HOGDescriptor_getDefaultPeopleDetector())

    @property
    def name(self):
        return "yunet" if self.yunet is not None else "haar"

    def faces(self, img):
        """[(x, y, w, h, score)] in pixels of img."""
        cv2 = self.cv2
        h, w = img.shape[:2]
        if self.yunet is not None:
            if self.size != (w, h):
                self.yunet.setInputSize((w, h)); self.size = (w, h)
            _, out = self.yunet.detect(img)
            return [] if out is None else [(float(f[0]), float(f[1]), float(f[2]), float(f[3]), float(f[14])) for f in out]
        g = cv2.equalizeHist(cv2.cvtColor(img, cv2.COLOR_BGR2GRAY))
        mn = (max(14, w // 70),) * 2
        found = []
        for c in self.haar:
            found += [(x, y, fw, fh, 0.7) for x, y, fw, fh in c.detectMultiScale(g, 1.08, 5, minSize=mn)]
        for flip in (False, True):
            gg = cv2.flip(g, 1) if flip else g
            for x, y, fw, fh in self.profile.detectMultiScale(gg, 1.1, 6, minSize=mn):
                found.append(((w - x - fw) if flip else x, y, fw, fh, 0.6))
        return _nms(found)

    def people(self, img):
        """[(x, y, w, h, score)] full bodies / upper bodies via HOG (slower: only used when no face is found)."""
        rects, weights = self.hog.detectMultiScale(img, winStride=(8, 8), padding=(8, 8), scale=1.06)
        return [(float(x), float(y), float(w), float(h), float(s)) for (x, y, w, h), s in zip(rects, np.ravel(weights)) if s > 0.5]


def _nms(boxes, iou=0.35):
    boxes = sorted(boxes, key=lambda b: -b[2] * b[3])
    keep = []
    for b in boxes:
        if all(_iou(b, k) < iou for k in keep):
            keep.append(b)
    return keep


def _iou(a, b):
    ax2, ay2, bx2, by2 = a[0] + a[2], a[1] + a[3], b[0] + b[2], b[1] + b[3]
    iw, ih = max(0, min(ax2, bx2) - max(a[0], b[0])), max(0, min(ay2, by2) - max(a[1], b[1]))
    inter = iw * ih
    return inter / max(a[2] * a[3] + b[2] * b[3] - inter, 1e-6)


def _frames(cap, times):
    cv2 = __import__("cv2")
    for t in times:
        cap.set(cv2.CAP_PROP_POS_MSEC, t * 1000)
        ok, fr = cap.read()
        if ok:
            yield t, fr


def _webcam_box(edge_freq, face, W, H):
    """Find the webcam overlay rectangle around a facecam from borders that stay put across samples."""
    cx, cy, fw = face[0] * W, face[1] * H, face[2] * W
    r = edge_freq

    def line(axis, lo, hi, span_lo, span_hi):
        lo, hi = int(max(0, lo)), int(min((W if axis == 0 else H) - 1, hi))
        s0, s1 = int(max(0, span_lo)), int(min((H if axis == 0 else W), span_hi))
        if hi - lo < 3 or s1 - s0 < 10:
            return None
        prof = r[s0:s1, lo:hi].mean(axis=0) if axis == 0 else r[lo:hi, s0:s1].mean(axis=1)
        i = int(np.argmax(prof))
        return lo + i if prof[i] > 0.45 else None

    vy0, vy1, hx0, hx1 = cy - 1.2 * fw, cy + 1.2 * fw, cx - 1.2 * fw, cx + 1.2 * fw
    left = line(0, cx - 4.5 * fw, cx - 0.9 * fw, vy0, vy1)
    right = line(0, cx + 0.9 * fw, cx + 4.5 * fw, vy0, vy1)
    top = line(1, cy - 4 * fw, cy - 0.9 * fw, hx0, hx1)
    bottom = line(1, cy + 0.9 * fw, cy + 4 * fw, hx0, hx1)
    # a webcam often touches the frame edge: accept the frame border as that side
    left = left if left is not None else (0 if cx - 4.5 * fw <= 0 else None)
    top = top if top is not None else (0 if cy - 4 * fw <= 0 else None)
    right = right if right is not None else (W - 1 if cx + 4.5 * fw >= W else None)
    bottom = bottom if bottom is not None else (H - 1 if cy + 4 * fw >= H else None)
    if None in (left, right, top, bottom):
        return None
    bw, bh = right - left, bottom - top
    if bw < 1.6 * fw or bh < 1.6 * fw or not (0.7 <= bw / max(bh, 1) <= 2.6) or bw * bh > 0.45 * W * H:
        return None
    return (left / W, top / H, bw / W, bh / H)


def _smooth_track(pts, dur, step=0.1, max_speed=0.35):
    """[(t, cx)] -> evenly spaced, gap-filled, median + EMA smoothed, speed-limited (fraction of width per s)."""
    if not pts:
        return []
    ts, xs = np.array([p[0] for p in pts]), np.array([p[1] for p in pts])
    grid = np.arange(0, dur + step, step)
    x = np.interp(grid, ts, xs)
    k = 5
    pad = np.pad(x, (k, k), mode="edge")
    x = np.array([np.median(pad[i:i + 2 * k + 1]) for i in range(len(x))])
    out, cur = [], x[0]
    for v in x:
        cur = cur + 0.18 * (v - cur)
        out.append(cur)
    lim = max_speed * step
    for i in range(1, len(out)):
        out[i] = out[i - 1] + float(np.clip(out[i] - out[i - 1], -lim, lim))
    return [(round(float(t), 2), float(v)) for t, v in zip(grid, out)]


def detect(video, samples=36):
    import cv2
    cap = cv2.VideoCapture(str(video))
    W0, H0 = cap.get(3) or 1920, cap.get(4) or 1080
    fps = cap.get(cv2.CAP_PROP_FPS) or 30
    dur = (cap.get(cv2.CAP_PROP_FRAME_COUNT) or 0) / fps or 30
    if H0 > W0 * 1.1:
        cap.release()
        return dict(layout="vertical", face=None, cam_box=None, track=None, src=(W0, H0), detector="-")
    det = FaceDetector()
    sc = SCAN_W / W0
    times = [dur * (i + 0.5) / samples for i in range(samples)]
    faces, bodies, edges = [], [], None
    for i, (t, fr) in enumerate(_frames(cap, times)):
        img = cv2.resize(fr, (SCAN_W, int(H0 * sc)))
        h, w = img.shape[:2]
        e = cv2.Canny(cv2.GaussianBlur(cv2.cvtColor(img, cv2.COLOR_BGR2GRAY), (3, 3), 0), 60, 160) > 0
        edges = e.astype(np.float32) if edges is None else edges + e
        fs = det.faces(img)
        for x, y, fw, fh, s in fs:
            faces.append((i, t, (x + fw / 2) / w, (y + fh / 2) / h, fw / w, s))
        if not fs:
            ps = det.people(cv2.resize(img, (640, int(640 * h / w))))
            if ps:
                x, y, pw, ph, s = max(ps, key=lambda p: p[2] * p[3])
                bodies.append((t, (x + pw / 2) / 640, pw / 640))
    n = max(1, len(times))
    edge_freq = (edges / n) if edges is not None else None
    base = dict(src=(W0, H0), detector=det.name, cam_box=None, track=None, face=None)
    if faces:
        best, best_hits = None, 0
        for f in faces:                                   # the most persistent face position = the webcam
            tol = max(0.035, 0.6 * f[4])
            near = [g for g in faces if abs(g[2] - f[2]) < tol and abs(g[3] - f[3]) < tol * 1.6]
            hits = len({g[0] for g in near})
            if hits > best_hits:
                best, best_hits = near, hits
        cx = float(np.median([f[2] for f in best])); cy = float(np.median([f[3] for f in best]))
        fw = float(np.median([f[4] for f in best]))
        if best_hits >= n * 0.35 and fw < 0.16:
            box = _webcam_box(edge_freq, (cx, cy, fw), edge_freq.shape[1], edge_freq.shape[0]) if edge_freq is not None else None
            cap.release()
            return dict(base, layout="facecam", face=(cx, cy, fw), cam_box=box)
        big = [f for f in faces if f[4] >= 0.09]
        if len({f[0] for f in big}) >= n * 0.3:          # a person fills the frame: follow them
            per_t = {}
            for f in big:
                if f[1] not in per_t or f[4] * f[5] > per_t[f[1]][4] * per_t[f[1]][5]:
                    per_t[f[1]] = f
            pts = sorted((t, f[2]) for t, f in per_t.items()) + [(t, c) for t, c, _ in bodies]
            pts.sort()
            cap.release()
            return dict(base, layout="face", face=(cx, cy, fw), track=_smooth_track(pts, dur))
    if len(bodies) >= n * 0.4:                            # people visible but no readable face
        cap.release()
        return dict(base, layout="face", face=(float(np.median([b[1] for b in bodies])), 0.4, 0.2),
                    track=_smooth_track([(t, c) for t, c, _ in bodies], dur))
    cap.release()
    return dict(base, layout="gameplay")


def sendcmd_crop(track, crop_w, frame_w, path):
    """Write an ffmpeg sendcmd file that moves crop@follow's x along the track; returns the first x."""
    lines, x0 = [], None
    for t, cx in track:
        x = int(min(max(cx * frame_w - crop_w / 2, 0), frame_w - crop_w))
        x0 = x if x0 is None else x0
        lines.append(f"{t:.2f} crop@follow x {x};")
    Path(path).write_text("\n".join(lines) + "\n", "utf-8")
    return x0 or 0
