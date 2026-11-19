"""Source-view check: rendered ID masks vs SAM3 masks of the same keyframe.

Per object: IoU over the frames it was measured in, plus centroid offset in
pixels; writes measured/compare/metrics.json and a few overlay images
(frame with the rendered object outlines) for review.
"""
import json
import sys
from pathlib import Path

import numpy as np
from PIL import Image, ImageDraw
from scipy import ndimage

run = Path(sys.argv[1]); mdir = run / (sys.argv[2] if len(sys.argv) > 2 else "measured")
src = mdir / "scene" / "source"
out = mdir / "compare"; out.mkdir(exist_ok=True)
meas = json.loads((run / "geometry/measured.json").read_text())
idc = json.loads((src / "id_colours.json").read_text())["id_colours"]
M = np.load(run / "geometry/sam3_masks.npz")
hh, hw = M["half_hw"]
frames = json.loads((run / "frames/frames.json").read_text())["frames"]
fid_of = {i: f["id"] for i, f in enumerate(frames)}


def sam_mask(k, size):
    m = np.unpackbits(M["mask"][k])[: hh * hw].reshape(hh, hw).astype(bool)
    return np.asarray(Image.fromarray(m).resize(size, Image.NEAREST))


def srgb(c):
    c = np.asarray(c, float)
    return np.where(c <= 0.0031308, 12.92 * c, 1.055 * np.power(c, 1 / 2.4) - 0.055)


def id_mask(img, colour):
    return np.all(np.abs(img - srgb(colour)) < 0.08, axis=-1)


ids = {}
metrics = {}
for oid, o in meas["objects"].items():
    per = {}
    for k in o["detections"]:
        fid = fid_of[int(M["frame"][k])]
        per.setdefault(fid, []).append(k)
    rows = []
    for fid, ks in per.items():
        if fid not in ids:
            ids[fid] = np.asarray(Image.open(src / f"id_{fid}.png").convert("RGB"), dtype=np.float32) / 255
        img = ids[fid]
        H, W = img.shape[:2]
        sm = np.zeros((H, W), bool)
        for k in ks:
            sm |= sam_mask(k, (W, H))
        rm = id_mask(img, idc[oid]) if oid in idc else np.zeros_like(sm)
        if sm.sum() < 50:
            continue
        inter, union = (sm & rm).sum(), (sm | rm).sum()
        cs = np.array(ndimage.center_of_mass(sm)); cr = np.array(ndimage.center_of_mass(rm)) if rm.any() else cs * np.nan
        rows.append({"frame": fid, "iou": round(float(inter / union), 3),
                     "offset_px": round(float(np.linalg.norm(cs - cr)), 1) if rm.any() else None,
                     "area_ratio": round(float(rm.sum() / sm.sum()), 2)})
    if rows:
        metrics[oid] = {"median_iou": float(np.median([r["iou"] for r in rows])),
                        "median_offset_px": float(np.nanmedian([r["offset_px"] if r["offset_px"] is not None else np.nan
                                                                for r in rows])),
                        "median_area_ratio": float(np.median([r["area_ratio"] for r in rows])), "views": rows}
for oid, m in sorted(metrics.items(), key=lambda kv: kv[1]["median_iou"]):
    print(f"{oid:18s} IoU {m['median_iou']:.2f}  offset {m['median_offset_px']:5.1f}px  area ratio {m['median_area_ratio']:.2f}  views {len(m['views'])}")
(out / "metrics.json").write_text(json.dumps(metrics, indent=1))

# overlays: frame | frame with rendered outlines
for fid in (sys.argv[3].split(",") if len(sys.argv) > 3 else ["F00", "F24", "F40", "F52"]):
    fr = Image.open(run / "frames" / f"{fid}.jpg").convert("RGB")
    img = np.asarray(Image.open(src / f"id_{fid}.png").convert("RGB"), dtype=np.float32) / 255
    ov = np.asarray(fr).copy()
    for oid, c in idc.items():
        m = id_mask(img, c)
        edge = m & ~ndimage.binary_erosion(m, iterations=2)
        ov[edge] = (srgb(c) * 255).astype(np.uint8) if not oid.startswith("room:") else (255, 255, 255)
    rp = src / f"rgb_{fid}.png"
    rgb = Image.open(rp).convert("RGB") if rp.is_file() else Image.fromarray((img * 255).astype(np.uint8))
    W, H = fr.size
    sheet = Image.new("RGB", (W * 3, H), "white")
    sheet.paste(fr, (0, 0)); sheet.paste(rgb.resize((W, H)), (W, 0)); sheet.paste(Image.fromarray(ov), (2 * W, 0))
    sheet.save(out / f"cmp_{fid}.jpg", quality=88)
print("wrote", out)
