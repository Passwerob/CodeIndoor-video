"""Where the grout lines of the tiled walls are (vggrpo env or any python with cv2).

    python geometry/tile_phase.py runtime/video/pantry

The tile pattern is procedural and world-anchored, so its grid lines land
wherever the world origin puts them: on the real wall they are somewhere else,
which reads as 'wrong tiles' in every close view. Here grout pixels (dark thin
lines inside the bright tile field) of the frames facing a tiled wall are cast
through the (per-frame corrected) video cameras onto the wall plane; the pitch
and phase that best stack them are the grid. Writes geometry/tile_phase.json
{wall: {pitch_u, phase_u, pitch_v, phase_v}} with u = y (east wall) or x
(north step), v = z, phases in metres in [0, pitch).
"""
import json
import sys
from pathlib import Path

import cv2
import numpy as np

run = Path(sys.argv[1])
plan = json.loads((run / "measured/scene_plan.json").read_text())
frames = {f["id"]: f for f in json.loads((run / "frames/frames.json").read_text())["frames"]}
W = plan["room"]["width"]
walls = {"east": {"axis": 0, "plane": W / 2 - 0.006, "u": 1, "urange": (0.1, 2.0), "zrange": (0.96, 1.72)},
         "north_step": {"axis": 1, "plane": 2.074, "u": 0, "urange": (0.5, 1.03), "zrange": (0.96, 1.72)}}


def rays(v, xs, ys):
    R = np.array(v["matrix"])                    # camera-to-world (blender camera: -z forward, +y up)
    d_cam = np.stack([(xs - v["cx"]) / v["fx"], -(ys - v["cy"]) / v["fy"], -np.ones_like(xs)], 1)
    return np.array(v["location"]), d_cam @ R.T


out = {}
for wname, w in walls.items():
    U, V = [], []
    for v in plan["source_views"]:
        img = cv2.imread(v["image"], cv2.IMREAD_GRAYSCALE)
        if img is None:
            continue
        img = img.astype(np.float32)
        bg = cv2.medianBlur(img.astype(np.uint8), 9).astype(np.float32)
        cand = (bg > 150) & (bg - img > 45)
        ys, xs = np.nonzero(cand)
        if len(xs) < 200:
            continue
        C, d = rays(v, xs.astype(np.float64) + 0.5, ys.astype(np.float64) + 0.5)
        a = w["axis"]
        with np.errstate(divide="ignore", invalid="ignore"):
            t = (w["plane"] - C[a]) / d[:, a]
        P = C + t[:, None] * d
        ok = (t > 0.2) & (P[:, w["u"]] > w["urange"][0]) & (P[:, w["u"]] < w["urange"][1]) & \
             (P[:, 2] > w["zrange"][0]) & (P[:, 2] < w["zrange"][1])
        if ok.sum() < 300:
            continue
        U.append(P[ok, w["u"]]); V.append(P[ok, 2])
    if not U:
        continue
    U, V = np.concatenate(U), np.concatenate(V)
    res = {"points": int(len(U))}
    for name, x in (("u", U), ("v", V)):
        best = None
        for p in np.arange(0.090, 0.110, 0.0005):
            ph = (x % p) / p * 2 * np.pi
            R_ = np.hypot(np.cos(ph).mean(), np.sin(ph).mean())     # circular concentration
            if best is None or R_ > best[0]:
                best = (R_, p, (np.arctan2(np.sin(ph).mean(), np.cos(ph).mean()) % (2 * np.pi)) / (2 * np.pi) * p)
        res[f"pitch_{name}"], res[f"phase_{name}"], res[f"concentration_{name}"] = \
            round(float(best[1]), 4), round(float(best[2]), 4), round(float(best[0]), 3)
    out[wname] = res
    print(wname, res)
(run / "geometry/tile_phase.json").write_text(json.dumps(out, indent=1))
