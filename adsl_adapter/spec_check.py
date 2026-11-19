"""The functional specification as a test (pure Python).

The problem this solves: an image tells the code agent "water dispenser" and
the agent builds something that LOOKS like one -- a white box with a dark top
-- with no display, no keys, no outlet, no drip tray. The functional spec read
from the video lists those parts (functional_spec.py); this module checks the
built part list against it, the same way scene_layout.expect() checks a
layout: typed violations, used as Agent feedback and as the acceptance gate.

Input parts are what the program produced, in the object frame (origin at
the bottom centre, -Y front, +Z up): [{"name", "min", "max", "material"}].
They come from dryrun_builder (before Blender, approximate boxes) or from
forge_runner (inside Blender, exact world bounds).

Severity
--------
hard  -- a fact the video established and the model can fix by construction:
         a functional part missing, a wrong count of keys / doors / drawers,
         a display that does not glow, the envelope exceeded.
soft  -- a judgement with a tolerance: position on the face, material class,
         a hinge whose swing clips the body. Reported in the feedback, never
         allowed to block acceptance on its own (positions read from a phone
         video are approximate, and a gate that cannot be satisfied loops).
"""
from __future__ import annotations

import articulation
import sim2real
import material_spec

CONTROL_KINDS = {"button", "touch_key", "knob", "switch", "dial", "lever"}
COUNTED_KINDS = CONTROL_KINDS | {"door", "drawer", "shelf", "tap", "outlet", "nozzle",
                                 "display", "indicator", "lid", "handle", "foot", "wheel"}
OPTIONAL_KINDS = {"label", "logo", "sticker", "cable", "hose", "other", "text"}
ENVELOPE_TOL = 0.012


def _v(sev: str, vid: str, msg: str) -> dict:
    return {"id": vid, "severity": sev, "message": msg}


def required(part: dict) -> bool:
    if part.get("kind") in OPTIONAL_KINDS:
        return False
    return str(part.get("confidence") or "medium").lower() != "low"


def expected_position(part: dict, W: float, D: float, H: float) -> tuple | None:
    """Where the spec says the part's centre is, in the object frame.

    u runs left->right across the face as seen from outside it; v runs
    bottom->top of the object (on the top/bottom face: front->back).
    """
    face = part.get("face")
    try:
        u, v = float(part.get("u")), float(part.get("v"))
    except (TypeError, ValueError):
        return None
    if not (0 <= u <= 1 and 0 <= v <= 1):
        return None
    return {"front": ((u - 0.5) * W, -D / 2, v * H),
            "back": ((0.5 - u) * W, D / 2, v * H),
            "left": (-W / 2, (0.5 - u) * D, v * H),
            "right": (W / 2, (u - 0.5) * D, v * H),
            "top": ((u - 0.5) * W, (v - 0.5) * D, H),
            "bottom": ((u - 0.5) * W, (v - 0.5) * D, 0.0)}.get(face)


def _location_issue(part, box, W, D, H) -> str | None:
    want = expected_position(part, W, D, H)
    if want is None or box is None:
        return None
    lo, hi = box
    got = [(lo[k] + hi[k]) / 2 for k in range(3)]
    half = [(hi[k] - lo[k]) / 2 for k in range(3)]
    tol = (0.22 * W + half[0], 0.22 * D + half[1], 0.16 * H + half[2])
    face = part.get("face")
    axes = {"front": (0, 2), "back": (0, 2), "left": (1, 2), "right": (1, 2),
            "top": (0, 1), "bottom": (0, 1)}.get(face, (0, 2))
    off = [k for k in axes if abs(got[k] - want[k]) > tol[k]]
    # and the part must reach its face (a "front" key buried inside the body)
    reach = {"front": lo[1] <= -D / 2 + 0.3 * D, "back": hi[1] >= D / 2 - 0.3 * D,
             "left": lo[0] <= -W / 2 + 0.3 * W, "right": hi[0] >= W / 2 - 0.3 * W,
             "top": hi[2] >= H - 0.3 * H, "bottom": lo[2] <= 0.3 * H}.get(face, True)
    if not off and reach:
        return None
    return (f"'{part['id']}' should sit on the {face} face near "
            f"({want[0]:+.2f}, {want[1]:+.2f}, {want[2]:.2f}) m; built centre is "
            f"({got[0]:+.2f}, {got[1]:+.2f}, {got[2]:.2f})"
            + ("" if reach else f" and it does not reach the {face} face"))


REACHABLE_KINDS = CONTROL_KINDS | {"tray", "outlet", "nozzle", "spout", "tap", "handle", "door",
                                   "drawer", "lid", "flap", "basin", "grille", "vent"}


def _inside(a, b, margin: float = 0.001) -> bool:
    """Box a lies entirely within box b."""
    return all(b[0][k] - margin <= a[0][k] and a[1][k] <= b[1][k] + margin for k in range(3))


def _overlap_volume(a, b) -> float:
    v = 1.0
    for k in range(3):
        d = min(a[1][k], b[1][k]) - max(a[0][k], b[0][k])
        if d <= 0:
            return 0.0
        v *= d
    return v


def _lineage(spec: dict) -> dict:
    """part id -> set of its ancestors' ids (spec parent links)."""
    parent = {p["id"]: p.get("parent") for p in spec.get("parts") or []}
    out = {}
    for pid in parent:
        seen, cur = set(), parent.get(pid)
        while cur and cur not in seen:
            seen.add(cur)
            cur = parent.get(cur)
        out[pid] = seen
    return out


def _see_through(key) -> bool:
    """Glass, water, translucent plastic: what is inside stays visible (roots
    in a glass vase of water are not buried)."""
    spec = material_spec.parse(key) if isinstance(key, str) else None
    if spec is None:
        return False
    mods = spec["mods"]
    return spec["kind"] == "glass" or float(mods.get("transmission", 0)) > 0.3 \
        or float(mods.get("alpha", 1)) < 0.8


def _is_recess(part: dict) -> bool:
    """A niche / alcove: empty space the body is built around. A tray or a
    nozzle INSIDE a niche also has face 'inside' but is a solid part."""
    return part.get("kind") == "recess" or (
        part.get("face") == "inside" and part.get("kind") in ("other", "panel", "housing"))


def _covers_opening(box, qbox, face: str, tol: float = 0.02) -> bool:
    """Does solid `qbox` lie across the opening `box` (seen from above/below)?

    A sink cut-out drawn as a thin dark plate on an intact countertop passed
    the volume test (the plate sits ON the slab, not in it); a ray down the
    centre of the opening still hits the slab.
    """
    cx, cy = (box[0][0] + box[1][0]) / 2, (box[0][1] + box[1][1]) / 2
    if not (qbox[0][0] < cx < qbox[1][0] and qbox[0][1] < cy < qbox[1][1]):
        return False
    if face == "top":
        return qbox[1][2] >= box[1][2] - tol and qbox[0][2] < box[1][2]
    return qbox[0][2] <= box[0][2] + tol and qbox[1][2] > box[0][2]


def _accessibility(spec: dict, parts: list[dict], matched: dict) -> list[dict]:
    """Functional parts must be reachable: not buried, recesses not filled.

    Presence by name is not enough: the first dispenser written from the spec
    had its outlet, drip tray and niche, all modelled INSIDE the solid body
    box -- every check passed and nothing could be seen or used.
    """
    out = []
    owner = {n: pid for pid, names in matched.items() for n in names}
    lineage = _lineage(spec)
    face = {p["id"]: p.get("face") for p in spec.get("parts") or []}
    boxes = {p["name"]: (p["min"], p["max"]) for p in parts}
    for p in spec.get("parts") or []:
        pid = p["id"]
        names = matched.get(pid) or []
        # a recess is checked even when the model filed it as 'other'
        recess = _is_recess(p)
        if not names or (not required(p) and not recess) \
                or str(p.get("confidence")).lower() == "low":
            continue
        box = articulation.union([q for q in parts if q["name"] in names])
        if box is None:
            continue
        related = {pid} | lineage.get(pid, set())
        children = {c for c, anc in lineage.items() if pid in anc}
        kinds = {sp["id"]: sp.get("kind") for sp in spec.get("parts") or []}
        for q in parts:
            qid = owner.get(q["name"])
            if q["name"] in names or qid in children:
                continue
            qbox = boxes[q["name"]]
            # Any required piece that sits entirely within another solid is
            # invisible -- not only controls: a charcoal control head inside a
            # full-height white body box rendered white (coplanar front face).
            # Only a recess may legitimately contain parts.
            q_recess = _is_recess({"face": face.get(qid), "kind": kinds.get(qid)})
            # only SOLID primitives can bury something: a gooseneck spout is a
            # swept tube whose bounding box is mostly air, and its own outlet
            # sits inside that box
            q_solid = q.get("type") in (None, "box", "cylinder", "sphere") \
                and not _see_through(q.get("material"))
            if not recess and p["kind"] not in OPTIONAL_KINDS and not q_recess and q_solid \
                    and _inside(box, qbox):
                out.append(_v("hard", f"spec.buried:{pid}",
                              f"'{pid}' lies entirely inside '{q['name']}', so it can be "
                              f"neither seen nor used; it must sit on or in an OPENING of the "
                              f"body (build the body from several boxes around it)"))
                break
            if recess and qid not in related and q_solid and p.get("face") in ("top", "bottom") \
                    and _covers_opening(box, qbox, p.get("face")):
                out.append(_v("hard", f"spec.filled:{pid}",
                              f"the opening '{pid}' is covered by '{q['name']}': looking straight "
                              f"{'down' if p.get('face') == 'top' else 'up'} at its centre you hit a "
                              f"solid surface, so it reads as a slot or a decal, not a hole. Build "
                              f"'{q['name']}' from four pieces AROUND the opening (front, back, left, "
                              f"right) and leave it empty, with a basin or inner walls below"))
                break
            if recess and qid not in related and q_solid:
                vol = max(1e-12, (box[1][0] - box[0][0]) * (box[1][1] - box[0][1])
                          * (box[1][2] - box[0][2]))
                if _overlap_volume(box, qbox) > 0.5 * vol:
                    out.append(_v("hard", f"spec.filled:{pid}",
                                  f"the recess '{pid}' is filled by '{q['name']}': a niche is "
                                  f"empty space, so the body must be built AROUND it from "
                                  f"several boxes (sides, back, top and bottom of the opening)"))
                    break
    return out


# Approximate luminance of the named palette keys (forge_runner.build_materials)
NAMED_LUM = {"screen_black": 0.01, "plastic_black": 0.01, "linen_charcoal": 0.05,
             "walnut_joinery": 0.04, "boiserie_walnut": 0.03, "oak_joinery": 0.12,
             "steel_brushed": 0.3, "brass_warm": 0.25, "gilded_bronze": 0.25,
             "ceramic_matte": 0.64, "marble_cream": 0.56, "linen_sand": 0.45,
             "boucle_grey": 0.35, "lamp_shade": 0.6}


def key_luminance(key) -> float | None:
    if not isinstance(key, str):
        return None
    if key in NAMED_LUM:
        return NAMED_LUM[key]
    spec = material_spec.parse(key)
    if spec is None:
        return None
    r, g, b = spec["rgb"]
    return 0.2126 * r + 0.7152 * g + 0.0722 * b


def _colour_issue(part: dict, gparts: list[dict]) -> str | None:
    """Light-for-dark (or dark-for-light) on the part's main piece.

    The first real-Builder dispenser passed every structural test with its
    control head WHITE; the video and the spec say charcoal (#33363F). A class
    check (plastic == plastic) cannot see that, and it is the most visible
    error on the object.
    """
    want = key_luminance(part.get("material_key"))
    if want is None or not gparts:
        return None
    main = max(gparts, key=lambda p: max(1e-9, (p["max"][0] - p["min"][0])
                                          * (p["max"][1] - p["min"][1])
                                          * (p["max"][2] - p["min"][2])))
    got = key_luminance(main.get("material"))
    if got is None:
        return None
    hi, lo = max(want, got), min(want, got)
    if hi - lo > 0.25 and hi > 3.0 * max(lo, 0.01):
        tone = "dark" if want < got else "light"
        return (f"'{part['id']}' is {tone} in the video ('{part['material_key']}') but its main "
                f"piece '{main['name']}' uses '{main.get('material')}'; use the spec's key")
    return None


def check(parts: list[dict], spec: dict, P: dict) -> dict:
    """Typed violations of `spec` by `parts`. See module docstring."""
    W, D, H = float(P["width"]), float(P["depth"]), float(P["height"])
    names = [p["name"] for p in parts]
    by_name = {p["name"]: p for p in parts}
    out: list[dict] = []
    matched: dict = {}

    # envelope (same rule as forge_runner, so a dry run predicts Blender);
    # grasp hardware is measured separately with its own allowance (sim2real)
    box = sim2real.body_union(parts) if parts else None
    for hname, w in sim2real.hardware_overshoot(parts, P):
        out.append(_v("hard", f"envelope.hardware:{hname}",
                      f"'{hname}' sticks {w * 1000:.0f} mm out of the envelope; grasp hardware may "
                      f"stand out by at most {sim2real.HARDWARE_ALLOWANCE * 1000:.0f} mm"))
    if box:
        lo, hi = box
        size = [hi[k] - lo[k] for k in range(3)]
        want = (W, D, H)
        over = [size[k] - want[k] for k in range(3)]
        if max(over) > ENVELOPE_TOL:
            k = max(range(3), key=lambda i: over[i])
            # Name the culprits: told only "x is 182 mm over", the model kept
            # the program unchanged for four attempts; the cause was one vent
            # drawn 17 cm wide on a side face.
            bounds = ((-W / 2, W / 2), (-D / 2, D / 2), (0.0, H))
            culprits = []
            for p in parts:
                if sim2real.is_grasp_hardware(p["name"]):
                    continue
                worst = max(max(bounds[i][0] - p["min"][i], p["max"][i] - bounds[i][1])
                            for i in range(3))
                if worst > ENVELOPE_TOL / 2:
                    culprits.append((worst, p["name"]))
            culprits.sort(reverse=True)
            ids = [sp["id"] for sp in spec.get("parts") or []]

            def in_spec(n):
                return any(n == i or n.startswith(i + "_") for i in ids)

            culprit_txt = ", ".join(
                f"{n} ({w * 1000:.0f} mm out{'' if in_spec(n) else '; NOT in the specification -- delete it'})"
                for w, n in culprits[:6])
            if k == 1:
                # the usual cause: a full-depth body with doors / keys added in front
                culprit_txt += ("; the body takes the whole depth and things were added on its "
                                "front or back -- make the body shallower by their thickness so "
                                "their outer faces land on y = -D/2 (front) / +D/2 (back)")
            out.append(_v("hard", "envelope",
                          f"built size {size[0]:.3f} x {size[1]:.3f} x {size[2]:.3f} m "
                          f"exceeds the envelope {W:.3f} x {D:.3f} x {H:.3f} m on "
                          f"{'xyz'[k]} by {over[k] * 1000:.0f} mm; parts outside the envelope: "
                          f"{culprit_txt or 'the body itself'} -- shrink or move them inside "
                          f"(the envelope is x in [-W/2, W/2], y in [-D/2, D/2], z in [0, H])"))

    for part in spec.get("parts") or []:
        pid, kind = part["id"], part.get("kind")
        count = max(1, int(part.get("count") or 1))
        groups = articulation.instance_groups(names, pid, count)
        present = [g for g in groups if g]
        matched[pid] = [n for g in groups for n in g]
        if not matched[pid]:
            if required(part):
                out.append(_v("hard", f"spec.missing:{pid}",
                              f"no part named '{pid}'"
                              + (f" / '{pid}_1'..'{pid}_{count}'" if count > 1 else "")
                              + f" ({kind}: {part.get('function') or part.get('shape') or ''})"))
            continue
        if count > 1 and len(present) != count:
            nums = articulation.numbered_instances(names, pid)
            stray = sorted({n for n in names if n.startswith(pid + "_")
                            and not any(n in g for g in groups)})
            sev = "hard" if kind in COUNTED_KINDS else "soft"
            out.append(_v(sev, f"spec.count:{pid}",
                          f"the video shows {count} x '{pid}' but the program has "
                          f"{len(present)} (numbered {nums or 'none'}); name them "
                          f"{pid}_1..{pid}_{count}"
                          + (f"; unnumbered parts {stray[:6]} do not count" if stray else "")))
        elif count == 1 and kind in COUNTED_KINDS:
            nums = articulation.numbered_instances(names, pid)
            if len(nums) > 1:
                out.append(_v("hard", f"spec.count:{pid}",
                              f"the video shows ONE '{pid}' but the program built "
                              f"{len(nums)} ({pid}_{nums[0]}..{pid}_{nums[-1]})"))
        gparts = [by_name[n] for n in matched[pid]]
        # light-emitting parts must use an emitting material
        if part.get("emissive") and not any(
                material_spec.is_emissive(p.get("material")) for p in gparts):
            out.append(_v("hard", f"spec.emission:{pid}",
                          f"'{pid}' glows in the video; give it an emitting material "
                          f"(e.g. '{part.get('material_key') or 'screen:#0E0E12:0.06:emit=#3355FF'}')"))
        if required(part) and part.get("kind") not in OPTIONAL_KINDS:
            issue = _colour_issue(part, gparts)
            if issue:
                out.append(_v("hard", f"spec.colour:{pid}", issue))
        # material class (soft: a VLM can mislabel satin plastic as paint)
        want_cls = material_spec.normalise_class(part.get("material_class")) \
            if part.get("material_class") else None
        got = [material_spec.parse(p.get("material")) for p in gparts]
        got_cls = {g["kind"] for g in got if g}
        if want_cls and got_cls and want_cls not in got_cls and not (
                {want_cls, *got_cls} <= {"paint", "plastic", "ceramic"}):
            out.append(_v("soft", f"spec.material:{pid}",
                          f"'{pid}' is {want_cls} in the video but built as "
                          f"{'/'.join(sorted(got_cls))}"))
        # position on its face (soft)
        if count == 1:
            issue = _location_issue(part, articulation.union(gparts), W, D, H)
            if issue:
                out.append(_v("soft", f"spec.location:{pid}", issue))

    out.extend(_accessibility(spec, parts, matched))
    joints = articulation.derive_all(spec, parts, (W, D, H)) if spec.get("joints") else []
    for j in joints:
        if not j["parts"]:
            out.append(_v("hard", f"joint.part:{j['id']}", j["issues"][0]))
        else:
            for issue in j["issues"]:
                out.append(_v("soft", f"joint.swing:{j['id']}", issue))

    out.extend(sim2real.grasp_issues(spec, parts, joints))
    out.extend(sim2real.extra_hardware(spec, parts))
    out.extend(sim2real.visible_latches(parts, joints))
    out.extend(_removed_parts_back(spec, parts))

    hard = [v for v in out if v["severity"] == "hard"]
    return {"violations": out, "hard": hard, "soft": [v for v in out if v not in hard],
            "matched": matched, "joints": joints,
            "coverage": {"required": sum(1 for p in spec.get("parts") or [] if required(p)),
                         "found": sum(1 for p in spec.get("parts") or []
                                      if required(p) and matched.get(p["id"]))}}


GENERIC_TOKENS = {"door", "main", "body", "front", "back", "left", "right", "top", "bottom", "upper", "lower",
                  "touch", "panel", "side", "inner", "outer"}


def _removed_parts_back(spec: dict, parts: list[dict]) -> list[dict]:
    """Parts the part study removed (a sink rim the video shows is not there,
    drainer grooves, a fourth key that was a third) must not come back under
    another name -- a program edited from an older version keeps them."""
    removed = spec.get("removed_parts") or []
    current = [p["id"] for p in spec.get("parts") or []]
    out = []
    for q in parts:
        n = q["name"].lower()
        if any(n == c or n.startswith(c + "_") or n.startswith(c) for c in current):
            continue
        for r in removed:
            first = r.split("_")[0]
            if n.startswith(r) or (len(first) >= 4 and first not in GENERIC_TOKENS and n.split("_")[0] == first):
                out.append(_v("hard", f"spec.removed:{q['name']}",
                              f"'{q['name']}' is '{r}', which the video close-ups show does NOT exist on this "
                              f"object (removed from the specification) -- delete it"))
                break
    return out


def feedback(report: dict, limit: int = 12) -> str:
    """Violations as the bullet list the code agent receives."""
    lines = []
    for v in (report["hard"] + report["soft"])[:limit]:
        tag = "MUST FIX" if v["severity"] == "hard" else "check"
        lines.append(f"- [{tag}] {v['id']}: {v['message']}")
    return "\n".join(lines)
