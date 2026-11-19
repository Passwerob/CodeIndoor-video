"""Surface patterns measured in metres from the reconstruction.

* splashback tiles: grout pitch along the wall and vertically (periodicity of
  dark grout pixels lifted to 3D), tile and grout colour relative to the same
  frame's painted wall;
* floor: speck coverage and speck size (dark blobs on near floor pixels,
  sized with the pixel footprint depth/f), base and speck colour relative to
  the same frame's wall.
Writes geometry/surfaces.json.
"""
import json
import sys
from pathlib import Path

import numpy as np
from PIL import Image
from scipy import ndimage

run = Path(sys.argv[1]); G = run / "geometry"
meas = json.loads((G / "measured.json").read_text())
fr = meas["frame"]; Rm = np.array(fr["R_room_from_any4d"]); S = fr["scale"]; cx, cy = fr["centre_raw"]
A = np.load(G / "any4d.npz")
PTS, DEP, K = A["pts"].astype(np.float64), A["depth"], A["K"]
NF, H, W, _ = PTS.shape
M = np.load(G / "sam3_masks.npz"); hh, hw = M["half_hw"]
frames = json.loads((run / "frames/frames.json").read_text())["frames"]
up = Rm[2]


def to_room(x):
    y = x @ Rm.T
    y[..., 2] = (x @ up + fr["floor_d"]) * S
    y[..., 0] = (y[..., 0] - cx) * S; y[..., 1] = (y[..., 1] - cy) * S
    return y


def mask(k, erode=2):
    m = np.unpackbits(M["mask"][k])[: hh * hw].reshape(hh, hw).astype(bool)
    m = np.asarray(Image.fromarray(m).resize((544, 960), Image.NEAREST))
    return ndimage.binary_erosion(m, iterations=erode)


def frame_rgb(i):
    return np.asarray(Image.open(run / "frames" / frames[i]["file"]).convert("RGB"), dtype=np.float32) / 255


def lin(c):
    return np.where(c <= 0.04045, c / 12.92, ((c + 0.055) / 1.055) ** 2.4)


def up_res(a):                                       # Any4D grid -> frame grid (nearest)
    yi = (np.arange(960) * H / 960).astype(int); xi = (np.arange(544) * W / 544).astype(int)
    return a[yi][:, xi]


def wall_ref(i):
    ws = [k for k in range(len(M["frame"])) if M["frame"][k] == i and str(M["phrase"][k]) == "wall" and M["score"][k] > 0.5]
    if not ws:
        return None
    m = np.zeros((960, 544), bool)
    for k in ws:
        m |= mask(k, 4)
    if m.sum() < 2000:
        return None
    px = lin(frame_rgb(i)[m])
    return np.median(px, 0)


def hexs(c):
    c = np.clip(c, 0, 1)
    s = np.where(c <= 0.0031308, 12.92 * c, 1.055 * np.power(c, 1 / 2.4) - 0.055)
    return "#" + "".join(f"{int(round(v * 255)):02X}" for v in s)


WALL_ALBEDO = lin(np.array([0xED, 0xED, 0xED]) / 255)   # the wall paint the scene already uses

# ---------------- tiles ----------------
tile_rows = []
pitch_votes = {"along": [], "vertical": []}
PITCHES = np.arange(0.04, 0.2501, 0.0025)       # long periods are biased up: any short span "aligns"
for k in range(len(M["frame"])):
    if str(M["phrase"][k]) != "tile backsplash" or M["score"][k] < 0.5:
        continue
    i = int(M["frame"][k])
    m = mask(k, 3)
    if m.sum() < 4000:
        continue
    img = lin(frame_rgb(i))
    lum = img.mean(-1)
    med = np.median(lum[m])
    grout = m & (lum < 0.45 * med)
    tile = m & (lum > 0.8 * med)
    ref = wall_ref(i)
    if ref is not None and tile.sum() > 1000 and grout.sum() > 200:
        gl = lum[grout]
        core = grout & (lum <= np.percentile(gl, 10))
        tile_rows.append((np.median(img[tile], 0) / ref, np.median(img[core], 0) / ref, grout.mean() / m.mean()))
    P = to_room(up_res(PTS[i]))
    g = P[grout]
    if len(g) < 300:
        continue
    # east-wall splashback: along = y, vertical = z
    # lines every p also line up at p/2, p/3...; never at 2p. Keep the spectrum
    for key, coord in (("along", g[:, 1]), ("vertical", g[:, 2])):
        pitch_votes[key].append(np.array([abs(np.exp(2j * np.pi * coord / p).mean()) for p in PITCHES]))
tiles = {}
if tile_rows:
    tr = np.median(np.array([t[0] for t in tile_rows]), 0); gr = np.median(np.array([t[1] for t in tile_rows]), 0)
    tiles = {"tile_hex": hexs(WALL_ALBEDO * tr), "grout_hex": hexs(WALL_ALBEDO * gr),
             "tile_over_wall": tr.round(3).tolist(), "grout_over_wall": gr.round(3).tolist(),
             "grout_area_fraction": round(float(np.median([t[2] for t in tile_rows])), 3), "frames": len(tile_rows)}
for key, votes in pitch_votes.items():
    spec = np.mean(votes, 0)
    top = spec.max()
    # the fundamental p is the longest peak whose half p/2 is also a peak
    # (grid lines every p line up at p/2 too); long spurious peaks have no half
    pk = [i for i in range(1, len(spec) - 1) if spec[i] >= spec[i - 1] and spec[i] >= spec[i + 1] and spec[i] > 0.4 * top]
    fund = [i for i in pk if any(abs(PITCHES[j] - PITCHES[i] / 2) < 0.008 for j in pk if j != i)]
    tiles[f"pitch_{key}_m"] = round(float(PITCHES[max(fund)] if fund else PITCHES[max(pk, key=lambda i: spec[i])]), 4)
    print(key, "spectrum peaks:", [(round(float(PITCHES[i]), 3), round(float(spec[i]), 3)) for i in range(1, len(spec) - 1)
                                   if spec[i] >= spec[i - 1] and spec[i] >= spec[i + 1] and spec[i] > 0.4 * top])
    # grout is 2-3 px wide: its median pixel mixes with the tile; the darkest
    # decile is the grout itself
pa, pv = tiles.get("pitch_along_m"), tiles.get("pitch_vertical_m")
if pa and pv and abs(pa - pv) / max(pa, pv) < 0.2:
    tiles["pitch_m"] = round((pa + pv) / 2, 3)          # square tiles
print("tiles:", tiles)

# ---------------- floor ----------------
rows = []
for k in range(len(M["frame"])):
    if str(M["phrase"][k]) != "floor" or M["score"][k] < 0.5:
        continue
    i = int(M["frame"][k])
    m = mask(k, 4)
    d = up_res(DEP[i]) * S
    near = m & (d < 1.6)
    if near.sum() < 8000:
        continue
    img = lin(frame_rgb(i)); lum = img.mean(-1)
    med = np.median(lum[near])
    dark = near & (lum < 0.6 * med)
    lab, n = ndimage.label(dark)
    if n < 20:
        continue
    sizes = ndimage.sum(np.ones_like(lum), lab, np.arange(1, n + 1))
    f = K[i][1, 1] * 960 / H
    foot = np.median(d[near]) / f                    # metres per pixel at the median depth
    ref = wall_ref(i)
    rows.append({"frame": frames[i]["id"], "coverage": float(dark.sum() / near.sum()),
                 "speck_d_m": float(2 * np.sqrt(np.median(sizes) / np.pi) * foot),
                 "speck_d90_m": float(2 * np.sqrt(np.percentile(sizes, 90) / np.pi) * foot),
                 "base_rel": (np.median(img[near & ~dark], 0) / ref).tolist() if ref is not None else None,
                 "speck_rel": (np.median(img[dark], 0) / ref).tolist() if ref is not None else None})
floor = {}
if rows:
    floor = {"coverage": round(float(np.median([r["coverage"] for r in rows])), 3),
             "speck_diameter_m": round(float(np.median([r["speck_d_m"] for r in rows])), 4),
             "speck_d90_m": round(float(np.median([r["speck_d90_m"] for r in rows])), 4), "frames": len(rows)}
    b = [r["base_rel"] for r in rows if r["base_rel"]]; s = [r["speck_rel"] for r in rows if r["speck_rel"]]
    if b:
        floor["base_hex"] = hexs(WALL_ALBEDO * np.median(np.array(b), 0))
        floor["speck_hex"] = hexs(WALL_ALBEDO * np.median(np.array(s), 0))
print("floor:", floor)
(G / "surfaces.json").write_text(json.dumps({"tiles": tiles, "floor": floor}, indent=1))
