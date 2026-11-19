"""Deterministic part-level geometric integrity checks.

Envelope-level SAT (layout_solver.py) certifies that two WHOLE objects do not
collide. It cannot tell you that a sconce candle floats 18 cm above its arm,
that a crystal string hangs in mid-air, or that two mouldings share a coplanar
face and will z-fight into a black seam. Those are part-level facts.

This module works on individual part AABBs plus an explicit list of joints
that are *supposed* to touch. It never asks an LLM to look at a render.

Every finding carries a repair_target at the layer that can actually fix it:
  floating / joint_gap / undeclared_intersection / coplanar -> 'geometry'
  clearance_margin                                        -> 'spatial'
That distinction is deliberate: the existing loop mislabels part-level
blockers as 'layout' and then tries to fix them by moving poses forever.

Pure standard library. Blender-side callers export AABBs to JSON first.
"""
from __future__ import annotations

import json
import math


# The concrete anti-penetration rules used by the direct-construction
# deliveries. Kept here so the Agent compiler can reuse them verbatim.
DESIGN_RULES = {
    "joint_tol_m": 0.02,
    "ground_tol_m": 0.03,
    "coplanar_gap_m": 0.002,
    "coplanar_min_area_m2": 0.015,
    "intersection_min_volume_m3": 2e-5,
    "intersection_min_axis_m": 0.012,
    "clearance_safety_margin_m": 0.05,
    "mitre_rule": "same-thickness border rails must be shortened so they "
                  "MEET at corners instead of overlapping; overlapping "
                  "coplanar rails produce black z-fighting seams",
    "occluder_rule": "a ceiling fixture must stay above the principal "
                     "entrance-to-focus sightline; otherwise raise the "
                     "fixture or move the camera, do not dim it",
    "margin_rule": "never place at the exact clearance limit: a measured "
                   "0.395 m against a required 0.400 m half-width fails as "
                   "soon as real mesh bounds replace conservative AABBs",
}


def _as_box(p):
    lo = [float(v) for v in p["min"]]
    hi = [float(v) for v in p["max"]]
    for i in range(3):
        if hi[i] < lo[i]:
            lo[i], hi[i] = hi[i], lo[i]
    return lo, hi


def _axis_gap(a, b, i):
    alo, ahi = a; blo, bhi = b
    return max(0.0, max(alo[i] - bhi[i], blo[i] - ahi[i]))


def gap(a, b):
    """Euclidean separation between two AABBs; 0 when they intersect."""
    return math.sqrt(sum(_axis_gap(a, b, i) ** 2 for i in range(3)))


def overlap_volume(a, b):
    """(volume, [per-axis overlap depths]) for two AABBs."""
    alo, ahi = a; blo, bhi = b
    depths = [max(0.0, min(ahi[i], bhi[i]) - max(alo[i], blo[i])) for i in range(3)]
    vol = depths[0] * depths[1] * depths[2]
    return vol, depths


def _projected_overlap(a, b, axis):
    other = [i for i in range(3) if i != axis]
    ds = []
    for i in other:
        ds.append(max(0.0, min(a[1][i], b[1][i]) - max(a[0][i], b[0][i])))
    return ds[0] * ds[1]


def _coplanar_faces(a, b, tol):
    """Return (axis, area) when two faces nearly coincide and overlap."""
    for axis in range(3):
        pairs = ((a[0][axis], b[1][axis]), (a[1][axis], b[0][axis]))
        for p, q in pairs:
            if abs(p - q) <= tol:
                area = _projected_overlap(a, b, axis)
                if area > 0:
                    return axis, area
    return None


def load_parts(path):
    with open(path, "r", encoding="utf-8") as fh:
        data = json.load(fh)
    return data["parts"] if isinstance(data, dict) and "parts" in data else data


def check(parts, *, joint_pairs=(), clearances=(), ground_z=0.0,
          config=None, strict_intersection=False):
    """Check part-level integrity.

    parts:      [{id, object, min:[x,y,z], max:[x,y,z], role?, material?}]
    joint_pairs: [(id_a, id_b, label)] pairs that are REQUIRED to touch.
    clearances:  [{id, required_m, measured_m, a?, b?}] measured net widths.
    strict_intersection: also report overlaps between parts of the SAME object
                 (carved/inset decoration normally overlaps its host by design,
                 so this is off by default).
    """
    cfg = dict(DESIGN_RULES)
    cfg.update(config or {})
    boxes = {}
    for p in parts:
        if not p.get("id"):
            continue
        boxes[p["id"]] = _as_box(p)
    meta = {p["id"]: p for p in parts if p.get("id")}
    findings = []

    def add(code, evidence, measured, expected, target, ids, severity="blocking"):
        findings.append({"code": code, "evidence": evidence,
                         "measured": measured, "expected": expected,
                         "repair_target": target, "component_ids": list(ids),
                         "severity": severity})

    # 1. declared joints must actually touch
    for pair in joint_pairs:
        a_id, b_id = pair[0], pair[1]
        label = pair[2] if len(pair) > 2 else f"{a_id} -> {b_id}"
        if a_id not in boxes or b_id not in boxes:
            add("missing_joint_part",
                f"Declared joint '{label}' references a missing part.",
                {"a": a_id, "b": b_id}, {"both_parts_present": True},
                "geometry", [a_id, b_id])
            continue
        g = gap(boxes[a_id], boxes[b_id])
        if g > cfg["joint_tol_m"]:
            add("joint_gap",
                f"Declared joint '{label}' is open by {g:.4f} m "
                f"(tolerance {cfg['joint_tol_m']:.3f} m); the child part is "
                f"visibly floating instead of seated on its support.",
                {"gap_m": round(g, 5), "part_a": a_id, "part_b": b_id},
                {"gap_max_m": cfg["joint_tol_m"]},
                "geometry", [a_id, b_id])

    # 2. no part may float: it must touch the floor or another part
    floating = []
    for pid, box in boxes.items():
        p = meta[pid]
        if p.get("role") in ("light", "camera", "empty", "helper"):
            continue
        if box[0][2] - ground_z <= cfg["ground_tol_m"]:
            continue
        nearest = min((gap(box, other), oid)
                      for oid, other in boxes.items() if oid != pid) \
                  if len(boxes) > 1 else (float("inf"), None)
        if nearest[0] > cfg["joint_tol_m"]:
            floating.append({"id": pid, "nearest_part": nearest[1],
                             "gap_m": round(nearest[0], 5),
                             "bottom_z_m": round(box[0][2], 4)})
    for f in floating:
        add("floating_part",
            f"Part {f['id']} has no support: its bottom is at z="
            f"{f['bottom_z_m']:.3f} m and the nearest part is "
            f"{f['gap_m']:.4f} m away.",
            f, {"max_gap_m": cfg["joint_tol_m"]},
            "geometry", [f["id"], f["nearest_part"]])

    # 3. undeclared solid intersections across different objects
    ids = list(boxes)
    intersections = []
    declared = {(p[0], p[1]) for p in joint_pairs} | \
               {(p[1], p[0]) for p in joint_pairs}
    for i, a_id in enumerate(ids):
        for b_id in ids[i + 1:]:
            same_object = meta[a_id].get("object") == meta[b_id].get("object")
            if same_object and not strict_intersection:
                continue
            if (a_id, b_id) in declared:
                continue
            if meta[a_id].get("role") in ("floor_covering", "shell") or \
               meta[b_id].get("role") in ("floor_covering", "shell"):
                continue
            vol, depths = overlap_volume(boxes[a_id], boxes[b_id])
            if vol >= cfg["intersection_min_volume_m3"] and \
               max(depths) >= cfg["intersection_min_axis_m"]:
                intersections.append({"a": a_id, "b": b_id,
                                      "volume_m3": round(vol, 7),
                                      "depths_m": [round(d, 4) for d in depths]})
    for it in intersections[:80]:
        add("undeclared_intersection",
            f"Parts {it['a']} and {it['b']} interpenetrate by "
            f"{it['volume_m3']:.6f} m3 with no declared joint.",
            it, {"declared_joint_required": True},
            "geometry", [it["a"], it["b"]], severity="review")

    # 4. coplanar faces -> z-fighting black seams
    coplanar = []
    for i, a_id in enumerate(ids):
        for b_id in ids[i + 1:]:
            if meta[a_id].get("object") == meta[b_id].get("object") and \
               not strict_intersection:
                continue
            # A foot resting on the floor, or a shell/structural butt joint,
            # legitimately shares a plane. Black z-fighting seams come from
            # overlapping decorative rails ABOVE the floor datum.
            if min(boxes[a_id][0][2], boxes[b_id][0][2]) <= \
                    ground_z + cfg["ground_tol_m"]:
                continue
            if meta[a_id].get("role") in ("shell", "structure") or \
               meta[b_id].get("role") in ("shell", "structure"):
                continue
            hit = _coplanar_faces(boxes[a_id], boxes[b_id],
                                  cfg["coplanar_gap_m"])
            if hit and hit[1] >= cfg["coplanar_min_area_m2"]:
                coplanar.append({"a": a_id, "b": b_id, "axis": hit[0],
                                 "area_m2": round(hit[1], 5)})
    for it in coplanar[:60]:
        add("coplanar_z_fighting",
            f"Parts {it['a']} and {it['b']} share a {it['area_m2']:.4f} m2 "
            f"coplanar face; shorten one rail so they meet instead of overlap "
            f"(mitre rule).",
            it, {"mitre_rule": cfg["mitre_rule"]},
            "geometry", [it["a"], it["b"]], severity="review")

    # 5. clearance must carry a safety margin, never sit at the exact limit
    for c in clearances:
        required = float(c["required_m"])
        measured = float(c["measured_m"])
        margin = float(c.get("margin_m", cfg["clearance_safety_margin_m"]))
        if measured - required < margin - 1e-9:
            add("clearance_zero_margin",
                f"Clearance '{c.get('id')}' measures {measured:.4f} m against "
                f"a required {required:.4f} m; margin "
                f"{measured - required:.4f} m is below the {margin:.3f} m "
                f"safety margin. Real mesh bounds will break this.",
                {"id": c.get("id"), "measured_m": measured,
                 "required_m": required, "margin_m": round(measured - required, 5)},
                {"margin_min_m": margin},
                "spatial", [c.get("a"), c.get("b")])

    blocking = [f for f in findings if f["severity"] == "blocking"]
    return {
        "status": "needs_revision" if blocking else "passed",
        "config": cfg,
        "counts": {"parts": len(boxes), "declared_joints": len(list(joint_pairs)),
                   "floating": len(floating), "joint_gaps": sum(
                       1 for f in findings if f["code"] == "joint_gap"),
                   "intersections": len(intersections),
                   "coplanar": len(coplanar),
                   "clearance_failures": sum(
                       1 for f in findings if f["code"] == "clearance_zero_margin"),
                   "blocking_findings": len(blocking),
                   "review_findings": len(findings) - len(blocking)},
        "findings": findings,
        "rules": DESIGN_RULES,
    }


def by_code(result):
    out = {}
    for f in result["findings"]:
        out.setdefault(f["code"], []).append(f)
    return out
