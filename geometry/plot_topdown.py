import json, sys
import numpy as np
from pathlib import Path
from PIL import Image, ImageDraw
run = Path(sys.argv[1]); G = run / "geometry"
m = json.load(open(G / "measured.json")); fr = m["frame"]
A = np.load(G / "any4d.npz"); R = np.array(fr["R_room_from_any4d"]); S = fr["scale"]; cx, cy = fr["centre_raw"]
up = R[2]
def to_m(x):
    y = x @ R.T; y[..., 2] = x @ up + fr["floor_d"]
    y[..., 0] = (y[..., 0] - cx) * S; y[..., 1] = (y[..., 1] - cy) * S; y[..., 2] *= S; return y
pts = A["pts"][:, ::3, ::3].reshape(-1, 3).astype(float)
mean, std = np.array([0.485, 0.456, 0.406]), np.array([0.229, 0.224, 0.225])
col = np.clip(A["img"][:, ::3, ::3].reshape(-1, 3) * std + mean, 0, 1)
Y = to_m(pts); keep = (Y[:, 2] > 0.03) & (Y[:, 2] < 2.6)
res = 0.01; lo = np.array([-3.0, -3.5]); size = (600, 700)
img = np.ones((size[1], size[0], 3)); cnt = np.zeros(size[::-1])
ij = ((Y[keep, :2] - lo) / res).astype(int); ok = (ij >= 0).all(1) & (ij[:, 0] < size[0]) & (ij[:, 1] < size[1])
acc = np.zeros_like(img); np.add.at(acc, (ij[ok, 1], ij[ok, 0]), col[keep][ok]); np.add.at(cnt, (ij[ok, 1], ij[ok, 0]), 1)
img = np.where(cnt[..., None] > 0, acc / np.maximum(cnt, 1)[..., None], 1.0)
I = Image.fromarray((img[::-1] * 255).astype(np.uint8)); d = ImageDraw.Draw(I)
def P(x, y): return ((x - lo[0]) / res, size[1] - 1 - (y - lo[1]) / res)
w = m["walls"]; d.rectangle([*P(w["x_west"], w["y_north"]), *P(w["x_east"], w["y_south"])], outline=(0, 160, 0), width=2)
for fid, c in m["cameras"].items():
    u, v = P(*c["location"][:2]); Rb = np.array(c["R_c2w_blender"]); f = -Rb[:, 2][:2]; f /= np.linalg.norm(f) + 1e-9
    d.ellipse([u - 3, v - 3, u + 3, v + 3], fill=(255, 0, 0)); d.line([u, v, u + 18 * f[0], v - 18 * f[1]], fill=(255, 0, 0), width=2)
    d.text((u + 4, v), fid[1:], fill=(200, 0, 0))
pal = [(31, 119, 180), (255, 127, 14), (44, 160, 44), (214, 39, 40), (148, 103, 189), (140, 86, 75), (227, 119, 194), (127, 127, 127), (188, 189, 34), (23, 190, 207), (0, 0, 0), (100, 50, 200), (50, 200, 100), (200, 100, 50)]
for k, (oid, o) in enumerate(m["objects"].items()):
    a, b = o["bbox_lo"], o["bbox_hi"]
    x0, y0 = P(min(a[0], b[0]), max(a[1], b[1])); x1, y1 = P(max(a[0], b[0]), min(a[1], b[1]))
    d.rectangle([x0, y0, x1, y1], outline=pal[k % len(pal)], width=2)
    d.text(P(a[0], b[1]), oid[:14], fill=pal[k % len(pal)])
d.text((5, 5), "N up, E right, 1 px = 1 cm", fill=(0, 0, 0))
I.save(G / sys.argv[2] if len(sys.argv) > 2 else G / "topdown_room.png"); print("ok")
