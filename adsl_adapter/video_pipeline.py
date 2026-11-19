"""Video -> editable, articulated Blender scene. The whole pipeline.

    # analysis (needs the gateway or DEEPSEEK_API_KEY; no Blender)
    python adsl_adapter/video_pipeline.py --video pantry.mp4 --run pantry

    # write a program per object, checked against its functional spec
    python adsl_adapter/video_pipeline.py --run pantry --forge [--only water_dispenser]

    # assemble the room in Blender (forged objects, or placeholders for the rest)
    python adsl_adapter/video_pipeline.py --run pantry --build-scene [--calibrate]

Stages, each cached under runtime/video/<run>/ (re-run one with --refresh NAME):

  frames      sharp, distinct keyframes + contact sheets           video_frames.py
  survey      inventory, light fixtures, surfaces, openings        video_survey.py
  ground      per-frame boxes -> evidence crops, white anchors     video_survey.py
  appearance  lighting (day/night, fixtures, CCT) + materials
              measured from pixels against the white wall          appearance.py
  spec        per object: parts / counts / function / joints,
              controls re-counted on a close-up                    functional_spec.py
  layout      room box, openings, wall/offset -> poses             video_layout.py
  plan        scene_plan.json for Blender
  forge       (--forge) furniture_forge with the spec as supervision
  scene       (--build-scene) video_scene_builder.py -> scene/scene.blend
  report      report.html (everything above on one page)
"""
from __future__ import annotations

import argparse
import json
import re
import subprocess
import sys
import time
from pathlib import Path

ADAPTER = Path(__file__).resolve().parent
if str(ADAPTER) not in sys.path:
    sys.path.insert(0, str(ADAPTER))

import appearance  # noqa: E402
import functional_spec  # noqa: E402
import video_frames as vf  # noqa: E402
import video_layout  # noqa: E402
import video_survey  # noqa: E402
import codeindoor_env as env  # noqa: E402

ROOT = env.ROOT
RUNS = ROOT / "runtime/video"
BLENDER = env.BLENDER
STAGES = ("frames", "survey", "ground", "appearance", "spec", "layout", "plan")


def _load(path: Path):
    return json.loads(path.read_text(encoding="utf-8"))


def _save(path: Path, data) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_text(json.dumps(data, ensure_ascii=False, indent=2, default=float),
                    encoding="utf-8")


def log(msg: str) -> None:
    print(f"[video] {msg}", flush=True)


# --------------------------------------------------------------------------
# stages
# --------------------------------------------------------------------------
def stage_frames(run: Path, video: Path | None, refresh: bool, target: int | None):
    meta = run / "frames" / "frames.json"
    if meta.is_file() and not refresh:
        frames = _load(meta)["frames"]
    else:
        if video is None or not video.is_file():
            raise SystemExit("--video is required for a new run")
        frames = vf.select_keyframes(video, run / "frames", target)
    sheets_meta = run / "sheets" / "sheets.json"
    if sheets_meta.is_file() and not refresh:
        sheets = _load(sheets_meta)
    else:
        sheets = vf.contact_sheets(frames, run / "frames", run / "sheets")
        _save(sheets_meta, sheets)
    log(f"frames: {len(frames)} keyframes, {len(sheets)} contact sheet(s)")
    return frames, sheets


def stage_survey(run, frames, sheets, refresh):
    path = run / "survey.json"
    inv = _load(path) if path.is_file() and not refresh else \
        video_survey.inventory(run, frames, sheets, refresh=refresh)
    log(f"survey: {len(inv['objects'])} objects, "
        f"{sum(l.get('count', 1) for l in inv.get('light_fixtures') or [])} light fixture(s), "
        f"{len(inv.get('materials') or [])} materials")
    return inv


def stage_ground(run, inv, frames, refresh):
    path = run / "grounding.json"
    if path.is_file() and not refresh:
        data = _load(path)
        g, ev = data["grounding"], data["evidence"]
    else:
        g = video_survey.ground(run, inv, frames, refresh=refresh)
        ev = video_survey.write_evidence(run, inv, g, frames)
        _save(path, {"grounding": g, "evidence": ev})
    merged = video_survey.dedupe(run, inv, ev, refresh=refresh)     # cached per pair
    if merged:
        _save(run / "survey.json", inv)
        _save(path, {"grounding": g, "evidence": ev})
        log("ground: merged " + ", ".join(f"{m['dropped']} -> {m['kept']}" for m in merged))
    log(f"ground: evidence for {sum(1 for v in ev.values() if v)}/{len(ev)} objects")
    return g, ev


def stage_appearance(run, inv, frames, grounding, refresh):
    path = run / "appearance.json"
    app = _load(path) if path.is_file() and not refresh else \
        appearance.run_appearance(run, inv, frames, grounding, refresh=refresh)
    lt = app["lighting"]
    log(f"appearance: {lt['time_of_day']}, "
        + ", ".join(f"{f.get('count')} x {f.get('type')} {f.get('cct_k'):.0f}K"
                    for f in lt["fixtures"])
        + f"; {len(app['materials'])} materials; camera WB {app['camera']['white_balance_k']:.0f}K")
    return app


def respec_from_review(run, inv, frames, evidence, app, only) -> dict:
    """Redo the functional spec of `only` with the review's sharper same-camera
    crops and its description of what the object really is."""
    rev_path = run / "measured/review/review.json"
    rev = _load(rev_path) if rev_path.is_file() else {}
    frames_by_id = {f["id"]: f for f in frames["frames"]} if isinstance(frames, dict) else \
        {f["id"]: f for f in frames}
    out = {}
    for o in inv["objects"]:
        if o["id"] not in (only or []):
            continue
        r = rev.get(o["id"]) or {}
        crops = sorted((run / "measured/review").glob(f"{o['id']}_F*_real.jpg"))
        notes = " | ".join(x for x in r.get("real_object") or [] if x) or None
        log(f"spec: {o['id']} again with {len(crops)} review crop(s); notes: {str(notes)[:120]}")
        spec = functional_spec.extract(run, o, evidence[o["id"]], frames_by_id, app.get("camera"),
                                       app.get("white_patches"), refresh=True, others=inv["objects"],
                                       extra_images=[str(c) for c in crops], notes=notes)
        out[o["id"]] = spec
        log(f"spec: {o['id']}: {spec.get('category')} | parts {[p['id'] for p in spec['parts']][:8]} | "
            f"joints {[j['id'] for j in spec.get('joints') or []]} | correction {spec.get('correction')}")
    return out


def stage_spec(run, inv, frames, evidence, app, refresh, only=None, priorities=("high", "medium")):
    by_id = {f["id"]: f for f in frames}
    specs = {}
    for o in inv["objects"]:
        if only and o["id"] not in only:
            continue
        if not only and o["priority"] not in priorities:
            continue
        path = run / "objects" / o["id"] / "spec.json"
        if path.is_file() and not refresh:
            specs[o["id"]] = _load(path)
            continue
        if not evidence.get(o["id"]):
            log(f"spec: {o['id']} has no evidence crop; skipped")
            continue
        t = time.time()
        specs[o["id"]] = functional_spec.extract(
            run, o, evidence[o["id"]], by_id, app["camera"], app["white_patches"],
            refresh=refresh, others=inv["objects"])
        s = specs[o["id"]]
        log(f"spec: {o['id']}: {len(s['parts'])} parts, {len(s['joints'])} joints "
            f"({time.time() - t:.0f}s)")
    return specs


def stage_layout(run, inv, sheets, specs, app, refresh):
    path = run / "layout.json"
    lay = _load(path) if path.is_file() and not refresh else \
        video_layout.run_layout(run, inv, sheets, specs, app, refresh=refresh)
    r = lay["room"]
    log(f"layout: room {r['width']:.2f} x {r['depth']:.2f} x {r['height']:.2f} m, "
        f"{len(lay['objects'])} objects, {len(lay['lights'])} lights, "
        f"{len(lay['issues'])} issue(s)")
    return lay


# --------------------------------------------------------------------------
# plan
# --------------------------------------------------------------------------
def _surface_rec(mats: dict, word: str, prefer: tuple = ()) -> dict | None:
    cands = [m for m in mats.values()
             if any(word in str(u).lower() for u in m.get("used_by") or [])
             or word in m["id"].lower()]
    if prefer:
        cands.sort(key=lambda m: 0 if m.get("class") in prefer else 1)
    return cands[0] if cands else None


def _surface(mats: dict, word: str, prefer: tuple = ()) -> str | None:
    rec = _surface_rec(mats, word, prefer)
    return rec["key"] if rec else None


def albedo_targets(mats: dict) -> dict:
    """Surface/white-wall brightness ratios measured in the video (same frame),
    the targets of the render-and-compare albedo calibration."""
    out = {"walls": 1.0}
    for group, word, prefer in (("floor", "floor", ()), ("ceiling", "ceiling", ("paint",))):
        rec = _surface_rec(mats, word, prefer)
        r = ((rec or {}).get("measured") or {}).get("ratio_to_white")
        if r:
            out[group] = r
    tile = next((m for m in mats.values() if m.get("class") == "tile"), None)
    r = ((tile or {}).get("measured") or {}).get("ratio_to_white")
    if r:
        out["splashback"] = r
    return out


def _main_material(spec: dict | None, inv_obj: dict, mats: dict) -> str:
    if spec:
        body = [p for p in spec["parts"] if p["kind"] in ("housing", "panel", "door")]
        if body:
            big = max(body, key=lambda p: p["size_frac"][0] * p["size_frac"][1])
            return big["material_key"]
    for m in mats.values():
        if inv_obj["id"] in (m.get("used_by") or []):
            return m["key"]
    return "paint:#BBBBBB:0.6"


def _wall_target_srgb(run: Path, frame: dict, white: dict, fallback) -> float | None:
    """Display value of the plain white wall in the frame the calibration
    camera imitates (or the video-wide anchor when that frame has none)."""
    wp = white.get(frame["id"])
    if wp:
        img = vf.imread(run / "frames" / frame["file"])
        px = appearance.region_pixels(img, wp.get("bbox"), shrink=0.1)
        if px is not None and len(px):
            lum = px @ [0.2126, 0.7152, 0.0722]
            return round(float(sorted(lum)[len(lum) // 2]), 3)
    return fallback


def _frame_median_srgb(path: Path) -> float:
    img = vf.imread(path)
    small = img[::4, ::4, ::-1].astype("float32") / 255.0
    lum = 0.2126 * small[..., 0] + 0.7152 * small[..., 1] + 0.0722 * small[..., 2]
    return round(float(sorted(lum.ravel())[lum.size // 2]), 3)


def compose_plan(run: Path, inv: dict, frames: list, app: dict, lay: dict, specs: dict) -> dict:
    room = lay["room"]
    W, D, H = room["width"], room["depth"], room["height"]
    mats = app["materials"]
    night = app["lighting"]["time_of_day"] == "night"
    surfaces = {"floor": _surface(mats, "floor"),
                "walls": _surface(mats, "wall", ("paint",)) or "paint:#EDEDED:0.75",
                "ceiling": _surface(mats, "ceiling", ("paint",)) or "paint:#F2F2F2:0.8"}
    forged, status = {}, {}
    for o in inv["objects"]:
        blend = ROOT / "runtime/outputs/forge" / o["id"] / f"{o['id']}.blend"
        best = ROOT / "runtime/outputs/forge" / f"{o['id']}_best" / f"{o['id']}.blend"
        if blend.is_file():
            forged[o["id"]], status[o["id"]] = str(blend), "accepted"
        elif best.is_file():
            # passed every deterministic check; the visual reviewer did not accept it
            forged[o["id"]], status[o["id"]] = str(best), "best_effort"
    inv_by_id = {o["id"]: o for o in inv["objects"]}
    objects = []
    for oid, pose in lay["objects"].items():
        objects.append({**{k: pose[k] for k in ("x", "y", "z", "yaw_deg", "dims", "mount")},
                        "id": oid, "blend": forged.get(oid), "ceiling_z": H,
                        "forge_status": status.get(oid, "placeholder"),
                        "placeholder_material": _main_material(specs.get(oid), inv_by_id[oid], mats)})
    info = _load(run / "frames" / "frames.json")["info"]
    portrait = info["height"] > info["width"]
    res = [720, 1280] if portrait else [1280, 720]
    # like-for-like targets from the frame the calibration camera imitates;
    # the per-material ratios (other frames) only fill what it cannot see
    targets = albedo_targets(mats)
    try:
        same = appearance.calibration_targets(run, frames[0])
    except Exception as exc:  # noqa: BLE001
        log(f"plan: calibration targets from {frames[0]['id']} failed: {exc}")
        same = {}
    wall_target = same.pop("_wall_srgb", None)
    same.pop("_frame", None)
    targets.update(same)
    plan = {
        "schema": "codeindoor.video_scene_plan.v1", "run": run.name,
        "room": {"width": W, "depth": D, "height": H, "surfaces": surfaces,
                 "openings": lay["openings"],
                 "splashbacks": video_layout.splashbacks(lay, mats)},
        "lights": appearance.light_plan(app["lighting"], lay["lights"], H),
        "world": {"time_of_day": app["lighting"]["time_of_day"],
                  "color": (0.004, 0.0045, 0.006) if night else (0.55, 0.62, 0.75),
                  "strength": 1.0},
        "camera": {"white_balance_k": app["camera"]["white_balance_k"], "exposure": 0.0,
                   "resolution": res, "calibration_view": "entrance",
                   "calibration_target_srgb": _frame_median_srgb(run / "frames" / frames[0]["file"]),
                   "exposure_anchor_srgb": app["camera"].get("exposure_anchor_srgb"),
                   "wall_target_srgb": wall_target or _wall_target_srgb(
                       run, frames[0], app.get("white_patches") or {},
                       app["camera"].get("exposure_anchor_srgb")),
                   "albedo_targets": targets},
        "objects": objects,
        "views": [
            # the first keyframe faces north by definition of the frame
            {"id": "entrance", "location": [0.0, -D / 2 + 0.45, 1.45],
             "look_at": [0.0, D / 2, 1.15], "lens_mm": 26.0},
            {"id": "overview", "location": [-W / 2 + 0.25, -D / 2 + 0.25, H - 0.3],
             "look_at": [W / 6, D / 6, 0.7], "lens_mm": 16.0},
            {"id": "reverse", "location": [0.0, D / 2 - 0.45, 1.45],
             "look_at": [0.0, -D / 2, 1.15], "lens_mm": 26.0}],
    }
    # close-ups of the forged objects that have joints: the structure is the
    # point of the demo, so each gets a camera in front of it
    import math as _m
    for o in objects:
        spec = specs.get(o["id"]) or {}
        if not o.get("blend") or not spec.get("joints"):
            continue
        w, d, h = o["dims"]
        yaw = _m.radians(o["yaw_deg"])
        fx, fy = _m.sin(yaw), -_m.cos(yaw)                   # the object's front
        cz = o["z"] + max(0.6 * h, 0.9 if o["z"] < 0.5 else 0.3)

        def blocked(px, py):
            # inside another object's footprint (below the camera height), or
            # outside the room: a camera there films the inside of a cabinet
            if not (-W / 2 + 0.12 < px < W / 2 - 0.12 and -D / 2 + 0.12 < py < D / 2 - 0.12):
                return True
            for other in objects:
                if other["id"] == o["id"] or other["z"] > cz:
                    continue
                hx, hy = video_layout.footprint_half(other["dims"], other["yaw_deg"])
                if abs(px - other["x"]) < hx + 0.1 and abs(py - other["y"]) < hy + 0.1:
                    return True
            return False

        base = min(max(1.1, 1.5 * max(w, h)), 2.4)
        cx = cy = None
        for dist in (base, base * 0.8, base * 1.25, base * 0.6):
            for side in (0.35, -0.35, 0.0, 0.6, -0.6):
                px, py = o["x"] + fx * dist - fy * side * dist, o["y"] + fy * dist + fx * side * dist
                if not blocked(px, py):
                    cx, cy = px, py
                    break
            if cx is not None:
                break
        if cx is None:
            continue
        plan["views"].append({"id": f"close_{o['id']}", "location": [cx, cy, cz],
                              "look_at": [o["x"], o["y"], o["z"] + 0.5 * h], "lens_mm": 32.0})
    _save(run / "scene_plan.json", plan)
    log(f"plan: {len(objects)} objects ({len(forged)} forged, "
        f"{len(objects) - len(forged)} placeholder), {len(plan['lights'])} lights, "
        f"{len(plan['room']['splashbacks'])} splashback(s)")
    return plan


# --------------------------------------------------------------------------
# forge + scene
# --------------------------------------------------------------------------
FORGE_MOUNT = {"wall": "wall", "ceiling": "ceiling"}


def review_feedback(run: Path, inv: dict, oid: str, separate: list[dict]) -> str | None:
    """The same-camera review (video_review.py) as repair instructions for the
    forge, minus anything about objects modelled separately or room surfaces."""
    path = run / "measured/review/review.json"
    if not path.is_file():
        return None
    rev = _load(path).get(oid)
    if not rev:
        return None
    words = {"backsplash", "splashback", "wall tile", "floor", "ceiling"}
    for x in separate:
        words.add(x["id"].replace("_", " "))
        words.update(w for w in re.split(r"[^a-z]+", x["category"].lower()) if len(w) > 3)
    keep = []
    for d in rev.get("differences") or []:
        text = " ".join(str(d.get(k, "")) for k in ("real", "model", "fix")).lower()
        if any(w in text for w in words):
            continue
        keep.append(d)
    if not keep:
        return None
    real = next((r for r in rev.get("real_object") or [] if r), "")
    lines = ["A same-camera comparison of your previous model with the real object (a frame of the video and a "
             "render of your model from that frame's recovered camera) found the differences below. Fix them; keep "
             "what already matches. Trust the video crops over any earlier description.",
             f"What the video actually shows: {real}"]
    for k, d in enumerate(keep[:8], 1):
        lines.append(f"{k}. [{d.get('aspect')}] REAL: {d.get('real')} | YOUR MODEL: {d.get('model')} | FIX: {d.get('fix')}")
    ok = [str(x) for r in rev.get("reviews") or [] for x in (r.get("already_right") or [])][:4]
    if ok:
        lines.append("Already right (keep): " + "; ".join(ok))
    return "\n".join(lines)


def part_check_feedback(run: Path, oid: str) -> str | None:
    """Item-by-item mismatches from the part check (video_review.py --part-check):
    the same camera, the facts the part study fixed, real vs model."""
    path = run / "measured/review/part_check.json"
    rows = _load(path).get(oid) if path.is_file() else None
    if not rows:
        return None
    lines = []
    for r in rows:
        for i in r.get("items") or []:
            if i.get("match") is False:
                lines.append(f"- {i['item']}: the VIDEO shows {i.get('real')}; YOUR MODEL shows {i.get('model')}")
        for d in (r.get("other_visible_differences") or [])[:3]:
            if not any(w in str(d).lower() for w in ("bucket", "plant", "bottle", "fridge unit", "wall socket", "worktop")):
                lines.append(f"- {d}")
    lines = list(dict.fromkeys(lines))
    if not lines:
        return None
    return ("Seen from the video's own camera, your model still differs from the real object in these "
            "points (fix each; keep everything else):\n" + "\n".join(lines[:10]))


def review_gate_items(run: Path, oid: str, separate: list[dict]) -> list[dict]:
    """The review's top differences as visual-gate requirements, so the
    reviewer does not accept a model that still has them."""
    path = run / "measured/review/review.json"
    rev = _load(path).get(oid) if path.is_file() else None
    if not rev:
        return []
    words = {x["id"].replace("_", " ") for x in separate} | {"backsplash", "splashback", "floor", "ceiling"}
    items = []
    for k, d in enumerate(rev.get("differences") or []):
        text = " ".join(str(d.get(x, "")) for x in ("real", "model", "fix")).lower()
        if any(w in text for w in words):
            continue
        items.append({"id": f"video_match_{k}", "feature": f"as in the video: {d.get('real')}",
                      "visible_evidence": f"not like the previous model ({d.get('model')})"})
        if len(items) == 4:
            break
    return items


def stage_forge(run: Path, inv: dict, specs: dict, evidence: dict, app: dict, only, attempts: int,
                use_trellis: bool, reforge: bool = False, from_review: bool = False,
                fresh: bool = False, skip_review: bool = False) -> dict:
    import furniture_forge as ff
    results = {}
    path = run / "forge_results.json"
    measured = {}
    if from_review:                               # measured envelope + review fixes, results kept apart
        measured = _load(run / "geometry/measured.json")["objects"]
        path = run / "measured/reforge" / ("_".join(sorted(only)) if only else "all") / "forge_results.json"
        path.parent.mkdir(parents=True, exist_ok=True)
    if path.is_file():
        results = _load(path)
    for o in inv["objects"]:
        oid = o["id"]
        if oid not in specs or (only and oid not in only):
            continue
        spec = specs[oid]
        d = spec["dimensions_m"]
        blend = ROOT / "runtime/outputs/forge" / oid / f"{oid}.blend"
        if blend.is_file() and not (reforge or from_review):
            log(f"forge: {oid} already accepted ({blend.name}); --reforge to redo")
            continue
        mats = [f"{m['id']}: '{m['key']}'" for m in app["materials"].values()
                if oid in (m.get("used_by") or [])]
        lines = []
        if mats:
            lines.append("Scene materials measured from the video for this object's surfaces "
                         "(use them verbatim where they apply): " + "; ".join(mats))
        # what the crops also show but is modelled separately: a base-cabinet
        # run kept growing a faucet out of its countertop
        carried, grew = {oid}, True                  # everything standing on it, transitively
        while grew:
            more = {x["id"] for x in inv["objects"] if x.get("on") in carried} - carried
            carried |= more
            grew = bool(more)
        others = [x for x in inv["objects"] if x["id"] != oid and (
            x["id"] in carried or o.get("on") == x["id"] or
            any(x["id"] in (m.get("used_by") or []) for m in app["materials"].values()
                if oid in (m.get("used_by") or [])))]
        if others:
            lines.append("These are SEPARATE objects, modelled on their own and placed by the "
                         "layout -- do not build them or any piece of them into this program: "
                         + ", ".join(f"{x['id']} ({x['category']})" for x in others))
        dims = [d["width"], d["depth"], d["height"]]
        feedback, resume = None, None
        if from_review:
            if oid in measured:
                dims = list(measured[oid]["dims"])
                lines.append(f"The envelope is MEASURED from a metric reconstruction of the video: width "
                             f"{dims[0]:.3f} m, depth {dims[1]:.3f} m, height {dims[2]:.3f} m. Build to it.")
            feedback = part_check_feedback(run, oid) if skip_review else review_feedback(run, inv, oid, others)
            cands = [c for c in (ROOT / "runtime/outputs/forge" / oid / "program.py",
                                 ROOT / "runtime/outputs/forge" / f"{oid}_best" / "program.py") if c.is_file()]
            if cands and not fresh:               # the newest: a best attempt that already passed the dry run
                resume = str(max(cands, key=lambda c: c.stat().st_mtime))
            if fresh and feedback:                # no previous program: the notes describe the target
                lines.append("Write a NEW program from scratch. " + feedback.replace(
                    "Fix them; keep what already matches.", "Build the object so none of these remain."))
                feedback = None
        context = "\n".join(lines) or None
        brief = o["brief"]
        sobj = spec.get("object") or {}
        if from_review and sobj.get("summary"):   # read from the crops; the survey brief can be wrong
            brief = f"{sobj.get('category') or o['category']}: {sobj['summary']}"
        entry = {"id": oid, "dimensions": dims,
                 "brief": brief, "mount": FORGE_MOUNT.get(o["mount"], "floor")}
        ev = {"images": [str(run / "objects" / oid / v["file"]) for v in evidence.get(oid, [])],
              "spec": spec, "frames": [v["frame"] for v in evidence.get(oid, [])],
              "context": context}
        if from_review:
            ev["gate_extra"] = [] if skip_review else review_gate_items(run, oid, others)
            # the sharpest video close-ups from the part study (panel first), else the review's crops
            study = sorted(p for d in (run / "closeups").glob(f"{oid}__*") for p in d.glob("crop_*.jpg"))[:1] + \
                sorted((run / "closeups" / oid).glob("crop_*.jpg"))[:2]
            real_crops = study or sorted((run / "measured/review").glob(f"{oid}_F*_real.jpg"))[:2]
            ev["images"] = [str(p) for p in real_crops[:3]] + ev["images"][:max(0, 3 - len(real_crops))]
        log(f"forge: {oid} ...")
        res = ff.forge(entry, max_attempts=attempts, evidence=ev, use_trellis=use_trellis,
                       initial_feedback=feedback, resume_code=resume)
        results[oid] = {"ok": res["ok"], "attempts": res["attempts"], "blend": res.get("blend"),
                        "handoff": res.get("handoff"),
                        "spec": (res.get("spec_report") or {}).get("coverage"),
                        "joints": len(res.get("joints") or []),
                        "log": [{k: e.get(k) for k in ("attempt", "stage", "hard", "note", "error")}
                                for e in res.get("log") or []]}
        log(f"forge: {oid}: ok={res['ok']} attempts={res['attempts']}")
        _save(path, results)
    return results


def stage_scene(run: Path, samples: int, calibrate: bool, render: bool = True,
                open_renders: bool = False, percentage: int = 100) -> int:
    argv = [BLENDER, "-b", "--factory-startup", "--python",
            str(ADAPTER / "video_scene_builder.py"), "--", str(run / "scene_plan.json"),
            "--samples", str(samples), "--percentage", str(percentage)]
    if open_renders:
        argv.append("--open-renders")
    if calibrate:
        argv.append("--calibrate")
    if not render:
        argv.append("--no-render")
    log("scene: " + " ".join(argv[:2]) + " ... video_scene_builder.py")
    proc = subprocess.run(argv, capture_output=True, text=True, timeout=7200)
    (run / "scene").mkdir(exist_ok=True)
    (run / "scene" / "blender_stdout.txt").write_text(
        (proc.stdout or "")[-20000:] + "\n--- STDERR ---\n" + (proc.stderr or "")[-6000:],
        encoding="utf-8")
    line = next((l for l in (proc.stdout or "").splitlines() if l.startswith("SCENE_BUILD=")), None)
    log("scene: " + (line or f"FAILED (exit {proc.returncode}); see scene/blender_stdout.txt"))
    return 0 if line else 1


# --------------------------------------------------------------------------
# main
# --------------------------------------------------------------------------
def main() -> int:
    ap = argparse.ArgumentParser(description=__doc__, formatter_class=argparse.RawTextHelpFormatter)
    ap.add_argument("--video", type=Path)
    ap.add_argument("--run", required=True, help="name under runtime/video/")
    ap.add_argument("--keyframes", type=int, help="number of keyframes (default ~1 per 5 s)")
    ap.add_argument("--refresh", action="append", default=[], choices=STAGES,
                    help="re-run this stage (and call the model again)")
    ap.add_argument("--all-objects", action="store_true",
                    help="also specify low-priority small items")
    ap.add_argument("--only", action="append", help="limit spec/forge to these object ids")
    ap.add_argument("--forge", action="store_true", help="write + build a program per object")
    ap.add_argument("--attempts", type=int, default=5)  # = harness_handoff.HANDOFF_ATTEMPTS
    ap.add_argument("--reforge", action="store_true", help="forge again even if accepted")
    ap.add_argument("--respec-from-review", action="store_true",
                    help="redo the functional spec of --only objects with the review's crops and notes")
    ap.add_argument("--skip-review-notes", action="store_true",
                    help="with --from-review: build from the (part-studied) spec and close-ups only, without the "
                         "older same-camera review notes")
    ap.add_argument("--fresh", action="store_true", help="with --from-review: author a new program, "
                    "do not edit the previous one")
    ap.add_argument("--from-review", action="store_true",
                    help="re-forge at the measured envelope with the same-camera review's fixes "
                         "(geometry/measured.json + measured/review/review.json)")
    ap.add_argument("--use_trellis", "--use-trellis", dest="use_trellis", action="store_true")
    ap.add_argument("--build-scene", action="store_true", help="assemble scene.blend in Blender")
    ap.add_argument("--samples", type=int, default=64)
    ap.add_argument("--calibrate", action="store_true",
                    help="match render exposure to the first keyframe")
    ap.add_argument("--open-renders", action="store_true",
                    help="also render every view with all doors / drawers opened")
    ap.add_argument("--percentage", type=int, default=100,
                    help="render resolution percentage (150 = 1080x1920 for portrait)")
    args = ap.parse_args()

    run = RUNS / args.run
    run.mkdir(parents=True, exist_ok=True)
    ref = set(args.refresh)
    frames, sheets = stage_frames(run, args.video, "frames" in ref, args.keyframes)
    inv = stage_survey(run, frames, sheets, "survey" in ref)
    grounding, evidence = stage_ground(run, inv, frames, "ground" in ref)
    app = stage_appearance(run, inv, frames, grounding, "appearance" in ref)
    prios = ("high", "medium", "low") if args.all_objects else ("high", "medium")
    specs = stage_spec(run, inv, frames, evidence, app, "spec" in ref, args.only, prios)
    if args.respec_from_review and args.only:
        specs.update(respec_from_review(run, inv, frames, evidence, app, args.only))
    # the layout needs every object's size: load specs that exist for the rest
    for o in inv["objects"]:
        p = run / "objects" / o["id"] / "spec.json"
        if o["id"] not in specs and p.is_file():
            specs[o["id"]] = _load(p)
    lay = stage_layout(run, inv, sheets, specs, app, "layout" in ref)
    compose_plan(run, inv, frames, app, lay, specs)
    if args.forge:
        stage_forge(run, inv, specs, evidence, app, args.only, args.attempts, args.use_trellis,
                    reforge=args.reforge, from_review=args.from_review, fresh=args.fresh,
                    skip_review=args.skip_review_notes)
        compose_plan(run, inv, frames, app, lay, specs)       # pick up the new .blend files
    status = 0
    if args.build_scene:
        status = stage_scene(run, args.samples, args.calibrate, open_renders=args.open_renders,
                             percentage=args.percentage)
    import video_report
    out = video_report.write(run)
    log(f"report: {out}")
    return status


if __name__ == "__main__":
    raise SystemExit(main())
