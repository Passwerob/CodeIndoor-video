"""Export real part AABBs from the delivered .blend for deterministic checks.

Run with: blender -b baroque_reception.blend --python export_blend_parts.py

Produces:
  parts.json            - one entry per mesh part (world AABB + metadata)
  scene_normalized.json - one entry per top-level object (for composition metrics)
  joint_pairs.json      - declared joints, derived from named construction rules
"""
import bpy
import json
import os
import re
from mathutils import Vector

ROOT = os.path.dirname(os.path.abspath(__file__))
ROOT_COLL_NAME = "ADSL_ReceptionLounge"

OWNER = {}
for coll in bpy.data.collections:
    if coll.name.startswith("OBJ_"):
        OWNER[coll.name[4:]] = coll


def owner_of(ob):
    for c in ob.users_collection:
        if c.name.startswith("OBJ_"):
            return c.name[4:]
    return None


def world_aabb(ob):
    corners = [ob.matrix_world @ Vector(c) for c in ob.bound_box]
    lo = [min(v[i] for v in corners) for i in range(3)]
    hi = [max(v[i] for v in corners) for i in range(3)]
    return lo, hi


depsgraph = bpy.context.evaluated_depsgraph_get()
parts = []
objs = {}
for ob in bpy.data.objects:
    # Scroll arms, cabriole legs, chains and festoons are CURVE objects with
    # a bevel. Their control-point bound_box omits the bevel radius, so
    # evaluate them to a real mesh before measuring the world AABB.
    if ob.type not in ("MESH", "CURVE", "SURFACE", "FONT"):
        continue
    own = owner_of(ob)
    if own is None:
        continue
    if ob.type == "MESH":
        lo, hi = world_aabb(ob)
    else:
        ev = ob.evaluated_get(depsgraph)
        me = ev.to_mesh()
        if me is None or not me.vertices:
            if me is not None:
                ev.to_mesh_clear()
            continue
        cs = [ev.matrix_world @ v.co for v in me.vertices]
        lo = [min(v[i] for v in cs) for i in range(3)]
        hi = [max(v[i] for v in cs) for i in range(3)]
        ev.to_mesh_clear()
    material = ""
    if ob.data.materials:
        material = ob.data.materials[0].name
    role = "solid"
    if own in ("room_shell", "boiserie", "columns", "windows", "entrance",
               "wall_panels"):
        role = "shell"
    elif own == "area_carpet":
        role = "floor_covering"
    elif "glass" in ob.name or "sky" in ob.name:
        role = "glazing"
    parts.append({
        "id": ob.name,
        "object": own,
        "min": [round(v, 6) for v in lo],
        "max": [round(v, 6) for v in hi],
        "role": role,
        "material": material,
        "adsl_type": str(ob.get("adsl_type", "")),
    })
    if own not in objs:
        objs[own] = {"lo": list(lo), "hi": list(hi), "count": 0}
    else:
        for i in range(3):
            objs[own]["lo"][i] = min(objs[own]["lo"][i], lo[i])
            objs[own]["hi"][i] = max(objs[own]["hi"][i], hi[i])
    objs[own]["count"] += 1

ROLE_MAP = {
    "room_shell": "shell", "boiserie": "shell", "columns": "shell",
    "windows": "shell", "entrance": "shell", "wall_panels": "structure",
    "fireplace": "wall_fixture", "gilt_mirror": "wall_fixture",
    "sofa_west": "sofa", "sofa_east": "sofa",
    "armchair_southwest": "armchair", "armchair_southeast": "armchair",
    "coffee_table": "coffee_table", "area_carpet": "floor_covering",
    "gueridon_west": "side_table", "gueridon_east": "side_table",
    "chandelier": "light", "wall_sconces": "light", "candelabra": "light",
}

manifest = json.load(open(os.path.join(ROOT, "object_manifest.json"),
                          encoding="utf-8"))
pose_by_id = {o["id"]: o for o in manifest["editable_objects"]}

objects = []
for oid, b in sorted(objs.items()):
    size = [round(b["hi"][i] - b["lo"][i], 4) for i in range(3)]
    pose = pose_by_id.get(oid, {})
    # Use the authored Empty-root pose, not the mesh centroid: asymmetric
    # crest details must not shift a mirrored pair off the symmetry axis.
    position = list(pose.get("position",
                             [(b["hi"][i] + b["lo"][i]) / 2 for i in range(3)]))
    objects.append({"id": oid, "role": ROLE_MAP.get(oid, "accent"),
                    "position": position, "size_xy_from_mesh": size,
                    "size": size,
                    "yaw_deg": pose.get("yaw_deg", 0),
                    "part_count": b["count"]})

# ---- declared joints: the construction intent that MUST hold ---------------
# Blender auto-suffixes duplicate part names ("arm_1.004"), and identical
# short names exist in different objects (sofa arms vs chandelier arms).
# Always resolve joints by OWNER + name pattern, never by bare name.
by_name = {p["id"]: p for p in parts}
joints = []

def owner_ids(owner):
    return [p["id"] for p in parts if p["object"] == owner]

def owner_match(owner, pattern):
    return [pid for pid in owner_ids(owner) if re.fullmatch(pattern, pid)]

def joint(a, b, label):
    if a in by_name and b in by_name:
        joints.append([a, b, label])

CH = "chandelier"
arms, bobs, sticks, crystals = {}, {}, {}, {}
for pid in owner_match(CH, r"arm_\d+(?:\.\d+)?"):
    arms.setdefault(int(re.match(r"arm_(\d+)", pid).group(1)), []).append(pid)
for pid in owner_match(CH, r"bobeche_\d+(?:\.\d+)?"):
    bobs[int(re.match(r"bobeche_(\d+)", pid).group(1))] = pid
for pid in owner_match(CH, r"candle_\d+_stick(?:\.\d+)?"):
    sticks[int(re.match(r"candle_(\d+)_stick", pid).group(1))] = pid
for pid in owner_match(CH, r"crystal_\d+_\d+(?:\.\d+)?"):
    m = re.match(r"crystal_(\d+)_(\d+)", pid)
    crystals.setdefault(int(m.group(1)), {})[int(m.group(2))] = pid
bowls = owner_match(CH, r"chandelier_bowl(?:\.\d+)?")
festoons = owner_match(CH, r"festoon_\d+(?:\.\d+)?")

for i in sorted(set(arms) & set(bobs)):
    for arm in arms[i]:
        joint(arm, bobs[i], "arm-to-bobeche %d" % i)
        if 0 in crystals.get(i, {}):
            joint(arm, crystals[i][0], "crystal hanger %d" % i)
    if i in sticks:
        joint(bobs[i], sticks[i], "bobeche-to-candle %d" % i)
    chain = crystals.get(i, {})
    for k in sorted(chain):
        if k + 1 in chain:
            joint(chain[k], chain[k + 1], "crystal chain %d/%d" % (i, k))
for f in festoons:
    for b in bowls:
        joint(f, b, "festoon anchor")

# wall sconces: plate -> arm -> candle. Tags look like "sconce_1_1.55".
sc = owner_ids("wall_sconces")
for pid in sc:
    m = re.match(r"(sconce_-?\d+_-?[\d.]+)_arm_(-?1)(?:\.\d+)?$", pid)
    if not m:
        continue
    pref, sign = m.group(1), m.group(2)
    for q in sc:
        if q.startswith(pref + "_plate"):
            joint(pid, q, "sconce arm root")
        if q.startswith(pref + "_candle_" + sign + "_stick"):
            joint(pid, q, "sconce arm to candle")

# floor candelabra: stem -> branch -> candle
ca = owner_ids("candelabra")
for pid in ca:
    m = re.match(r"(candelabrum_-?1)_branch_(\d+)(?:\.\d+)?$", pid)
    if not m:
        continue
    pref, idx = m.group(1), m.group(2)
    for q in ca:
        if q.startswith(pref + "_stem"):
            joint(pid, q, "branch root")
        if q.startswith(pref + "_candle_" + idx + "_stick"):
            joint(pid, q, "branch to candle")

# gueridons: marble top -> finial and top -> cabriole legs
for own in ("gueridon_west", "gueridon_east"):
    ids = owner_ids(own)
    tops = [q for q in ids if re.fullmatch(r"top(?:\.\d+)?", q)]
    finials = [q for q in ids if re.fullmatch(r"finial(?:\.\d+)?", q)]
    # Only the leg SHAFT meets the tabletop. "leg_0_claw" / "leg_0_shoe" are
    # ground-level feet and must not be declared as top joints.
    legs = [q for q in ids if re.fullmatch(r"leg_\d+(?:\.\d+)?", q)]
    for t in tops:
        for f in finials:
            joint(t, f, own + " top-to-finial")
        for leg in legs:
            joint(t, leg, own + " top-to-leg")

with open(os.path.join(ROOT, "parts.json"), "w", encoding="utf-8") as fh:
    json.dump({"parts": parts}, fh, ensure_ascii=False)
with open(os.path.join(ROOT, "joint_pairs.json"), "w", encoding="utf-8") as fh:
    json.dump(joints, fh, ensure_ascii=False, indent=1)
with open(os.path.join(ROOT, "scene_normalized.json"), "w", encoding="utf-8") as fh:
    json.dump({
        "room": {"width": 8.0, "depth": 6.4, "height": 3.6},
        "objects": objects,
        "focus_id": "coffee_table",
        "enclosure_group": ["sofa_west", "sofa_east",
                            "armchair_southwest", "armchair_southeast"],
        "symmetry_pairs": [["sofa_west", "sofa_east"],
                           ["armchair_southwest", "armchair_southeast"],
                           ["gueridon_west", "gueridon_east"]],
        "entrance": {"position": [0, -3.22]},
        # The direct construction has no frozen access/route contract. Do not
        # invent one: these metrics are reported as not_measured for this
        # sample and only the Agent sample (which owns a real contract) is
        # judged on them.
        "access_areas": [],
        "routes": [],
        "contract_note": "no frozen spatial contract; circulation metrics not measured",
    }, fh, ensure_ascii=False, indent=1)

print("EXPORT_PARTS", len(parts), flush=True)
print("EXPORT_OBJECTS", len(objects), flush=True)
print("EXPORT_JOINTS", len(joints), flush=True)
