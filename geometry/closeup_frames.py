"""Sharpest, largest close-ups of chosen objects from the VIDEO (not keyframes).

Keyframes are spaced for room coverage; the frames that show a control panel
squarely (the dispenser head at 2-9 s from the door, 225-227 s up close) fall
between them. For each object: decode the video at `step` seconds inside
windows around the keyframes that saw it, segment it with SAM3 (its prompt
phrase), score each frame by mask area x sharpness, keep the top crops at
least 1.5 s apart, upscale them, and save them for the part study.
Run in the `vggrpo` env:

    python geometry/closeup_frames.py runtime/video/pantry water_dispenser sink ...
"""
from __future__ import annotations

import json
import sys
from pathlib import Path

import cv2
import numpy as np
import torch
from PIL import Image

run = Path(sys.argv[1]); objs = sys.argv[2:]
G = run / "geometry"
meta = json.loads((run / "frames/frames.json").read_text())
video = Path(meta.get("video") or "")
frames = meta["frames"]
fidx = {f["id"]: f for f in frames}
measured = json.loads((G / "measured.json").read_text())["objects"]
prompts = json.loads((G / "prompts.json").read_text())
M = np.load(G / "sam3_masks.npz")

import local_paths  # noqa: E402  (SAM3_CHECKPOINT from env / runtime/.env)
from sam3.model_builder import build_sam3_image_model  # noqa: E402
from sam3.model.sam3_image_processor import Sam3Processor  # noqa: E402
model = build_sam3_image_model(checkpoint_path=local_paths.SAM3_CHECKPOINT,
                               load_from_HF=False)
proc = Sam3Processor(model, confidence_threshold=0.35)
torch.autocast("cuda", dtype=torch.bfloat16).__enter__()
cap = cv2.VideoCapture(str(video))
fps = cap.get(cv2.CAP_PROP_FPS) or 30.0
duration = cap.get(cv2.CAP_PROP_FRAME_COUNT) / fps


def grab(t):
    cap.set(cv2.CAP_PROP_POS_MSEC, t * 1000.0)
    ok, bgr = cap.read()
    return cv2.cvtColor(bgr, cv2.COLOR_BGR2RGB) if ok else None


for spec_arg in objs:
    oid, _, part_phrase = spec_arg.partition(":")       # "water_dispenser:control panel" = a part pass
    o = measured[oid]
    phrase = part_phrase or (prompts.get(oid) or [oid.replace("_", " ")])[0]
    seen = sorted({frames[int(M["frame"][k])]["id"] for k in o.get("detections") or []})
    times = []
    for f in seen:
        t0 = float(fidx[f]["t"])
        times += list(np.arange(max(0.0, t0 - 8.0), min(duration - 0.1, t0 + 8.0), 0.5))
    times = sorted(set(round(t, 2) for t in times))
    rows = []
    for t in times:
        img = grab(t)
        if img is None:
            continue
        st = proc.set_image(Image.fromarray(img))
        proc.reset_all_prompts(st)
        out = proc.set_text_prompt(phrase, st)
        if len(out["scores"]) == 0:
            continue
        j = int(torch.argmax(out["scores"]))
        m = out["masks"][j].squeeze().float().cpu().numpy() > 0.5
        if m.sum() < (300 if part_phrase else 1500):
            continue
        ys, xs = np.nonzero(m)
        x0, x1, y0, y1 = xs.min(), xs.max(), ys.min(), ys.max()
        H, W = m.shape
        cut = (x0 <= 1) + (y0 <= 1) + (x1 >= W - 2) + (y1 >= H - 2)
        gray = cv2.cvtColor(img[y0:y1 + 1, x0:x1 + 1], cv2.COLOR_RGB2GRAY)
        sharp = float(cv2.Laplacian(gray, cv2.CV_64F).var())
        rows.append({"t": float(t), "area": float(m.mean()), "sharp": sharp, "cut_edges": int(cut),
                     "bbox": [int(x0), int(y0), int(x1), int(y1)], "score": float(m.mean()) * min(sharp, 400.0) /
                     (1.0 + 0.5 * cut)})
    rows.sort(key=lambda r: -r["score"])
    chosen = []
    for r in rows:
        if all(abs(r["t"] - c["t"]) >= 1.5 for c in chosen):
            chosen.append(r)
        if len(chosen) == 8:
            break
    # also keep the most FRONTAL-looking small views: the frames whose box is most
    # complete (no cut edges) even if small -- a panel read whole at a distance
    whole = [r for r in rows if r["cut_edges"] == 0 and all(abs(r["t"] - c["t"]) >= 1.5 for c in chosen)]
    whole.sort(key=lambda r: -r["sharp"] * r["area"])
    chosen += whole[:3]
    od = run / "closeups" / (oid + ("__" + part_phrase.replace(" ", "_") if part_phrase else ""))
    od.mkdir(parents=True, exist_ok=True)
    for k, r in enumerate(chosen):
        img = grab(r["t"])
        x0, y0, x1, y1 = r["bbox"]
        pw, ph = int((x1 - x0) * 0.12) + 8, int((y1 - y0) * 0.12) + 8
        H, W = img.shape[:2]
        crop = Image.fromarray(img[max(0, y0 - ph):min(H, y1 + ph), max(0, x0 - pw):min(W, x1 + pw)])
        s = 900 / max(crop.size)
        if s > 1:
            crop = crop.resize((int(crop.width * s), int(crop.height * s)), Image.LANCZOS)
        r["file"] = f"crop_{k}_t{r['t']:.1f}.jpg"
        crop.save(od / r["file"], quality=93)
    (od / "closeups.json").write_text(json.dumps({"object": oid, "phrase": phrase, "frames_scanned": len(times),
                                                  "chosen": chosen}, indent=1))
    print(f"{oid:18s} scanned {len(times)} frames, kept {len(chosen)}: "
          + ", ".join(f"{c['t']:.1f}s" for c in chosen))
