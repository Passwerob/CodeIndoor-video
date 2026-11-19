"""Refine compact objects' boxes by silhouette agreement with the SAM3 masks.

The point-cloud box (2-98 percentiles) is inflated by mask bleed and flying
pixels at depth edges. Here the box is projected into every keyframe the object
was segmented in, and its free faces are moved (coordinate descent, 1-2 cm
steps) to maximise the mean IoU between the projected box and the mask.
Faces fixed by the measurement stay fixed: the back on a wall, the bottom on
the floor or the support. Updates geometry/measured.json in place (keeps the
previous box under "box_from_points").
"""
import json
import sys
from pathlib import Path

import numpy as np
from PIL import Image, ImageDraw

run = Path(sys.argv[1]); G = run / "geometry"
meas = json.loads((G / "measured.json").read_text())
layout = json.loads((run / "layout.json").read_text())["objects"]
M = np.load(G / "sam3_masks.npz"); hh, hw = M["half_hw"]
frames = json.loads((run / "frames/frames.json").read_text())["frames"]
fid_of = {i: f["id"] for i, f in enumerate(frames)}
SKIP_KINDS = ("base_cabinet_run", "sink", "wet_floor_sign")   # base run: masks leave out the worktop
BOX_LIKE = ()                                    # (a run spans two depths: over the worktop and over the fridge)
SCALE = 0.5                                         # work at half the Any4D resolution


def sam_mask(k, size):
    m = np.unpackbits(M["mask"][k])[: hh * hw].reshape(hh, hw).astype(bool)
    return np.asarray(Image.fromarray(m).resize(size, Image.NEAREST))


def project(cam, X):
    R = np.array(cam["R_c2w_blender"]); C = np.array(cam["location"])
    Xc = (X - C) @ R                                 # blender camera coords
    x, y, z = Xc[:, 0], -Xc[:, 1], -Xc[:, 2]
    u = cam["fx"] * SCALE * x / z + cam["cx"] * SCALE
    v = cam["fy"] * SCALE * y / z + cam["cy"] * SCALE
    return np.stack([u, v], 1), z


def corners(lo, hi):
    return np.array([[x, y, z] for x in (lo[0], hi[0]) for y in (lo[1], hi[1]) for z in (lo[2], hi[2])])


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
    uv, z = project(cam, corners(lo, hi))
    if (z < 0.05).any():
        return None
    img = Image.new("1", (W, H), 0)
    ImageDraw.Draw(img).polygon([tuple(p) for p in hull(uv)], fill=1)
    return np.asarray(img)


LAMBDA = 0.5                                       # IoU cost of moving all faces by their extent


def score(views, lo, hi, lo0=None, hi0=None):
    reg = 0.0
    if lo0 is not None:
        ext0 = np.maximum(hi0 - lo0, 0.05)
        reg = LAMBDA * float(np.mean(np.concatenate([np.abs(lo - lo0) / ext0, np.abs(hi - hi0) / ext0])))
    ious = []
    for cam, sm in views:
        bm = box_mask(cam, lo, hi, sm.shape[1], sm.shape[0])
        if bm is None:
            continue
        u = (bm | sm).sum()
        ious.append((bm & sm).sum() / u if u else 0)
    return (float(np.mean(ious)) if ious else 0.0) - reg


report = {}
for oid, o in meas["objects"].items():
    if oid in SKIP_KINDS:
        continue
    cams = meas["cameras"]
    W, H = int(next(iter(cams.values()))["width"] * SCALE), int(next(iter(cams.values()))["height"] * SCALE)
    per = {}
    for k in o["detections"]:
        per.setdefault(fid_of[int(M["frame"][k])], []).append(k)
    views = []
    for fid, ks in per.items():
        sm = np.zeros((H, W), bool)
        for k in ks:
            sm |= sam_mask(k, (W, H))
        # masks cut by the image border are fine: the projected box is clipped the same way
        if sm.sum() < 40:
            continue
        views.append((cams[fid], sm))
    if len(views) < 3:
        report[oid] = "skipped: fewer than 3 views"
        continue
    lo, hi = np.array(o["bbox_lo"], float), np.array(o["bbox_hi"], float)
    lo0, hi0 = lo.copy(), hi.copy()
    # free faces: (array, axis) pairs; fixed = wall-backed face and the bottom
    free = [("hi", 2)] + ([("lo", 2)] if layout.get(oid, {}).get("mount") == "wall" else [])
    wall = o.get("wall") if o.get("against_wall") else None
    for ax in (0, 1):
        for side in ("lo", "hi"):
            if wall == "east" and (ax, side) == (0, "hi"): continue
            if wall == "west" and (ax, side) == (0, "lo"): continue
            if wall == "north" and (ax, side) == (1, "hi"): continue
            if wall == "south" and (ax, side) == (1, "lo"): continue
            free.append((side, ax))
    best = score(views, lo, hi, lo0, hi0)
    start = best
    for step in (0.03, 0.015, 0.0075):
        improved = True
        while improved:
            improved = False
            for side, ax in free:
                for sgn in (-1, 1):
                    L, Hh = lo.copy(), hi.copy()
                    (L if side == "lo" else Hh)[ax] += sgn * step
                    ext0 = hi0 - lo0
                    if (Hh - L)[ax] < 0.7 * ext0[ax] or (Hh - L)[ax] > 1.3 * ext0[ax] + 0.02:
                        continue
                    s = score(views, L, Hh, lo0, hi0)
                    if s > best + 1e-4:
                        best, lo, hi, improved = s, L, Hh, True
    # points over-cover (mask bleed, flying pixels), a box silhouette under-covers
    # rounded shapes: take the midpoint of the two estimates of every face
    if oid not in BOX_LIKE:
        lo, hi = (lo + lo0) / 2, (hi + hi0) / 2
    iou0, iou1 = score(views, lo0, hi0), score(views, lo, hi)
    gain = iou1 - iou0
    if gain < 0.01:
        report[oid] = f"kept (IoU {iou0:.2f}, refinement +{gain:.3f})"
        continue
    ext = hi - lo
    o["box_from_points"] = {"bbox_lo": o["bbox_lo"], "bbox_hi": o["bbox_hi"], "dims": o["dims"]}
    o["bbox_lo"], o["bbox_hi"] = lo.tolist(), hi.tolist()
    c = (lo + hi) / 2
    o["x"], o["y"] = float(c[0]), float(c[1])
    o["dims"] = [round(float(ext[1]), 3), round(float(ext[0]), 3), round(float(ext[2]), 3)] if o.get("wall") in ("east", "west") \
        else [round(float(ext[0]), 3), round(float(ext[1]), 3), round(float(ext[2]), 3)] if o.get("wall") else o["dims"]
    if not o.get("wall"):                            # free yaw: scale the PCA dims by the axis-box change
        e0 = hi0 - lo0
        o["dims"] = [round(o["box_from_points"]["dims"][0] * ext[0] / e0[0], 3),
                     round(o["box_from_points"]["dims"][1] * ext[1] / e0[1], 3), round(float(ext[2]), 3)]
    o["silhouette_iou"] = {"before": round(iou0, 3), "after": round(iou1, 3), "views": len(views)}
    report[oid] = f"IoU {iou0:.2f} -> {iou1:.2f} ({len(views)} views); dims {o['box_from_points']['dims']} -> {o['dims']}"
for k, v in report.items():
    print(f"{k:18s} {v}")
(G / "measured.json").write_text(json.dumps(meas, indent=1))
