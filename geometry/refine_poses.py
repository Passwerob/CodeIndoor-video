"""Shift objects so their boxes agree with the SAM3 silhouettes seen through the
per-frame corrected video cameras (vggrpo env).

    python geometry/refine_poses.py runtime/video/pantry microwave water_dispenser ...

refine_boxes.py fitted the faces of every box with the raw Any4D cameras, whose
per-frame depth drift (up to ~10 cm, see refine_cameras.py) leaks into the
boxes. Here the box keeps its (hand-checked) size and yaw from the scene plan and
only its floor position (x, y) is searched, on a grid then finer, through the
corrected cameras (geometry/camera_refine.json). A shift is proposed only when
the mean silhouette IoU rises by > 0.02; written to geometry/pose_refine.json.
"""
import json
import sys
from pathlib import Path

import numpy as np
from PIL import Image, ImageDraw

run = Path(sys.argv[1]); G = run / "geometry"
oids = sys.argv[2:]
meas = json.loads((G / "measured.json").read_text())
plan = {e["id"]: e for e in json.loads((run / "measured/scene_plan.json").read_text())["objects"]}
cref = json.loads((G / "camera_refine.json").read_text())
M = np.load(G / "sam3_masks.npz"); hh, hw = M["half_hw"]
frames = json.loads((run / "frames/frames.json").read_text())["frames"]
fid_of = {i: f["id"] for i, f in enumerate(frames)}
SCALE = 0.5


def sam_mask(k, size):
    m = np.unpackbits(M["mask"][k])[: hh * hw].reshape(hh, hw).astype(bool)
    return np.asarray(Image.fromarray(m).resize(size, Image.NEAREST))


def project(cam, X):
    R = np.array(cam["R_c2w_blender"]); C = np.array(cam["location"])
    Xc = (X - C) @ R
    x, y, z = Xc[:, 0], -Xc[:, 1], -Xc[:, 2]
    return np.stack([cam["fx"] * SCALE * x / z + cam["cx"] * SCALE, cam["fy"] * SCALE * y / z + cam["cy"] * SCALE], 1), z


def hull(pts):
    pts = sorted(map(tuple, pts))
    def cross(o, a, b): return (a[0] - o[0]) * (b[1] - o[1]) - (a[1] - o[1]) * (b[0] - o[0])
    lower, upper = [], []
    for p in pts:
        while len(lower) >= 2 and cross(lower[-2], lower[-1], p) <= 0: lower.pop()
        lower.append(p)
    for p in reversed(pts):
        while len(upper) >= 2 and cross(upper[-2], upper[-1], p) <= 0: upper.pop()
        upper.append(p)
    return lower[:-1] + upper[:-1]


def box_mask(cam, lo, hi, W, H):
    X = np.array([[x, y, z] for x in (lo[0], hi[0]) for y in (lo[1], hi[1]) for z in (lo[2], hi[2])])
    uv, z = project(cam, X)
    if (z < 0.05).any():
        return None
    img = Image.new("1", (W, H), 0)
    ImageDraw.Draw(img).polygon([tuple(p) for p in hull(uv)], fill=1)
    return np.asarray(img)


def mean_iou(views, lo, hi):
    ious = []
    for cam, sm in views:
        bm = box_mask(cam, lo, hi, sm.shape[1], sm.shape[0])
        if bm is None:
            continue
        u = (bm | sm).sum()
        ious.append((bm & sm).sum() / u if u else 0.0)
    return float(np.mean(ious)) if ious else 0.0


out = {}
cams = meas["cameras"]
for oid in oids:
    o, e = meas["objects"][oid], plan[oid]
    W, H = int(next(iter(cams.values()))["width"] * SCALE), int(next(iter(cams.values()))["height"] * SCALE)
    per = {}
    for k in o.get("detections") or []:
        per.setdefault(fid_of[int(M["frame"][k])], []).append(k)
    views = []
    for fid, ks in per.items():
        sm = np.zeros((H, W), bool)
        for k in ks:
            sm |= sam_mask(k, (W, H))
        if sm.sum() < 40:
            continue
        cam = dict(cams[fid])
        if fid in cref:
            cam["location"] = cref[fid]["location"]
        views.append((cam, sm))
    dims = e.get("dims") if e.get("fit_dims") else (e.get("measured_dims") or e.get("dims"))
    w, d, h = dims
    if abs(abs(e["yaw_deg"]) - 90) < 1:
        w, d = d, w
    c = np.array([e["x"], e["y"]])
    z0 = e["z"]

    def box(c):
        return np.array([c[0] - w / 2, c[1] - d / 2, z0]), np.array([c[0] + w / 2, c[1] + d / 2, z0 + h])
    base = mean_iou(views, *box(c))
    best = (base, c.copy())
    for step, span in ((0.02, 0.12), (0.005, 0.02)):
        c0 = best[1].copy()
        for dx in np.arange(-span, span + 1e-9, step):
            for dy in np.arange(-span, span + 1e-9, step):
                cc = c0 + [dx, dy]
                s = mean_iou(views, *box(cc))
                if s > best[0]:
                    best = (s, cc)
    shift = best[1] - c
    out[oid] = {"views": len(views), "iou_before": round(base, 3), "iou_after": round(best[0], 3),
                "shift": [round(float(v), 3) for v in shift], "x": round(float(best[1][0]), 3),
                "y": round(float(best[1][1]), 3), "accept": bool(best[0] - base > 0.02)}
    print(oid, out[oid])
(G / "pose_refine.json").write_text(json.dumps(out, indent=1))
