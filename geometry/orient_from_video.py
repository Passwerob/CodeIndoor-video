"""Which way does each object's FRONT face? Decided by where its functional
parts are seen in the video, not by which face was filmed most.

For an object whose spec puts functional parts on its front (display, keys,
outlet, door, handle...), up to six keyframes that see it from different
directions are cropped; the vision model is told what the front carries and
says where the front points in each crop (toward the camera / away / image
left / image right). With the recovered camera pose each answer becomes a
room direction; the confident majority wins. Run with the CodeIndoor venv
(needs vlm.py); writes geometry/orientation.json.

    python geometry/orient_from_video.py runtime/video/pantry
"""
from __future__ import annotations

import json
import math
import sys
from pathlib import Path

import numpy as np
from PIL import Image

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT / "adsl_adapter"))
import vlm  # noqa: E402

FRONT_KINDS = {"display", "touch_key", "button", "knob", "switch", "dial", "door", "drawer", "outlet",
               "tap", "handle", "recess", "tray", "lever", "nozzle", "spout"}
DIRS = {"east": np.array([1.0, 0.0]), "west": np.array([-1.0, 0.0]),
        "north": np.array([0.0, 1.0]), "south": np.array([0.0, -1.0])}
PROMPT = """The crop shows one object: {category} (outlined region, the object in the middle).
Its FRONT is the side that carries: {front}.{sides}
Where does the object's FRONT point in this image?
- "toward_camera": the front faces the camera (you see the front, maybe slightly from the side);
- "away": the front faces away (you see the back);
- "image_left": the front faces the LEFT edge of the image (you see the object's right-hand side);
- "image_right": the front faces the RIGHT edge of the image (you see its left-hand side);
- "unsure": the crop does not show enough.
Judge from the parts you can actually see. Answer json:
{{"front_faces": "...", "confidence": 0.0-1.0, "seen": "the parts you see that decide it"}}"""


def snap(v):
    v = np.asarray(v[:2], float)
    n = np.linalg.norm(v)
    if n < 1e-6:
        return None, 0.0
    v = v / n
    name = max(DIRS, key=lambda k: DIRS[k] @ v)
    return name, float(DIRS[name] @ v)


def main(run: Path) -> int:
    vlm.ensure_vision(vlm.vision_model())
    meas = json.loads((run / "geometry/measured.json").read_text())
    M = np.load(run / "geometry/sam3_masks.npz")
    hh, hw = (int(x) for x in M["half_hw"])
    frames = json.loads((run / "frames/frames.json").read_text())["frames"]
    fid_of = {i: f["id"] for i, f in enumerate(frames)}
    survey = {o["id"]: o for o in json.loads((run / "survey.json").read_text())["objects"]}
    out_dir = run / "geometry/orient_crops"
    out_dir.mkdir(exist_ok=True)
    result = {}
    for oid, o in meas["objects"].items():
        sp = run / "objects" / oid / "spec.json"
        if not sp.is_file():
            continue
        spec = json.loads(sp.read_text())
        front = [p for p in spec.get("parts") or [] if p.get("face") == "front" and p.get("kind") in FRONT_KINDS]
        if not front:
            continue
        sides = {f: [p["id"].replace("_", " ") for p in spec["parts"] if p.get("face") == f and p.get("kind") not in
                     ("label", "logo", "other")] for f in ("left", "right", "back")}
        side_txt = "".join(f" Its {f.upper()} side (seen from the front) carries: {', '.join(v[:4])}."
                           for f, v in sides.items() if v)
        centre = np.array([o["x"], o["y"], o["z"] + o["dims"][2] / 2])
        # candidate frames: masks of this object, binned by viewing azimuth
        per = {}
        for k in o.get("detections") or []:
            per.setdefault(fid_of[int(M["frame"][k])], []).append(int(k))
        cands = []
        for f, ks in per.items():
            m = np.zeros((hh, hw), bool)
            for k in ks:
                m |= np.unpackbits(M["mask"][k])[: hh * hw].reshape(hh, hw).astype(bool)
            if m.sum() < 150:
                continue
            cam = np.array(meas["cameras"][f]["location"])
            az = math.degrees(math.atan2(*(cam[:2] - centre[:2])[::-1]))
            cands.append({"frame": f, "area": float(m.mean()), "az": az, "mask": m})
        bins = {}
        for c in cands:
            b = int(((c["az"] + 360) % 360) // 45)
            if b not in bins or c["area"] > bins[b]["area"]:
                bins[b] = c
        chosen = sorted(bins.values(), key=lambda c: -c["area"])[:6]
        votes, rows = {k: 0.0 for k in DIRS}, []
        for c in chosen:
            f = c["frame"]
            img = Image.open(run / "frames" / f"{f}.jpg").convert("RGB")
            W, H = img.size
            ys, xs = np.nonzero(c["mask"])
            sx, sy = W / hw, H / hh
            x0, x1, y0, y1 = xs.min() * sx, (xs.max() + 1) * sx, ys.min() * sy, (ys.max() + 1) * sy
            pw, ph = (x1 - x0) * 0.35 + 16, (y1 - y0) * 0.35 + 16
            crop = img.crop((max(0, int(x0 - pw)), max(0, int(y0 - ph)), min(W, int(x1 + pw)), min(H, int(y1 + ph))))
            path = out_dir / f"{oid}_{f}.jpg"
            crop.save(path, quality=92)
            ans = vlm.ask_json(PROMPT.format(category=survey.get(oid, {}).get("category", oid),
                                             front=", ".join(p["id"].replace("_", " ") for p in front[:8]),
                                             sides=side_txt),
                               [("crop", path)], stage=f"orient_{oid}", cache_dir=run / "vlm_calls",
                               max_tokens=600, min_side=320)
            a = ans["data"] or {}
            ff = str(a.get("front_faces", "unsure")).strip().lower()
            conf = float(a.get("confidence") or 0.0)
            cam = meas["cameras"][f]
            R = np.array(cam["R_c2w_blender"])
            to_cam = np.array(cam["location"]) - centre
            vec = {"toward_camera": to_cam, "away": -to_cam, "image_right": R[:, 0], "image_left": -R[:, 0]}.get(ff)
            d, align = snap(vec) if vec is not None else (None, 0.0)
            if d and align > 0.75 and conf >= 0.5:      # oblique answers are too ambiguous to snap
                votes[d] += conf
            rows.append({"frame": f, "answer": ff, "confidence": conf, "direction": d,
                         "alignment": round(align, 2), "seen": a.get("seen")})
        total = sum(votes.values())
        best = max(votes, key=votes.get)
        decided = total >= 1.0 and votes[best] >= 0.6 * total
        geo = {"east": "west", "west": "east", "north": "south", "south": "north"}.get(o.get("wall") or "", None)
        geo_front = {"west": "east", "east": "west", "north": "south", "south": "north"}.get(o.get("wall"))
        result[oid] = {"front": best if decided else None, "votes": {k: round(v, 2) for k, v in votes.items()},
                       "share": round(votes[best] / total, 2) if total else 0.0, "views": rows,
                       "geometry_front": geo_front,
                       "agrees_with_geometry": (best == geo_front) if decided and geo_front else None}
        flag = "" if not decided else ("" if best == geo_front else f"   <-- geometry said {geo_front}")
        print(f"{oid:18s} front {best if decided else 'UNDECIDED':6s} share {result[oid]['share']:.2f} "
              f"votes {result[oid]['votes']}{flag}")
    (run / "geometry/orientation.json").write_text(json.dumps(result, indent=1, ensure_ascii=False))
    return 0


if __name__ == "__main__":
    raise SystemExit(main(Path(sys.argv[1])))
