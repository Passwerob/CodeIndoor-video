"""Front of each object from WHERE ITS FUNCTIONAL PARTS ARE, in metres.

The spec says which parts sit on the front (display, keys, outlet, handle,
door...). Those parts are segmented with SAM3 in every keyframe that shows
the object, lifted to 3D with the Any4D point maps, and only points inside the
object's measured box (+6 cm) are kept. The face of the box their centroid
lies against is the front: the control head and dispensing niche of the water
dispenser lie on its south face, so it faces south, however long the camera
lingered on its east side. Run in the `vggrpo` env; writes
geometry/front_parts.json.
"""
from __future__ import annotations

import json
import sys
from pathlib import Path

import numpy as np
import torch
from PIL import Image
from scipy import ndimage

PHRASES = {"display": "display screen", "touch_key": "buttons", "button": "buttons", "knob": "knob",
           "switch": "switch", "dial": "dial", "door": "door", "drawer": "drawer", "outlet": "spout",
           "spout": "spout", "nozzle": "spout", "tap": "faucet", "handle": "handle", "tray": "drip tray",
           "recess": "dispensing area", "lever": "lever"}

run = Path(sys.argv[1]); G = run / "geometry"
meas = json.loads((G / "measured.json").read_text())
fr = meas["frame"]; Rm = np.array(fr["R_room_from_any4d"]); S = fr["scale"]; cx, cy = fr["centre_raw"]; up = Rm[2]
A = np.load(G / "any4d.npz")
PTS, DEP = A["pts"].astype(np.float64), A["depth"]
NF, H, W, _ = PTS.shape
gy, gx = np.gradient(DEP, axis=(1, 2))
VALID = (np.hypot(gx, gy) / np.maximum(DEP, 1e-3) < 0.04)
M = np.load(G / "sam3_masks.npz")
frames = json.loads((run / "frames/frames.json").read_text())["frames"]
fidx = {f["id"]: i for i, f in enumerate(frames)}


def to_room(x):
    y = x @ Rm.T
    y[..., 2] = (x @ up + fr["floor_d"]) * S
    y[..., 0] = (y[..., 0] - cx) * S; y[..., 1] = (y[..., 1] - cy) * S
    return y


import local_paths  # noqa: E402  (SAM3_CHECKPOINT from env / runtime/.env)
from sam3.model_builder import build_sam3_image_model  # noqa: E402
from sam3.model.sam3_image_processor import Sam3Processor  # noqa: E402
model = build_sam3_image_model(checkpoint_path=local_paths.SAM3_CHECKPOINT,
                               load_from_HF=False)
proc = Sam3Processor(model, confidence_threshold=0.3)
torch.autocast("cuda", dtype=torch.bfloat16).__enter__()

result = {}
cache = {}
for oid, o in meas["objects"].items():
    sp = run / "objects" / oid / "spec.json"
    if not sp.is_file():
        continue
    spec = json.loads(sp.read_text())
    kinds = [p["kind"] for p in spec.get("parts") or [] if p.get("face") == "front" and p.get("kind") in PHRASES]
    if not kinds:
        continue
    phrases = []
    for k in kinds:
        if PHRASES[k] not in phrases:
            phrases.append(PHRASES[k])
    phrases = phrases[:4]
    lo = np.array(o["bbox_lo"]) - 0.06; hi = np.array(o["bbox_hi"]) + 0.06
    c = (np.array(o["bbox_lo"]) + np.array(o["bbox_hi"])) / 2
    half = np.maximum((np.array(o["bbox_hi"]) - np.array(o["bbox_lo"])) / 2, 0.03)
    fids = sorted({frames[int(M["frame"][k])]["id"] for k in o.get("detections") or []})
    pts = {ph: [] for ph in phrases}
    for f in fids:
        i = fidx[f]
        P = to_room(PTS[i])
        for ph in phrases:
            key = (f, ph)
            if key not in cache:
                im = Image.open(run / "frames" / frames[i]["file"]).convert("RGB")
                st = proc.set_image(im)
                proc.reset_all_prompts(st)
                out = proc.set_text_prompt(ph, st)
                ms = []
                for m in out["masks"]:
                    mm = m.squeeze().float().cpu().numpy() > 0.5
                    mm = np.asarray(Image.fromarray(mm).resize((W, H), Image.NEAREST))
                    ms.append(ndimage.binary_erosion(mm, iterations=1))
                cache[key] = ms
            for mm in cache[key]:
                q = P[mm & VALID[i]]
                q = q[np.all((q >= lo) & (q <= hi), axis=1)]
                if len(q) >= 20:
                    pts[ph].append(q)
    allq = [np.concatenate(v) for v in pts.values() if v]
    if not allq:
        result[oid] = {"front": None, "reason": "no functional part found inside the box", "phrases": phrases}
        print(f"{oid:18s} no parts found ({phrases})")
        continue
    # weight each phrase equally (a big 'door' mask must not drown the display)
    cents = [np.median(q, 0) for q in allq]
    cen = np.mean(cents, 0)
    off = (cen - c) / half
    cand = {"east": off[0], "west": -off[0], "north": off[1], "south": -off[1]}
    order = sorted(cand, key=cand.get, reverse=True)
    margin = cand[order[0]] - cand[order[1]]
    decided = cand[order[0]] > 0.35 and margin > 0.2
    result[oid] = {"front": order[0] if decided else None, "offset_norm": {k: round(float(v), 2) for k, v in cand.items()},
                   "margin": round(float(margin), 2), "phrases": phrases,
                   "points": {ph: int(sum(len(x) for x in v)) for ph, v in pts.items()},
                   "parts_centroid": [round(float(v), 3) for v in cen]}
    print(f"{oid:18s} front {order[0] if decided else 'UNDECIDED':6s} offsets {result[oid]['offset_norm']} "
          f"margin {margin:.2f} pts {result[oid]['points']}")
(G / "front_parts.json").write_text(json.dumps(result, indent=1))
