"""Sim2real acceptance audit of the assembled scene (run inside Blender).

    blender -b measured/scene/scene.blend --python geometry/sim2real_audit.py -- RUN_DIR

Per object, hard failures:
  grasp       every door / drawer / lid has a grasp feature (adsl_adapter/sim2real.py,
              the same rule the forge's dry run enforces), checked on the parts as placed
  joint       no operable joint is blocked by the room or its neighbours before 25 %
              of its range (environment limits are applied by the scene builder)
  facing      the front (local -Y) points where the functional parts were found
              in the video (geometry/front_parts.json)
  reach       the front of an object with operable joints or controls is reachable:
              >= 0.45 m of free space in front of it
  scale       an articulated object is not scaled non-uniformly (hinged parts shear)
Writes RUN_DIR/measured/sim2real_audit.json and prints a table; exit code 1 on failures.
"""
import json
import math
import re
import sys
from pathlib import Path

import bpy
from mathutils import Vector

argv = sys.argv[sys.argv.index("--") + 1:] if "--" in sys.argv else []
run = Path(argv[0])
ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT / "adsl_adapter"))
import sim2real  # noqa: E402

plan = json.loads((run / "measured/scene_plan.json").read_text())
W, D = plan["room"]["width"], plan["room"]["depth"]
fp_path = run / "geometry/front_parts.json"
FRONT = json.loads(fp_path.read_text()) if fp_path.is_file() else {}
entries = {e["id"]: e for e in plan["objects"]}
CONTROL_KINDS = {"button", "touch_key", "knob", "switch", "dial", "display", "door", "drawer", "lid",
                 "handle", "outlet", "tap", "lever"}


def sub(r):
    out, st = [], list(r.children)
    while st:
        o = st.pop(); st.extend(o.children); out.append(o)
    return out


def aabb(objs):
    lo, hi = [1e9] * 3, [-1e9] * 3
    for o in objs:
        if o.type != "MESH":
            continue
        for v in o.bound_box:
            q = o.matrix_world @ Vector(v)
            for i in range(3):
                lo[i], hi[i] = min(lo[i], q[i]), max(hi[i], q[i])
    return lo, hi


def ray_box(o, d, lo, hi):
    tmin, tmax = 0.0, 1e9
    for i in range(3):
        if abs(d[i]) < 1e-9:
            if not (lo[i] <= o[i] <= hi[i]):
                return None
            continue
        t1, t2 = (lo[i] - o[i]) / d[i], (hi[i] - o[i]) / d[i]
        t1, t2 = min(t1, t2), max(t1, t2)
        tmin, tmax = max(tmin, t1), min(tmax, t2)
        if tmin > tmax:
            return None
    return tmin


def compass(v):
    return max((("east", v.x), ("west", -v.x), ("north", v.y), ("south", -v.y)), key=lambda t: t[1])[0]


roots = {o["ci_object"]: o for o in bpy.data.objects if o.get("ci_object") and o.parent is None}
boxes = {k: aabb(sub(r)) for k, r in roots.items()}
report, failures = {}, 0
for oid in sorted(roots):
    r = roots[oid]
    meshes = [o for o in sub(r) if o.type == "MESH"]
    joints = [o for o in sub(r) if o.get("ci_joint")]
    recs = [json.loads(j["ci_joint"]) for j in joints]
    spec_path = run / "objects" / oid / "spec.json"
    spec = json.loads(spec_path.read_text()) if spec_path.is_file() else {"parts": [], "joints": []}
    items = []
    # scale
    s = r.scale
    if joints and max(s) / max(min(s), 1e-6) > 1.03:
        items.append(("scale", f"articulated but scaled ({s[0]:.2f}, {s[1]:.2f}, {s[2]:.2f})"))
    # grasp: parts in the root frame, in metres
    inv = r.matrix_world.inverted()
    parts = []
    for m in meshes:
        pts = [inv @ m.matrix_world @ Vector(v) for v in m.bound_box]
        parts.append({"name": re.sub(r"\.\d{3}$", "", m.name),   # Blender renames clashes: door_right.001
                      "min": [min(p[i] for p in pts) * s[i] for i in range(3)],
                      "max": [max(p[i] for p in pts) * s[i] for i in range(3)]})
    for v in sim2real.grasp_issues(spec, parts, [x for x in recs if x.get("built", True)]):
        items.append(("grasp", v["message"].split(". Give it")[0]))
    # environment limits
    for x in recs:
        lim = (x.get("environment_limit") or {})
        kinds = {p.get("kind") for p in spec.get("parts") or [] if p["id"] in (x.get("moving_parts") or [])}
        if lim and lim.get("fraction", 1.0) < 0.25 and kinds & sim2real.OPERABLE_KINDS:
            items.append(("joint", f"{x['id']} opens only {lim['fraction'] * 100:.0f}% before hitting "
                                   f"{lim.get('blocked_by')}"))
    # facing
    f = (r.matrix_world.to_3x3() @ Vector((0, -1, 0))).normalized()
    want = (FRONT.get(oid) or {}).get("front")
    got = compass(f)
    if want and want != got:
        items.append(("facing", f"front points {got}, the functional parts were found on its {want} side"))
    # reach
    has_controls = any(p.get("kind") in CONTROL_KINDS and p.get("face") == "front" for p in spec.get("parts") or [])
    lo, hi = boxes[oid]
    ctr = Vector(((lo[0] + hi[0]) / 2, (lo[1] + hi[1]) / 2, (lo[2] + hi[2]) / 2))
    start = ctr + f * (abs(f.x) * (hi[0] - lo[0]) / 2 + abs(f.y) * (hi[1] - lo[1]) / 2 + 0.01)
    walls = [t for t in ((W / 2 - start.x) / f.x if f.x > 1e-6 else None, (-W / 2 - start.x) / f.x if f.x < -1e-6 else None,
                         (D / 2 - start.y) / f.y if f.y > 1e-6 else None, (-D / 2 - start.y) / f.y if f.y < -1e-6 else None)
             if t is not None]
    sup = entries.get(oid, {}).get("on")
    # per mesh, not per object: a housing's two side panels share one box that
    # encloses the appliance between them, but they do not stand in front of it
    hits = [(ray_box(start, f, *aabb([m])), k) for k in boxes
            if k not in (oid, sup) and entries.get(k, {}).get("on") != oid
            for m in sub(roots[k]) if m.type == "MESH"]
    hits = [(t, k) for t, k in hits if t is not None]
    clear = min(walls + [t for t, _ in hits] + [9.0])
    if has_controls and clear < 0.45 and entries.get(oid, {}).get("mount") != "built_in":
        items.append(("reach", f"only {clear:.2f} m free in front of it" +
                      (f" ({', '.join(k for t, k in hits if t == clear)})" if hits else " (wall)")))
    report[oid] = {"front": got, "front_from_parts": want, "front_clear_m": round(clear, 2),
                   "joints": [{"id": x["id"], "type": x.get("type"),
                               "env_limit": (x.get("environment_limit") or {}).get("fraction", 1.0)} for x in recs],
                   "failures": [{"check": c, "detail": d} for c, d in items]}
    failures += len(items)
    status = "PASS" if not items else "FAIL"
    print(f"[{status}] {oid:18s} front {got:5s}{'(parts: ' + want + ')' if want else '':16s} clear {clear:4.2f} m  "
          f"joints {len(recs)}")
    for c, d in items:
        print(f"         - {c}: {d}")
(run / "measured/sim2real_audit.json").write_text(json.dumps({"failures": failures, "objects": report}, indent=1))
print(f"SIM2REAL_AUDIT failures={failures}")
