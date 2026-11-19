"""What is in the room, and where each thing is seen best (video -> evidence).

Two passes, because they need different inputs:

1. INVENTORY over the contact sheets: one request sees every keyframe at
   once, so it can tell one bin filmed twice from two bins, and it knows the
   whole room before naming anything. Output: one entry per PHYSICAL object
   (id, category, mount, support, rough size, the frames that show it), the
   light fixtures (kept apart: they become Blender lights, see appearance.py),
   the room's surfaces/openings and which frames show the ceiling and windows.

2. GROUNDING on full-resolution keyframes: for each frame, the objects that
   inventory says are visible there are boxed (DeepSeek-flash boxes were
   checked by eye on four frames and were tight), plus one patch of plain
   white wall in the same frame. That patch is the photometric anchor for
   every colour later measured in this frame (appearance.py): a phone
   re-exposes every frame, so a colour is only meaningful relative to a
   reference IN THE SAME FRAME.

Evidence for an object = up to three crops from different moments of the
video (different viewpoints), ranked by size in frame, completeness and
sharpness.
"""
from __future__ import annotations

import json
import re
from pathlib import Path

import video_frames as vf
import vlm

MOUNTS = ("floor", "wall", "ceiling", "on_surface", "built_in")
KINDS = ("furniture", "appliance", "cabinetry", "fixture", "decor", "small_item")
PRIORITIES = ("high", "medium", "low")

INVENTORY_PROMPT = """These {n} images are contact sheets of keyframes from ONE handheld phone video walking through ONE indoor room. Every tile is labelled with its frame id (F00, F01, ...) and timestamp. Frames are in time order; the camera may pass the same object several times.

Build the inventory a 3D artist needs to rebuild this room as an editable scene.

Rules:
- One entry per PHYSICAL object. If the same bin appears at 10 s and at 200 s it is ONE entry with both frames, unless you can see two at once or they clearly differ. Two identical chairs seen together are two entries (chair_1, chair_2).
- Include furniture, appliances, built-in cabinetry (a run of kitchen base cabinets with its countertop is one entry, the wall cabinets above it another), fixtures (sink, faucet, sockets are NOT needed), and visible small items (bins, buckets, plants, bottles, cleaning tools).
- Light fixtures (pendants, downlights, panels, strips, lamps) go in "light_fixtures", not in "objects".
- "frames": every frame id where the object is clearly visible; "best_frame": the single frame where it is largest and least occluded.
- "on": the id of the object it stands on (a microwave on the base-cabinet run), else null.
- "movable_parts": what can be opened, pulled, pressed or turned on it (doors, drawers, lids, flaps, buttons, taps), or "none".
- Do not invent anything you cannot see.

Return a json object:
{{"room": {{"type": "...", "shape": "rectangular|L-shaped|galley|irregular", "size_guess_m": [width, depth, height], "size_basis": "which known-size objects you used",
           "floor": "...", "walls": "...", "ceiling": "...",
           "openings": [{{"id": "window_1", "kind": "window|door|opening", "frames": ["F01"], "note": "..."}}],
           "architectural_features": ["steps, beams, columns, exposed pipes, niches ..."]}},
 "objects": [{{"id": "snake_case", "category": "...", "brief": "one sentence: what this specific object looks like (shape, colours, materials)",
              "kind": "furniture|appliance|cabinetry|fixture|decor|small_item", "mount": "floor|wall|ceiling|on_surface|built_in", "on": null,
              "size_guess_m": [w, d, h], "frames": ["F00"], "best_frame": "F00", "movable_parts": "...",
              "priority": "high|medium|low"}}],
 "light_fixtures": [{{"id": "...", "type": "...", "count": 1, "frames": ["F04"]}}],
 "ceiling_frames": ["frames where the ceiling and its fixtures are visible"],
 "window_frames": ["frames where a window is visible"],
 "materials": [{{"id": "snake_case", "description": "...", "used_by": ["object ids or floor/walls/ceiling/splashback"], "frames": ["F09"]}}]}}"""

GROUND_PROMPT = """This is keyframe {fid} of a phone video of a room ({w}x{h} pixels).
Locate each of these objects if it is visible in THIS frame:
{items}

For each visible object give its tight bounding box [x0, y0, x1, y1] in normalised coordinates 0..1000 of the full frame (x to the right, y downward), whether it is complete (not cut off by the frame edge), and roughly what fraction of the object is visible (0..1). Skip objects that are not visible.
Also give ONE bounding box of a flat, evenly lit patch of plain WHITE painted wall or ceiling in this frame (no shadow, no highlight, no object, no text, no tile grout), or null if there is none.
Return a json object {{"detections": [{{"id": "...", "bbox": [x0, y0, x1, y1], "complete": true, "visible_fraction": 1.0}}], "white_patch": {{"bbox": [x0, y0, x1, y1], "surface": "wall|ceiling"}}}}"""


def _snake(text: str) -> str:
    s = re.sub(r"[^a-z0-9]+", "_", str(text or "").lower()).strip("_")
    return s or "object"


def _frame_ids(value, known: set) -> list[str]:
    out = []
    for f in value or []:
        m = re.search(r"F(\d+)", str(f).upper())
        if m:
            fid = f"F{int(m.group(1)):02d}"
            if fid in known and fid not in out:
                out.append(fid)
    return out


def _size(value) -> list[float] | None:
    try:
        vals = [float(v) for v in value][:3]
    except (TypeError, ValueError):
        return None
    if len(vals) != 3 or not all(0.01 <= v <= 20 for v in vals):
        return None
    return [round(v, 3) for v in vals]


def normalise_inventory(raw: dict, known: set) -> dict:
    """Sanitise ids, enums and frame references; keep what the model said."""
    seen: set = set()
    objects = []
    for o in raw.get("objects") or []:
        oid = _snake(o.get("id") or o.get("category"))
        base, k = oid, 2
        while oid in seen:
            oid, k = f"{base}_{k}", k + 1
        seen.add(oid)
        frames = _frame_ids(o.get("frames"), known)
        best = _frame_ids([o.get("best_frame")], known)
        if best and best[0] not in frames:
            frames.insert(0, best[0])
        objects.append({
            "id": oid, "category": str(o.get("category") or oid),
            "brief": str(o.get("brief") or o.get("category") or oid),
            "kind": o.get("kind") if o.get("kind") in KINDS else "furniture",
            "mount": o.get("mount") if o.get("mount") in MOUNTS else "floor",
            "on": _snake(o["on"]) if o.get("on") else None,
            "size_guess_m": _size(o.get("size_guess_m")),
            "frames": frames, "best_frame": (best or frames or [None])[0],
            "movable_parts": str(o.get("movable_parts") or "none"),
            "priority": o.get("priority") if o.get("priority") in PRIORITIES else "medium"})
    ids = {o["id"] for o in objects}
    for o in objects:                       # a support that is not an object is dropped
        if o["on"] and o["on"] not in ids:
            o["on_text"], o["on"] = o["on"], None
    room = raw.get("room") or {}
    lights = [{"id": _snake(l.get("id") or l.get("type")), "type": str(l.get("type") or ""),
               "count": int(l.get("count") or 1) if str(l.get("count") or "1").isdigit() else 1,
               "frames": _frame_ids(l.get("frames"), known)}
              for l in raw.get("light_fixtures") or []]
    return {"room": {**room, "size_guess_m": _size(room.get("size_guess_m")),
                     "openings": [{**op, "id": _snake(op.get("id") or op.get("kind")),
                                   "frames": _frame_ids(op.get("frames"), known)}
                                  for op in room.get("openings") or []]},
            "objects": objects, "light_fixtures": lights,
            "ceiling_frames": _frame_ids(raw.get("ceiling_frames"), known),
            "window_frames": _frame_ids(raw.get("window_frames"), known),
            "materials": [{**m, "id": _snake(m.get("id") or m.get("description")),
                           "frames": _frame_ids(m.get("frames"), known)}
                          for m in raw.get("materials") or []]}


def inventory(run: Path, frames: list[dict], sheets: list[dict], refresh: bool = False) -> dict:
    known = {f["id"] for f in frames}
    images = [(f"sheet_{i}", run / "sheets" / s["file"]) for i, s in enumerate(sheets)]
    ans = vlm.ask_json(INVENTORY_PROMPT.format(n=len(images)), images, stage="inventory",
                       cache_dir=run / "vlm_calls", refresh=refresh, max_tokens=7000,
                       max_side=2000)
    inv = normalise_inventory(ans["data"], known)
    inv["vlm"] = ans["meta"]
    (run / "survey.json").write_text(json.dumps(inv, ensure_ascii=False, indent=2),
                                     encoding="utf-8")
    return inv


# --------------------------------------------------------------------------
# grounding
# --------------------------------------------------------------------------
def plan_grounding(inv: dict, per_object: int = 4) -> dict[str, list[str]]:
    """frame id -> object ids to box there. Best frame first, then frames
    spread over the video, so the crops come from different viewpoints."""
    want: dict[str, list[str]] = {}
    for o in inv["objects"]:
        frames = list(o["frames"])
        chosen = [o["best_frame"]] if o["best_frame"] else []
        rest = [f for f in frames if f not in chosen]
        if rest:
            step = max(1, len(rest) // max(1, per_object - len(chosen)))
            chosen += rest[::step][:per_object - len(chosen)]
        for fid in chosen:
            want.setdefault(fid, [])
            if o["id"] not in want[fid]:
                want[fid].append(o["id"])
    return dict(sorted(want.items()))


def ground(run: Path, inv: dict, frames: list[dict], refresh: bool = False) -> dict:
    by_id = {f["id"]: f for f in frames}
    objs = {o["id"]: o for o in inv["objects"]}
    plan = plan_grounding(inv)
    result: dict = {"frames": {}, "objects": {oid: [] for oid in objs}}
    for fid, oids in plan.items():
        f = by_id[fid]
        items = "\n".join(f"- {oid}: {objs[oid]['brief']}" for oid in oids)
        ans = vlm.ask_json(GROUND_PROMPT.format(fid=fid, w=f["width"], h=f["height"], items=items),
                           [(fid, run / "frames" / f["file"])], stage=f"ground_{fid}",
                           cache_dir=run / "vlm_calls", refresh=refresh, max_tokens=1500)
        data = ans["data"]
        wp = data.get("white_patch") or None
        wbox = vf.bbox_pixels((wp or {}).get("bbox"), f["width"], f["height"]) if wp else None
        result["frames"][fid] = {"white_patch": (wp if wbox else None), "objects": oids}
        for det in data.get("detections") or []:
            oid = _snake(det.get("id"))
            if oid not in objs:
                continue
            box = vf.bbox_pixels(det.get("bbox"), f["width"], f["height"])
            if box is None:
                continue
            area = (box[2] - box[0]) * (box[3] - box[1]) / float(f["width"] * f["height"])
            try:
                vis = max(0.0, min(1.0, float(det.get("visible_fraction", 1.0))))
            except (TypeError, ValueError):
                vis = 1.0
            result["objects"][oid].append({
                "frame": fid, "t": f["t"], "bbox": [float(v) for v in det["bbox"]],
                "complete": bool(det.get("complete", True)), "visible_fraction": vis,
                "area": round(area, 4), "sharpness_rel": f["sharpness_rel"]})
    return result


DEDUPE_PROMPT = """These crops come from different moments of ONE walkthrough video of one room.
A ({a_id}, seen at {a_t}): {a_brief}
B ({b_id}, seen at {b_t}): {b_brief}
Are A and B the SAME physical object filmed twice, or two different objects? Use the surroundings visible in the crops (wall, floor, neighbours) as well as the object itself.
Return a json object {{"same": true, "reason": "..."}}"""


def _head(o: dict) -> str:
    words = re.findall(r"[a-z]+", (o.get("category") or o["id"]).lower())
    return words[-1] if words else o["id"]


def dedupe(run: Path, inv: dict, evidence: dict, refresh: bool = False) -> list[dict]:
    """Merge inventory entries that are one object filmed twice.

    The inventory pass decides identity from small contact-sheet tiles; the
    pantry got a 'tall slim bin' seen once next to the counter that is the
    same push-lid bin it had already listed. Same-category pairs are shown to
    the model as crops and merged when it says they are one object.
    """
    merged = []
    objs = inv["objects"]
    gone: set = set()
    for i, a in enumerate(objs):
        for b in objs[i + 1:]:
            if a["id"] in gone or b["id"] in gone or _head(a) != _head(b):
                continue
            ea, eb = evidence.get(a["id"]) or [], evidence.get(b["id"]) or []
            if not ea or not eb:
                continue
            imgs = [("A", run / "objects" / a["id"] / ea[0]["file"]),
                    ("B", run / "objects" / b["id"] / eb[0]["file"])]
            ans = vlm.ask_json(DEDUPE_PROMPT.format(
                a_id=a["id"], a_t=f"{ea[0]['t']:.0f}s", a_brief=a["brief"],
                b_id=b["id"], b_t=f"{eb[0]['t']:.0f}s", b_brief=b["brief"]), imgs,
                stage=f"dedupe_{a['id']}_{b['id']}", cache_dir=run / "vlm_calls",
                refresh=refresh, max_tokens=300)
            if ans["data"].get("same") is True:
                keep, drop = (a, b) if len(ea) >= len(eb) else (b, a)
                keep["frames"] = list(dict.fromkeys(keep["frames"] + drop["frames"]))
                gone.add(drop["id"])
                merged.append({"kept": keep["id"], "dropped": drop["id"],
                               "reason": ans["data"].get("reason")})
    if gone:
        inv["objects"] = [o for o in objs if o["id"] not in gone]
        for o in inv["objects"]:
            if o.get("on") in gone:
                o["on"] = next(m["kept"] for m in merged if m["dropped"] == o["on"])
        for oid in gone:
            evidence.pop(oid, None)
        inv.setdefault("merged", []).extend(merged)
    return merged


def score(view: dict) -> float:
    return (view["area"] ** 0.5) * (1.0 if view["complete"] else 0.55) \
        * (0.4 + 0.6 * view["visible_fraction"]) * min(1.5, 0.5 + 0.5 * view["sharpness_rel"])


def select_views(views: list[dict], k: int = 3, min_gap_s: float = 4.0) -> list[dict]:
    """Best-scoring views from different moments (= different viewpoints)."""
    picked: list[dict] = []
    for v in sorted(views, key=score, reverse=True):
        if all(abs(v["t"] - p["t"]) >= min_gap_s for p in picked):
            picked.append(v)
        if len(picked) == k:
            break
    return picked


def write_evidence(run: Path, inv: dict, grounding: dict, frames: list[dict]) -> dict:
    """Crop the chosen views; objects/<id>/view_k.jpg + evidence.json."""
    by_id = {f["id"]: f for f in frames}
    evidence = {}
    for o in inv["objects"]:
        views = select_views(grounding["objects"].get(o["id"]) or [])
        out_dir = run / "objects" / o["id"]
        out_dir.mkdir(parents=True, exist_ok=True)
        rec = []
        for k, v in enumerate(views):
            img = vf.imread(run / "frames" / by_id[v["frame"]]["file"])
            cut = vf.crop(img, v["bbox"], margin=0.08)
            if cut is None:
                continue
            path = out_dir / f"view_{k}.jpg"
            vf.imwrite(path, cut[0])
            rec.append({**v, "file": path.name, "crop_px": list(cut[1]),
                        "white_patch": (grounding["frames"].get(v["frame"]) or {}).get("white_patch")})
        evidence[o["id"]] = rec
        (out_dir / "evidence.json").write_text(json.dumps(
            {"object": o, "views": rec}, ensure_ascii=False, indent=2), encoding="utf-8")
    (run / "grounding.json").write_text(json.dumps(
        {"grounding": grounding, "evidence": evidence}, ensure_ascii=False, indent=2),
        encoding="utf-8")
    return evidence
