"""Per-object appearance review from the video's own cameras (AHa-3D style).

For every object the reconstruction measured, two keyframes where it is seen
large and whole are chosen; the scene is rendered from those recovered
cameras (scene/source/rgb_*.png, ID masks id_*.png), and the real crop and the
model crop -- same camera, same framing -- go side by side to the vision model,
which lists concrete differences and an imperative fix for each. The fixes are
what a re-forge is told; the real crops stay its reference.

    python adsl_adapter/video_review.py --run pantry --select     # choose frames
    (render those frames:  video_scene_builder.py ... --source-views --source-frames F00,F24,...)
    python adsl_adapter/video_review.py --run pantry --review     # crops + VLM
"""
from __future__ import annotations

import argparse
import json
from pathlib import Path

import numpy as np
from PIL import Image
from scipy import ndimage

import vlm

ROOT = Path(__file__).resolve().parents[1]

REVIEW_PROMPT = """You get two crops taken with the SAME camera and the SAME framing:
image "REAL": a frame of a phone video showing a real object (our earlier label for it: "{brief}" -- this label
  may be WRONG; trust only what you see in REAL);
image "MODEL": our procedural 3D model of that object, rendered from the recovered camera of that frame.

List the differences a person would notice: shape and silhouette, proportions, number and layout of parts
(doors, drawers, panels, handles, buttons, lids, legs), colours and materials (matte/glossy/metal/glass/wood),
printed text or logos large enough to matter, missing parts, extra parts.
Rules:
- A feature goes in "real" ONLY if you can see it in the REAL image. Never attribute the MODEL's features to REAL.
- If REAL has no handles / no frame / no legs, say so explicitly; if MODEL has them, the fix is to remove them.
- Ignore lighting, exposure, render noise, background, small crop offsets. If unsure, leave it out.

Answer with a json object:
{{"real_object": "one sentence: what REAL actually shows",
  "same_kind": true/false,
  "score": 0-10 (10 = indistinguishable in shape and look),
  "differences": [{{"aspect": "shape|proportion|parts|colour|material|label|missing|extra",
                    "real": "what REAL shows", "model": "what MODEL shows",
                    "fix": "one imperative instruction for the 3D program, with numbers where possible"}}],
  "already_right": ["short notes on what matches"]}}
Order differences by how much they matter. At most 8."""


def _run_dir(name: str) -> Path:
    p = Path(name)
    return p if p.is_dir() else ROOT / "runtime/video" / name


def _masks(run: Path):
    M = np.load(run / "geometry/sam3_masks.npz")
    return M, tuple(int(x) for x in M["half_hw"])


def _sam(M, hw, k, size):
    hh, ww = hw
    m = np.unpackbits(M["mask"][k])[: hh * ww].reshape(hh, ww).astype(bool)
    return np.asarray(Image.fromarray(m).resize(size, Image.NEAREST))


def select(run: Path, per_object: int = 2) -> dict:
    """Frames where each object is seen large and whole, distinct views."""
    meas = json.loads((run / "geometry/measured.json").read_text())
    M, hw = _masks(run)
    frames = json.loads((run / "frames/frames.json").read_text())["frames"]
    fid = {i: f["id"] for i, f in enumerate(frames)}
    out = {}
    for oid, o in meas["objects"].items():
        per = {}
        for k in o.get("detections") or []:
            per.setdefault(fid[int(M["frame"][k])], []).append(k)
        cand = []
        for f, ks in per.items():
            m = np.zeros((960, 544), bool)
            for k in ks:
                m |= _sam(M, hw, k, (544, 960))
            if m.sum() < 400:
                continue
            border = (m[0].sum() + m[-1].sum() + m[:, 0].sum() + m[:, -1].sum())
            perim = (m & ~ndimage.binary_erosion(m)).sum() + 1
            cut = border / perim
            ys, xs = np.nonzero(m)
            cand.append({"frame": f, "area": float(m.mean()), "cut": float(cut),
                         "bbox": [int(xs.min()), int(ys.min()), int(xs.max()), int(ys.max())],
                         "anchor": f in (o.get("anchor_frames") or [])})
        # whole (little of the outline on the image border), then large; anchors win ties
        cand.sort(key=lambda c: (c["cut"] > 0.25, -(c["area"] * (1.2 if c["anchor"] else 1.0))))
        chosen = []
        for c in cand:
            if all(abs(int(c["frame"][1:]) - int(x["frame"][1:])) > 2 for x in chosen):
                chosen.append(c)
            if len(chosen) == per_object:
                break
        out[oid] = chosen
    (run / "measured").mkdir(exist_ok=True)
    (run / "measured/review_frames.json").write_text(json.dumps(out, indent=1))
    frames_needed = sorted({c["frame"] for v in out.values() for c in v})
    print("review frames:", ",".join(frames_needed))
    return out


def _srgb(c):
    c = np.asarray(c, float)
    return np.where(c <= 0.0031308, 12.92 * c, 1.055 * np.power(c, 1 / 2.4) - 0.055)


def pairs(run: Path, mdir: Path) -> dict:
    sel = json.loads((mdir / "review_frames.json").read_text())
    src = mdir / "scene/source"
    idc = json.loads((src / "id_colours.json").read_text())["id_colours"]
    out_dir = mdir / "review"
    out_dir.mkdir(exist_ok=True)
    made = {}
    for oid, chosen in sel.items():
        made[oid] = []
        for c in chosen:
            f = c["frame"]
            rgb_p = src / f"rgb_{f}.png"
            if not rgb_p.is_file():
                continue
            real = Image.open(run / "frames" / f"{f}.jpg").convert("RGB")
            model = Image.open(rgb_p).convert("RGB").resize(real.size)
            ids = np.asarray(Image.open(src / f"id_{f}.png").convert("RGB"), dtype=np.float32) / 255
            rm = np.all(np.abs(ids - _srgb(idc.get(oid, (0, 0, 0)))) < 0.08, axis=-1)
            x0, y0, x1, y1 = c["bbox"]
            if rm.any():
                ys, xs = np.nonzero(rm)
                x0, y0, x1, y1 = min(x0, xs.min()), min(y0, ys.min()), max(x1, xs.max()), max(y1, ys.max())
            pw, ph = (x1 - x0) * 0.2 + 12, (y1 - y0) * 0.2 + 12
            box = (max(0, int(x0 - pw)), max(0, int(y0 - ph)), min(real.width, int(x1 + pw)), min(real.height, int(y1 + ph)))
            a, b = real.crop(box), model.crop(box)
            s = 420 / max(a.size)
            a, b = a.resize((max(1, int(a.width * s)), max(1, int(a.height * s)))), b.resize((max(1, int(b.width * s)), max(1, int(b.height * s))))
            pair = Image.new("RGB", (a.width * 2 + 8, a.height), "white")
            pair.paste(a, (0, 0)); pair.paste(b, (a.width + 8, 0))
            path = out_dir / f"{oid}_{f}.jpg"
            pair.save(path, quality=92)
            a.save(out_dir / f"{oid}_{f}_real.jpg", quality=92)
            b.save(out_dir / f"{oid}_{f}_model.jpg", quality=92)
            made[oid].append(str(path))
    return made


def review(run: Path, mdir: Path, refresh: bool = False) -> dict:
    vlm.ensure_vision(vlm.vision_model())
    survey = json.loads((run / "survey.json").read_text())
    brief = {o["id"]: o["category"] for o in survey["objects"]}     # category only: the long brief can be wrong
    made = pairs(run, mdir)
    results = {}
    for oid, paths in made.items():
        if not paths:
            continue
        reviews = []
        for p in paths:
            real_p, model_p = p.replace(".jpg", "_real.jpg"), p.replace(".jpg", "_model.jpg")
            ans = vlm.ask_json(REVIEW_PROMPT.format(brief=brief.get(oid, oid)), [("REAL", real_p), ("MODEL", model_p)],
                               stage=f"review_{oid}", cache_dir=run / "vlm_calls", refresh=refresh,
                               max_tokens=2500)
            reviews.append({"pair": p, **(ans["data"] or {})})
        diffs, seen = [], set()
        for r in reviews:
            for d in r.get("differences") or []:
                key = (d.get("aspect"), str(d.get("fix", ""))[:60])
                if key not in seen:
                    seen.add(key)
                    diffs.append(d)
        score = float(np.mean([float(r.get("score", 0)) for r in reviews]))
        results[oid] = {"score": round(score, 1), "real_object": [r.get("real_object") for r in reviews],
                        "differences": diffs[:10], "reviews": reviews}
        print(f"{oid:18s} score {score:4.1f}  differences {len(diffs)}")
    (mdir / "review/review.json").write_text(json.dumps(results, indent=1, ensure_ascii=False))
    return results


PART_CHECK_PROMPT = """Two crops from the SAME camera and framing: image "REAL" is a frame of a phone video, image
"MODEL" is our 3D model rendered from that frame's recovered camera. Object: {category}.
Check each item below IN BOTH images. Count one by one; if an item is not visible in an image, say so.
{items}
Answer json: {{"items": [{{"item": "...", "real": "what REAL shows (a count or a short description)",
"model": "what MODEL shows", "match": true/false/null (null = not visible in one of them)}}],
"other_visible_differences": ["..."]}}"""


def checklist(spec: dict) -> list[str]:
    """The facts the part study fixed, as items to verify visually."""
    items = []
    kinds = {}
    for p in spec.get("parts") or []:
        kinds.setdefault(p.get("kind"), []).append(p)
    keys = sum(int(p.get("count") or 1) for k in ("touch_key", "button", "knob", "switch") for p in kinds.get(k, []))
    if keys:
        items.append(f"number of buttons / touch keys and their layout around the display (spec: {keys})")
    for p in kinds.get("display", []):
        items.append(f"display: shape and position ({p.get('shape') or ''})"[:200])
    doors = sum(int(p.get("count") or 1) for p in kinds.get("door", []))
    drawers = sum(int(p.get("count") or 1) for p in kinds.get("drawer", []))
    if doors:
        items.append(f"number of doors (spec: {doors})")
    if drawers:
        items.append(f"number of drawers (spec: {drawers})")
    for p in kinds.get("handle", []):
        items.append(f"handle shape and how far it stands out ({p.get('shape') or ''})"[:220])
    if (kinds.get("door") or kinds.get("drawer")) and not kinds.get("handle"):
        items.append("handles: the doors / drawers have NO protruding handle (hidden grip or push-to-open)")
    if kinds.get("basin") or kinds.get("recess"):
        items.append("basin: rim on top of the worktop or undermount; drain position; drainer grooves on the worktop")
    items.append("main colour and finish of the body")
    return items


def part_check(run: Path, mdir: Path, oids: list[str], refresh: bool = False) -> dict:
    vlm.ensure_vision(vlm.vision_model())
    made = pairs(run, mdir)
    out = {}
    for oid in oids:
        spec = json.loads((run / "objects" / oid / "spec.json").read_text())
        cat = ((spec.get("object") or {}).get("category") or oid)
        items = checklist(spec)
        res = []
        for pth in made.get(oid) or []:
            ans = vlm.ask_json(PART_CHECK_PROMPT.format(category=cat, items="\n".join(f"- {i}" for i in items)),
                               [("REAL", pth.replace(".jpg", "_real.jpg")), ("MODEL", pth.replace(".jpg", "_model.jpg"))],
                               stage=f"partcheck_{oid}", cache_dir=run / "vlm_calls", refresh=refresh,
                               max_tokens=1800, max_side=1100)
            res.append({"pair": pth, **(ans["data"] or {})})
        out[oid] = res
        for r in res:
            bad = [i for i in r.get("items") or [] if i.get("match") is False]
            print(f"{oid:18s} {Path(r['pair']).name:28s} mismatches: " +
                  ("; ".join(f"{i['item'][:50]} (real {str(i.get('real'))[:40]} / model {str(i.get('model'))[:40]})" for i in bad)
                   or "none"))
    (mdir / "review/part_check.json").write_text(json.dumps(out, indent=1, ensure_ascii=False))
    return out


def main() -> int:
    ap = argparse.ArgumentParser()
    ap.add_argument("--run", required=True)
    ap.add_argument("--measured", default="measured")
    ap.add_argument("--select", action="store_true")
    ap.add_argument("--review", action="store_true")
    ap.add_argument("--refresh", action="store_true")
    ap.add_argument("--part-check", nargs="+", help="verify the part-studied facts of these objects visually")
    a = ap.parse_args()
    run = _run_dir(a.run)
    if a.select:
        select(run)
    if a.review:
        review(run, run / a.measured, a.refresh)
    if a.part_check:
        part_check(run, run / a.measured, a.part_check, a.refresh)
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
