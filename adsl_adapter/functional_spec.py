"""The detailed representation of one object: function, structure, materials.

The failure this exists for
---------------------------
From an image the model knows "this is a water dispenser" and reproduces the
LOOK of one -- a white box with a dark top. What it does not carry over is
what makes it that object: how many keys the control head has and which one
dispenses hot water, where the outlet is, that the drip tray lifts out, that
the lower front is a service door hinged on one side. A model built from
appearance alone has none of those parts, and nothing downstream (editing,
simulation, a robot opening the door) can use it.

So every object gets a written specification read from its video crops:

  parts   every piece a modeller must build separately, with count, the face
          it is on and its position there (u, v), its size as fractions of
          the object, material, printed labels/icons, function, interaction;
  joints  the STRUCTURE: what moves (revolute / prismatic / continuous /
          press), which edge carries the hinge, which way it opens, how far
          -- in SceneCode's URDF convention (articulation.py);
  usage   how a person operates it, step by step.

Counting is the known weak spot of VLMs (four keys read as "a row of keys"),
so controls get a second, dedicated pass: the control region is cut from the
full-resolution frame, enlarged, and the model enumerates the controls one
by one with a box each. Part colours are measured from pixels the same way
appearance.py measures surfaces.

The spec then supervises the code agent three times: as the brief it writes
from (spec_block), as a deterministic test of the built part list
(spec_check.py), and as the checklist of the visual gate (gate_checklist).
"""
from __future__ import annotations

import json
import re
from pathlib import Path

import numpy as np

import appearance
import articulation
import material_spec
import video_frames as vf
import vlm

KINDS = ("housing", "panel", "door", "drawer", "lid", "flap", "tray", "shelf", "handle",
         "knob", "button", "touch_key", "switch", "lever", "dial", "display", "indicator",
         "tap", "outlet", "nozzle", "spout", "grille", "vent", "foot", "wheel", "hinge",
         "basin", "recess", "cushion", "leg", "frame", "glass", "cable", "hose", "label",
         "logo", "other")
KIND_ALIASES = {"body": "housing", "casing": "housing", "cabinet": "housing", "shell": "housing",
                "key": "touch_key", "touch key": "touch_key", "touch": "touch_key",
                "screen": "display", "led": "indicator", "light": "indicator",
                "indicator_light": "indicator", "faucet": "tap", "spigot": "tap",
                "grill": "grille", "sticker": "label", "plinth": "foot", "base": "foot",
                "rack": "shelf", "bowl": "basin", "sink": "basin", "pull": "handle",
                "niche": "recess", "alcove": "recess", "cavity": "recess", "compartment": "recess"}
CONTROL_KINDS = {"button", "touch_key", "knob", "switch", "lever", "dial", "display",
                 "indicator"}
FACES = ("front", "top", "left", "right", "back", "bottom", "inside")
MOVING_KINDS = {"door": ("revolute", 100.0), "lid": ("revolute", 95.0),
                "flap": ("revolute", 60.0), "drawer": ("prismatic", 0.0),
                "tray": ("prismatic", 0.0), "knob": ("continuous", 300.0),
                "lever": ("revolute", 60.0), "button": ("press", 0.003)}

SPEC_PROMPT = """You are writing the FUNCTIONAL AND STRUCTURAL SPECIFICATION of one object for a modeller who will rebuild it as an editable, articulated 3D program. Recognising the category is not enough: the modeller needs every operable or functional part, how many there are, where each one sits, what it does, how it moves, and what it is made of.

Object: <<OID>> -- <<CATEGORY>>. <<BRIEF>>
It <<SUPPORT>>. Rough size from the room survey (width x depth x height, metres; width = left-right seen from its front): <<SIZE>>.
The images are crops of THIS object from a phone video, taken at different moments: <<LABELS>>. Ignore everything else in the crops.
These are modelled as SEPARATE objects -- do not list them, or their pieces, as parts of this one: <<OTHERS>>.
The one-line description above comes from a quick survey over small thumbnails and can be WRONG. Describe what the crops actually show. If the object is not what that description says, set "category" to what it really is and explain in "object.correction" (e.g. "not a standing sign: two warning stickers on the floor").

Rules:
1. Describe the object from its FRONT (the side a user faces when using it). left/right = the object's own left/right as seen standing in front of it.
2. parts: every visible piece a modeller must build separately -- body panels, doors, drawers, lids, flaps, trays, shelves, handles, knobs, buttons, touch keys, switches, displays, indicator lights, taps / outlets / nozzles, grilles / vents, feet, and RECESSES (a dispensing niche, a cup alcove: kind recess, face inside -- empty space the body is built around); cables, hoses and labels are optional.
   - count: how many identical ones exist; count them one by one.
   - face: front|top|left|right|back|bottom|inside (inside = within a recess or niche).
   - u, v: centre of the part on that face, as fractions: u 0 = left edge, 1 = right edge of that face seen from outside it; v 0 = bottom, 1 = top of the whole object (on the top or bottom face v runs 0 = front edge to 1 = back edge).
   - size_frac: [x, y, z] = the part's extent along the object's left-right axis (fraction of the object's WIDTH), front-back axis (fraction of its DEPTH) and vertical axis (fraction of its HEIGHT). Axes are the object's, whatever face the part is on: a vent on the left side face is thin in x and spans y and z.
   - material: class (paint|plastic|metal|glass|ceramic|tile|stone|wood|fabric|leather|rubber|screen), hex as seen, finish (mirror|polished|glossy|satin|matte), translucent, emissive (and emit_hex when it glows).
   - bbox_in_view: {"view": "<label>", "bbox": [x0, y0, x1, y1]} where the part is clearest, normalised 0..1000 in that crop.
   - function: what it does for the user. interaction: press|touch|rotate|pull|push|slide|lift|hinge|none.
   - labels: text or icons printed on or next to it, exactly as seen (e.g. "热水", "PUSH", a snowflake icon).
   - parent: id of the part it is mounted on (null for the main body).
   - confidence: high|medium|low (low = guessed or barely visible).
3. joints (the STRUCTURE): every part that moves gets one -- doors, drawers, lids, flaps, removable trays, knobs, levers, physical push buttons, swivel spouts. Touch keys and printed icons do not move.
   - type: revolute (hinged door / lid / flap / lever), prismatic (drawer / tray / slide-out), continuous (freely spinning knob), press (push button).
   - moving_parts: ids of the parts that move together (a door AND its handle).
   - hinge_edge: the edge of the moving part that carries the hinge: left|right|top|bottom|front|back, or center for a pivot through the middle (swing flap, knob). Evidence: visible hinges, the handle sits on the OPPOSITE edge, a frame showing it open.
   - opens_toward: front|up|down|left|right|back -- where the free edge (or the drawer) goes.
   - axis: the rotation axis in the object's frame -- x (left-right), y (front-back), z (vertical). Required for center pivots, knobs and swivels; for edge hinges it follows from the edge.
   - range: maximum opening; degrees for revolute / continuous, metres for prismatic.
   - A part with two motions (a mixer lever that lifts AND swings) gets two joints, listed in order from the body outward.
4. dimensions_m is the bounding box of the object AS IT IS IN THE ROOM, in its current pose: something lying flat on the floor has a height of a few centimetres.
5. Never invent hidden parts. Whatever you could not see or verify goes in "uncertain".

Return a json object:
{"object": {"category": "...", "subtype": "...", "summary": "two sentences: what it is and how it is used", "correction": null},
 "dimensions_m": {"width": 0.0, "depth": 0.0, "height": 0.0, "basis": "..."},
 "parts": [{"id": "snake_case", "kind": "...", "count": 1, "parent": null, "face": "front", "u": 0.5, "v": 0.5, "size_frac": [1.0, 1.0, 1.0], "shape": "...", "material": {"class": "...", "hex": "#RRGGBB", "finish": "...", "translucent": false, "emissive": false, "emit_hex": null}, "labels": [], "function": "...", "interaction": "...", "bbox_in_view": {"view": "view_0", "bbox": [0, 0, 1000, 1000]}, "confidence": "high"}],
 "joints": [{"id": "...", "type": "...", "moving_parts": ["..."], "hinge_edge": "...", "opens_toward": "...", "axis": null, "range": 0, "evidence": "...", "confidence": "..."}],
 "controls_summary": {"buttons": 0, "touch_keys": 0, "knobs": 0, "switches": 0, "displays": 0, "indicator_lights": 0, "outlets": 0, "doors": 0, "drawers": 0},
 "how_it_is_used": ["..."],
 "uncertain": ["..."]}"""

AUDIT_PROMPT = """This is a close-up of the <<REGION>> of a <<CATEGORY>> (<<OID>>), cut from a phone video and enlarged.
Enumerate EVERY operable control and EVERY indicator on it individually -- left to right, then top to bottom. Do not merge adjacent keys; one key carrying two icons is still one key. Distinguish physical buttons (they move when pressed) from touch keys (flat sensor areas on a panel).
For each: bbox [x0, y0, x1, y1] normalised 0..1000 in this image; type (button|touch_key|knob|switch|lever|display|indicator); the printed label or icon (describe icons: water drop, snowflake, flame, thermometer, lock, power, cup, gear ...); its colour when lit (or null); its function.
Return a json object {"controls": [{"bbox": [0, 0, 0, 0], "type": "...", "label": "...", "lit_colour": null, "function": "..."}], "count_by_type": {"touch_key": 0}, "readability": "clear|partial|unreadable", "notes": "..."}"""


def _snake(text) -> str:
    s = re.sub(r"[^a-z0-9]+", "_", str(text or "").lower()).strip("_")
    return s or "part"


def _num(v, default=None, lo=None, hi=None):
    try:
        x = float(v)
    except (TypeError, ValueError):
        return default
    if lo is not None:
        x = max(lo, x)
    if hi is not None:
        x = min(hi, x)
    return x


def _kind(value) -> str:
    """Map a free-text kind onto KINDS.

    Longest known kind contained in the text wins ("cabinet door" -> door,
    "door handle" -> handle, "vent grille" -> grille); aliases only after
    that, or "cabinet door" became a housing.
    """
    k = _snake(value)
    if k in KINDS:
        return k
    hits = [kind for kind in KINDS if kind in k]
    if hits:
        return max(hits, key=len)
    for word, mapped in KIND_ALIASES.items():
        if word.replace(" ", "_") in k:
            return mapped
    return "other"


# --------------------------------------------------------------------------
# normalisation
# --------------------------------------------------------------------------
TOP_KEYS = ("object", "dimensions_m", "parts", "joints", "controls_summary",
            "how_it_is_used", "uncertain")


def unwrap(raw: dict) -> dict:
    """Hoist the schema's keys when the model nested them.

    Observed: the whole answer returned inside "object" ({"object": {...,
    "parts": [...], "joints": [...]}}), which read as an object with zero
    parts. Any dict up to two levels down that carries "parts" is merged up.
    """
    if not isinstance(raw, dict) or isinstance(raw.get("parts"), list):
        return raw
    stack = [(raw, 0)]
    while stack:
        node, depth = stack.pop()
        for value in list(node.values()):
            if isinstance(value, dict):
                if isinstance(value.get("parts"), list):
                    out = dict(raw)
                    for k in TOP_KEYS:
                        if k in value and (k not in out or k == "parts" or not out.get(k)):
                            out[k] = value[k]
                    if isinstance(out.get("object"), dict) and "parts" in out["object"]:
                        out["object"] = {k: v for k, v in out["object"].items()
                                         if k not in TOP_KEYS}
                    return out
                if depth < 2:
                    stack.append((value, depth + 1))
    return raw


def normalise(raw: dict, size_guess: list | None = None) -> dict:
    """Sanitise every field the model returned; derive what can be derived.

    Ids become unique snake_case and parents / moving parts are remapped to
    them; enums are enforced; each part's material becomes a parametric key;
    doors, drawers and lids without a joint get one inferred (and flagged as
    such); a hinge that contradicts its own handle is reported.
    """
    checks: list[str] = []
    rename: dict[str, str] = {}
    parts: list[dict] = []
    for p in raw.get("parts") or []:
        pid = _snake(p.get("id") or p.get("kind"))
        base, k = pid, 2
        while pid in {q["id"] for q in parts}:
            pid, k = f"{base}_{k}", k + 1
        rename.setdefault(_snake(p.get("id")), pid)
        mat = p.get("material") or {}
        size = p.get("size_frac") or [0.2, 0.2, 0.05]
        try:
            size = [min(1.0, max(0.003, float(s))) for s in list(size)[:3]]
        except (TypeError, ValueError):
            size = [0.2, 0.2, 0.05]
        while len(size) < 3:
            size.append(0.05)
        emissive = bool(mat.get("emissive"))
        parts.append({
            "id": pid, "kind": _kind(p.get("kind")),
            "count": int(_num(p.get("count"), 1, 1, 64)),
            "parent": p.get("parent"),
            "face": p.get("face") if p.get("face") in FACES else "front",
            "u": _num(p.get("u"), 0.5, 0.0, 1.0), "v": _num(p.get("v"), 0.5, 0.0, 1.0),
            "size_frac": [round(s, 4) for s in size],
            "shape": str(p.get("shape") or ""),
            "material_class": material_spec.normalise_class(mat.get("class")),
            "material_vlm": {k: mat.get(k) for k in ("class", "hex", "finish", "translucent",
                                                       "emissive", "emit_hex")},
            "emissive": emissive,
            "labels": [str(x) for x in (p.get("labels") or []) if str(x).strip()][:12],
            "function": str(p.get("function") or ""),
            "interaction": str(p.get("interaction") or "none"),
            "bbox_in_view": p.get("bbox_in_view") if isinstance(p.get("bbox_in_view"), dict) else None,
            "confidence": str(p.get("confidence") or "medium").lower()
            if str(p.get("confidence") or "").lower() in ("high", "medium", "low") else "medium"})
    ids = {p["id"] for p in parts}
    for p in parts:
        par = rename.get(_snake(p["parent"])) if p["parent"] else None
        p["parent"] = par if par in ids and par != p["id"] else None
        m = p["material_vlm"]
        p["material_key"] = material_spec.from_observation(
            m.get("class"), m.get("hex"), m.get("finish"), emissive=p["emissive"],
            emit_hex=m.get("emit_hex"), translucent=bool(m.get("translucent")))

    joints = []
    for j in raw.get("joints") or []:
        moving = [rename.get(_snake(m)) for m in (j.get("moving_parts") or [])]
        moving = [m for m in moving if m in ids]
        if not moving:
            checks.append(f"joint {j.get('id')!r} dropped: its moving parts are not in the part list")
            continue
        jtype = str(j.get("type") or "").lower()
        if jtype not in articulation.JOINT_TYPES:
            jtype = MOVING_KINDS.get(next(p["kind"] for p in parts if p["id"] == moving[0]),
                                     ("revolute", 100.0))[0]
        joints.append({"id": _snake(j.get("id") or ("joint_" + moving[0])), "type": jtype,
                       "moving_parts": moving,
                       "hinge_edge": str(j.get("hinge_edge") or "").lower() or None,
                       "opens_toward": str(j.get("opens_toward") or "").lower() or None,
                       "range": articulation.range_magnitude(
                           j.get("range"), articulation.DEFAULT_RANGE.get(jtype, 0.0)),
                       "axis": j.get("axis"), "evidence": str(j.get("evidence") or ""),
                       "confidence": str(j.get("confidence") or "medium").lower(),
                       "source": "video"})
    jointed = {m for j in joints for m in j["moving_parts"]}
    for p in parts:                       # a door with no joint is a modelling hole
        if p["kind"] in MOVING_KINDS and p["id"] not in jointed and p["kind"] != "button":
            jtype, rng = MOVING_KINDS[p["kind"]]
            j = {"id": f"{p['id']}_joint", "type": jtype, "moving_parts": [p["id"]],
                 "hinge_edge": None, "opens_toward": "front" if p["face"] != "top" else "up",
                 "range": rng, "axis": None, "evidence": "inferred from the part kind",
                 "confidence": "low", "source": "inferred"}
            if jtype == "revolute":
                j["hinge_edge"] = "back" if p["face"] == "top" else "left"
            joints.append(j)
            checks.append(f"{p['id']}: no joint read from the video; inferred {jtype}")
    _check_hinges(parts, joints, checks)

    dims = raw.get("dimensions_m") or {}
    guess = size_guess or [None, None, None]
    out_dims = {}
    for i, key in enumerate(("width", "depth", "height")):
        val = _num(dims.get(key), None, 0.02, 8.0) or _num(guess[i], None, 0.02, 8.0) or 0.5
        out_dims[key] = round(val, 3)
    out_dims["basis"] = str(dims.get("basis") or "survey guess")
    return {"object": raw.get("object") or {}, "dimensions_m": out_dims, "parts": parts,
            "joints": joints, "controls_summary": raw.get("controls_summary") or {},
            "how_it_is_used": [str(x) for x in raw.get("how_it_is_used") or []],
            "uncertain": [str(x) for x in raw.get("uncertain") or []], "checks": checks}


def _check_hinges(parts: list[dict], joints: list[dict], checks: list[str]) -> None:
    """A handle sits on the edge OPPOSITE the hinge. Say so when they disagree,
    and fix the hinge when the joint itself was only a guess."""
    by_id = {p["id"]: p for p in parts}
    for j in joints:
        if j["type"] != "revolute" or j.get("hinge_edge") not in ("left", "right"):
            continue
        door = by_id.get(j["moving_parts"][0])
        if not door or door["face"] not in ("front", "back"):
            continue
        handles = [p for p in parts if p["kind"] in ("handle", "knob") and
                   (p.get("parent") in j["moving_parts"] or p["id"] in j["moving_parts"])]
        if not handles:
            continue
        side = "right" if handles[0]["u"] > door["u"] + 0.02 else (
            "left" if handles[0]["u"] < door["u"] - 0.02 else None)
        if door["face"] == "back" and side:
            side = "left" if side == "right" else "right"   # u is mirrored on the back
        expect = {"right": "left", "left": "right"}.get(side or "")
        if expect and expect != j["hinge_edge"]:
            if j.get("confidence") in ("low",) or j.get("source") == "inferred":
                checks.append(f"{j['id']}: hinge moved to the {expect} edge (handle is on the {side})")
                j["hinge_edge"] = expect
            else:
                checks.append(f"{j['id']}: hinge on the {j['hinge_edge']} edge but the handle is "
                              f"also on the {side}; kept the video reading -- verify")


# --------------------------------------------------------------------------
# control audit
# --------------------------------------------------------------------------
AUDIT_TYPE = {"button": "button", "touch_key": "touch_key", "knob": "knob", "switch": "switch",
              "lever": "lever", "display": "display", "indicator": "indicator", "dial": "knob"}


def _frame_box(view: dict, bbox_crop) -> list[float] | None:
    """A 0..1000 box inside a crop -> a 0..1000 box of the full frame."""
    try:
        x0, y0, x1, y1 = [float(v) for v in bbox_crop]
        X0, Y0, X1, Y1 = view["crop_px"]
        fw, fh = view["frame_w"], view["frame_h"]
    except (KeyError, TypeError, ValueError):
        return None
    cw, ch = X1 - X0, Y1 - Y0
    return [(X0 + x0 * cw / 1000) * 1000 / fw, (Y0 + y0 * ch / 1000) * 1000 / fh,
            (X0 + x1 * cw / 1000) * 1000 / fw, (Y0 + y1 * ch / 1000) * 1000 / fh]


LOCATE_PROMPT = """This is one frame of a phone video. Find the <<REGION>> of the <<CATEGORY>> (<<OID>>): <<DESC>>.
Give its tight bounding box [x0, y0, x1, y1] normalised 0..1000 of this frame (just that region, not the whole appliance), or null if it is not visible or seen only edge-on; whether its controls/markings face the camera; and how legible the individual keys, icons and text on it are in this frame (0 = cannot tell them apart, 1 = every icon readable).
Return a json object {"bbox": [x0, y0, x1, y1] or null, "facing_camera": true, "legibility": 0.0}"""


def dense_frames(run: Path, centres: list[float], frames_by_id: dict, window: float = 6.0,
                 step: float = 0.75, limit: int = 16) -> list[str]:
    """Extra frames from the VIDEO around the given times (not keyframes).

    Keyframes are chosen for coverage of the room; the one moment the camera
    faced a control panel squarely (225-228 s for the pantry dispenser) fell
    between two of them. Details are read from the video itself.
    """
    meta = json.loads((run / "frames" / "frames.json").read_text(encoding="utf-8"))
    video = Path(meta["video"])
    if not video.is_file():
        return []
    times: list[float] = []
    for c in centres:
        k = -window
        while k <= window + 1e-6:
            t = round(c + k, 2)
            if t >= 0 and all(abs(t - u) > step * 0.5 for u in times):
                times.append(t)
            k += step
    ids = []
    for t in sorted(times)[:limit]:
        fid = f"D{int(round(t * 100)):06d}"
        path = run / "frames" / f"{fid}.jpg"
        if fid not in frames_by_id:
            if not path.is_file():
                img = vf.grab(video, t)
                if img is None:
                    continue
                vf.imwrite(path, img)
            else:
                img = vf.imread(path)
            sharp = vf.sharpness(img)
            frames_by_id[fid] = {"id": fid, "t": t, "file": path.name,
                                 "width": int(img.shape[1]), "height": int(img.shape[0]),
                                 "sharpness_rel": round(sharp / max(1.0, meta.get(
                                     "median_sharpness") or sharp), 2), "dense": True}
        ids.append(fid)
    return ids


def best_region_crop(run: Path, oid: str, spec: dict, region_id: str, region: dict | None,
                     frames: list[str], frames_by_id: dict, refresh: bool = False,
                     dense_centres: list[float] | None = None):
    """The largest view of a sub-region (a control panel) anywhere in the video.

    The spec's own box for the panel comes from whichever crop the model
    found clearest for the WHOLE object -- often a distant view in which the
    panel is 60 px tall and nothing on it can be counted. So the panel is
    located again in every keyframe that shows the object, and in frames
    pulled from the video around the moments the object filled the screen,
    and the frame where it is largest is used.
    """
    cand = list(dict.fromkeys(frames))[:8]
    if dense_centres:
        cand += [f for f in dense_frames(run, dense_centres, frames_by_id) if f not in cand]
    if not cand:
        return None
    desc = (region or {}).get("function") or (region or {}).get("shape") or "the control area"
    prompt = (LOCATE_PROMPT.replace("<<REGION>>", region_id.replace("_", " "))
              .replace("<<CATEGORY>>", str(spec["object"].get("category") or oid))
              .replace("<<OID>>", oid).replace("<<DESC>>", desc))
    best = None
    # one frame per request: asked for boxes in eight frames at once, the model
    # answered with a single box keyed by the object name
    for fid in cand:
        f = frames_by_id[fid]
        ans = vlm.ask_json(prompt, [(fid, run / "frames" / f["file"])],
                           stage=f"locate_{oid}_{_snake(region_id)}_{fid}",
                           cache_dir=run / "vlm_calls", refresh=refresh, max_tokens=300)
        box = ans["data"].get("bbox")
        px = vf.bbox_pixels(box, f["width"], f["height"]) if box else None
        if px is None:
            continue
        area = (px[2] - px[0]) * (px[3] - px[1])
        if area > 0.55 * f["width"] * f["height"]:
            continue              # boxed the whole appliance, not the panel
        facing = 1.0 if ans["data"].get("facing_camera", True) else 0.3
        legible = _num(ans["data"].get("legibility"), 0.5, 0.05, 1.0)
        # what matters is reading it: legibility first, size second
        score = legible * facing * (area ** 0.5) * min(1.3, 0.6 + 0.4 * f.get("sharpness_rel", 1.0))
        if best is None or score > best[0]:
            best = (score, fid, box, px)
    return best


def audit_controls(run: Path, oid: str, spec: dict, views: list[dict], frames_by_id: dict,
                   refresh: bool = False, object_frames: list[str] | None = None) -> list[dict]:
    """Re-count controls on an enlarged full-resolution crop of their panel."""
    parts = {p["id"]: p for p in spec["parts"]}
    controls = [p for p in spec["parts"] if p["kind"] in CONTROL_KINDS]
    if not controls:
        return []
    groups: dict = {}
    for c in controls:
        groups.setdefault(c.get("parent") or "__object__", []).append(c)
    audits = []
    frames = list(object_frames or []) + [v["frame"] for v in views]
    for parent, members in groups.items():
        doubtful = any(m["confidence"] != "high" or m["count"] >= 3 for m in members)
        if not doubtful and len(members) < 3:
            continue
        region = parts.get(parent)
        # moments where the object filled the frame: the panel is likely
        # seen squarely somewhere near them
        close = sorted(views, key=lambda v: v.get("area", 0), reverse=True)[:2]
        found = best_region_crop(run, oid, spec, parent if region else "control panel",
                                 region, frames, frames_by_id, refresh=refresh,
                                 dense_centres=[v["t"] for v in close if v.get("area", 0) > 0.15])
        if not found:
            continue
        _, fid, box, px = found
        img = vf.imread(run / "frames" / frames_by_id[fid]["file"])
        cut = vf.crop(img, box, margin=0.15)
        if cut is None:
            continue
        path = run / "objects" / oid / f"audit_{_snake(parent)}.jpg"
        vf.imwrite(path, cut[0])
        prompt = (AUDIT_PROMPT.replace("<<REGION>>", parent if region else "control area")
                  .replace("<<CATEGORY>>", str(spec["object"].get("category") or oid))
                  .replace("<<OID>>", oid))
        ans = vlm.ask_json(prompt, [("closeup", path)], stage=f"audit_{oid}_{_snake(parent)}",
                           cache_dir=run / "vlm_calls", refresh=refresh, min_side=640,
                           max_tokens=2500)
        audits.append({"parent": parent, "image": path.name, "frame": fid,
                       "crop_px": [int(v) for v in px],
                       **reconcile(spec, parent, ans["data"])})
    return audits


def reconcile(spec: dict, parent: str, audit: dict) -> dict:
    """Fold the enumerated controls back into the spec's counts and labels."""
    found = [c for c in audit.get("controls") or [] if isinstance(c, dict)]
    readable = str(audit.get("readability") or "partial").lower() != "unreadable"
    by_type: dict = {}
    for c in found:
        t = AUDIT_TYPE.get(_kind(c.get("type")), None)
        if t:
            by_type.setdefault(t, []).append(c)
    changes = []
    if not readable or not found:
        return {"changes": ["audit unreadable: counts kept"], "by_type": {}}
    clear = str(audit.get("readability") or "").lower() == "clear"
    members = [p for p in spec["parts"] if (p.get("parent") or "__object__") == parent
               and p["kind"] in CONTROL_KINDS]
    for kind, items in by_type.items():
        same = [p for p in members if AUDIT_TYPE.get(p["kind"]) == kind]
        if len(same) == 1:
            p = same[0]
            if p["count"] != len(items) and (clear or p["confidence"] == "low"):
                changes.append(f"{p['id']}: count {p['count']} -> {len(items)} (enumerated)")
                p["count"] = len(items)
            elif p["count"] != len(items):
                changes.append(f"{p['id']}: enumeration found {len(items)}, spec says "
                               f"{p['count']} (close-up only partly readable: kept, verify)")
            p["labels_audit"] = [str(c.get("label") or "") for c in items]
            if clear:
                p["labels"] = p["labels_audit"] or p["labels"]
                p["confidence"] = "high"
            p["functions_each"] = [str(c.get("function") or "") for c in items]
            if any(c.get("lit_colour") for c in items) and kind in ("display", "indicator"):
                p["emissive"] = True
        elif not same and not clear:
            changes.append(f"possible {kind} x{len(items)} seen in a partly readable close-up "
                           f"-- not added")
        elif not same:
            ref = next((p for p in spec["parts"] if p["id"] == parent), None)
            new = {"id": _unique(spec, kind), "kind": kind, "count": len(items),
                   "parent": parent if parent != "__object__" else None,
                   "face": (ref or {}).get("face", "front"), "u": (ref or {}).get("u", 0.5),
                   "v": (ref or {}).get("v", 0.5), "size_frac": [0.06, 0.03, 0.01],
                   "shape": "small", "material_class": "plastic",
                   "material_vlm": {}, "emissive": kind in ("display", "indicator"),
                   "labels": [str(c.get("label") or "") for c in items],
                   "function": "; ".join(str(c.get("function") or "") for c in items)[:300],
                   "interaction": "press" if kind == "button" else (
                       "touch" if kind == "touch_key" else "none"),
                   "bbox_in_view": None, "confidence": "medium",
                   "functions_each": [str(c.get("function") or "") for c in items]}
            new["material_key"] = ("screen:#0E0E12:0.06:emit=#3355FF" if new["emissive"]
                                   else "plastic:#2A2A2E:0.35")
            spec["parts"].append(new)
            changes.append(f"added {new['id']} x{len(items)} (found by the enumeration)")
        else:
            total = sum(p["count"] for p in same)
            if total == len(items):
                # same count confirmed; attach the enumerated label to each key,
                # left to right -- replacing the first reading only if clear
                ordered = sorted(same, key=lambda p: p["u"])
                xs = sorted(items, key=lambda c: (c.get("bbox") or [0])[0])
                for p, c in zip(ordered, xs):
                    p["labels_audit"] = [str(c.get("label") or "")]
                    if clear and c.get("label"):
                        p["labels"] = [str(c["label"])]
                    p["confidence"] = "high"     # the COUNT is now confirmed twice
                changes.append(f"{kind}: {total} confirmed by the enumeration")
            elif str(audit.get("readability")).lower() == "clear":
                # A clear close-up outranks the whole-object reading: entries the
                # enumeration cannot find stop being required, missing ones are added.
                ordered = sorted(same, key=lambda p: p["u"])
                for p in ordered[len(items):]:
                    p["confidence"] = "low"
                    changes.append(f"{p['id']}: not found in the close-up; no longer required")
                if len(items) > total:
                    extra = len(items) - total
                    ref = ordered[-1]
                    new = dict(ref, id=_unique(spec, kind), count=extra, labels=[
                        str(c.get("label") or "") for c in items[total:]],
                        confidence="medium", bbox_in_view=None)
                    spec["parts"].append(new)
                    changes.append(f"added {new['id']} x{extra} (found by the enumeration)")
            else:
                changes.append(f"{kind}: spec lists {total} across {len(same)} entries, "
                               f"enumeration found {len(items)} -- kept the spec, verify")
    return {"changes": changes, "by_type": {k: len(v) for k, v in by_type.items()},
            "readability": audit.get("readability"), "notes": audit.get("notes")}


def _unique(spec: dict, base: str) -> str:
    ids = {p["id"] for p in spec["parts"]}
    pid, k = base, 2
    while pid in ids:
        pid, k = f"{base}_{k}", k + 1
    return pid


# --------------------------------------------------------------------------
# part colours from pixels
# --------------------------------------------------------------------------
def glow_colour(img, box, vlm_emit_hex=None) -> tuple[str | None, str]:
    """Emission colour of a display / LED region.

    The lit digits are the brightest pixels and are clipped to near-white by
    the phone, so the median says "white". The glow's hue lives in the most
    saturated pixels around them; if there are none, the model's colour.
    """
    px = appearance.region_pixels(img, box, shrink=0.0)
    if px is not None and len(px) >= 12:
        mx, mn = px.max(axis=1), px.min(axis=1)
        sat = (mx - mn) / np.maximum(mx, 1e-6)
        lit = (mx > 0.25) & (sat > 0.25)
        if lit.sum() >= max(6, 0.01 * len(px)):
            top = px[lit][np.argsort(sat[lit])[-max(6, int(0.3 * lit.sum())):]]
            return appearance.emission_colour(appearance.srgb_to_lin(np.median(top, axis=0))), \
                "measured (saturated glow pixels)"
    if vlm_emit_hex:
        return material_spec._clean_hex(vlm_emit_hex), "VLM (no saturated glow pixels)"
    return None, "none"


def measure_parts(run: Path, spec: dict, views: list[dict], frames_by_id: dict,
                  camera: dict | None, white: dict) -> None:
    labels = {f"view_{k}": v for k, v in enumerate(views)}
    cache: dict = {}
    for p in spec["parts"]:
        ref = p.get("bbox_in_view") or {}
        view = labels.get(ref.get("view"))
        if not view:
            continue
        box = _frame_box(view, ref.get("bbox"))
        if box is None:
            continue
        fid = view["frame"]
        if fid not in cache:
            cache[fid] = vf.imread(run / "frames" / frames_by_id[fid]["file"])
        img = cache[fid]
        cls = p["material_class"]
        m = p["material_vlm"]
        if p["emissive"]:
            emit, source = glow_colour(img, box, m.get("emit_hex"))
            if emit:
                p["material_key"] = material_spec.from_observation(
                    "screen" if p["kind"] == "display" else cls, m.get("hex") or "#101014",
                    m.get("finish") or "glossy", emissive=True, emit_hex=emit)
                p["measured"] = {"emit_hex": emit, "frame": fid, "source": source}
            continue
        if cls == "metal" or cls == "screen":
            continue                      # reflections, not colour
        meas = appearance.measure(img, box, white.get(fid), cls, camera=camera)
        if not meas.get("ok"):
            continue
        ok, why = appearance.consistent_with_vlm(m.get("hex"), material_spec.hex_to_linear(
            meas["display_hex"]))
        meas["frame"] = fid
        if not ok:
            meas["rejected"] = why
            p["measured"] = meas
            continue
        p["measured"] = meas
        p["material_key"] = material_spec.from_observation(
            cls, meas["albedo_hex"], m.get("finish"), translucent=bool(m.get("translucent")))


# --------------------------------------------------------------------------
# entry + views onto the spec
# --------------------------------------------------------------------------
def extract(run: Path, obj: dict, evidence: list[dict], frames_by_id: dict,
            camera: dict | None = None, white: dict | None = None,
            refresh: bool = False, others: list[dict] | None = None,
            extra_images: list | None = None, notes: str | None = None) -> dict:
    """Spec pass + control audit + part colours for one object.

    `others`: the rest of the inventory. Objects standing on / built into this
    one (and its own support) are named in the prompt as separate, so a base
    cabinet run does not also claim the sink and the tap as its parts.
    """
    oid = obj["id"]
    views = []
    for k, v in enumerate(evidence[:3]):
        f = frames_by_id[v["frame"]]
        views.append({**v, "frame_w": f["width"], "frame_h": f["height"]})
    images = [(f"view_{k}", run / "objects" / oid / v["file"]) for k, v in enumerate(views)]
    if not images:
        raise RuntimeError(f"{oid}: no evidence crops")
    support = (f"stands on {obj['on']}" if obj.get("on") else
               {"wall": "is mounted on a wall", "ceiling": "hangs from the ceiling",
                "built_in": "is built into the cabinetry"}.get(obj.get("mount"),
                                                                "stands on the floor"))
    related = [o for o in (others or []) if o["id"] != oid and (
        o.get("on") == oid or obj.get("on") == o["id"] or o.get("kind") in ("appliance", "fixture")
        or o.get("mount") == "built_in")]
    others_txt = ", ".join(f"{o['id']} ({o.get('category')})" for o in related[:14]) or "none"
    prompt = (SPEC_PROMPT.replace("<<OTHERS>>", others_txt).replace("<<OID>>", oid)
              .replace("<<CATEGORY>>", str(obj.get("category") or oid))
              .replace("<<BRIEF>>", str(obj.get("brief") or ""))
              .replace("<<SUPPORT>>", support)
              .replace("<<SIZE>>", " x ".join(f"{s:.2f}" for s in obj["size_guess_m"])
                       if obj.get("size_guess_m") else "unknown")
              .replace("<<LABELS>>", ", ".join(f"{l} (t={v['t']:.0f}s)" for (l, _), v in
                                               zip(images, views))))
    if extra_images:                      # sharper same-camera crops from the review
        images = images + [(f"close_{k}", Path(p)) for k, p in enumerate(extra_images[:2])]
    if notes:
        prompt += ("\n\nA close look at sharper crops of the same object (the close_* images) "
                   "found: " + notes + "\nIf this contradicts the earlier description, follow the "
                   "close crops and say so in object.correction.")
    ans = vlm.ask_json(prompt, images, stage=f"spec_{oid}", cache_dir=run / "vlm_calls",
                       refresh=refresh, max_tokens=8000, min_side=448)
    spec = normalise(unwrap(ans["data"]), obj.get("size_guess_m"))
    if not spec["parts"]:
        # one corrective retry: an empty parts list is never the right answer
        ans = vlm.ask_json(prompt + "\n\nIMPORTANT: put \"parts\" and \"joints\" at the TOP "
                                    "level of the json object, next to \"object\"; never "
                                    "return an empty parts list.", images,
                           stage=f"spec_{oid}_retry", cache_dir=run / "vlm_calls",
                           refresh=refresh, max_tokens=8000, min_side=448)
        spec = normalise(unwrap(ans["data"]), obj.get("size_guess_m"))
    spec["id"] = oid
    spec["audits"] = audit_controls(run, oid, spec, views, frames_by_id, refresh=refresh,
                                    object_frames=obj.get("frames"))
    measure_parts(run, spec, views, frames_by_id, camera, white or {})
    spec["evidence"] = [{"view": f"view_{k}", "frame": v["frame"], "t": v["t"], "file": v["file"]}
                        for k, v in enumerate(views)]
    spec["vlm"] = ans["meta"]
    (run / "objects" / oid / "spec.json").write_text(
        json.dumps(spec, ensure_ascii=False, indent=2, default=float), encoding="utf-8")
    return spec


def spec_block(spec: dict) -> str:
    """The specification as the code agent reads it."""
    d = spec["dimensions_m"]
    obj = spec.get("object") or {}
    lines = [
        "FUNCTIONAL SPECIFICATION (read from the video of the real object; your program is "
        "checked against it part by part, so every line below must exist in the code):",
        f"  object: {obj.get('category', spec.get('id'))} -- {obj.get('subtype', '')}. "
        f"{obj.get('summary', '')}",
        f"  measured size: {d['width']:.2f} W x {d['depth']:.2f} D x {d['height']:.2f} H m "
        f"(the envelope P is authoritative)",
        "  NAMING CONTRACT: name each part exactly by its id; a counted part is id_1 .. id_n; "
        "extra pieces of one part are id_<piece> (or id_<k>_<piece>), e.g. door_handle, "
        "touch_key_2_icon.",
        "  parts: face; centre u,v on that face; box = extents along x (left-right), "
        "y (front-back), z (up) as fractions of P -- the same order as B.box size=(x, y, z); "
        "material key. Nothing may leave the envelope: parts on a face sit flush with it "
        "or a few mm proud, never beyond P:"]
    for p in spec["parts"]:
        cnt = f" x{p['count']}" if p["count"] > 1 else ""
        names = (f" -> {p['id']}_1..{p['id']}_{p['count']}" if p["count"] > 1 else "")
        lab = f"; labels {p['labels']}" if p.get("labels") else ""
        glow = "; GLOWS" if p.get("emissive") else ""
        par = f"; on {p['parent']}" if p.get("parent") else ""
        opt = " (optional)" if p["kind"] in ("label", "logo", "cable", "hose", "other") else ""
        if p["kind"] == "recess":
            opt += (" (an OPENING: empty space -- build the surrounding body/slab from pieces "
                    "around it, never a solid with a dark plate on top)")
        sx, sy, sz = p["size_frac"]
        lines.append(f"    - {p['id']} [{p['kind']}]{cnt}{names}{opt}: {p['face']} face, "
                     f"u={p['u']:.2f} v={p['v']:.2f}, box x={sx:.2f}*W y={sy:.2f}*D "
                     f"z={sz:.2f}*H, '{p['material_key']}'{glow}{par}; {p['function']}{lab}")
    if spec.get("joints"):
        lines.append("  STRUCTURE -- moving parts must be SEPARATE parts (never merged into the "
                     "body), built in the CLOSED position; the hinge axis is derived from where "
                     "you place them, so put the hinge edge exactly where described:")
        for j in spec["joints"]:
            rng = (f"{j['range']:.0f} deg" if j["type"] in ("revolute", "continuous")
                   else f"{j['range']:.3f} m")
            edge = f", hinge on its {j['hinge_edge']} edge" if j.get("hinge_edge") else ""
            lines.append(f"    - {j['id']}: {j['type']} {j['moving_parts']}{edge}, opens "
                         f"toward {j.get('opens_toward') or 'front'}, range {rng}")
    if spec.get("how_it_is_used"):
        lines.append("  how it is used: " + " / ".join(spec["how_it_is_used"][:6]))
    if spec.get("uncertain"):
        lines.append("  not visible in the video (build plainly, do not decorate): "
                     + "; ".join(spec["uncertain"][:6]))
    return "\n".join(lines)


def gate_checklist(spec: dict) -> list[dict]:
    """Visual-gate requirements derived from the spec (counts, glow, structure)."""
    items = []
    countable = [p for p in spec["parts"] if p["kind"] in CONTROL_KINDS | {
        "door", "drawer", "tap", "outlet", "shelf", "tray", "lid"}]
    if countable:
        desc = ", ".join(f"{p['count']} x {p['id'].replace('_', ' ')}" for p in countable[:10])
        items.append({"id": "functional_parts_present",
                      "feature": "the functional parts read from the video are all visible "
                                 "with the right counts: " + desc,
                      "visible_evidence": "each control, door, drawer and outlet is a distinct "
                                          "visible element in the right place"})
    glow = [p["id"].replace("_", " ") for p in spec["parts"] if p.get("emissive")]
    if glow:
        items.append({"id": "emissive_parts_glow",
                      "feature": "these parts visibly glow: " + ", ".join(glow),
                      "visible_evidence": "lit display / indicator colour, not a flat dark patch"})
    moving = [m for j in spec.get("joints") or [] for m in j["moving_parts"]]
    if moving:
        items.append({"id": "moving_parts_distinct",
                      "feature": "openable parts read as separate pieces: "
                                 + ", ".join(sorted(set(moving)))[:300],
                      "visible_evidence": "visible seams / gaps around doors, drawers and lids"})
    return items


def summary(spec: dict) -> str:
    obj = spec.get("object") or {}
    parts = ", ".join(f"{p['count']} x {p['id']}" for p in spec["parts"][:16])
    return f"{obj.get('summary', '')} Parts: {parts}."
