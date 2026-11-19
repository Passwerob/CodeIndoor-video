"""Deterministic, measurable composition priors for room-scale planning.

The LAYOUT prompt already says "the entrance must open onto the principal
focal group" and "secondary objects stay in peripheral niches". Nothing
measures those sentences, so a sparse four-object plan passes every gate and
still reads as an empty room. This module turns that prose into numbers the
planner can be rejected against, and returns the numbers with the rejection.

Design rule: these metrics are necessary, not sufficient. They block plans
that are measurably incoherent (isolated inventory, radiating routes,
overlapping use areas, asymmetric principal group, occluded focal view).
They do not claim to certify beauty. A rendered visual judge still owns that.

Pure standard library: usable by the planner, an offline validator and a
delivery self-check without importing pydantic, numpy or bpy.
"""
from __future__ import annotations

import hashlib
import math
from dataclasses import dataclass, asdict


@dataclass(frozen=True)
class Archetype:
    id: str
    focus_roles: tuple
    enclosure_roles: tuple
    peripheral_roles: tuple
    min_enclosure_members: int
    coverage_pct: tuple
    focus_distance_cv_max: float
    facing_tolerance_deg: float
    symmetry_tolerance_m: float
    shared_trunk_ratio_min: float
    area_overlap_max_m2: float
    focal_corridor_occlusion_max: float
    min_azimuth_coverage_deg: float
    notes: str


# Thresholds are calibrated against two real artefacts in this workspace:
#   negative: runs/complete_discussion_room_20261023_01 (11.54% coverage,
#             4 isolated pieces, 5 radiating routes, ~2.3 m2 area overlap)
#   positive: deliveries/baroque_reception_pbr_20261104 (focused enclosure,
#             strict symmetry, one shared entrance trunk, no area overlap)
ARCHETYPES = {
    "baroque_salon": Archetype(
        id="baroque_salon",
        focus_roles=("fireplace", "focus", "coffee_table", "principal_axis"),
        enclosure_roles=("sofa", "armchair", "seat", "seating"),
        peripheral_roles=("gueridon", "side_table", "candelabra", "sconce",
                          "plant", "accent", "console"),
        min_enclosure_members=4,
        coverage_pct=(10.0, 48.0),
        focus_distance_cv_max=0.45,
        facing_tolerance_deg=48.0,
        symmetry_tolerance_m=0.12,
        shared_trunk_ratio_min=0.30,
        area_overlap_max_m2=0.10,
        focal_corridor_occlusion_max=0.20,
        min_azimuth_coverage_deg=150.0,
        notes="Axial salon: one dominant focal wall (fireplace/mirror), a "
              "mirrored left-right seating enfilade, a rug-defined "
              "conversation core, and peripheral accents in wall niches.",
    ),
    "reception_lounge": Archetype(
        id="reception_lounge",
        focus_roles=("coffee_table", "focus", "fireplace", "media"),
        enclosure_roles=("sofa", "armchair", "seat", "seating"),
        peripheral_roles=("side_table", "lamp", "plant", "bookshelf",
                          "accent", "console"),
        min_enclosure_members=3,
        coverage_pct=(14.0, 45.0),
        focus_distance_cv_max=0.50,
        facing_tolerance_deg=55.0,
        symmetry_tolerance_m=0.25,
        shared_trunk_ratio_min=0.28,
        area_overlap_max_m2=0.10,
        focal_corridor_occlusion_max=0.22,
        min_azimuth_coverage_deg=120.0,
        notes="Conversation-first lounge: define the rug/core area, then "
              "arrange all seats to face one shared focus. Open floor is the "
              "residual of a good arrangement, not a protected rectangle.",
    ),
    "study": Archetype(
        id="study",
        focus_roles=("desk", "work_surface"),
        enclosure_roles=("chair", "seat", "shelf", "storage"),
        peripheral_roles=("lamp", "plant", "accent"),
        min_enclosure_members=2,
        coverage_pct=(12.0, 42.0),
        focus_distance_cv_max=0.60,
        facing_tolerance_deg=60.0,
        symmetry_tolerance_m=0.40,
        shared_trunk_ratio_min=0.20,
        area_overlap_max_m2=0.15,
        focal_corridor_occlusion_max=0.30,
        min_azimuth_coverage_deg=60.0,
        notes="Task-first room: one working surface, reachable storage, and "
              "a clear chair pullback zone.",
    ),
    "generic": Archetype(
        id="generic",
        focus_roles=("focus", "table"),
        enclosure_roles=("seat", "seating", "sofa", "chair"),
        peripheral_roles=("accent", "side_table", "lamp"),
        min_enclosure_members=2,
        coverage_pct=(8.0, 55.0),
        focus_distance_cv_max=0.70,
        facing_tolerance_deg=70.0,
        symmetry_tolerance_m=0.50,
        shared_trunk_ratio_min=0.15,
        area_overlap_max_m2=0.25,
        focal_corridor_occlusion_max=0.35,
        min_azimuth_coverage_deg=60.0,
        notes="Fallback: only the structural composition checks apply.",
    ),
}

# Roles that do not count as freestanding furnishing footprints. Wall
# fixtures, ceiling lights and the shell must not inflate a sparse room into
# a seemingly furnished one.
_NON_FURNITURE = ("shell", "floor_covering", "structure", "light",
                  "wall_fixture", "glazing", "camera", "helper")

_BAROQUE_TERMS = ("baroque", "rococo", "巴洛克", "洛可可", "沙龙", "宫廷",
                  "palace", "salon", "凡尔赛", "versailles")
_LOUNGE_TERMS = ("reception", "lounge", "living room", "sitting room",
                 "会客", "客厅", "起居室", "会客厅", "接待")
_STUDY_TERMS = ("study", "home office", "书房", "书桌", "学习")


def infer_archetype(brief):
    """Match a brief to a composition prior. Style wins over room type."""
    text = " ".join(str(brief or "").split()).casefold()
    if any(t in text for t in _BAROQUE_TERMS):
        return ARCHETYPES["baroque_salon"]
    if any(t in text for t in _STUDY_TERMS):
        return ARCHETYPES["study"]
    if any(t in text for t in _LOUNGE_TERMS):
        return ARCHETYPES["reception_lounge"]
    return ARCHETYPES["generic"]


def _rect(center, size):
    cx, cy = float(center[0]), float(center[1])
    w, d = float(size[0]), float(size[1])
    return (cx - w / 2, cy - d / 2, cx + w / 2, cy + d / 2)


def _overlap_area(a, b):
    x0 = max(a[0], b[0]); y0 = max(a[1], b[1])
    x1 = min(a[2], b[2]); y1 = min(a[3], b[3])
    return max(0.0, x1 - x0) * max(0.0, y1 - y0)


def _cv(values):
    vals = [v for v in values if v is not None and v > 1e-9]
    if len(vals) < 2:
        return None
    mean = sum(vals) / len(vals)
    if mean < 1e-9:
        return None
    var = sum((v - mean) ** 2 for v in vals) / len(vals)
    return math.sqrt(var) / mean


def _forward(yaw_deg):
    """Local front is -Y before world yaw (FineScene convention)."""
    a = math.radians(float(yaw_deg or 0.0))
    return (math.sin(a), -math.cos(a))


def _angle_between(u, v):
    nu = math.hypot(*u); nv = math.hypot(*v)
    if nu < 1e-9 or nv < 1e-9:
        return None
    c = (u[0] * v[0] + u[1] * v[1]) / (nu * nv)
    return math.degrees(math.acos(max(-1.0, min(1.0, c))))


def _common_prefix_length(routes, tol=1e-3):
    """Longest shared leading polyline (metres) among all route point lists."""
    paths = [r.get("points") or [] for r in routes]
    paths = [p for p in paths if len(p) >= 2]
    if len(paths) < 2:
        return 0.0, None
    shared = 0.0
    n = min(len(p) for p in paths)
    for i in range(n - 1):
        a = paths[0][i]; b = paths[0][i + 1]
        # Compare the SAME segment index in every path. Comparing q[0]/q[1]
        # here would only ever verify the first segment and would silently
        # stop at i=1 even when all routes share a long trunk.
        if any(len(q) <= i + 1 or math.dist(a, q[i]) > tol or
               math.dist(b, q[i + 1]) > tol for q in paths[1:]):
            break
        shared += math.dist(a, b)
    total = [sum(math.dist(p[j], p[j + 1]) for j in range(len(p) - 1))
             for p in paths]
    return shared, sum(total) / len(total)


def _azimuth_coverage(angles):
    """Largest angular gap -> covered arc. Small arc means clustered seats."""
    if len(angles) < 2:
        return None
    s = sorted(a % 360.0 for a in angles)
    gaps = [(s[(i + 1) % len(s)] - s[i]) % 360.0 for i in range(len(s))]
    return 360.0 - max(gaps)


def evaluate(scene, archetype=None, brief=""):
    """Measure a normalized scene against an archetype.

    scene keys (all optional unless noted):
      room: {width, depth, height}            (required for coverage)
      objects: [{id, role, position, size, yaw_deg, group}]
      focus_id / enclosure_group / symmetry_pairs
      entrance: {position:[x,y]}
      access_areas: [{id, center:[x,y], size:[w,d]}]
      routes: [{id, points:[[x,y],...]}]
    Returns {'status','archetype','metrics','issues'}. Each issue carries
    measured/expected and a repair_target at the layer that can actually fix it.
    """
    arch = archetype or infer_archetype(brief or scene.get("brief", ""))
    objects = [o for o in scene.get("objects", []) if o.get("id")]
    by_id = {o["id"]: o for o in objects}
    metrics = {}
    issues = []

    def issue(code, evidence, measured, expected, target, ids=()):
        issues.append({"code": code, "evidence": evidence,
                       "measured": measured, "expected": expected,
                       "repair_target": target, "component_ids": list(ids),
                       "severity": "blocking"})

    furniture = [o for o in objects
                 if o.get("role") not in _NON_FURNITURE]
    room = scene.get("room") or {}
    W = float(room.get("width", 0) or 0)
    D = float(room.get("depth", 0) or 0)
    floor_area = W * D

    # 1. furniture coverage: catches the "4 small boxes in a big empty room"
    if floor_area > 0 and furniture:
        occupied = sum(float(o["size"][0]) * float(o["size"][1])
                       for o in furniture if o.get("size"))
        pct = 100.0 * occupied / floor_area
        metrics["footprint_coverage_pct"] = round(pct, 3)
        lo, hi = arch.coverage_pct
        if pct < lo:
            issue("sparse_inventory",
                  f"Furniture covers only {pct:.2f}% of the floor "
                  f"(minimum {lo:.1f}%). No coordinate tweak can fill a room "
                  f"whose frozen inventory is this sparse.",
                  {"pct": round(pct, 3)}, {"pct_min": lo},
                  "decomposition", [o["id"] for o in furniture])
        elif pct > hi:
            issue("overcrowded_inventory",
                  f"Furniture covers {pct:.2f}% of the floor (maximum {hi:.1f}%).",
                  {"pct": round(pct, 3)}, {"pct_max": hi}, "decomposition")
    else:
        metrics["footprint_coverage_pct"] = None

    # 2. focus + enclosure group
    focus_id = scene.get("focus_id")
    focus = by_id.get(focus_id)
    group = scene.get("enclosure_group")
    if group is None:
        group = [o["id"] for o in objects
                 if o.get("role") in arch.enclosure_roles]
    group = [g for g in group if g in by_id and g != focus_id]
    metrics["focus_id"] = focus_id
    metrics["enclosure_members"] = len(group)
    if len(group) < arch.min_enclosure_members:
        issue("missing_enclosure_group",
              f"Only {len(group)} enclosing seats; a {arch.id} needs at least "
              f"{arch.min_enclosure_members} members sharing one focus.",
              {"members": len(group)}, {"members_min": arch.min_enclosure_members},
              "decomposition", group)

    if focus and group:
        fx, fy = float(focus["position"][0]), float(focus["position"][1])
        dists, facings, angles = [], [], []
        for gid in group:
            o = by_id[gid]
            px, py = float(o["position"][0]), float(o["position"][1])
            v = (fx - px, fy - py)
            d = math.hypot(*v)
            dists.append(d)
            angles.append(math.degrees(math.atan2(v[1], v[0])))
            f = _forward(o.get("yaw_deg", 0.0))
            a = _angle_between(f, v)
            if a is not None:
                facings.append(a)
        metrics["focus_distance_cv"] = (round(_cv(dists), 4)
                                       if _cv(dists) is not None else None)
        metrics["focus_distance_m"] = [round(d, 3) for d in dists]
        metrics["focus_facing_max_deg"] = (round(max(facings), 2)
                                           if facings else None)
        metrics["azimuth_coverage_deg"] = (round(_azimuth_coverage(angles), 2)
                                           if _azimuth_coverage(angles) is not None else None)
        cv = _cv(dists)
        if cv is not None and cv > arch.focus_distance_cv_max:
            issue("no_shared_focus",
                  f"Seats sit at uneven distances from the focus "
                  f"(CV={cv:.3f}, max {arch.focus_distance_cv_max:.2f}). "
                  f"They read as isolated objects, not one conversation group.",
                  {"cv": round(cv, 4), "distances_m": [round(d, 3) for d in dists]},
                  {"cv_max": arch.focus_distance_cv_max}, "layout", group)
        if metrics["focus_facing_max_deg"] is not None and \
                metrics["focus_facing_max_deg"] > arch.facing_tolerance_deg:
            issue("seats_not_facing_focus",
                  f"A seat deviates {metrics['focus_facing_max_deg']:.1f} deg "
                  f"from the shared focus (tolerance {arch.facing_tolerance_deg:.0f} deg).",
                  {"max_deg": metrics["focus_facing_max_deg"]},
                  {"max_deg": arch.facing_tolerance_deg}, "layout", group)
        az = _azimuth_coverage(angles)
        if az is not None and az < arch.min_azimuth_coverage_deg:
            issue("no_enclosure_arc",
                  f"Seats occupy only {az:.0f} deg of arc around the focus "
                  f"(minimum {arch.min_azimuth_coverage_deg:.0f} deg); the "
                  f"group is a row, not an enclosure.",
                  {"arc_deg": round(az, 1)},
                  {"arc_min_deg": arch.min_azimuth_coverage_deg},
                  "layout", group)

    # 3. symmetry of the principal group (axial archetypes only)
    pairs = scene.get("symmetry_pairs") or []
    if pairs:
        errs = []
        for a, b in pairs:
            if a in by_id and b in by_id:
                pa, pb = by_id[a]["position"], by_id[b]["position"]
                xa, ya = float(pa[0]), float(pa[1])
                xb, yb = float(pb[0]), float(pb[1])
                # A pair may be mirrored about either room axis. Score the
                # better one so a face-to-face pair seated north/south is not
                # punished for failing a left/right mirror test.
                err_x = max(abs(xa + xb), abs(ya - yb))
                err_y = max(abs(ya + yb), abs(xa - xb))
                errs.append(min(err_x, err_y))
        metrics["symmetry_error_m"] = round(max(errs), 4) if errs else None
        if errs and max(errs) > arch.symmetry_tolerance_m:
            issue("principal_axis_broken",
                  f"Mirrored principal pieces are off-axis by {max(errs):.3f} m "
                  f"(tolerance {arch.symmetry_tolerance_m:.2f} m).",
                  {"error_m": round(max(errs), 4)},
                  {"tolerance_m": arch.symmetry_tolerance_m},
                  "layout", [i for p in pairs for i in p])

    # 4. area-vs-area and area-vs-circulation overlap. The existing contract
    #    only reserves areas against GEOMETRY, so this is currently legal.
    areas = scene.get("access_areas") or []
    overlaps = []
    for i, a in enumerate(areas):
        if not a.get("center") or not a.get("size"):
            continue
        ra = _rect(a["center"], a["size"])
        for b in areas[i + 1:]:
            if not b.get("center") or not b.get("size"):
                continue
            v = _overlap_area(ra, _rect(b["center"], b["size"]))
            if v > 1e-6:
                overlaps.append({"a": a.get("id"), "b": b.get("id"),
                                 "m2": round(v, 4)})
    # The frozen contract also reserves circulation-only zones. The existing
    # validator checks areas against GEOMETRY but never area-vs-zone, so a
    # person standing at the bookshelf can legally occupy everyone's aisle.
    zone_overlaps = []
    for z in (scene.get("circulation_zones") or []):
        if not z.get("center") or not z.get("size"):
            continue
        rz = _rect(z["center"], z["size"])
        for a in areas:
            if not a.get("center") or not a.get("size"):
                continue
            v = _overlap_area(rz, _rect(a["center"], a["size"]))
            if v > 1e-6:
                zone_overlaps.append({"zone": z.get("id"), "area": a.get("id"),
                                     "m2": round(v, 4)})
    metrics["circulation_vs_access_overlap_m2"] = round(
        sum(x["m2"] for x in zone_overlaps), 4)
    metrics["circulation_vs_access_overlaps"] = zone_overlaps
    total_overlap = round(sum(x["m2"] for x in overlaps) +
                          sum(x["m2"] for x in zone_overlaps), 4)
    metrics["access_area_overlap_m2"] = total_overlap
    metrics["access_area_overlaps"] = overlaps
    if total_overlap > arch.area_overlap_max_m2:
        issue("use_areas_are_not_exclusive",
              f"Operator/access areas overlap by {total_overlap:.3f} m2 "
              f"(max {arch.area_overlap_max_m2:.2f} m2). The person browsing "
              f"the shelf is standing in everyone's corridor.",
              {"total_m2": total_overlap, "pairs": overlaps[:8]},
              {"total_max_m2": arch.area_overlap_max_m2}, "spatial")

    # 5. routes should share one trunk, not radiate from the entrance
    routes = scene.get("routes") or []
    shared, mean_len = _common_prefix_length(routes)
    metrics["shared_trunk_m"] = round(shared, 3)
    metrics["mean_route_length_m"] = round(mean_len, 3) if mean_len else None
    ratio = (shared / mean_len) if mean_len else None
    metrics["shared_trunk_ratio"] = round(ratio, 4) if ratio is not None else None
    if ratio is not None and ratio < arch.shared_trunk_ratio_min:
        issue("radiating_routes",
              f"Routes share only {ratio:.2f} of their length as a common "
              f"entrance trunk (minimum {arch.shared_trunk_ratio_min:.2f}). "
              f"One line per target is a wiring diagram, not a circulation plan.",
              {"shared_m": round(shared, 3), "mean_route_m": round(mean_len, 3),
               "ratio": round(ratio, 4)},
              {"ratio_min": arch.shared_trunk_ratio_min}, "spatial")

    # 6. entrance -> focus sightline must stay clear of peripheral objects
    entrance = scene.get("entrance") or {}
    if focus and entrance.get("position"):
        ex, ey = float(entrance["position"][0]), float(entrance["position"][1])
        fx, fy = float(focus["position"][0]), float(focus["position"][1])
        length = math.hypot(fx - ex, fy - ey)
        if length > 0.2:
            # Define the corridor ONCE, before the loop: an empty loops leaves
            # it defined and the ratio still computes as zero.
            cor = _rect(((ex + fx) / 2, (ey + fy) / 2),
                        (max(0.9, abs(fx - ex)), max(0.9, abs(fy - ey))))
            blocked = 0.0
            for o in objects:
                if o["id"] in group or o["id"] == focus_id:
                    continue
                if o.get("role") in _NON_FURNITURE:
                    continue
                if not o.get("size"):
                    continue
                r = _rect(o["position"], o["size"])
                blocked += _overlap_area(r, cor)
            ratio2 = blocked / max(1.0, _overlap_area(cor, cor))
            metrics["focal_corridor_occlusion"] = round(ratio2, 4)
            if ratio2 > arch.focal_corridor_occlusion_max:
                issue("focal_view_occluded",
                      f"Peripheral objects occupy {ratio2:.1%} of the "
                      f"entrance-to-focus corridor (max "
                      f"{arch.focal_corridor_occlusion_max:.0%}).",
                      {"ratio": round(ratio2, 4)},
                      {"ratio_max": arch.focal_corridor_occlusion_max},
                      "layout")

    metrics["not_measured"] = [k for k, v in metrics.items() if v is None]
    return {"status": "needs_revision" if issues else "passed",
            "archetype": arch.id,
            "archetype_notes": arch.notes,
            "metrics": metrics,
            "issues": issues,
            "issue_count": len(issues)}


def as_planning_issues(result, stage="composition"):
    """Shape issues for Diagnostics.issues / coarse_spatial_judge.json."""
    out = []
    for it in result["issues"]:
        ident = hashlib.sha256(
            (it["code"] + it["evidence"]).encode("utf-8")).hexdigest()[:12]
        out.append({
            "id": "cmp_" + ident,
            "code": "composition_" + it["code"],
            "evidence": "[COMPOSITION] " + it["evidence"] +
                        f" repair_target={it['repair_target']}",
            "paths": ["/composition"],
            "component_ids": it["component_ids"],
            "scenario": "baseline",
            "measured": it["measured"],
            "expected": it["expected"],
            "stage": stage,
        })
    return out


def composition_archetype_prompt(brief, archetype=None):
    """Prior text injected into decomposition / spatial / layout prompts.

    It states measurable thresholds, not adjectives: the model can be told
    exactly which number failed and which layer owns the fix.
    """
    a = archetype or infer_archetype(brief)
    return (
        "MEASURABLE COMPOSITION PRIOR "
        f"[archetype={a.id}]: {a.notes}\n"
        "Compose the room in this order and keep the order visible in your "
        "reasoning: (1) name the single shared focus; (2) define one "
        "conversation/enclosure group around it; (3) place the group as one "
        "coherent figure (mirrored axis where the archetype asks for it); "
        "(4) put peripheral accents in wall niches OUTSIDE the entrance-to-"
        "focus sightline; (5) only then draw circulation, as ONE shared "
        "entrance trunk that branches near each activity.\n"
        f"Thresholds that will be measured and can reject this candidate: "
        f"furniture coverage {a.coverage_pct[0]:.0f}-{a.coverage_pct[1]:.0f}% "
        f"of floor; at least {a.min_enclosure_members} enclosing seats sharing "
        f"one focus; focus-distance CV <= {a.focus_distance_cv_max:.2f}; every "
        f"seat within {a.facing_tolerance_deg:.0f} deg of the focus; enclosure "
        f"arc >= {a.min_azimuth_coverage_deg:.0f} deg; mirrored pair error <= "
        f"{a.symmetry_tolerance_m:.2f} m; access-area overlap <= "
        f"{a.area_overlap_max_m2:.2f} m2; shared route trunk >= "
        f"{a.shared_trunk_ratio_min:.2f} of mean route length.\n"
        "Do NOT satisfy these by adding protected open-space rectangles, by "
        "spreading one route per object from the entrance, or by declaring "
        "prose about openness instead of arranging real inventory. A measured "
        "failure reports its repair_target: decomposition for sparse inventory, "
        "layout for poses/facing/symmetry, spatial for zones/areas/routes. "
        "Repair at that layer; moving coordinates cannot fix a sparse inventory."
    )


def thresholds(archetype=None):
    return asdict(archetype or ARCHETYPES["reception_lounge"])


# ---------------------------------------------------------------------------
# Live FineScene adapter. Kept in this module (rather than staged_planner.py)
# so the 350 KB planner only needs a small guarded hook, and so the adapter
# can be unit-tested offline against frozen run artefacts.
# ---------------------------------------------------------------------------

def _number(value, parameters):
    if isinstance(value, (int, float)):
        return float(value)
    try:
        return float(value)
    except (TypeError, ValueError):
        pass
    try:
        from general_scene import scalar
        return float(scalar(value, parameters))
    except Exception:
        return None


def _role(obj):
    role = getattr(obj, "collision_role", "solid") or "solid"
    text = (str(getattr(obj, "semantic", "") or "") + " " +
            str(getattr(obj, "category", "") or "")).casefold()
    if role == "shell":
        return "shell"
    if role == "floor_covering":
        return "floor_covering"
    if "sofa" in text or "couch" in text:
        return "sofa"
    if "chair" in text or "seat" in text or "armchair" in text:
        return "armchair"
    if "coffee table" in text:
        return "coffee_table"
    if "table" in text or "desk" in text:
        return "coffee_table"
    if "shelf" in text or "bookcase" in text or "cabinet" in text:
        return "bookshelf"
    if "rug" in text or "carpet" in text:
        return "floor_covering"
    return "accent"


def from_finescene(outline, layout, spatial, brief=""):
    """Convert live planner artefacts into the normalized scene dict."""
    parameters = getattr(outline, "parameters", {}) or {}
    placements = {p.id: p for p in layout.placements}
    objects = []
    for o in outline.objects:
        p = placements.get(o.id)
        if p is None:
            continue
        dims = [_number(v, parameters) for v in o.dimensions]
        size = [v if v is not None else 0.0 for v in dims]
        role = _role(o)
        objects.append({"id": o.id, "role": role,
                        "position": [float(x) for x in p.position],
                        "size": size, "yaw_deg": float(getattr(p, "yaw_deg", 0) or 0),
                        "collision_role": getattr(o, "collision_role", "solid")})
    extent = [float(x) for x in outline.extent]
    room = {"width": extent[0], "depth": extent[1], "height": extent[2]}

    areas = [{"id": a.id, "center": list(a.center), "size": list(a.size)}
             for a in getattr(spatial, "access_areas", [])]
    routes = [{"id": r.id, "points": [list(p) for p in r.points]}
              for r in getattr(spatial, "routes", [])]

    entrance = None
    for op in getattr(spatial, "openings", []):
        if getattr(op, "kind", "") == "entrance":
            off = float(op.offset)
            wall = op.wall
            if wall == "y_min":
                xy = [off, -extent[1] / 2]
            elif wall == "y_max":
                xy = [off, extent[1] / 2]
            elif wall == "x_min":
                xy = [-extent[0] / 2, off]
            else:
                xy = [extent[0] / 2, off]
            entrance = {"position": xy, "opening_id": op.id}
            break

    arch = infer_archetype(brief)
    focus_id = next((o["id"] for o in objects
                     if o["role"] in arch.focus_roles), None)
    group = [o["id"] for o in objects if o["role"] in arch.enclosure_roles]
    # Mirror pairs of like-kind seats when exactly two exist.
    by_role = {}
    for o in objects:
        by_role.setdefault(o["role"], []).append(o["id"])
    pairs = [v for v in by_role.values() if len(v) == 2 and
             v[0] in group and v[1] in group]
    return {"room": room, "objects": objects, "brief": brief,
            "focus_id": focus_id, "enclosure_group": group,
            "entrance": entrance, "access_areas": areas, "routes": routes,
            "symmetry_pairs": pairs}


def composition_hook(outline, layout, spatial, brief, report=None):
    """Guarded entry point for staged_planner.validate_coarse.

    Returns {'status','archetype','metrics','issues'} where issues are already
    shaped for coarse_spatial_judge.json. Never raises: a broken adapter must
    not take down an otherwise valid planning run.
    """
    try:
        scene = from_finescene(outline, layout, spatial, brief)
        result = evaluate(scene, brief=brief)
        return {"status": result["status"], "archetype": result["archetype"],
                "archetype_notes": result["archetype_notes"],
                "metrics": result["metrics"], "issue_count": result["issue_count"],
                "issues": as_planning_issues(result, stage="composition"),
                "scene_summary": {"objects": len(scene["objects"]),
                                  "focus_id": scene["focus_id"],
                                  "enclosure_group": scene["enclosure_group"],
                                  "entrance": scene["entrance"]}}
    except Exception as exc:
        return {"status": "blocked", "archetype": None, "metrics": {},
                "issue_count": 0, "issues": [],
                "blocked_reason": type(exc).__name__ + ": " + str(exc)[:300]}
