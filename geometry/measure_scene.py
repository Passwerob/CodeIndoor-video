"""Measured room + object poses from Any4D point maps and SAM3 masks.

Inputs (RUN/geometry): any4d.npz, sam3_masks.npz, prompts.json; RUN/grounding.json,
RUN/survey.json, RUN/layout.json (only for ids, mounts and supports).
Output: RUN/geometry/measured.json (room, objects, cameras) + diagnostics.

Frame of the output = the layout frame: origin floor centre, +x east, +y north
(north = the horizontal direction the first keyframe looks at, snapped to the
room's Manhattan axes), +z up, metres.
"""
from __future__ import annotations

import argparse
import json
from pathlib import Path

import numpy as np
from PIL import Image
from scipy import ndimage

ap = argparse.ArgumentParser()
ap.add_argument("run")
ap.add_argument("--counter-height", type=float, default=0.86,
                help="worktop height used to fix the metric scale (m)")
ap.add_argument("--scale", type=float, default=None, help="override the scale factor")
args = ap.parse_args()
run = Path(args.run)
G = run / "geometry"

A = np.load(G / "any4d.npz")
PTS, POSES, K, DEP = A["pts"].astype(np.float64), A["poses"].astype(np.float64), A["K"], A["depth"]
NF, H, W, _ = PTS.shape
IDS = [str(x) for x in A["ids"]]
mean, std = np.array([0.485, 0.456, 0.406]), np.array([0.229, 0.224, 0.225])
RGB = np.clip(A["img"] * std + mean, 0, 1)

# per-pixel normals and validity (no flying pixels at depth edges)
dx = np.zeros_like(PTS); dy = np.zeros_like(PTS)
dx[:, :, 1:-1] = PTS[:, :, 2:] - PTS[:, :, :-2]
dy[:, 1:-1] = PTS[:, 2:] - PTS[:, :-2]
NRM = np.cross(dx, dy)
NRM /= np.linalg.norm(NRM, axis=-1, keepdims=True) + 1e-12
_tocam = POSES[:, None, None, :3, 3] - PTS            # orient every normal toward its camera
NRM *= np.where((NRM * _tocam).sum(-1, keepdims=True) < 0, -1.0, 1.0)
del _tocam
gy, gx = np.gradient(DEP, axis=(1, 2))
VALID = (np.hypot(gx, gy) / np.maximum(DEP, 1e-3) < 0.04)
VALID[:, :2] = VALID[:, -2:] = False
VALID[:, :, :2] = VALID[:, :, -2:] = False

M = np.load(G / "sam3_masks.npz")
hh, hw = M["half_hw"]
DET = [{"i": k, "frame": int(M["frame"][k]), "phrase": str(M["phrase"][k]), "score": float(M["score"][k]),
        "box": M["box"][k]} for k in range(len(M["frame"]))]


def det_mask(k: int, erode: int = 1) -> np.ndarray:
    m = np.unpackbits(M["mask"][k])[: hh * hw].reshape(hh, hw).astype(bool)
    m = np.asarray(Image.fromarray(m).resize((W, H), Image.NEAREST))
    if erode:
        m = ndimage.binary_erosion(m, iterations=erode)
    return m


def masked_points(k: int, erode: int = 1):
    f = DET[k]["frame"]
    m = det_mask(k, erode) & VALID[f]
    return PTS[f][m], NRM[f][m], RGB[f][m]


def by_phrase(ph: str, min_score: float = 0.4):
    return [d for d in DET if d["phrase"] == ph and d["score"] >= min_score]


# --------------------------------------------------------------------------
# gravity, floor, Manhattan axes
# --------------------------------------------------------------------------
down0 = POSES[:, :3, 1].mean(0)
down0 /= np.linalg.norm(down0)
fp, fn = [], []
for d in by_phrase("floor", 0.5):
    p, n, _ = masked_points(d["i"], 3)
    keep = np.abs(n @ down0) > 0.85
    fp.append(p[keep]); fn.append(n[keep])
fp = np.concatenate(fp)
rng = np.random.default_rng(0)
best = (0, None)
for _ in range(400):
    s = fp[rng.choice(len(fp), 3, replace=False)]
    n = np.cross(s[1] - s[0], s[2] - s[0])
    if np.linalg.norm(n) < 1e-9:
        continue
    n /= np.linalg.norm(n)
    if abs(n @ down0) < 0.9:
        continue
    inl = np.abs((fp - s[0]) @ n) < 0.015
    if inl.sum() > best[0]:
        best = (inl.sum(), inl)
inl = best[1]
c0 = fp[inl].mean(0)
_, _, vt = np.linalg.svd(fp[inl] - c0, full_matrices=False)
up = vt[2] * (-1 if vt[2] @ down0 > 0 else 1)          # cameras are above the floor
floor_d = -(c0 @ up)                                    # height(x) = x.up + floor_d
print(f"floor: {inl.sum()} inliers of {len(fp)}, up={up.round(3)}")


def height(x):
    return x @ up + floor_d


wp, wn = [], []
for d in by_phrase("wall", 0.5):
    p, n, _ = masked_points(d["i"], 3)
    keep = np.abs(n @ up) < 0.15
    wp.append(p[keep]); wn.append(n[keep])
wp, wn = np.concatenate(wp), np.concatenate(wn)
wn_h = wn - np.outer(wn @ up, up)
wn_h /= np.linalg.norm(wn_h, axis=1, keepdims=True)
e1 = np.cross(up, [0.0, 0.0, 1.0]); e1 /= np.linalg.norm(e1); e2 = np.cross(up, e1)
ang = np.arctan2(wn_h @ e2, wn_h @ e1) % (np.pi / 2)
hist, edges = np.histogram(ang, 180, (0, np.pi / 2))
hist = ndimage.uniform_filter1d(hist.astype(float), 5, mode="wrap")
a0 = (edges[hist.argmax()] + edges[hist.argmax() + 1]) / 2
# refine with the circular mean of the angles near the peak
dd = (ang - a0 + np.pi / 4) % (np.pi / 2) - np.pi / 4
a0 += np.mean(dd[np.abs(dd) < np.radians(6)])
axA = np.cos(a0) * e1 + np.sin(a0) * e2
axB = np.cross(up, axA)
fwd0 = POSES[0, :3, 2] - (POSES[0, :3, 2] @ up) * up
cands = [axA, axB, -axA, -axB]
north = max(cands, key=lambda v: v @ fwd0)
east = np.cross(north, up)
print(f"manhattan peak share {hist.max() / hist.sum():.3f}; F00 looks {np.degrees(np.arccos(north @ fwd0 / np.linalg.norm(fwd0))):.1f} deg off north")
Rm = np.stack([east, north, up])                        # any4d -> (east, north, up)


def to_room(x):
    y = x @ Rm.T
    y[..., 2] = height(x)
    return y


# --------------------------------------------------------------------------
# walls, ceiling, metric scale
# --------------------------------------------------------------------------
WP = to_room(wp)
wn_r = wn @ Rm.T


# floor footprint (points of floor masks at floor height) bounds the room
FLOOR_R = to_room(fp[inl])
CAMS_R = to_room(POSES[:, :3, 3])


def wall_planes(axis: int):
    """Outermost wall planes along `axis`: a wall-mask peak qualifies only if
    (almost) no floor lies beyond it and every camera is inside; a side with no
    qualifying peak was never filmed and is put just beyond floor and cameras."""
    sel = np.abs(wn_r[:, axis]) > 0.85
    v = WP[sel, axis]
    edges = np.arange(v.min() - 0.02, v.max() + 0.04, 0.02)
    h, e = np.histogram(v, edges)
    h = ndimage.uniform_filter1d(h.astype(float), 3)
    thr = 0.03 * h.max()
    peaks = [i for i in range(1, len(h) - 1) if h[i] >= h[i - 1] and h[i] >= h[i + 1] and h[i] > thr]
    centres = [(e[i] + e[i + 1]) / 2 for i in peaks]
    fv, cv = FLOOR_R[:, axis], CAMS_R[:, axis]

    def refine(c):
        near = np.abs(v - c) < 0.05
        return float(np.median(v[near]))
    lo_c = [c for c in centres if (fv < c - 0.05).mean() < 0.01 and (cv > c + 0.1).all()]
    hi_c = [c for c in centres if (fv > c + 0.05).mean() < 0.01 and (cv < c - 0.1).all()]
    lo = (refine(max(lo_c, key=lambda c: (h[peaks[centres.index(c)]] > 0.2 * h.max(), -abs(c - np.percentile(fv, 0.5))))),
          "observed") if lo_c else (min(np.percentile(fv, 0.3), cv.min()) - 0.25, "unobserved")
    hi = (refine(max(hi_c, key=lambda c: (h[peaks[centres.index(c)]] > 0.2 * h.max(), -abs(c - np.percentile(fv, 99.5))))),
          "observed") if hi_c else (max(np.percentile(fv, 99.7), cv.max()) + 0.25, "unobserved")
    return lo, hi, [(round(c, 2), int(h[i])) for c, i in zip(centres, peaks)]


(x_w, x_w_s), (x_e, x_e_s), px = wall_planes(0)
(y_s, y_s_s), (y_n, y_n_s), py = wall_planes(1)
print("wall peaks x:", px)
print("wall peaks y:", py)
print(f"walls raw: west {x_w:.2f} ({x_w_s}) east {x_e:.2f} ({x_e_s}) south {y_s:.2f} ({y_s_s}) north {y_n:.2f} ({y_n_s})")

cz = []
for d in by_phrase("ceiling", 0.5):
    p, n, _ = masked_points(d["i"], 3)
    keep = np.abs(n @ up) > 0.85
    cz.append(height(p[keep]))
cz = np.concatenate(cz) if cz else np.array([np.nan])

ct = []
for d in by_phrase("countertop", 0.5):
    p, n, _ = masked_points(d["i"], 2)
    keep = (n @ up) < -0.85 if False else np.abs(n @ up) > 0.85
    hgt = height(p[keep])
    ct.append(hgt[(hgt > 0.3) & (hgt < 2.0)])
ct = np.concatenate(ct)
counter_raw = float(np.median(ct))
cam_h = height(POSES[:, :3, 3])
door_top = []
for d in by_phrase("door", 0.5):
    p, _, _ = masked_points(d["i"], 2)
    if len(p) > 200:
        door_top.append(np.percentile(height(p), 98))
raw_cues = {"counter_height": counter_raw, "counter_iqr": np.percentile(ct, [25, 75]).tolist(),
            "camera_height_median": float(np.median(cam_h)),
            "ceiling_height": float(np.nanmedian(cz)), "door_top_median": float(np.median(door_top)) if door_top else None,
            "room_x": x_e - x_w, "room_y": y_n - y_s}
S = args.scale or args.counter_height / counter_raw
print("raw cues:", {k: (round(v, 3) if isinstance(v, float) else v) for k, v in raw_cues.items()})
print(f"scale = {S:.4f}  (counter {counter_raw:.3f} -> {counter_raw * S:.3f} m)")
cx, cy = (x_w + x_e) / 2, (y_s + y_n) / 2


def to_metric(y):
    out = y.copy()
    out[..., 0] = (y[..., 0] - cx) * S
    out[..., 1] = (y[..., 1] - cy) * S
    out[..., 2] = y[..., 2] * S
    return out


room = {"walls_observed": {"west": x_w_s, "east": x_e_s, "south": y_s_s, "north": y_n_s},
        "width": (x_e - x_w) * S, "depth": (y_n - y_s) * S, "height": float(np.nanmedian(cz)) * S,
        "counter_height": counter_raw * S, "scale": S, "camera_height_median": float(np.median(cam_h)) * S,
        "door_top_median": (np.median(door_top) * S) if door_top else None}
print("room (m):", {k: (round(v, 3) if isinstance(v, float) else v) for k, v in room.items()})

# --------------------------------------------------------------------------
# cameras in the room frame (Blender convention: looks along -Z, +Y up)
# --------------------------------------------------------------------------
cams = {}
for i, fid in enumerate(IDS):
    Rc = POSES[i, :3, :3]
    c = to_metric(to_room(POSES[i, :3, 3][None]))[0]
    Rr = Rm @ Rc                       # columns: camera x,y,z (opencv) in room axes
    Rb = Rr @ np.diag([1, -1, -1])    # blender camera axes
    k = K[i].astype(float)
    cams[fid] = {"location": c.tolist(), "R_c2w_blender": Rb.tolist(),
                 "fx": float(k[0, 0]), "fy": float(k[1, 1]), "cx": float(k[0, 2]), "cy": float(k[1, 2]),
                 "width": W, "height": H}

# --------------------------------------------------------------------------
# objects
# --------------------------------------------------------------------------
prompts = json.loads((G / "prompts.json").read_text())
ground = json.loads((run / "grounding.json").read_text())["grounding"]["objects"]
layout = json.loads((run / "layout.json").read_text())["objects"]
fw, fh = 544, 960
fidx = {f: i for i, f in enumerate(IDS)}


def iou(a, b):
    x0, y0 = max(a[0], b[0]), max(a[1], b[1])
    x1, y1 = min(a[2], b[2]), min(a[3], b[3])
    inter = max(0, x1 - x0) * max(0, y1 - y0)
    ua = (a[2] - a[0]) * (a[3] - a[1]) + (b[2] - b[0]) * (b[3] - b[1]) - inter
    return inter / ua if ua > 0 else 0.0


METRIC_CACHE = {}


def metric_points(k):
    if k not in METRIC_CACHE:
        p, n, c = masked_points(k, 1)
        METRIC_CACHE[k] = (to_metric(to_room(p)), n @ Rm.T, c)
    return METRIC_CACHE[k]


def robust_box(p, lo=3, hi=97):
    return np.percentile(p, lo, axis=0), np.percentile(p, hi, axis=0)


def largest_component(p, voxel=0.03):
    """Drop stray points: keep the largest 26-connected voxel cluster (plus any
    cluster at least 30% of its size, e.g. two doors of one carcass)."""
    if len(p) < 50:
        return np.ones(len(p), bool)
    ijk = np.floor((p - p.min(0)) / voxel).astype(int)
    grid = np.zeros(ijk.max(0) + 1, bool)
    grid[tuple(ijk.T)] = True
    lab, n = ndimage.label(grid, structure=np.ones((3, 3, 3)))
    if n <= 1:
        return np.ones(len(p), bool)
    pl = lab[tuple(ijk.T)]
    counts = np.bincount(pl)
    counts[0] = 0
    keep = np.nonzero(counts >= 0.1 * counts.max())[0]
    return np.isin(pl, keep)


def bbox_clip(k, gb):
    """Points of detection k whose pixels fall inside the grounded box gb (frame px)."""
    fi = DET[k]["frame"]
    mask = det_mask(k, 1) & VALID[fi]
    vv, uu = np.nonzero(mask)
    sx, sy = W / fw, H / fh
    return (uu >= gb[0] * sx) & (uu <= gb[2] * sx) & (vv >= gb[1] * sy) & (vv <= gb[3] * sy)


anchors = {}
for oid, phs in prompts.items():
    if oid.startswith("_"):
        continue
    pts, nrm, used, dets = [], [], [], []
    for g in ground.get(oid, []):
        if g["frame"] not in fidx:
            continue
        fi = fidx[g["frame"]]
        gb = [g["bbox"][0] * fw / 1000, g["bbox"][1] * fh / 1000, g["bbox"][2] * fw / 1000, g["bbox"][3] * fh / 1000]
        boxed = [d for d in DET if d["frame"] == fi and d["phrase"] == "box:" + oid]
        text = [d for d in DET if d["frame"] == fi and d["phrase"] in phs and d["score"] >= 0.35 and iou(d["box"], gb) > 0.3]
        pick = (boxed or []) + text[:0]
        if not pick and text:
            pick = [max(text, key=lambda d: iou(d["box"], gb))]
        for d in pick:
            p, _, _ = metric_points(d["i"])
            if len(p) < 30:
                continue
            keep = bbox_clip(d["i"], gb)
            pts.append(p[keep]); nrm.append(metric_points(d["i"])[1][keep]); used.append(g["frame"])
            dets.append(int(d["i"]))
    if pts:
        P, Nn_ = np.concatenate(pts), np.concatenate(nrm)
        k = largest_component(P)
        anchors[oid] = {"points": P[k], "normals": Nn_[k], "frames": sorted(set(used)), "dets": dets}
    else:
        print("no anchor for", oid)

FLOOR_M = to_metric(FLOOR_R)
RAW_BOXES = {}                                   # filled before poses are decided (point boxes)
supports_of = {L.get("on") for L in layout.values() if L.get("on")}
_fpp = G / "front_parts.json"
FRONT_PARTS = json.loads(_fpp.read_text()) if _fpp.is_file() else {}
results = {}
for oid, phs in prompts.items():
    if oid.startswith("_") or oid not in anchors:
        continue
    a = anchors[oid]["points"]
    lo, hi = robust_box(a, 3, 97)
    margin = np.array([0.08, 0.08, 0.05])
    rivals = [r for r, rp in prompts.items() if r != oid and not r.startswith("_") and set(rp) & set(phs) and r in anchors]
    rival_boxes = [robust_box(anchors[r]["points"], 5, 95) for r in rivals]
    acc, frames_used = [(a, anchors[oid]["normals"], None)], set(anchors[oid]["frames"])
    dets = list(anchors[oid]["dets"])
    for d in DET:
        if d["phrase"] not in phs or d["score"] < 0.4:
            continue
        p, n, c = metric_points(d["i"])
        if len(p) < 30:
            continue
        ins = np.all((p >= lo - margin) & (p <= hi + margin), axis=1)
        for rl, rh in rival_boxes:                         # never take a rival's points
            ins &= ~np.all((p >= rl) & (p <= rh), axis=1)
        if ins.mean() < 0.3 or ins.sum() < 30:
            continue
        acc.append((p[ins], n[ins], None)); frames_used.add(IDS[d["frame"]]); dets.append(int(d["i"]))
    allp = np.concatenate([x[0] for x in acc]); alln = np.concatenate([x[1] for x in acc])
    if oid in supports_of:                     # a worktop belongs to the carcass under it
        clo, chi = robust_box(allp, 3, 97)
        for d in by_phrase("countertop", 0.4):
            p, n, _ = metric_points(d["i"])
            inside = np.all((p[:, :2] >= clo[:2] - 0.1) & (p[:, :2] <= chi[:2] + 0.1), axis=1) & \
                (np.abs(p[:, 2] - room["counter_height"]) < 0.08)
            if inside.sum() > 30:
                allp = np.concatenate([allp, p[inside]]); alln = np.concatenate([alln, n[inside]])
    k = largest_component(allp)
    results[oid] = {"points": allp[k], "normals": alln[k], "frames": sorted(frames_used), "dets": dets,
                    "anchor_frames": anchors[oid]["frames"]}

for _oid, _r in results.items():
    RAW_BOXES[_oid] = robust_box(_r["points"], 2, 98)
half = {"x": room["width"] / 2, "y": room["depth"] / 2}
WALL_N = {"east": (1, 0), "west": (-1, 0), "north": (0, 1), "south": (0, -1)}
out_objects = {}
order = sorted(results, key=lambda o: 0 if (layout.get(o, {}).get("on") in (None, "")) else 1)
for oid in order:
    r = results[oid]
    p = r["points"]
    lo, hi = robust_box(p, 2, 98)
    L = layout.get(oid, {})
    # clamp to the room
    lo[:2] = np.maximum(lo[:2], [-half["x"], -half["y"]]); hi[:2] = np.minimum(hi[:2], [half["x"], half["y"]])
    # facing: dominant horizontal normal of the object's own surface, snapped to
    # the room axes (fronts are what the camera sees most); supported items take
    # their support's facing when they have no clear front of their own
    nh = r["normals"][:, :2]
    strong = np.linalg.norm(nh, axis=1) > 0.9
    votes = {}
    for wname, (wx, wy) in WALL_N.items():          # facing AWAY from wall wname
        votes[wname] = int(((nh[strong] @ -np.array([wx, wy], float)) > 0.9).sum())
    total = max(1, strong.sum())
    best_wall = max(votes, key=votes.get)
    sup = L.get("on")
    if L.get("mount") in ("on_surface", "built_in") and sup in out_objects and out_objects[sup]["wall"] \
            and votes[best_wall] < 0.5 * total:
        best_wall = out_objects[sup]["wall"]      # small things: normals are noisy, follow the support
    elif votes[best_wall] < 0.15 * total and sup in out_objects:
        best_wall = out_objects[sup]["wall"] or best_wall
    # the FRONT is where the functional parts are (geometry/locate_front_parts.py);
    # the most-filmed face is only a fallback -- the dispenser's east side was
    # filmed most, its controls face south
    geometric_wall = best_wall
    fp = FRONT_PARTS.get(oid, {}).get("front")
    facing_source = "most-observed face"
    if fp:
        best_wall = {"south": "north", "north": "south", "east": "west", "west": "east"}[fp]
        facing_source = "functional parts"
    elif L.get("mount") in ("on_surface", "built_in") and sup in out_objects and out_objects[sup]["wall"]:
        facing_source = "support"
    flat = (hi[2] - lo[2]) < 0.05 and L.get("mount") == "floor"
    wall = None if flat else best_wall
    gap = None
    if wall:
        gap = {"east": half["x"] - hi[0], "west": lo[0] + half["x"], "north": half["y"] - hi[1],
               "south": lo[1] + half["y"]}[wall]
    # does it stand off its wall? only if FLOOR was seen between its back and the
    # wall (the dispenser: floor and a bucket behind it); a fridge or a cabinet
    # run hides the floor behind it all the way to the wall
    floor_behind, blockers = 0, []
    if wall and gap is not None and gap > 0.05:
        Fm = FLOOR_M
        sl = 0.2 * (hi - lo)
        if wall in ("east", "west"):
            band = (np.abs(Fm[:, 1] - (lo[1] + hi[1]) / 2) < (hi[1] - lo[1]) / 2 - sl[1])
            band &= (Fm[:, 0] > hi[0] + 0.03) if wall == "east" else (Fm[:, 0] < lo[0] - 0.03)
        else:
            band = (np.abs(Fm[:, 0] - (lo[0] + hi[0]) / 2) < (hi[0] - lo[0]) / 2 - sl[0])
            band &= (Fm[:, 1] > hi[1] + 0.03) if wall == "north" else (Fm[:, 1] < lo[1] - 0.03)
        floor_behind = int(band.sum())
        # another object standing in that gap (the bucket behind the dispenser)
        for other, ob in RAW_BOXES.items():
            if other == oid or layout.get(other, {}).get("on"):
                continue
            olo, ohi = ob
            lat = 1 if wall in ("east", "west") else 0
            ax = 1 - lat
            if ohi[lat] < lo[lat] + 0.05 or olo[lat] > hi[lat] - 0.05:
                continue
            between = (olo[ax] > hi[ax] - 0.02) if wall in ("east", "north") else (ohi[ax] < lo[ax] + 0.02)
            if between:
                floor_behind = max(floor_behind, 10 ** 6)
                blockers.append(other)
    seen_depth = float(floor_behind)
    against = bool(wall) and room["walls_observed"][wall] == "observed" and gap is not None \
        and not (L.get("mount") in ("on_surface", "built_in")) \
        and (gap < 0.15 or (gap < 0.6 and floor_behind < 300))
    flat_front = False
    if against:                                   # the front plane: median of front-facing points
        fn = -np.array(WALL_N[wall], float)
        fpts = p[(r["normals"][:, :2] @ fn) > 0.9]
        ax_ = 0 if wall in ("east", "west") else 1
        iqr = float(np.subtract(*np.percentile(fpts[:, ax_], [75, 25]))) if len(fpts) > 200 else 9.0
        flat_front = iqr < 0.08                  # doors+plinth+worktop edge ~5 cm; a round bucket ~19 cm
        if flat_front:                            # a flat front only: a round body's "front" is its middle
            fc = float(np.median(fpts[:, ax_]))
            if fn[ax_] < 0: lo[ax_] = fc
            else: hi[ax_] = fc
    if against and not flat_front and gap is not None and gap > 0.08 and floor_behind >= 300:
        against = False                           # round/irregular and clear of the wall: keep its own depth
    if against:                                   # the unseen back reaches the wall
        if wall == "east": hi[0] = half["x"]
        if wall == "west": lo[0] = -half["x"]
        if wall == "north": hi[1] = half["y"]
        if wall == "south": lo[1] = -half["y"]
    if sup and sup in out_objects:
        top = out_objects[sup]["z"] + out_objects[sup]["dims"][2]
        if L.get("mount") == "on_surface" and abs(lo[2] - top) < 0.15:
            lo[2] = top
    if L.get("mount") == "floor":
        lo[2] = 0.0                               # floor-standing: the foot is often unsegmented
    hi[2] = max(hi[2], lo[2] + 0.004)
    ext = hi - lo
    if wall:
        f = -np.array(WALL_N[wall], float)
        yaw = float(np.degrees(np.arctan2(f[0], -f[1])))
        w, d = (ext[1], ext[0]) if wall in ("east", "west") else (ext[0], ext[1])
    else:
        q = p[:, :2] - p[:, :2].mean(0)
        _, evec = np.linalg.eigh(np.cov(q.T))
        major = evec[:, 1]
        th = np.degrees(np.arctan2(major[1], major[0]))
        snap = round(th / 90) * 90
        if abs(th - snap) < 15:
            th = snap
        c_, s_ = np.cos(np.radians(th)), np.sin(np.radians(th))
        qq = q @ np.array([[c_, s_], [-s_, c_]]).T
        w = np.percentile(qq[:, 0], 98) - np.percentile(qq[:, 0], 2)
        d = np.percentile(qq[:, 1], 98) - np.percentile(qq[:, 1], 2)
        yaw = float(th)
    facing_wall = wall
    centre = (lo + hi) / 2
    out_objects[oid] = {"x": float(centre[0]), "y": float(centre[1]), "z": float(lo[2]), "yaw_deg": round(yaw, 2),
                        "dims": [round(float(w), 3), round(float(d), 3), round(float(ext[2]), 3)],
                        "wall": facing_wall, "against_wall": bool(against), "facing_votes": votes,
                        "facing_source": facing_source, "facing_geometric_wall": geometric_wall,
                        "floor_points_behind": int(min(seen_depth, 10 ** 6)), "objects_behind": blockers,
                        "facing_conflict": bool(fp) and geometric_wall != best_wall, "bbox_lo": lo.tolist(), "bbox_hi": hi.tolist(),
                        "frames": r["frames"], "anchor_frames": r["anchor_frames"], "n_points": int(len(p)),
                        "detections": r["dets"]}
    print(f"{oid:18s} c=({centre[0]:+.2f},{centre[1]:+.2f}) z={lo[2]:.2f} dims={np.round([w, d, ext[2]], 2)} "
          f"yaw={yaw:6.1f} wall={facing_wall} views={len(r['frames'])} pts={len(p)} facing<-{facing_source}"
          + (f" (CONFLICT: most-observed face says back to {geometric_wall})" if bool(fp) and geometric_wall != best_wall else ""))
missing = [o for o in layout if o not in out_objects]
print("not measured:", missing)

# --------------------------------------------------------------------------
# windows (openings in the walls) and pendant lights
# --------------------------------------------------------------------------
def clusters(points, voxel=0.06, min_pts=300):
    if len(points) < min_pts:
        return []
    ijk = np.floor((points - points.min(0)) / voxel).astype(int)
    grid = np.zeros(ijk.max(0) + 1, bool); grid[tuple(ijk.T)] = True
    lab, n = ndimage.label(grid, structure=np.ones((3, 3, 3)))
    pl = lab[tuple(ijk.T)]
    return [points[pl == k] for k in range(1, n + 1) if (pl == k).sum() >= min_pts]


win = [metric_points(d["i"])[0] for d in by_phrase("window", 0.5)]
openings = []
if win:
    for c in clusters(np.concatenate(win), 0.08, 400):
        lo_, hi_ = robust_box(c, 3, 97)
        gaps = {"east": half["x"] - hi_[0], "west": lo_[0] + half["x"], "north": half["y"] - hi_[1], "south": lo_[1] + half["y"]}
        wname = min(gaps, key=gaps.get)
        if gaps[wname] > 0.4:
            continue
        along_ax = 1 if wname in ("east", "west") else 0
        a0, a1 = lo_[along_ax], hi_[along_ax]
        # offset from the wall's left end, seen from inside facing the wall (layout convention)
        left = {"north": -half["x"], "south": half["x"], "east": half["y"], "west": -half["y"]}[wname]
        off = abs((a0 if wname in ("north", "west") else a1) - left)
        openings.append({"id": f"window_{len(openings) + 1}", "kind": "window", "wall": wname,
                         "offset_m": round(off, 3), "width_m": round(a1 - a0, 3),
                         "sill_m": round(float(lo_[2]), 3), "height_m": round(float(hi_[2] - lo_[2]), 3),
                         "n_points": int(len(c))})
print("openings:", openings)

lamp = [metric_points(d["i"])[0] for ph in ("pendant lamp", "ceiling light") for d in by_phrase(ph, 0.5)]
lights = []
if lamp:
    L_all = np.concatenate(lamp)
    L_all = L_all[L_all[:, 2] > 1.8]
    for c in clusters(L_all, 0.08, 150):
        lo_, hi_ = robust_box(c, 5, 95)
        if (hi_[:2] - lo_[:2]).max() > 0.8:
            continue
        lights.append({"x": round(float(np.median(c[:, 0])), 3), "y": round(float(np.median(c[:, 1])), 3),
                       "bottom_z": round(float(lo_[2]), 3), "top_z": round(float(hi_[2]), 3),
                       "diameter": round(float(np.mean(hi_[:2] - lo_[:2])), 3), "n_points": int(len(c))})
print("lights:", lights)

np.savez_compressed(G / "object_points.npz", **{oid: r["points"].astype(np.float32) for oid, r in results.items()})
json.dump({"room": room, "raw_cues": raw_cues, "frame": {"R_room_from_any4d": Rm.tolist(), "floor_d": floor_d,
                                                         "centre_raw": [cx, cy], "scale": S},
           "openings": openings, "lights": lights,
           "walls": {"x_west": -half["x"], "x_east": half["x"], "y_south": -half["y"], "y_north": half["y"]},
           "objects": out_objects, "unmeasured": missing, "cameras": cams},
          open(G / "measured.json", "w"), indent=1)
print("wrote", G / "measured.json")
