"""X-ray check: reconstructed points no object, wall, floor or ceiling explains.

Everything inside the room box, farther than 6 cm from the four walls, the floor
and the ceiling, and outside every measured object box (+3 cm), is clustered;
large clusters are structure the scene is missing.
"""
import json, sys
from pathlib import Path
import numpy as np
from scipy import ndimage

run = Path(sys.argv[1]); G = run / "geometry"
m = json.loads((G / "measured.json").read_text()); fr = m["frame"]
R = np.array(fr["R_room_from_any4d"]); S = fr["scale"]; cx, cy = fr["centre_raw"]; up = R[2]
A = np.load(G / "any4d.npz")
P = A["pts"][:, ::2, ::2].reshape(-1, 3).astype(float)
dep = A["depth"][:, ::2, ::2].reshape(-1)
Y = P @ R.T; Y[:, 2] = (P @ up + fr["floor_d"]) * S; Y[:, 0] = (Y[:, 0] - cx) * S; Y[:, 1] = (Y[:, 1] - cy) * S
Y = Y[dep * S < 3.0]                                  # near points only: far depth is noisy
W, D, H = m["room"]["width"], m["room"]["depth"], m["room"]["height"]
k = (np.abs(Y[:, 0]) < W / 2 - 0.06) & (np.abs(Y[:, 1]) < D / 2 - 0.06) & (Y[:, 2] > 0.06) & (Y[:, 2] < H - 0.1)
for o in m["objects"].values():
    lo, hi = np.array(o["bbox_lo"]) - 0.03, np.array(o["bbox_hi"]) + 0.03
    k &= ~np.all((Y >= lo) & (Y <= hi), axis=1)
U = Y[k]
vox = 0.05
ijk = np.floor((U - U.min(0)) / vox).astype(int)
grid = np.zeros(ijk.max(0) + 1, np.int32); np.add.at(grid, tuple(ijk.T), 1)
occ = grid >= 3
lab, n = ndimage.label(occ, structure=np.ones((3, 3, 3)))
rows = []
for c in range(1, n + 1):
    cells = np.argwhere(lab == c)
    if len(cells) < 25:
        continue
    lo = cells.min(0) * vox + U.min(0); hi = (cells.max(0) + 1) * vox + U.min(0)
    rows.append((len(cells), lo.round(2).tolist(), hi.round(2).tolist()))
rows.sort(reverse=True)
print(f"{k.sum()} unexplained near points of {len(Y)}; clusters >= 25 voxels:")
for r in rows[:12]:
    print(f"  {r[0]:5d} vox  x {r[1][0]:+.2f}..{r[2][0]:+.2f}  y {r[1][1]:+.2f}..{r[2][1]:+.2f}  z {r[1][2]:.2f}..{r[2][2]:.2f}")
