"""Gravity + Manhattan frame from the Any4D point maps, top-down density view."""
import sys, json
import numpy as np
from pathlib import Path
from PIL import Image

run = Path(sys.argv[1]); out = run / "geometry"
d = np.load(out / "any4d.npz")
pts, img, P = d["pts"], d["img"], d["poses"]
N, H, W, _ = pts.shape
# image back to 0..1 (dinov2 norm)
mean = np.array([0.485, 0.456, 0.406]); std = np.array([0.229, 0.224, 0.225])
rgb = np.clip(img * std + mean, 0, 1)
# normals from grid
dx = pts[:, 1:-1, 2:] - pts[:, 1:-1, :-2]; dy = pts[:, 2:, 1:-1] - pts[:, :-2, 1:-1]
n = np.cross(dx, dy); n /= np.linalg.norm(n, axis=-1, keepdims=True) + 1e-9
X = pts[:, 1:-1, 1:-1].reshape(-1, 3); C = rgb[:, 1:-1, 1:-1].reshape(-1, 3); Nn = n.reshape(-1, 3)
sel = np.random.default_rng(0).choice(len(X), 400000, replace=False)
X, C, Nn = X[sel], C[sel], Nn[sel]
down0 = P[:, :3, 1].mean(0); down0 /= np.linalg.norm(down0)
print("mean camera down", down0.round(3))
# floor = normals near +-down0, choose plane far along down0
cand = np.abs(Nn @ down0) > 0.9
h = X[cand] @ down0
print("heights of horizontal pts pct", np.percentile(h, [5, 25, 50, 75, 95, 99]).round(2))
# refine up by averaging normals of horizontal points in the lowest band (floor)
fl = cand.copy(); fl[cand] = h > np.percentile(h, 85)
nf = Nn[fl] * np.sign(Nn[fl] @ down0)[:, None]
down = nf.mean(0); down /= np.linalg.norm(down)
hf = X[fl] @ down; floor_h = np.median(hf)
print("down", down.round(3), "floor height along down", round(float(floor_h), 3), "spread", np.percentile(hf - floor_h, [10, 90]).round(3))
# ceiling
up = -down
vert = np.abs(Nn @ down) < 0.2
wn = Nn[vert] - np.outer(Nn[vert] @ down, down)
wn /= np.linalg.norm(wn, axis=1, keepdims=True)
# basis in horizontal plane
e1 = np.cross(down, [1, 0, 0]); e1 /= np.linalg.norm(e1); e2 = np.cross(down, e1)
ang = np.arctan2(wn @ e2, wn @ e1) % (np.pi / 2)
hist, edges = np.histogram(ang, 90, (0, np.pi / 2))
k = hist.argmax(); a0 = (edges[k] + edges[k + 1]) / 2
print("manhattan angle deg", round(np.degrees(a0), 1), "peak share", round(hist[k] / hist.sum(), 3))
ax = np.cos(a0) * e1 + np.sin(a0) * e2; ay = np.cross(up, ax)   # right-handed: x, y horizontal, z up
R = np.stack([ax, ay, up])                                   # world_any4d -> manhattan
Y = (X @ R.T); Y[:, 2] -= (-floor_h)                         # z=0 at floor
Y[:, 2] = (X @ up) + floor_h
print("z pct", np.percentile(Y[:, 2], [1, 5, 50, 95, 99]).round(2))
for a, nm in ((0, "x"), (1, "y")):
    print(nm, "pct", np.percentile(Y[:, a], [1, 3, 50, 97, 99]).round(2))
cams = (P[:, :3, 3] @ R.T); cams[:, 2] = P[:, :3, 3] @ up + floor_h
print("camera heights", np.percentile(cams[:, 2], [5, 50, 95]).round(2))
json.dump({"R_world_from_any4d": R.tolist(), "floor_offset": float(floor_h)}, open(out / "frame.json", "w"), indent=1)
# top-down image
m = (Y[:, 2] > 0.05) & (Y[:, 2] < 2.3)
lo = np.percentile(Y[m, :2], 0.5, axis=0) - 0.2; hi = np.percentile(Y[m, :2], 99.5, axis=0) + 0.2
res = 0.01; S = ((hi - lo) / res).astype(int) + 1
ij = ((Y[m, :2] - lo) / res).astype(int)
ok = (ij >= 0).all(1) & (ij[:, 0] < S[0]) & (ij[:, 1] < S[1]); ij = ij[ok]
acc = np.zeros((S[1], S[0], 3)); cnt = np.zeros((S[1], S[0]))
np.add.at(acc, (ij[:, 1], ij[:, 0]), C[m][ok]); np.add.at(cnt, (ij[:, 1], ij[:, 0]), 1)
im = np.where(cnt[..., None] > 0, acc / np.maximum(cnt, 1)[..., None], 1.0)
im = (im[::-1] * 255).astype(np.uint8)
I = Image.fromarray(im)
from PIL import ImageDraw
dr = ImageDraw.Draw(I)
for i, c in enumerate(cams):
    u = (c[0] - lo[0]) / res; v = S[1] - 1 - (c[1] - lo[1]) / res
    dr.ellipse([u - 4, v - 4, u + 4, v + 4], fill=(255, 0, 0))
    f = (P[i, :3, 2] @ R.T)[:2]; f /= np.linalg.norm(f) + 1e-9
    dr.line([u, v, u + 25 * f[0], v - 25 * f[1]], fill=(255, 0, 0), width=2)
    dr.text((u + 5, v + 2), str(i), fill=(0, 0, 255))
I.save(out / "topdown.png")
print("topdown", I.size, "extent m", (hi - lo).round(2))

# height histogram of upward-facing / downward-facing surfaces
hz = Y[:, 2]; nz = Nn @ up
for nm, mm in (("up-facing", nz < -0.9), ("down-facing", nz > 0.9)):
    hh, ee = np.histogram(hz[mm], 60, (-0.2, 5.8))
    print(nm, " ".join(f"{(ee[i]+ee[i+1])/2:.1f}:{hh[i]}" for i in range(60) if hh[i] > 300))
