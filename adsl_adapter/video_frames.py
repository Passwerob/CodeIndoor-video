"""Video -> keyframes, contact sheets and crops (OpenCV; ffmpeg as fallback).

A phone walkthrough is minutes of mostly redundant, often motion-blurred
frames: the test pantry is 266 s, 7987 frames, 544x960 portrait. The VLM
needs a few dozen SHARP frames that together cover the room, so selection is
segment-wise: the timeline is cut into K equal segments and each segment
contributes its sharpest frame (variance of the Laplacian), skipping frames
that are nearly black/white and frames that repeat the previous pick (camera
held still). Segments, not a global top-K, because the sharpest frames of a
walkthrough cluster wherever the camera paused, and coverage is the point.

Contact sheets put many keyframes in one image with an `F07 12.5s` label on
each tile, so one request can see the whole room and refer back to frames by
id. Crops are always cut from the full-resolution keyframe, never from a
sheet.

    python adsl_adapter/video_frames.py --video room.mp4 --out runtime/video/demo
"""
from __future__ import annotations

import argparse
import json
import math
import shutil
import subprocess
from pathlib import Path

import numpy as np

try:
    import cv2
except ImportError:  # pragma: no cover
    cv2 = None


def _need_cv2():
    if cv2 is None:
        raise RuntimeError("the video pipeline needs OpenCV: pip install opencv-python")


def imread(path) -> np.ndarray:
    _need_cv2()
    img = cv2.imdecode(np.fromfile(str(path), dtype=np.uint8), cv2.IMREAD_COLOR)
    if img is None:
        raise ValueError(f"cannot read {path}")
    return img


def imwrite(path, img, quality: int = 92) -> None:
    _need_cv2()
    ext = Path(path).suffix.lower() or ".jpg"
    params = [cv2.IMWRITE_JPEG_QUALITY, quality] if ext in (".jpg", ".jpeg") else []
    ok, buf = cv2.imencode(ext, img, params)
    if not ok:
        raise ValueError(f"cannot encode {path}")
    Path(path).parent.mkdir(parents=True, exist_ok=True)
    buf.tofile(str(path))                       # unicode-safe on Windows


# --------------------------------------------------------------------------
# decoding
# --------------------------------------------------------------------------
def probe(video: Path) -> dict:
    _need_cv2()
    cap = cv2.VideoCapture(str(video))
    if not cap.isOpened():
        raise RuntimeError(f"OpenCV cannot open {video}")
    n = int(cap.get(cv2.CAP_PROP_FRAME_COUNT) or 0)
    fps = float(cap.get(cv2.CAP_PROP_FPS) or 0.0) or 30.0
    ok, frame = cap.read()
    cap.release()
    if not ok:
        raise RuntimeError(f"{video}: no decodable frame")
    h, w = frame.shape[:2]
    return {"frames": n, "fps": fps, "duration": n / fps if n else 0.0,
            "width": w, "height": h}


def _ffmpeg_exe() -> str | None:
    exe = shutil.which("ffmpeg")
    if exe:
        return exe
    try:
        import imageio_ffmpeg  # type: ignore
        return imageio_ffmpeg.get_ffmpeg_exe()
    except Exception:  # noqa: BLE001
        return None


def grab(video: Path, t: float) -> np.ndarray | None:
    """One frame at time t (seconds): OpenCV seek, ffmpeg if OpenCV fails."""
    _need_cv2()
    cap = cv2.VideoCapture(str(video))
    fps = float(cap.get(cv2.CAP_PROP_FPS) or 30.0)
    cap.set(cv2.CAP_PROP_POS_FRAMES, int(round(t * fps)))
    ok, frame = cap.read()
    cap.release()
    if ok:
        return frame
    exe = _ffmpeg_exe()
    if not exe:
        return None
    proc = subprocess.run([exe, "-v", "error", "-ss", f"{t:.3f}", "-i", str(video),
                           "-frames:v", "1", "-f", "image2pipe", "-vcodec", "png", "-"],
                          capture_output=True, timeout=120)
    if proc.returncode != 0 or not proc.stdout:
        return None
    return cv2.imdecode(np.frombuffer(proc.stdout, np.uint8), cv2.IMREAD_COLOR)


def iter_samples(video: Path, sample_fps: float):
    """(t, frame) at about `sample_fps`, decoding sequentially (fast, exact)."""
    _need_cv2()
    cap = cv2.VideoCapture(str(video))
    fps = float(cap.get(cv2.CAP_PROP_FPS) or 30.0)
    step = max(1, int(round(fps / max(sample_fps, 0.1))))
    idx = 0
    while True:
        ok = cap.grab()
        if not ok:
            break
        if idx % step == 0:
            ok, frame = cap.retrieve()
            if ok:
                yield idx / fps, frame
        idx += 1
    cap.release()


# --------------------------------------------------------------------------
# metrics + selection
# --------------------------------------------------------------------------
def sharpness(img: np.ndarray) -> float:
    g = cv2.cvtColor(img, cv2.COLOR_BGR2GRAY)
    h, w = g.shape
    s = 360.0 / max(1, min(h, w))
    if s < 1:
        g = cv2.resize(g, (int(w * s), int(h * s)), interpolation=cv2.INTER_AREA)
    return float(cv2.Laplacian(g, cv2.CV_64F).var())


def thumb(img: np.ndarray) -> np.ndarray:
    g = cv2.cvtColor(img, cv2.COLOR_BGR2GRAY)
    t = cv2.resize(g, (24, 24), interpolation=cv2.INTER_AREA).astype(np.float32)
    t -= t.mean()
    n = float(np.linalg.norm(t)) or 1.0
    return t / n


def select_keyframes(video: Path, out_dir: Path, target: int | None = None,
                     sample_fps: float = 2.0, min_brightness: float = 0.06,
                     max_brightness: float = 0.96, repeat_ncc: float = 0.985) -> list[dict]:
    """Pick sharp, distinct frames covering the whole video; write F###.jpg."""
    info = probe(video)
    cands = []
    for t, frame in iter_samples(video, sample_fps):
        b = float(frame.mean()) / 255.0
        cands.append({"t": round(t, 3), "sharpness": sharpness(frame), "brightness": round(b, 3),
                      "thumb": thumb(frame)})
    if not cands:
        raise RuntimeError(f"{video}: no frames decoded")
    duration = info["duration"] or cands[-1]["t"]
    k = target or int(min(60, max(12, round(duration / 5.0))))
    k = min(k, len(cands))
    picked: list[dict] = []
    for s in range(k):
        t0, t1 = duration * s / k, duration * (s + 1) / k
        seg = [c for c in cands if t0 <= c["t"] < t1
               and min_brightness <= c["brightness"] <= max_brightness]
        if not seg:
            continue
        best = max(seg, key=lambda c: c["sharpness"])
        if picked and float((best["thumb"] * picked[-1]["thumb"]).sum()) > repeat_ncc:
            continue                                 # camera held still: same view
        picked.append(best)
    out_dir.mkdir(parents=True, exist_ok=True)
    med = float(np.median([c["sharpness"] for c in cands])) or 1.0
    frames = []
    for i, c in enumerate(picked):
        img = grab(video, c["t"])
        if img is None:
            continue
        fid = f"F{i:02d}"
        path = out_dir / f"{fid}.jpg"
        imwrite(path, img)
        frames.append({"id": fid, "t": c["t"], "file": path.name,
                       "sharpness": round(c["sharpness"], 1),
                       "sharpness_rel": round(c["sharpness"] / med, 2),
                       "brightness": c["brightness"],
                       "width": int(img.shape[1]), "height": int(img.shape[0])})
    (out_dir / "frames.json").write_text(json.dumps(
        {"video": str(video), "info": info, "sample_fps": sample_fps, "target": k,
         "frames": frames}, ensure_ascii=False, indent=2), encoding="utf-8")
    return frames


# --------------------------------------------------------------------------
# contact sheets + crops
# --------------------------------------------------------------------------
def contact_sheets(frames: list[dict], frame_dir: Path, out_dir: Path,
                   per_sheet: int = 24, tile_long: int = 420) -> list[dict]:
    """Grids of labelled tiles; returns [{"file", "frames": [ids]}]."""
    out_dir.mkdir(parents=True, exist_ok=True)
    sheets = []
    for s in range(0, len(frames), per_sheet):
        chunk = frames[s:s + per_sheet]
        imgs = [imread(frame_dir / f["file"]) for f in chunk]
        h0, w0 = imgs[0].shape[:2]
        sc = tile_long / max(h0, w0)
        tw, th = int(w0 * sc), int(h0 * sc)
        cols = int(math.ceil(math.sqrt(len(chunk) * th / max(tw, 1) * 1.0)))
        cols = max(1, min(len(chunk), cols if tw < th else max(2, cols)))
        rows = int(math.ceil(len(chunk) / cols))
        sheet = np.full((rows * th, cols * tw, 3), 255, np.uint8)
        for i, (f, img) in enumerate(zip(chunk, imgs)):
            tile = cv2.resize(img, (tw, th), interpolation=cv2.INTER_AREA)
            label = f"{f['id']} {f['t']:.0f}s"
            cv2.rectangle(tile, (0, 0), (int(13 * len(label)) + 8, 30), (0, 0, 0), -1)
            cv2.putText(tile, label, (4, 22), cv2.FONT_HERSHEY_SIMPLEX, 0.65,
                        (0, 255, 255), 2, cv2.LINE_AA)
            r, c = divmod(i, cols)
            sheet[r * th:(r + 1) * th, c * tw:(c + 1) * tw] = tile
        path = out_dir / f"sheet_{len(sheets):02d}.jpg"
        imwrite(path, sheet, quality=88)
        sheets.append({"file": path.name, "frames": [f["id"] for f in chunk]})
    return sheets


def bbox_pixels(bbox1000, w: int, h: int, margin: float = 0.0) -> tuple[int, int, int, int] | None:
    """0..1000 box (x0,y0,x1,y1) -> clamped pixel box with a relative margin."""
    try:
        x0, y0, x1, y1 = [float(v) for v in bbox1000]
    except (TypeError, ValueError):
        return None
    if x1 < x0:
        x0, x1 = x1, x0
    if y1 < y0:
        y0, y1 = y1, y0
    dx, dy = (x1 - x0) * margin, (y1 - y0) * margin
    X0 = int(max(0, min(w, (x0 - dx) * w / 1000.0)))
    Y0 = int(max(0, min(h, (y0 - dy) * h / 1000.0)))
    X1 = int(max(0, min(w, (x1 + dx) * w / 1000.0)))
    Y1 = int(max(0, min(h, (y1 + dy) * h / 1000.0)))
    if X1 - X0 < 4 or Y1 - Y0 < 4:
        return None
    return X0, Y0, X1, Y1


def crop(img: np.ndarray, bbox1000, margin: float = 0.08) -> tuple[np.ndarray, tuple] | None:
    h, w = img.shape[:2]
    box = bbox_pixels(bbox1000, w, h, margin)
    if box is None:
        return None
    X0, Y0, X1, Y1 = box
    return img[Y0:Y1, X0:X1].copy(), box


def main() -> int:
    ap = argparse.ArgumentParser()
    ap.add_argument("--video", required=True)
    ap.add_argument("--out", required=True)
    ap.add_argument("--target", type=int)
    args = ap.parse_args()
    out = Path(args.out)
    frames = select_keyframes(Path(args.video), out / "frames", args.target)
    sheets = contact_sheets(frames, out / "frames", out / "sheets")
    print(json.dumps({"frames": len(frames), "sheets": [s["file"] for s in sheets]}))
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
