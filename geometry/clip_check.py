"""Pivot and interpenetration check of the assembled scene (run inside Blender).

    blender -b measured/scene/scene.blend --python geometry/clip_check.py -- RUN_DIR

1. origins  every object root sits at the bottom centre of its footprint (the
            point it rotates about when moved in Blender)
2. pivots   every joint: where its pivot lies on the moving parts' box, its axis,
            and whether opening moves the part outward / upward as it should
3. static   penetration depth between meshes: across objects and the room, and
            thin parts (cords, hoses, cloth, leaves, brush) inside their own object.
            A vertex counts when it is inside a CLOSED mesh by more than TOL
            (BVH nearest point + outward normal), so touching contact is not clipping
4. motion   each joint driven to 25/50/75/100 %: its moving parts vs everything else
Writes RUN_DIR/measured/clip_check.json and prints the findings.
"""
import json
import math
import sys
from pathlib import Path

import bmesh
import bpy
from mathutils import Vector
from mathutils.bvhtree import BVHTree

argv = sys.argv[sys.argv.index("--") + 1:] if "--" in sys.argv else []
run = Path(argv[0])
TOL = 0.004                      # deeper than this counts as clipping
THIN = ("cord", "hose", "cloth", "leaf", "foliage", "brush", "handle", "plug", "spout", "lever")
dg = bpy.context.evaluated_depsgraph_get()


def sub(r):
    out, st = [], list(r.children)
    while st:
        o = st.pop(); st.extend(o.children); out.append(o)
    return out


roots = {o["ci_object"]: o for o in bpy.data.objects if o.get("ci_object") and o.parent is None}
owner = {}
for k, r in roots.items():
    for o in sub(r):
        owner[o.name] = k
room = [o for o in bpy.data.objects if o.type == "MESH" and o.name not in owner and not o.hide_render
        and not o.name.startswith(("CUT_", "_"))]
for o in room:
    owner[o.name] = "ROOM"


class Geo:
    def __init__(self, o):
        ev = o.evaluated_get(dg)
        me = ev.to_mesh()
        mw = o.matrix_world
        self.verts = [mw @ v.co for v in me.vertices]
        self.faces = [tuple(p.vertices) for p in me.polygons]
        bm = bmesh.new()
        bm.from_mesh(me)
        self.closed = len(bm.edges) > 0 and all(e.is_manifold for e in bm.edges)
        bm.free()
        ev.to_mesh_clear()
        self.lo = Vector([min(v[i] for v in self.verts) for i in range(3)]) if self.verts else Vector((1e9,) * 3)
        self.hi = Vector([max(v[i] for v in self.verts) for i in range(3)]) if self.verts else Vector((-1e9,) * 3)
        self.bvh = BVHTree.FromPolygons(self.verts, self.faces, epsilon=0.0) if self.faces else None
        # face centres too: big quads have no vertex inside a small intruder
        self.samples = list(self.verts) + [sum((self.verts[i] for i in f), Vector()) / len(f) for f in self.faces]


def geos(objs):
    return {o.name: Geo(o) for o in objs if o.type == "MESH" and not o.hide_render}


def depth_inside(a, b):
    """Deepest sample of `a` inside closed mesh `b` (0 if none / b not closed)."""
    if not b.closed or b.bvh is None:
        return 0.0, 0
    lo, hi = b.lo - Vector((TOL,) * 3), b.hi + Vector((TOL,) * 3)
    best, n = 0.0, 0
    for p in a.samples:
        if not all(lo[i] <= p[i] <= hi[i] for i in range(3)):
            continue
        loc, nrm, _, dist = b.bvh.find_nearest(p)
        if loc is None or dist <= TOL:
            continue
        if inside(b, p):                     # ray parity: robust for thin shells, unlike a face normal
            n += 1
            best = max(best, dist)
    return best, n


DIRS = (Vector((0.3, 0.2, 1.0)).normalized(), Vector((-0.7, 0.6, -0.2)).normalized())


def inside(b, p):
    """Odd number of crossings along two skew rays (both must agree)."""
    votes = 0
    for d in DIRS:
        q, hits = p.copy(), 0
        for _ in range(64):
            loc, _, _, _ = b.bvh.ray_cast(q, d)
            if loc is None:
                break
            hits += 1
            q = loc + d * 1e-5
        votes += hits % 2
    return votes == 2


def overlap(a, b, pad=0.0):
    return all(a.lo[i] - pad <= b.hi[i] and b.lo[i] - pad <= a.hi[i] for i in range(3))


report = {"origins": [], "pivots": [], "static": [], "motion": []}
plan_mount = {e["id"]: e.get("mount") for e in json.loads((run / "measured/scene_plan.json").read_text())["objects"]}

# ---- 1. origins -------------------------------------------------------------------
G = geos([o for o in bpy.data.objects if o.name in owner])
for k, r in roots.items():
    parts = [G[o.name] for o in sub(r) if o.name in G and not any(w in o.name.lower() for w in ("cord", "hose", "plug"))]
    lo = Vector([min(g.lo[i] for g in parts) for i in range(3)])
    # the footprint is what stands on the support (lowest 2 cm): a tap's spout or a
    # plant's leaves overhang to one side, but it turns about its base
    foot = [v for g in parts for v in g.verts if v.z < lo.z + 0.02]
    c = Vector(((min(v.x for v in foot) + max(v.x for v in foot)) / 2, (min(v.y for v in foot) + max(v.y for v in foot)) / 2, 0))
    o = r.matrix_world.translation
    dxy = math.hypot(o.x - c.x, o.y - c.y)
    rec = {"object": k, "origin": [round(v, 3) for v in o], "footprint_centre": [round(c.x, 3), round(c.y, 3)],
           "bottom_z": round(lo.z, 3), "off_centre_m": round(dxy, 3), "origin_above_bottom_m": round(o.z - lo.z, 3)}
    if (plan_mount.get(k) or "floor") in ("wall", "ceiling"):    # hangs: no footprint, use the whole box
        hi = Vector([max(g.hi[i] for g in parts) for i in range(3)])
        c = (lo + hi) / 2
        dxy = math.hypot(o.x - c.x, o.y - c.y)
        rec.update({"footprint_centre": [round(c.x, 3), round(c.y, 3)], "off_centre_m": round(dxy, 3)})
    rec["ok"] = dxy < 0.03 and abs(o.z - lo.z) < 0.01
    report["origins"].append(rec)

# ---- 2. pivots --------------------------------------------------------------------
joints = [o for o in bpy.data.objects if o.get("ci_joint")]


def moving_meshes(j):
    return [o for o in sub(j) if o.type == "MESH"]


def group_box(objs):
    gs = [Geo(o) for o in objs]
    return (Vector([min(g.lo[i] for g in gs) for i in range(3)]), Vector([max(g.hi[i] for g in gs) for i in range(3)]))


def set_open(j, v):
    j["open"] = v
    j.update_tag()
    bpy.context.view_layer.update()


for j in joints:
    rec = json.loads(j["ci_joint"])
    k = owner.get(j.name) or next((owner[o.name] for o in moving_meshes(j) if o.name in owner), "?")
    r = roots.get(k)
    mm = moving_meshes(j)
    if not mm or r is None:
        continue
    set_open(j, 0.0)
    lo, hi = group_box(mm)
    piv = j.matrix_world.translation.copy()
    ax = (j.matrix_world.to_3x3() @ Vector([1.0 if i == rec.get("axis_index") else 0.0 for i in range(3)])).normalized()
    front = (r.matrix_world.to_3x3() @ Vector((0, -1, 0))).normalized()

    def where(i):
        span = hi[i] - lo[i]
        if span < 1e-4:
            return "flat"
        t = (piv[i] - lo[i]) / span
        return "min" if t < 0.1 else "max" if t > 0.9 else ("centre" if 0.35 < t < 0.65 else f"{t:.2f}")
    c0 = (lo + hi) / 2
    frac = float((rec.get("environment_limit") or {}).get("fraction", 1.0))
    set_open(j, min(0.3, frac) if frac > 0 else 0.3)
    l1, h1 = group_box(mm)
    c1 = (l1 + h1) / 2
    set_open(j, 0.0)
    d = c1 - c0
    jt = rec.get("type")
    verdict = "ok"
    if jt == "revolute" and rec.get("hinge_edge") in (None, "center"):
        verdict = "ok"                                # a swivel / lever turns in place: no outward test
    elif jt == "revolute":
        if abs(ax.z) > 0.9:                          # side-hinged door: free edge comes forward
            verdict = "ok" if d.dot(front) > 0.005 else "WRONG: opening does not move it outward"
        else:                                         # lid / flap / drop-down: it rises or comes forward
            verdict = "ok" if (d.z > 0.003 or d.dot(front) > 0.003) else "WRONG: opening does not lift or bring it out"
    elif jt == "prismatic":
        if abs((j.matrix_world.to_3x3() @ Vector([1.0 if i == rec.get("axis_index") else 0.0 for i in range(3)])).z) > 0.9:
            verdict = "ok" if d.z > 0.003 else "WRONG: does not lift"          # a strainer lifts out
        else:
            verdict = "ok" if d.dot(front) > 0.005 else "WRONG: drawer does not slide outward"
    report["pivots"].append({
        "object": k, "joint": rec.get("id"), "type": jt, "pivot": [round(v, 3) for v in piv],
        "axis_world": [round(v, 2) for v in ax], "pivot_on_box": {"x": where(0), "y": where(1), "z": where(2)},
        "hinge_edge": rec.get("hinge_edge"), "open_move_m": [round(v, 3) for v in d],
        "env_fraction": frac, "verdict": verdict})

# ---- 3. static penetration ---------------------------------------------------------
G = geos([o for o in bpy.data.objects if o.name in owner])
names = list(G)
seen = set()
for i, a in enumerate(names):
    for b in names[i + 1:]:
        A, Bg = G[a], G[b]
        oa, ob = owner[a], owner[b]
        if oa == "ROOM" and ob == "ROOM":
            continue
        same = oa == ob
        if same and not any(w in (a + b).lower() for w in THIN):
            continue
        if not overlap(A, Bg):
            continue
        d1, n1 = depth_inside(A, Bg)
        d2, n2 = depth_inside(Bg, A)
        dep = max(d1, d2)
        if dep > TOL:
            report["static"].append({"a": a, "a_obj": oa, "b": b, "b_obj": ob, "depth_m": round(dep, 4),
                                     "points": n1 + n2, "same_object": same})

# ---- 4. motion -------------------------------------------------------------------
for j in joints:
    rec = json.loads(j["ci_joint"])
    mm = [o for o in moving_meshes(j) if not o.hide_render]
    if not mm:
        continue
    mnames = {o.name for o in mm}
    k = owner.get(mm[0].name, "?")
    frac = float((rec.get("environment_limit") or {}).get("fraction", 1.0))
    hits = []
    for f in (0.25, 0.5, 0.75, 1.0):
        set_open(j, f)
        Gm = geos(mm)
        # everything else stays closed: reuse the static geometry
        others = {n: g for n, g in G.items() if n not in mnames and any(overlap(m, g, 0.01) for m in Gm.values())}
        worst = None
        for an, A in Gm.items():
            for bn, Bg in others.items():
                if not overlap(A, Bg):
                    continue
                dep = max(depth_inside(A, Bg)[0], depth_inside(Bg, A)[0])
                if dep > TOL and (worst is None or dep > worst[0]):
                    worst = (dep, an, bn, owner[bn])
        if worst:
            hits.append({"open": f, "within_env_limit": f <= frac + 1e-6, "depth_m": round(worst[0], 4),
                         "moving": worst[1], "hits": worst[2], "hits_obj": worst[3]})
    set_open(j, 0.0)
    report["motion"].append({"object": k, "joint": rec.get("id"), "type": rec.get("type"), "env_fraction": frac,
                             "collisions": hits})

(run / "measured/clip_check.json").write_text(json.dumps(report, indent=1))
print("== ORIGINS")
for r in report["origins"]:
    print(("  ok  " if r["ok"] else "  BAD ") + f"{r['object']:18s} off-centre {r['off_centre_m']:.3f} m, origin "
          f"{r['origin_above_bottom_m']:+.3f} m above bottom")
print("== PIVOTS")
for p in report["pivots"]:
    print(f"  {p['verdict'][:5]:5s} {p['object']:18s} {p['joint']:24s} {p['type']:10s} axis {p['axis_world']} "
          f"pivot on box x:{p['pivot_on_box']['x']} y:{p['pivot_on_box']['y']} z:{p['pivot_on_box']['z']} "
          f"hinge {p['hinge_edge']} move {p['open_move_m']}" + ("" if p["verdict"] == "ok" else "  <- " + p["verdict"]))
print("== STATIC (depth > %.0f mm)" % (TOL * 1000))
for s in sorted(report["static"], key=lambda s: -s["depth_m"]):
    print(f"  {s['depth_m'] * 1000:6.1f} mm  {s['a_obj']}/{s['a']}  <->  {s['b_obj']}/{s['b']}  ({s['points']} pts)")
print("== MOTION")
for m in report["motion"]:
    first = next((h for h in m["collisions"]), None)
    print(f"  {m['object']:18s} {m['joint']:24s} env {m['env_fraction']:.2f}  " +
          ("clear" if not first else ", ".join(f"{h['open']:.2f}:{h['depth_m'] * 1000:.0f}mm {h['moving']}->{h['hits_obj']}/{h['hits']}"
                                                + ("" if h["within_env_limit"] else "(beyond limit)") for h in m["collisions"])))
print("CLIP_CHECK done")
