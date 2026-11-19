"""Brightness/colour of each surface group, real frame vs render, same camera.

Ratios are relative to the painted walls in the same view (removes exposure);
a group whose ratio is far from 1 has the wrong albedo in the scene.
"""
import json, sys
from pathlib import Path
import numpy as np
from PIL import Image

run = Path(sys.argv[1]); src = run / "measured/scene/source"
idc = json.loads((src / "id_colours.json").read_text())["id_colours"]


def lin(c):
    return np.where(c <= 0.04045, c / 12.92, ((c + 0.055) / 1.055) ** 2.4)


def srgb(c):
    c = np.asarray(c, float)
    return np.where(c <= 0.0031308, 12.92 * c, 1.055 * np.power(c, 1 / 2.4) - 0.055)


rows = {}
for rp in sorted(src.glob("rgb_F*.png")):
    f = rp.stem[4:]
    real = lin(np.asarray(Image.open(run / "frames" / f"{f}.jpg").convert("RGB"), dtype=np.float32) / 255)
    rend = lin(np.asarray(Image.open(rp).convert("RGB").resize((real.shape[1], real.shape[0])), dtype=np.float32) / 255)
    ids = np.asarray(Image.open(src / f"id_{f}.png").convert("RGB").resize((real.shape[1], real.shape[0]), Image.NEAREST),
                     dtype=np.float32) / 255
    med = {}
    for g, c in idc.items():
        m = np.all(np.abs(ids - srgb(c)) < 0.08, axis=-1)
        if m.sum() < 1500:
            continue
        from scipy import ndimage
        m = ndimage.binary_erosion(m, iterations=4)
        if m.sum() < 800:
            continue
        med[g] = (np.median(real[m], 0), np.median(rend[m], 0))
    if "room:walls" not in med:
        continue
    wr, wm = med["room:walls"]
    for g, (r, m) in med.items():
        rel = (r.mean() / wr.mean()) / max(m.mean() / wm.mean(), 1e-6)
        chroma_r, chroma_m = r / max(r.mean(), 1e-6), m / max(m.mean(), 1e-6)
        rows.setdefault(g, []).append((rel, chroma_r / chroma_m))
out = {}
for g, v in sorted(rows.items()):
    rel = float(np.median([x[0] for x in v])); ch = np.median(np.array([x[1] for x in v]), 0)
    out[g] = {"luminance_real_over_model": round(rel, 3), "chroma_real_over_model": ch.round(3).tolist(), "views": len(v)}
    print(f"{g:22s} lum x{rel:5.2f}  chroma {ch.round(2)}  ({len(v)} views)")
(run / "measured/photometric.json").write_text(json.dumps(out, indent=1))
