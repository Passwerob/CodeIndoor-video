"""scene_plan.json + geometry/measured.json -> measured/scene_plan.json.

Room size, object poses/sizes, windows and pendant positions come from the
reconstruction; materials, lights' photometry, forged assets and the camera's
white balance stay as before. Every keyframe becomes a source view rendered
with its recovered camera (pose + intrinsics) for the side-by-side review.
"""
from __future__ import annotations

import copy
import json
import sys
from pathlib import Path

run = Path(sys.argv[1])
out_dir = run / (sys.argv[2] if len(sys.argv) > 2 else "measured")
plan = json.loads((run / "scene_plan.json").read_text(encoding="utf-8"))
meas = json.loads((run / "geometry/measured.json").read_text(encoding="utf-8"))
frames = {f["id"]: f for f in json.loads((run / "frames/frames.json").read_text())["frames"]}

P = copy.deepcopy(plan)
room = meas["room"]
P["room"]["width"], P["room"]["depth"], P["room"]["height"] = (round(room["width"], 3), round(room["depth"], 3),
                                                               round(room["height"], 3))
P["room"]["walls_observed"] = room["walls_observed"]
P["room"]["openings"] = [{k: v for k, v in o.items() if k != "n_points"} for o in meas.get("openings", [])]
objs = meas["objects"]
# splashback: along the wall cabinets, from the worktop up to their underside
wc, bc = objs.get("wall_cabinet_run"), objs.get("base_cabinet_run")
for sb in P["room"].get("splashbacks") or []:
    if wc and bc and sb["wall"] == "east":
        sb["from"] = round(wc["bbox_lo"][1], 3)
        sb["to"] = round(wc["bbox_hi"][1], 3)
        sb["z0"] = round(bc["z"] + bc["dims"][2], 3)
        sb["z1"] = round(wc["z"], 3)

surf_path = run / "geometry/surfaces.json"
if surf_path.is_file():                      # patterns measured in metres from the reconstruction
    sf = json.loads(surf_path.read_text())
    t, fl = sf.get("tiles") or {}, sf.get("floor") or {}
    if t.get("pitch_m") and t.get("grout_hex"):
        for sb in P["room"].get("splashbacks") or []:
            sb["material"] = (f"tile:{t['tile_hex']}:0.12:grout={t['grout_hex']}:size={t['pitch_m']:.3f}:gap=0.003")
    if fl.get("speck_d90_m"):
        floor_key = P["room"]["surfaces"]["floor"].split(":")
        base = ":".join(floor_key[:3])
        # blurred specks read lighter than they are: the speck colour is the
        # darkest decile's, i.e. near the terrazzo chips' charcoal
        P["room"]["surfaces"]["floor"] = (f"{base}:speckle=#4A4A4A:size={fl['speck_d90_m']:.4f}"
                                          f":coverage={max(fl['coverage'] * 2, 0.02):.3f}")
rf0 = out_dir / "room_features.json"
if rf0.is_file() and json.loads(rf0.read_text()).get("floor"):   # hand-read floor finish
    P["room"]["surfaces"]["floor"] = json.loads(rf0.read_text())["floor"]
P["room"]["skirting"] = {"height": 0.04, "depth": 0.012, "material": "paint:#1C1C1C:0.45"}
PHOTO = {}
photo_path = out_dir / "photometric_adjust.json"
if photo_path.is_file():
    PHOTO = json.loads(photo_path.read_text())
if PHOTO.get("floor_scale"):                  # floor albedo relative to the walls, from the same-camera check
    fk = P["room"]["surfaces"]["floor"].split(":")
    rgb = [int(fk[1][i:i + 2], 16) / 255 for i in (1, 3, 5)]
    lin_ = [((c + 0.055) / 1.055) ** 2.4 if c > 0.04045 else c / 12.92 for c in rgb]
    lin_ = [min(1.0, c * PHOTO["floor_scale"]) for c in lin_]
    srgb_ = [1.055 * c ** (1 / 2.4) - 0.055 if c > 0.0031308 else 12.92 * c for c in lin_]
    fk[1] = "#" + "".join(f"{int(round(c * 255)):02X}" for c in srgb_)
    P["room"]["surfaces"]["floor"] = ":".join(fk)
H = P["room"]["height"]
template = P["lights"][0] if P.get("lights") else None
lights = []
for k, m in enumerate(meas.get("lights") or []):
    if template is None:
        break
    L = copy.deepcopy(template)
    L["id"] = f"pendant_{k + 1}"
    hz = L["housing"]
    hz["diameter"] = round(min(max(m["diameter"], 0.08), 0.2), 3)
    hz["height"] = round(min(max(m["top_z"] - m["bottom_z"], 0.12), 0.25), 3)
    hz["top_z"] = round(m["bottom_z"] + hz["height"], 3)
    hz["ceiling_z"] = H
    L["location"] = [m["x"], m["y"], m["bottom_z"]]
    L["measured"] = True
    # same-camera photometry: with a 75 deg cone the wall cabinets (36-63 deg off
    # the pendants' axis) got no direct light and read 1.8x too dark, the base
    # cabinets under them 2x too bright; the footage shows a wide beam
    L["spot_size_deg"] = float(PHOTO.get("spot_size_deg", 120.0))
    L["spot_blend"] = float(PHOTO.get("spot_blend", 0.6))
    lights.append(L)
if lights:
    P["lights"] = lights

layout_objs = json.loads((run / "layout.json").read_text(encoding="utf-8"))["objects"]
out_dir.mkdir(parents=True, exist_ok=True)
for e in P["objects"]:
    if layout_objs.get(e["id"], {}).get("on"):
        e["on"] = layout_objs[e["id"]]["on"]       # supports: built-ins carve them
    m = objs.get(e["id"])
    if not m:
        e["measured"] = False
        continue
    # small things: the measured box is noisy (a bottle's box takes in the leaves
    # beside it) and stretching a forged model to it ruins its proportions; keep
    # the measured position, the model's own size
    small = max(m["dims"]) < 0.4
    e.update({"x": round(m["x"], 3), "y": round(m["y"], 3), "z": round(m["z"], 3), "yaw_deg": m["yaw_deg"],
              "fit_dims": not small, "measured": True, "ceiling_z": H})
    if not small:
        e["dims"] = m["dims"]
    else:
        e["measured_dims"] = m["dims"]
# room architecture added by hand (measured/room_features.json)
rf_path = out_dir / "room_features.json"
if rf_path.is_file():
    P["room"]["features"] = json.loads(rf_path.read_text())["features"]
# objects the automatic stage never listed (checked by hand against the footage and
# the point cloud): measured/extra_objects.json, each with its own pose and blend
ex_path = out_dir / "extra_objects.json"
for e in (json.loads(ex_path.read_text()) if ex_path.is_file() else {}).get("objects", []):
    if not any(o["id"] == e["id"] for o in P["objects"]):
        P["objects"].append(dict(e, measured=True))
# pose corrections checked against the point cloud by hand (measured/pose_overrides.json):
# each entry says what was wrong with the automatic box and where the numbers come from
po_path = out_dir / "pose_overrides.json"
pose_ov = json.loads(po_path.read_text()) if po_path.is_file() else {}
for e in P["objects"]:
    o = pose_ov.get(e["id"])
    if o:
        e.update({k: v for k, v in o.items() if not k.startswith("_")})
        e["pose_override"] = o.get("_why", "pose_overrides.json")
        if e["id"] == "wall_cabinet_run":           # the splashback runs up to the cabinets' underside
            for sb in P["room"].get("splashbacks") or []:
                if sb["wall"] == "east":
                    sb["z1"] = round(e["z"], 3)
ov_path = out_dir / "blend_overrides.json"
overrides = json.loads(ov_path.read_text()) if ov_path.is_file() else {}
root = Path(__file__).resolve().parents[1]
for e in P["objects"]:
    if e["id"] in overrides:
        e["blend"] = str((root / overrides[e["id"]]).resolve())
        e["forge_status"] = "accepted"
        e["blend_choice"] = "override (blend_overrides.json)"

# same-camera photometric calibration (factors written by the check, clamped);
# not for hand-built objects: their materials were set against the footage and
# the factors were fitted to the old forged models
for e in P["objects"]:
    f = (PHOTO.get("albedo_scale") or {}).get(e["id"])
    if "outputs/hand/" in str(e.get("blend", "")):
        e.pop("albedo_scale", None)
        continue
    if f:
        e["albedo_scale"] = round(min(1.6, max(0.5, float(f))), 3)
tg = (P.get("camera") or {}).get("albedo_targets") or {}
for k, f in (PHOTO.get("target_scale") or {}).items():
    if k in tg:
        tg[k] = round(tg[k] * min(1.6, max(0.5, float(f))), 4)

views = []
for fid, c in meas["cameras"].items():
    f = frames[fid]
    sx, sy = f["width"] / c["width"], f["height"] / c["height"]
    views.append({"id": "src_" + fid, "source_frame": fid, "location": c["location"], "matrix": c["R_c2w_blender"],
                  "fx": c["fx"] * sx, "fy": c["fy"] * sy, "cx": c["cx"] * sx, "cy": c["cy"] * sy,
                  "resolution": [f["width"], f["height"]], "image": str((run / "frames" / f["file"]).resolve())})
# per-frame correction of Any4D's depth drift (geometry/refine_cameras.py): the
# camera centre moves with the frame's scale/offset, so each video camera sees
# the consensus room the way its frame saw itself
cref_path = run / "geometry" / "camera_refine.json"
cref = json.loads(cref_path.read_text()) if cref_path.is_file() else {}
for v in views:
    r = cref.get(v["source_frame"])
    if r:
        v["location_raw"] = v["location"]
        v["location"] = r["location"]
        v["refine"] = {"s": r["s"], "t": r["t"]}
P["source_views"] = views
P["views"] = [v for v in P["views"] if v["id"] in ("overview", "entrance", "reverse")]
for v in P["views"]:
    if v["id"] == "entrance":                    # the first keyframe's own camera, wider
        c = meas["cameras"]["F00"]
        v["location"] = c["location"]
        fwd = [-c["R_c2w_blender"][i][2] for i in range(3)]
        v["look_at"] = [c["location"][i] + fwd[i] for i in range(3)]
P["camera"]["resolution"] = [720, 1280]
P["measured_from"] = str(run / "geometry/measured.json")
out_dir.mkdir(parents=True, exist_ok=True)
(out_dir / "scene_plan.json").write_text(json.dumps(P, indent=1), encoding="utf-8")
print("wrote", out_dir / "scene_plan.json", "objects", sum(1 for e in P["objects"] if e.get("measured")),
      "lights", len(P["lights"]), "openings", len(P["room"]["openings"]), "source views", len(views))
