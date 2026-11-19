"""Per-frame camera correction against the consensus room (run in the vggrpo env).

    python geometry/refine_cameras.py runtime/video/pantry

Why: Any4D's per-frame geometry drifts. The tiled east wall lands anywhere from
x = 0.95 to 1.16 m depending on the frame, while the room and the objects are a
consensus over all frames. Rendering the consensus model from a frame's raw
camera therefore misses that frame's image by up to ~10 cm, even though the
camera reprojects its own points to ~1 px.

Model: the true position of a point reconstructed in frame k is
X = s_k * R + t_k (scale drift + offset; the rotation is shared and good).
s_k, t_k come from that frame's points on known planes: floor z = 0, the counter
top, and the walls x = x_E, x = x_W, y = y_N (robust least squares with a
prior s = 1, t = 0). Because a pinhole projection is unchanged when the scene
and the camera centre are scaled and shifted together, the corrected camera
centre C' = s_k * C + t_k sees the consensus model as frame k saw itself.
Writes geometry/camera_refine.json {frame: {s, t, location, n_planes}}.
"""
import json
import sys
from pathlib import Path

import numpy as np

run = Path(sys.argv[1])
G = run / "geometry"
A = np.load(G / "any4d.npz")
meas = json.loads((G / "measured.json").read_text())
fr = meas["frame"]
Rm = np.array(fr["R_room_from_any4d"]); fd = fr["floor_d"]; cx0, cy0 = fr["centre_raw"]; S = fr["scale"]
plan = json.loads((run / "measured/scene_plan.json").read_text())
room = plan["room"]
xE, xW, yN = room["width"] / 2, -room["width"] / 2, room["depth"] / 2
counter = next((e for e in plan["objects"] if e["id"] == "base_cabinet_run"), None)
z_ct = (counter["z"] + counter["dims"][2]) if counter else None
views = {v["source_frame"]: v for v in plan["source_views"]}
ids = [str(x) for x in A["ids"]]


def to_room(P):
    y = P @ Rm.T
    o = np.empty_like(y)
    o[..., 0] = (y[..., 0] - cx0) * S
    o[..., 1] = (y[..., 1] - cy0) * S
    o[..., 2] = (y[..., 2] + fd) * S
    return o


def normals(P):
    dx = np.zeros_like(P); dy = np.zeros_like(P)
    dx[:, 1:-1] = P[:, 2:] - P[:, :-2]
    dy[1:-1] = P[2:] - P[:-2]
    n = np.cross(dx, dy)
    return n / np.maximum(np.linalg.norm(n, axis=-1, keepdims=True), 1e-9)


out = {}
for k, fid in enumerate(ids):
    fid = fid if fid.startswith("F") else f"F{int(fid):02d}"
    R = to_room(A["pts"][k].astype(np.float64))
    N = normals(R)
    ok = np.isfinite(R).all(-1) & (A["depth"][k] > 0)
    R, N = R[ok], N[ok]
    rows, rhs, w = [], [], []          # unknowns [s, tx, ty, tz]

    def add(sel, axis, target, weight=1.0, cap=4000):
        idx = np.flatnonzero(sel)
        if len(idx) < 300:
            return 0
        if len(idx) > cap:
            idx = np.random.default_rng(k).choice(idx, cap, replace=False)
        for i in idx:
            r = [R[i, axis], 0.0, 0.0, 0.0]
            r[1 + axis] = 1.0
            rows.append(r); rhs.append(target); w.append(weight / len(idx) * 1000)
        return 1

    up = np.abs(N[:, 2]) > 0.9
    n_pl = 0
    n_pl += add(up & (np.abs(R[:, 2]) < 0.12), 2, 0.0)
    if z_ct:
        cx, cy = counter["x"], counter["y"]
        cw, cd = counter["dims"][1], counter["dims"][0]          # yaw -90: depth along x
        inside = (np.abs(R[:, 0] - cx) < cw / 2 - 0.05) & (np.abs(R[:, 1] - cy) < cd / 2 - 0.05)
        n_pl += add(up & inside & (np.abs(R[:, 2] - z_ct) < 0.07), 2, z_ct)
    vx = np.abs(N[:, 0]) > 0.9
    vy = np.abs(N[:, 1]) > 0.9
    tall = (R[:, 2] > 0.95) & (R[:, 2] < 1.75)
    n_pl += add(vx & tall & (np.abs(R[:, 0] - xE) < 0.16) & (R[:, 1] > 0.15) & (R[:, 1] < 2.0), 0, xE)
    n_pl += add(vx & (R[:, 2] > 0.2) & (R[:, 2] < 2.2) & (np.abs(R[:, 0] - xW) < 0.16)
                & (R[:, 1] > 0.75) & (R[:, 1] < 1.35), 0, xW)
    n_pl += add(vy & (R[:, 2] > 0.2) & (R[:, 2] < 2.2) & (np.abs(R[:, 1] - yN) < 0.16)
                & (R[:, 0] > -0.5) & (R[:, 0] < 0.4), 1, yN)
    if not rows:
        continue
    Am, b, wv = np.array(rows), np.array(rhs), np.array(w)
    # priors: s = 1 (sigma 0.04), t = 0 (sigma 0.25)
    prior_A = np.diag([1 / 0.04, 1 / 0.25, 1 / 0.25, 1 / 0.25])
    prior_b = np.array([1 / 0.04, 0, 0, 0])
    x = np.array([1.0, 0, 0, 0])
    for _ in range(6):                  # IRLS (Huber, 3 cm)
        res = Am @ x - b
        hub = np.where(np.abs(res) < 0.03, 1.0, 0.03 / np.maximum(np.abs(res), 1e-9))
        sw = np.sqrt(wv * hub)
        M = np.vstack([Am * sw[:, None], prior_A])
        y = np.concatenate([b * sw, prior_b])
        x = np.linalg.lstsq(M, y, rcond=None)[0]
    s, t = float(x[0]), x[1:]
    v = views.get(fid)
    if v is None:
        continue
    C = np.array(v["location"])
    out[fid] = {"s": round(s, 4), "t": [round(float(a), 4) for a in t], "n_planes": n_pl,
                "location": [round(float(a), 4) for a in s * C + t],
                "residual_cm": round(float(np.median(np.abs(Am @ x - b))) * 100, 2)}
(G / "camera_refine.json").write_text(json.dumps(out, indent=1))
for f, r in out.items():
    print(f, r["s"], r["t"], r["n_planes"], r["residual_cm"])
