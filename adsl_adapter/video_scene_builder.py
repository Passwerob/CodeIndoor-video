"""Build the filmed room as ONE .blend: shell, materials, lights, objects (Blender side).

    blender -b --factory-startup --python adsl_adapter/video_scene_builder.py -- \
        runtime/video/<run>/scene_plan.json [--samples 64] [--no-render] [--calibrate]

Everything here is instantiation; every number comes from scene_plan.json,
which video_pipeline.py composes from the pure-Python stages:

  room      W x D x H box built OUTSIDE the interior (so the inside is exactly
            the measured size), walls split around windows / doors
            (video_layout.wall_segments), tile splashback zones, floor and
            ceiling -- each surface with the material MEASURED from the video
            (appearance.py) as a native procedural Principled material;
  lights    one Blender light per observed fixture at its colour temperature
            (light.use_temperature, Blender 5.x), with its housing, cable,
            canopy and a glowing diffuser; night = a dark world;
  camera    the phone's white balance (view_settings.white_balance_*) so a
            4000 K pendant looks like the neutral white the phone recorded,
            and an exposure that can be calibrated against the video;
  objects   forged .blend files appended by their ROOT (joint pivots and
            `open` drivers included); objects not forged yet are labelled
            placeholder boxes with their measured size and material.
"""
from __future__ import annotations

import json
import math
import sys
from pathlib import Path

import bmesh
import bpy
from mathutils import Matrix, Vector

ADAPTER = Path(__file__).resolve().parent
if str(ADAPTER) not in sys.path:
    sys.path.insert(0, str(ADAPTER))

import blender_articulation  # noqa: E402
import blender_materials  # noqa: E402
import blender_render  # noqa: E402
import video_layout  # noqa: E402  (pure Python geometry)
import sim2real  # noqa: E402  (flexible parts, grasp hardware)

MAT_CACHE: dict = {}


# --------------------------------------------------------------------------
# basics
# --------------------------------------------------------------------------
def collection(name: str):
    col = bpy.data.collections.get(name)
    if col is None:
        col = bpy.data.collections.new(name)
        bpy.context.scene.collection.children.link(col)
    return col


def material(key: str | None):
    if not key:
        key = "paint:#BBBBBB:0.6"
    mat = blender_materials.material_for_key(key, MAT_CACHE)
    if mat is None:                         # never leave a surface white by accident
        mat = blender_materials.material_for_key("paint:#FF00FF:0.5", MAT_CACHE)
    return mat


def box(name: str, lo, hi, col, key: str | None = None, props: dict | None = None):
    """Axis-aligned box with REAL vertex coordinates (scale 1), so object-space
    and world-space patterns are both metric."""
    lo, hi = Vector(lo), Vector(hi)
    bm = bmesh.new()
    bmesh.ops.create_cube(bm, size=1.0)
    for v in bm.verts:
        v.co = Vector((lo[i] + (v.co[i] + 0.5) * (hi[i] - lo[i]) for i in range(3)))
    me = bpy.data.meshes.new(name + "_mesh")
    bm.to_mesh(me)
    bm.free()
    obj = bpy.data.objects.new(name, me)
    col.objects.link(obj)
    if key is not None:
        obj.data.materials.append(material(key))
        obj["ci_material"] = key
    for k, v in (props or {}).items():
        obj[k] = v
    return obj


def cylinder(name: str, radius: float, depth: float, centre, col, key: str | None,
             segments: int = 32):
    bm = bmesh.new()
    bmesh.ops.create_cone(bm, cap_ends=True, segments=segments, radius1=radius,
                          radius2=radius, depth=depth)
    me = bpy.data.meshes.new(name + "_mesh")
    bm.to_mesh(me)
    bm.free()
    obj = bpy.data.objects.new(name, me)
    col.objects.link(obj)
    obj.location = centre
    if key:
        obj.data.materials.append(material(key))
        obj["ci_material"] = key
    return obj


# --------------------------------------------------------------------------
# room
# --------------------------------------------------------------------------
def build_room(plan: dict) -> dict:
    room = plan["room"]
    W, D, H = room["width"], room["depth"], room["height"]
    surf = room.get("surfaces") or {}
    col = collection("ROOM")
    t = 0.12
    made = {"walls": 0, "openings": 0, "splashbacks": 0}
    box("floor", (-W / 2 - t, -D / 2 - t, -0.06), (W / 2 + t, D / 2 + t, 0.0), col,
        surf.get("floor"), {"ci_surface": "floor"})
    box("ceiling", (-W / 2 - t, -D / 2 - t, H), (W / 2 + t, D / 2 + t, H + 0.06), col,
        surf.get("ceiling"), {"ci_surface": "ceiling"})
    openings = room.get("openings") or []
    for wall in video_layout.WALLS:
        seg = video_layout.wall_segments(wall, W, D, H, openings, t)
        for k, (lo, hi) in enumerate(seg["solids"]):
            box(f"wall_{wall}_{k}", lo, hi, col, surf.get("walls"), {"ci_surface": f"wall_{wall}"})
            made["walls"] += 1
        for hole in seg["holes"]:
            build_opening(hole, seg, col, plan)
            made["openings"] += 1
    sk = room.get("skirting")
    if sk:                                       # a thin board along every wall, broken at door openings
        h, d = float(sk.get("height", 0.04)), float(sk.get("depth", 0.012))
        doors = [o for o in openings if o.get("kind") == "door" and o.get("sill_m", 0.0) < 0.05]
        for wall in video_layout.WALLS:
            if (room.get("walls_observed") or {}).get(wall) == "unobserved":
                continue
            L = W if wall in ("north", "south") else D
            spans = [(0.0, L)]
            for o in (o for o in doors if o["wall"] == wall):
                a, b = o["offset_m"], o["offset_m"] + o["width_m"]
                spans = [s_ for sp in spans for s_ in ((sp[0], min(sp[1], a)), (max(sp[0], b), sp[1])) if s_[1] - s_[0] > 0.01]
            for j, (a, b) in enumerate(spans):
                if wall == "north":
                    lo, hi = (-W / 2 + a, D / 2 - d, 0.0), (-W / 2 + b, D / 2, h)
                elif wall == "south":
                    lo, hi = (W / 2 - b, -D / 2, 0.0), (W / 2 - a, -D / 2 + d, h)
                elif wall == "east":
                    lo, hi = (W / 2 - d, D / 2 - b, 0.0), (W / 2, D / 2 - a, h)
                else:
                    lo, hi = (-W / 2, -D / 2 + a, 0.0), (-W / 2 + d, -D / 2 + b, h)
                box(f"skirting_{wall}_{j}", lo, hi, col, sk.get("material"), {"ci_surface": "skirting"})
    # architecture the wall-plane fit cannot express, measured by hand from the
    # point cloud and the footage (measured/room_features.json): a pier in a
    # corner, a stepped wall, a painted band, a threshold strip, sockets
    for f in room.get("features") or []:
        box(f["name"], f["lo"], f["hi"], col, f["material"], {"ci_surface": f.get("surface", "feature"),
                                                               "ci_note": f.get("why", "")[:200]})
        made["features"] = made.get("features", 0) + 1
    for k, sb in enumerate(room.get("splashbacks") or []):
        plane = {"north": D / 2, "south": -D / 2, "east": W / 2, "west": -W / 2}[sb["wall"]]
        inward = -1.0 if sb["wall"] in ("north", "east") else 1.0
        n0, n1 = sorted((plane, plane + inward * 0.006))
        if sb["axis"] == "x":
            lo, hi = (sb["from"], n0, sb["z0"]), (sb["to"], n1, sb["z1"])
        else:
            lo, hi = (n0, sb["from"], sb["z0"]), (n1, sb["to"], sb["z1"])
        box(f"splashback_{k}", lo, hi, col, sb["material"], {"ci_surface": "splashback"})
        made["splashbacks"] += 1
    return made


def build_opening(hole: dict, seg: dict, col, plan: dict) -> None:
    """A window gets a dark frame and a glass pane; a door gets a leaf."""
    op, lo, hi = hole["opening"], list(hole["lo"]), list(hole["hi"])
    n_axis = 1 if seg["axis"] == "x" else 0
    u_axis = 0 if seg["axis"] == "x" else 1
    frame_key = "metal:#2A2B2D:0.4"
    f = 0.05                                       # frame member width
    mid = (lo[n_axis] + hi[n_axis]) / 2
    if op["kind"] == "window":
        pane_lo, pane_hi = list(lo), list(hi)
        pane_lo[n_axis], pane_hi[n_axis] = mid - 0.004, mid + 0.004
        night = plan.get("world", {}).get("time_of_day") == "night"
        box(f"{op['id']}_glass", pane_lo, pane_hi, col,
            "glass:#20262C:0.02" if night else "glass:#FFFFFF:0.02",
            {"ci_opening": op["id"]})
        for side in range(4):                       # left, right, bottom, top members
            a, b = list(lo), list(hi)
            a[n_axis], b[n_axis] = mid - 0.03, mid + 0.03
            if side == 0:
                b[u_axis] = a[u_axis] + f
            elif side == 1:
                a[u_axis] = b[u_axis] - f
            elif side == 2:
                b[2] = a[2] + f
            else:
                a[2] = b[2] - f
            box(f"{op['id']}_frame_{side}", a, b, col, frame_key, {"ci_opening": op["id"]})
    else:
        leaf_lo, leaf_hi = list(lo), list(hi)
        leaf_lo[n_axis], leaf_hi[n_axis] = mid - 0.02, mid + 0.02
        box(f"{op['id']}_leaf", leaf_lo, leaf_hi, col, plan["room"].get("door_material")
            or "paint:#E9E7E2:0.5", {"ci_opening": op["id"]})


# --------------------------------------------------------------------------
# lights
# --------------------------------------------------------------------------
def build_lights(plan: dict) -> int:
    col = collection("LIGHTS")
    n = 0
    for L in plan.get("lights") or []:
        hz = L["housing"]
        x, y, z = L["location"]
        r = hz["diameter"] / 2
        if L["kind"] == "pendant":
            top = hz["top_z"]
            bottom = top - hz["height"]
            cylinder(f"{L['id']}_housing", r, hz["height"], (x, y, (top + bottom) / 2), col,
                     f"paint:{hz['colour_hex']}:0.35" if hz.get("material") != "metal"
                     else f"metal:{hz['colour_hex']}:0.3")
            cylinder(f"{L['id']}_diffuser", r * 0.9, 0.004, (x, y, bottom - 0.002), col,
                     L["emitter_key"])
            if hz.get("cable") and hz["ceiling_z"] - top > 0.01:
                length = hz["ceiling_z"] - top
                cylinder(f"{L['id']}_cable", 0.0018, length, (x, y, top + length / 2), col,
                         "rubber:#111111:0.6", segments=8)
                cylinder(f"{L['id']}_canopy", 0.045, 0.02, (x, y, hz["ceiling_z"] - 0.01), col,
                         f"paint:{hz['colour_hex']}:0.4")
            z_light = bottom - 0.01
        else:
            cylinder(f"{L['id']}_diffuser", r, 0.01, (x, y, hz["ceiling_z"] - 0.005), col,
                     L["emitter_key"])
            z_light = hz["ceiling_z"] - 0.02
        data = bpy.data.lights.new(L["id"], L["type"])
        data.energy = float(L["energy_w"])
        if hasattr(data, "use_temperature"):        # Blender 5.x: physical colour temperature
            data.use_temperature = True
            data.temperature = float(L["temperature_k"])
            data.color = (1.0, 1.0, 1.0)
        else:
            data.color = tuple(L["color_rgb"])
        data.shadow_soft_size = float(L["shadow_soft_size"])
        if L["type"] == "SPOT":
            data.spot_size = math.radians(float(L["spot_size_deg"]))
            data.spot_blend = float(L["spot_blend"])
        elif L["type"] == "AREA":
            data.shape = "DISK"
            data.size = float(L["area_size"][0])
        obj = bpy.data.objects.new(L["id"], data)
        col.objects.link(obj)
        obj.location = (x, y, z_light)               # lights point down -Z by default
        obj["ci_fixture"] = str(L.get("fixture"))
        obj["ci_light"] = json.dumps(L, default=float)
        n += 1
    return n


def build_world(plan: dict) -> None:
    w = plan.get("world") or {}
    world = bpy.data.worlds.new("VideoWorld")
    world.use_nodes = True
    bg = next((n for n in world.node_tree.nodes if n.type == "BACKGROUND"), None)
    if bg is not None:
        bg.inputs["Color"].default_value = (*w.get("color", (0.004, 0.0045, 0.006)), 1.0)
        bg.inputs["Strength"].default_value = float(w.get("strength", 1.0))
    bpy.context.scene.world = world


# --------------------------------------------------------------------------
# objects
# --------------------------------------------------------------------------
def import_forged(entry: dict, col) -> dict | None:
    blend = Path(entry.get("blend") or "")
    if not blend.is_file():
        return None
    with bpy.data.libraries.load(str(blend), link=False) as (src, dst):
        dst.objects = list(src.objects)
    loaded = [o for o in dst.objects if o is not None]
    root = next((o for o in loaded if o.name.startswith("ROOT_")), None) or next(
        (o for o in loaded if o.parent is None and o.type == "EMPTY"), None)
    if root is None:
        for o in loaded:
            bpy.data.objects.remove(o, do_unlink=True)
        return None

    def descendants(node):
        out = []
        for child in node.children:
            out.append(child)
            out.extend(descendants(child))
        return out

    keep = [root] + [o for o in descendants(root) if o.type in ("MESH", "EMPTY")]
    for o in loaded:                                  # studio rig, ground, cameras
        if o not in keep:
            bpy.data.objects.remove(o, do_unlink=True)
    for o in keep:
        col.objects.link(o)
    blender_articulation.rebind_drivers(keep)
    z = entry["z"]
    if entry.get("mount") == "ceiling":
        z = entry.get("ceiling_z", z)          # forged ceiling pieces hang from their origin
    root.location = (entry["x"], entry["y"], z)
    root.rotation_euler = (0.0, 0.0, math.radians(entry["yaw_deg"]))
    if entry.get("fit_dims"):
        fit_to_dims(root, keep, entry)
    root["ci_object"] = entry["id"]
    root["ci_source"] = ("forged" if entry.get("forge_status") != "best_effort"
                         else "forged (passed the spec checks; visual review not passed)")
    return {"id": entry["id"], "mode": entry.get("forge_status") or "forged", "parts": len(keep) - 1,
            "joints": sum(1 for o in keep if o.get("ci_joint"))}


def fit_to_dims(root, parts, entry: dict) -> None:
    """Scale a forged asset so its closed-pose bounding box is the measured box:
    width/depth/height along its local x/y/z, footprint centred on (x, y),
    bottom at z. Measurements come from the video reconstruction, the asset's
    proportions from the forge; this keeps the measured placement exact."""
    yaw = math.radians(entry["yaw_deg"])
    root.location, root.rotation_euler, root.scale = (0.0, 0.0, 0.0), (0.0, 0.0, 0.0), (1.0, 1.0, 1.0)
    bpy.context.view_layer.update()
    inv = root.matrix_world.inverted()
    lo, hi = [1e9] * 3, [-1e9] * 3
    for o in parts:
        # a power cord hangs outside the measured rigid body: it must not shrink it
        if o.type != "MESH" or sim2real.is_flexible(o.name):
            continue
        for v in o.bound_box:
            q = inv @ o.matrix_world @ Vector(v)
            for i in range(3):
                lo[i], hi[i] = min(lo[i], q[i]), max(hi[i], q[i])
    if lo[0] > hi[0]:
        return
    dims = entry["dims"]
    sc = [dims[i] / (hi[i] - lo[i]) if hi[i] - lo[i] > 1e-4 else 1.0 for i in range(3)]
    if any(o.get("ci_joint") for o in parts) and max(sc) / max(min(sc), 1e-6) > 1.03:
        # a hinged door under a non-uniformly scaled parent shears as it opens and
        # no longer matches its joint record (sim2real): scale uniformly, and say
        # the asset should be forged at the measured size instead
        g = (sc[0] * sc[1] * sc[2]) ** (1.0 / 3.0)
        root["ci_fit_note"] = (f"articulated: uniform scale {g:.3f} instead of {[round(v, 3) for v in sc]}; "
                               f"forge it at the measured size")
        sc = [g, g, g]
    root.scale = sc
    cx, cy, bz = (lo[0] + hi[0]) / 2 * sc[0], (lo[1] + hi[1]) / 2 * sc[1], lo[2] * sc[2]
    rx, ry = cx * math.cos(yaw) - cy * math.sin(yaw), cx * math.sin(yaw) + cy * math.cos(yaw)
    root.location = (entry["x"] - rx, entry["y"] - ry, entry["z"] - bz)
    root.rotation_euler = (0.0, 0.0, yaw)
    root["ci_fit_scale"] = [round(v, 4) for v in sc]


def placeholder(entry: dict, col) -> dict:
    w, d, h = entry["dims"]
    root = bpy.data.objects.new(f"ROOT_{entry['id']}", None)
    col.objects.link(root)
    body = box(f"PH_{entry['id']}", (-w / 2, -d / 2, 0.0), (w / 2, d / 2, h), col,
               entry.get("placeholder_material"),
               {"ci_placeholder": True, "ci_object": entry["id"]})
    body.parent = root
    root.location = (entry["x"], entry["y"], entry["z"])
    root.rotation_euler = (0.0, 0.0, math.radians(entry["yaw_deg"]))
    root["ci_object"] = entry["id"]
    root["ci_source"] = "placeholder (not forged yet)"
    return {"id": entry["id"], "mode": "placeholder"}


def _subtree_meshes(root):
    out, stack = [], list(root.children)
    while stack:
        o = stack.pop()
        stack.extend(o.children)
        if o.type == "MESH":
            out.append(o)
    return out


def _world_aabb(objs):
    lo, hi = [1e9] * 3, [-1e9] * 3
    for o in objs:
        for v in o.bound_box:
            q = o.matrix_world @ Vector(v)
            for i in range(3):
                lo[i], hi[i] = min(lo[i], q[i]), max(hi[i], q[i])
    return lo, hi


def apply_albedo_scales(plan: dict) -> list[dict]:
    """Per-object albedo factors from the same-camera photometric check
    (geometry/photometric_check.py): the object's materials are copied (forged
    materials can be shared) and their base colour multiplied."""
    roots = {o.get("ci_object"): o for o in bpy.data.objects if o.get("ci_object") and o.parent is None}
    done = []
    for e in plan.get("objects") or []:
        f = e.get("albedo_scale")
        if not f or e["id"] not in roots:
            continue
        copies = {}
        for m in _subtree_meshes(roots[e["id"]]):
            for slot in m.material_slots:
                mat = slot.material
                if mat is None or not mat.use_nodes or blender_materials._find_bsdf(mat.node_tree) is None:
                    continue
                if mat.name not in copies:
                    copies[mat.name] = mat.copy()
                    _scale_base_colour(copies[mat.name], float(f))
                slot.material = copies[mat.name]
        done.append({"id": e["id"], "factor": f, "materials": len(copies)})
    return done


def _probe_points(objs):
    """27 points per mesh (3x3x3 over its own box), in world space."""
    pts = []
    for o in objs:
        bb = [Vector(v) for v in o.bound_box]
        lo = Vector((min(v.x for v in bb), min(v.y for v in bb), min(v.z for v in bb)))
        hi = Vector((max(v.x for v in bb), max(v.y for v in bb), max(v.z for v in bb)))
        for i in (0.02, 0.5, 0.98):
            for j in (0.02, 0.5, 0.98):
                for k in (0.02, 0.5, 0.98):
                    pts.append(o.matrix_world @ Vector((lo.x + i * (hi.x - lo.x), lo.y + j * (hi.y - lo.y),
                                                        lo.z + k * (hi.z - lo.z))))
    return pts


def settle_off_walls(plan: dict, max_shift: float = 0.05) -> list[dict]:
    """An object whose joint cannot open because the wall behind it is in the way
    (a bin's rear-hinged lid) is moved forward, 1 cm at a time, up to `max_shift`
    -- nobody pushes a bin into the skirting -- until the joint opens at least
    half way, or it stops helping."""
    moved = []
    roots = {o.get("ci_object"): o for o in bpy.data.objects if o.get("ci_object") and o.parent is None}
    for oid, r in roots.items():
        joints = [o for o in _subtree_meshes_and_empties(r) if o.get("ci_joint") and "open" in o.keys()
                  and json.loads(o["ci_joint"]).get("type") == "revolute"]
        if not joints:
            continue
        fwd = (r.matrix_world.to_3x3() @ Vector((0.0, -1.0, 0.0))).normalized()
        fwd.z = 0.0
        base = r.location.copy()
        best = (_open_fraction(plan, oid, joints), 0.0)
        if best[0] >= 0.5:
            continue
        for k in range(1, int(round(max_shift * 100)) + 1):
            r.location = base + fwd * (k / 100.0)
            bpy.context.view_layer.update()
            f = _open_fraction(plan, oid, joints)
            if f > best[0] + 1e-6:
                best = (f, k / 100.0)
            if f >= 0.5:
                break
        r.location = base + fwd * best[1]
        bpy.context.view_layer.update()
        if best[1] > 0:
            r["ci_wall_standoff_m"] = best[1]
            moved.append({"object": oid, "shift_m": best[1], "joint_open_fraction": best[0]})
    return moved


def _open_fraction(plan: dict, oid: str, joints: list) -> float:
    """Smallest collision-free opening fraction over `joints` (wall/room only)."""
    W, D = plan["room"]["width"], plan["room"]["depth"]
    worst = 1.0
    for j in joints:
        moving = _subtree_meshes(j)
        free = 1.0
        for t in [x / 20 for x in range(1, 21)]:
            j["open"] = t
            j.update_tag()
            bpy.context.view_layer.update()
            if any(abs(q.x) > W / 2 + 0.005 or abs(q.y) > D / 2 + 0.005 for q in _probe_points(moving)):
                free = t - 0.05
                break
        j["open"] = 0.0
        j.update_tag()
        worst = min(worst, free)
    bpy.context.view_layer.update()
    return worst


def limit_joints_by_environment(plan: dict) -> list[dict]:
    """Open every joint step by step; where its moving parts first meet a wall or
    another object, that is its real limit (a bin lid against the wall behind
    it). The driver, the limit constraint and the joint record are scaled to
    the collision-free fraction, so `open = 1` is the furthest it really goes."""
    import re as _re
    room = plan["room"]
    W, D = room["width"], room["depth"]
    roots = {o.get("ci_object"): o for o in bpy.data.objects if o.get("ci_object") and o.parent is None}
    boxes = []                                    # (object id, mesh name, lo, hi) of every static mesh
    for oid, r in roots.items():
        for m in _subtree_meshes(r):
            lo, hi = _world_aabb([m])
            boxes.append((oid, m, lo, hi))
    done = []
    for oid, r in roots.items():
        for j in [o for o in _subtree_meshes_and_empties(r) if o.get("ci_joint") and "open" in o.keys()]:
            rec = json.loads(j["ci_joint"])
            if rec.get("type") not in ("revolute", "prismatic"):
                continue
            moving = [o for o in _subtree_meshes(j)]
            if not moving:
                continue
            own = {m.name for m in _subtree_meshes(r)}
            entry = next((e for e in plan.get("objects") or [] if e["id"] == oid), {})
            # a built-in lives inside the volume carved out of its support: the
            # carcass's box still covers the hole, so it is not an obstacle
            skip = {entry.get("on")} if entry.get("mount") == "built_in" else set()

            def collides():
                bpy.context.view_layer.update()
                for q in _probe_points(moving):
                    if abs(q.x) > W / 2 + 0.005 or abs(q.y) > D / 2 + 0.005 or q.z < -0.005:
                        return "wall/floor"
                    for bo, bm, lo, hi in boxes:
                        if bm.name in own or bo == oid or bo in skip:
                            continue
                        if all(lo[i] + 0.01 < q[i] < hi[i] - 0.01 for i in range(3)):
                            return bo
                return None
            free, hit = 1.0, None
            for t in [x / 20 for x in range(1, 21)]:
                j["open"] = t
                j.update_tag()
                hit = collides()
                if hit:
                    free = round(max(0.0, t - 0.05), 2)
                    break
            j["open"] = 0.0
            j.update_tag()
            bpy.context.view_layer.update()
            if free >= 1.0:
                continue
            ad = j.animation_data
            for fc in (ad.drivers if ad else []):
                expr = fc.driver.expression
                m = _re.search(r"o\*(-?[0-9.]+)$", expr)
                if m:
                    fc.driver.expression = expr[:m.start(1)] + f"{float(m.group(1)) * free:.6f}"
            for c in j.constraints:
                if c.name == "ci_joint_range":
                    for attr in ("min_x", "min_y", "min_z", "max_x", "max_y", "max_z"):
                        if hasattr(c, attr) and attr.startswith(("min", "max")) and rec.get("type") == "revolute":
                            setattr(c, attr, getattr(c, attr) * free)
            for k in ("open_value", "upper", "lower", "range"):
                if isinstance(rec.get(k), (int, float)):
                    rec[k] = rec[k] * free
            rec["environment_limit"] = {"fraction": free, "blocked_by": hit}
            j["ci_joint"] = json.dumps(rec, ensure_ascii=False, default=float)
            j["ci_env_limit"] = free
            done.append({"object": oid, "joint": j.name, "fraction": free, "blocked_by": hit})
    return done


def _subtree_meshes_and_empties(root):
    out, stack = [], list(root.children)
    while stack:
        o = stack.pop()
        stack.extend(o.children)
        out.append(o)
    return out


def carve_built_ins(plan: dict) -> list[dict]:
    """A built-in (sink, hob) occupies a volume of its support: forged
    carcasses are solid blocks, so without this the sink bowl is buried in the
    base cabinet. A hidden cutter box (the built-in's footprint, from its bottom
    to just above its rim) is subtracted from every support part it touches,
    as a live Boolean modifier."""
    bpy.context.view_layer.update()
    roots = {o.get("ci_object"): o for o in bpy.data.objects if o.get("ci_object") and o.parent is None}
    col = collection("CUTTERS")
    done = []
    for e in plan.get("objects") or []:
        if e.get("mount") != "built_in" or not e.get("on"):
            continue
        obj, sup = roots.get(e["id"]), roots.get(e["on"])
        if obj is None or sup is None:
            continue
        lo, hi = _world_aabb(_subtree_meshes(obj))
        if lo[0] > hi[0]:
            continue
        # from just below the bottom to well above the rim: an undermount bowl
        # hangs under the worktop, whose full thickness must be cut. The worktop
        # keeps a 4 mm lip over the bowl's wall; everything else is cut to the bowl
        clo, chi = (lo[0] + 0.004, lo[1] + 0.004, lo[2] - 0.004), (hi[0] - 0.004, hi[1] - 0.004, hi[2] + 0.08)
        cutter = box(f"CUT_{e['id']}", clo, chi, col, None, {"ci_cutter_for": e["id"]})
        flush = box(f"CUT_{e['id']}_body", (lo[0] - 0.001, lo[1] - 0.001, lo[2] - 0.004),
                    (hi[0] + 0.001, hi[1] + 0.001, hi[2] + 0.004), col, None, {"ci_cutter_for": e["id"]})
        flush.hide_render = True
        flush.display_type = "WIRE"
        cutter.hide_render = True
        cutter.display_type = "WIRE"
        cut = []
        for m in _subtree_meshes(sup):
            a, b = _world_aabb([m])
            if all(a[i] < chi[i] and b[i] > clo[i] for i in range(3)):
                mod = m.modifiers.new(f"carve_{e['id']}", "BOOLEAN")
                mod.operation = "DIFFERENCE"
                mod.object = cutter if "countertop" in m.name else flush
                mod.solver = "EXACT"
                cut.append(m.name)
        cutter.hide_set(True)
        flush.hide_set(True)
        done.append({"built_in": e["id"], "support": e["on"], "cut_parts": cut})
    return done


def build_objects(plan: dict) -> list[dict]:
    col = collection("OBJECTS")
    out = []
    for entry in plan.get("objects") or []:
        res = import_forged(entry, col) or placeholder(entry, col)
        out.append(res)
    return out


# --------------------------------------------------------------------------
# camera, render, calibration
# --------------------------------------------------------------------------
def camera(view: dict, col):
    data = bpy.data.cameras.new(view["id"])
    data.lens = float(view.get("lens_mm", 24.0))
    obj = bpy.data.objects.new("CAM_" + view["id"], data)
    col.objects.link(obj)
    if "matrix" in view:                       # a recovered video camera: pose + pinhole intrinsics
        from mathutils import Matrix
        R = Matrix(view["matrix"])
        M = R.to_4x4()
        M.translation = Vector(view["location"])
        obj.matrix_world = M
        w, h = view["resolution"]
        data.sensor_fit = "VERTICAL" if h >= w else "HORIZONTAL"
        if data.sensor_fit == "VERTICAL":
            data.sensor_height = 24.0
            data.lens = view["fy"] * data.sensor_height / h
        else:
            data.sensor_width = 36.0
            data.lens = view["fx"] * data.sensor_width / w
        big = max(w, h)
        data.shift_x = -(view["cx"] - w / 2) / big
        data.shift_y = (view["cy"] - h / 2) / big
        data.clip_start = 0.02
        obj["ci_source_frame"] = view.get("source_frame", "")
        return obj
    obj.location = view["location"]
    direction = Vector(view["look_at"]) - Vector(view["location"])
    obj.rotation_euler = direction.to_track_quat("-Z", "Y").to_euler()
    return obj


def setup_render(plan: dict, samples: int) -> None:
    scene = bpy.context.scene
    cam = plan.get("camera") or {}
    plan["_device"] = blender_render.configure(scene)
    scene.cycles.samples = samples
    scene.cycles.use_denoising = True
    res = cam.get("resolution") or [720, 1280]
    scene.render.resolution_x, scene.render.resolution_y = int(res[0]), int(res[1])
    scene.render.image_settings.file_format = "PNG"
    vs = scene.view_settings
    vs.view_transform = "AgX"
    vs.exposure = float(cam.get("exposure", 0.0))
    if hasattr(vs, "use_white_balance") and cam.get("white_balance_k"):
        vs.use_white_balance = True
        vs.white_balance_temperature = float(cam["white_balance_k"])


def render(cam_obj, path: Path) -> str:
    scene = bpy.context.scene
    scene.camera = cam_obj
    scene.render.filepath = str(path)
    bpy.ops.render.render(write_still=True)
    return str(path)


def median_srgb(path: Path) -> float:
    img = bpy.data.images.load(str(path))
    px = list(img.pixels[:])
    lum = sorted(0.2126 * px[i] + 0.7152 * px[i + 1] + 0.0722 * px[i + 2]
                 for i in range(0, len(px), 4 * 7))            # every 7th pixel
    bpy.data.images.remove(img)
    return lum[len(lum) // 2] if lum else 0.0


def _lin(c: float) -> float:
    return c / 12.92 if c <= 0.04045 else ((c + 0.055) / 1.055) ** 2.4


def calibrate(plan: dict, cam_obj, out_dir: Path, rounds: int = 3, tag: str = "") -> list[dict]:
    """Expose the render the way the phone exposed the video: on the white wall.

    The phone auto-exposes; the reference both sides share is the plain white
    wall. Its display value in the keyframe this camera imitates is the
    target; the render's wall pixels (from a surface-ID mask) are moved onto
    it. Whole-frame medians were tried first and chased the floor instead:
    every albedo correction of the floor moved the exposure of the walls.
    """
    import numpy as np
    cam_cfg = plan.get("camera") or {}
    target = cam_cfg.get("wall_target_srgb") or cam_cfg.get("calibration_target_srgb")
    if not target:
        return []
    scene = bpy.context.scene
    keep = (scene.cycles.samples, scene.render.resolution_percentage)
    scene.cycles.samples, scene.render.resolution_percentage = 16, 35
    groups = _surface_groups()
    log = []
    for k in range(rounds):
        colours = _id_render(cam_obj, groups, out_dir / f"_calib_id{tag}_{k}.png")
        path = out_dir / f"_calib{tag}_{k}.png"
        render(cam_obj, path)
        px = _pixels(path)
        got = None
        if "walls" in colours:
            ids = _pixels(out_dir / f"_calib_id{tag}_{k}.png")
            c = np.array(colours["walls"], dtype=np.float32)
            enc = np.where(c <= 0.0031308, c * 12.92, 1.055 * c ** (1 / 2.4) - 0.055)
            mask = np.all(np.abs(ids - enc) < 0.03, axis=-1)
            if mask.sum() > 200:
                got = float(np.median((px @ np.array([0.2126, 0.7152, 0.0722],
                                                     dtype=np.float32))[mask]))
        basis = "walls"
        if got is None:
            got, basis = median_srgb(path), "frame median"
        step = 0.0 if got <= 1e-4 else max(-3.0, min(3.0, math.log2(_lin(target) / max(_lin(got), 1e-5))))
        scene.view_settings.exposure += step
        log.append({"round": k, "basis": basis, "render_srgb": round(got, 3), "target": target,
                    "exposure": round(scene.view_settings.exposure, 3)})
        if abs(step) < 0.08:
            break
    scene.cycles.samples, scene.render.resolution_percentage = keep
    return log


def _surface_groups() -> dict:
    groups: dict = {}
    for obj in bpy.data.objects:
        s = obj.get("ci_surface")
        if not s:
            continue
        g = "walls" if str(s).startswith("wall_") else str(s)
        groups.setdefault(g, []).append(obj)
    return groups


def _pixels(path: Path):
    import numpy as np
    img = bpy.data.images.load(str(path))
    w, h = img.size
    px = np.array(img.pixels[:], dtype=np.float32).reshape(h, w, 4)[..., :3]
    bpy.data.images.remove(img)
    return px


def _id_render(cam_obj, groups: dict, path: Path) -> dict:
    """Render every surface group as a flat emission colour (a mask image)."""
    scene = bpy.context.scene
    saved = {o.name: list(o.data.materials) for g in groups.values() for o in g}
    vs = scene.view_settings
    keep = (vs.view_transform, vs.exposure, getattr(vs, "use_white_balance", False),
            scene.cycles.samples, scene.cycles.use_denoising)
    colours = {}
    for k, (g, objs) in enumerate(sorted(groups.items())):
        col = ((k * 67 % 255) / 255.0, (k * 131 % 255) / 255.0 + 0.1, 0.9 - k * 0.11)
        colours[g] = tuple(max(0.0, min(1.0, c)) for c in col)
        mat = bpy.data.materials.new(f"_id_{g}")
        mat.use_nodes = True
        nt = mat.node_tree
        nt.nodes.clear()
        out = nt.nodes.new("ShaderNodeOutputMaterial")
        em = nt.nodes.new("ShaderNodeEmission")
        em.inputs["Color"].default_value = (*colours[g], 1.0)
        nt.links.new(em.outputs[0], out.inputs["Surface"])
        for o in objs:
            o.data.materials.clear()
            o.data.materials.append(mat)
    vs.view_transform, vs.exposure = "Standard", 0.0
    if hasattr(vs, "use_white_balance"):
        vs.use_white_balance = False
    scene.cycles.samples, scene.cycles.use_denoising = 1, False
    render(cam_obj, path)
    for name, mats in saved.items():
        o = bpy.data.objects[name]
        o.data.materials.clear()
        for m in mats:
            o.data.materials.append(m)
    (vs.view_transform, vs.exposure, wb, scene.cycles.samples, scene.cycles.use_denoising) = keep
    if hasattr(vs, "use_white_balance"):
        vs.use_white_balance = wb
    return colours


def _scale_base_colour(mat, factor: float) -> None:
    """Multiply a material's base colour, whether it is a value or a pattern."""
    bsdf = blender_materials._find_bsdf(mat.node_tree)
    inp = bsdf.inputs["Base Color"]
    if inp.is_linked:
        nt = mat.node_tree
        link = inp.links[0]
        mix = nt.nodes.new("ShaderNodeMixRGB")
        mix.blend_type = "MULTIPLY"
        mix.inputs["Fac"].default_value = 1.0
        mix.inputs["Color2"].default_value = (factor, factor, factor, 1.0)
        nt.links.new(link.from_socket, mix.inputs["Color1"])
        nt.links.new(mix.outputs[0], inp)
    else:
        c = inp.default_value
        inp.default_value = (min(1.0, c[0] * factor), min(1.0, c[1] * factor),
                             min(1.0, c[2] * factor), c[3])
    mat["ci_albedo_scale"] = float(mat.get("ci_albedo_scale", 1.0)) * factor


def _group_ratios(cam_obj, groups: dict, out_dir: Path, tag: str) -> dict:
    """Median LINEAR luminance of each surface group / that of the walls."""
    import numpy as np
    colours = _id_render(cam_obj, groups, out_dir / f"_id_{tag}.png")
    render(cam_obj, out_dir / f"_lin_{tag}.png")
    ids, beauty = _pixels(out_dir / f"_id_{tag}.png"), _pixels(out_dir / f"_lin_{tag}.png")
    unclipped = beauty.max(axis=-1) < 0.98
    lin = np.where(beauty <= 0.04045, beauty / 12.92, ((beauty + 0.055) / 1.055) ** 2.4)
    lum = lin @ np.array([0.2126, 0.7152, 0.0722], dtype=np.float32)
    med = {}
    for g, col in colours.items():
        c = np.array(col, dtype=np.float32)
        enc = np.where(c <= 0.0031308, c * 12.92, 1.055 * c ** (1 / 2.4) - 0.055)
        mask = np.all(np.abs(ids - enc) < 0.03, axis=-1) & unclipped
        if mask.sum() > 200:
            med[g] = float(np.median(lum[mask]))
    if "walls" not in med:
        return {}
    return {g: v / max(med["walls"], 1e-6) for g, v in med.items()}


class _LinearView:
    """Standard view, lower exposure: ratios measured in linear light."""

    def __enter__(self):
        scene = bpy.context.scene
        vs = scene.view_settings
        self.keep = (scene.cycles.samples, scene.render.resolution_percentage,
                     vs.view_transform, vs.exposure)
        scene.cycles.samples, scene.render.resolution_percentage = 24, 40
        # 4 stops under the display exposure: the floor under a pendant must not
        # clip, or it drops out of the ratio (8-bit still resolves the walls)
        vs.view_transform, vs.exposure = "Standard", self.keep[3] - 4.0
        return self

    def __exit__(self, *exc):
        scene = bpy.context.scene
        vs = scene.view_settings
        (scene.cycles.samples, scene.render.resolution_percentage, vs.view_transform,
         vs.exposure) = self.keep
        return False


def calibrate_light_spread(plan: dict, cam_obj, out_dir: Path, rounds: int = 4) -> list[dict]:
    """Widen the beams until the floor/wall balance matches the video.

    The fixture's beam is the model's judgement ("medium"); a 75-degree spot
    lit the floor six times brighter than the walls, while in the video the
    white walls are bright up to the ceiling. Darkening the floor cannot fix
    that -- the walls were lit by the floor's bounce and darkened with it --
    so the light distribution is corrected first, and only then the albedo.
    """
    target = ((plan.get("camera") or {}).get("albedo_targets") or {}).get("floor")
    if not target:
        return []
    groups = _surface_groups()
    lights = [o for o in bpy.data.objects if o.type == "LIGHT" and o.get("ci_fixture")]
    log = []
    with _LinearView():
        for r in range(rounds):
            ratios = _group_ratios(cam_obj, groups, out_dir, f"spread{r}")
            fl = ratios.get("floor")
            if fl is None:
                log.append({"round": r, "note": "floor or walls not visible"})
                break
            entry = {"round": r, "floor_to_wall": round(fl, 3), "target": target}
            if fl <= target * 1.6:
                log.append(entry)
                break
            for o in lights:
                d = o.data
                if d.type == "SPOT" and math.degrees(d.spot_size) < 165:
                    d.spot_size = min(math.radians(170.0), d.spot_size * 1.35)
                    d.spot_blend = min(1.0, d.spot_blend + 0.25)
                elif d.type == "SPOT":
                    d.type = "POINT"
            entry["lights"] = [{"id": o.name, "type": o.data.type,
                                "spot_deg": round(math.degrees(o.data.spot_size), 1)
                                if o.data.type == "SPOT" else None} for o in lights[:1]]
            log.append(entry)
    return log


def calibrate_albedo(plan: dict, cam_obj, out_dir: Path, rounds: int = 2) -> list[dict]:
    """Render-and-compare: surface/white-wall ratios in the render vs the video.

    The video gives, per material, its brightness relative to the white wall
    in the same frame (appearance.py, `ratio_to_white`). Those ratios still
    contain the light falling on the sample: a floor under a pendant reads
    0.8 of the wall although it is grey, because horizontal surfaces catch
    more of a downlight. The renderer reproduces that geometry, so -- once the
    light distribution is right (calibrate_light_spread) -- the albedo is
    nudged until the RENDER shows the same ratios. Ratios are taken in linear
    light (Standard view, exposure below clipping): AgX compresses the bright
    floor and made the ratio move the wrong way when the floor was darkened.
    """
    targets = (plan.get("camera") or {}).get("albedo_targets") or {}
    if not targets:
        return []
    groups = _surface_groups()
    log = []
    with _LinearView():
        for r in range(rounds):
            ratios = _group_ratios(cam_obj, groups, out_dir, f"alb{r}")
            if not ratios:
                log.append({"round": r, "note": "no wall visible from the calibration view"})
                break
            step = {"round": r, "render_ratio": {}, "factor": {}}
            for g, target in targets.items():
                if g == "walls" or g not in ratios or not target:
                    continue
                ratio = ratios[g]
                step["render_ratio"][g] = round(ratio, 3)
                # a fine adjustment: whatever is left beyond +-30 % after the
                # light distribution was corrected is not the material's fault
                f = max(0.7, min(1.4, float(target) / max(ratio, 1e-6)))
                if abs(f - 1.0) > 0.05:
                    for m in {s.material for o in groups[g] for s in o.material_slots
                              if s.material}:
                        _scale_base_colour(m, f)
                    step["factor"][g] = round(f, 3)
            log.append(step)
            if not step["factor"]:
                break
    return log


ID_PALETTE = [(1, 0, 0), (0, 1, 0), (0, 0, 1), (1, 1, 0), (1, 0, 1), (0, 1, 1), (1, .5, 0), (.5, 0, 1),
              (0, .5, 1), (1, 0, .5), (.5, 1, 0), (0, 1, .5), (.5, .5, 1), (1, .5, .5), (.5, 1, .5), (1, 1, .5),
              (.5, 0, 0), (0, .5, 0), (0, 0, .5), (.5, .5, 0), (.5, 0, .5), (0, .5, .5), (1, 1, 1), (.25, .25, .25)]


def object_groups() -> dict:
    """mesh objects per scene object (ROOT_* subtree) and per room surface."""
    groups: dict = {}
    for root in bpy.data.objects:
        oid = root.get("ci_object")
        if oid and root.parent is None:
            stack = list(root.children)
            while stack:
                o = stack.pop()
                stack.extend(o.children)
                if o.type == "MESH":
                    groups.setdefault(str(oid), []).append(o)
    for g, objs in _surface_groups().items():
        groups.setdefault("room:" + g, []).extend(objs)
    return groups


def video_cameras(plan: dict) -> dict:
    """One camera per keyframe at its recovered pose and intrinsics, with the
    video frame packed in as its background image: look through any of them
    (Numpad 0) and the model sits on top of the footage it was measured from."""
    col = collection("VIDEO_CAMERAS")
    out = {}
    for v in plan.get("source_views") or []:
        cam = camera(v, col)
        img_path = v.get("image")
        if img_path and Path(img_path).is_file():
            img = bpy.data.images.load(img_path, check_existing=True)
            img.pack()
            cam.data.show_background_images = True
            bg = cam.data.background_images.new()
            bg.image = img
            bg.alpha = 0.5
            bg.display_depth = "FRONT"
            bg.frame_method = "FIT"
        out[v["source_frame"]] = cam
    return out


def render_source_views(plan: dict, out_dir: Path, ids_only: bool = False) -> dict:
    """RGB from every recovered video camera, then one flat-colour ID pass per
    camera (every scene object and room surface its own colour, all else black)."""
    scene = bpy.context.scene
    src = out_dir / "source"
    src.mkdir(parents=True, exist_ok=True)
    only = set(plan.get("_source_frames") or [])
    made = plan.get("_video_cams") or video_cameras(plan)
    cams = [(v, made[v["source_frame"]]) for v in plan.get("source_views") or []
            if v["source_frame"] in made and (not only or v["source_frame"] in only)]
    keep_res = (scene.render.resolution_x, scene.render.resolution_y, scene.render.resolution_percentage)
    out = {"views": []}
    for v, c in ([] if ids_only else cams):
        scene.render.resolution_x, scene.render.resolution_y = v["resolution"]
        scene.render.resolution_percentage = 100
        render(c, src / f"rgb_{v['source_frame']}.png")
    groups = object_groups()
    colours = {g: ID_PALETTE[k % len(ID_PALETTE)] for k, g in enumerate(sorted(groups))}
    saved = {o.name: list(o.data.materials) for o in bpy.data.objects if o.type == "MESH"}
    black = bpy.data.materials.new("_id_black")
    black.use_nodes = True
    black.node_tree.nodes.clear()
    o_ = black.node_tree.nodes.new("ShaderNodeOutputMaterial")
    e_ = black.node_tree.nodes.new("ShaderNodeEmission")
    e_.inputs["Color"].default_value = (0, 0, 0, 1)
    black.node_tree.links.new(e_.outputs[0], o_.inputs["Surface"])
    member = {o.name: g for g, objs in groups.items() for o in objs}
    mats = {}
    for g, c in colours.items():
        m = bpy.data.materials.new(f"_idobj_{g}")
        m.use_nodes = True
        nt = m.node_tree
        nt.nodes.clear()
        out_n = nt.nodes.new("ShaderNodeOutputMaterial")
        em = nt.nodes.new("ShaderNodeEmission")
        em.inputs["Color"].default_value = (*c, 1.0)
        nt.links.new(em.outputs[0], out_n.inputs["Surface"])
        mats[g] = m
    for o in bpy.data.objects:
        if o.type == "MESH":
            o.data.materials.clear()
            o.data.materials.append(mats[member[o.name]] if o.name in member else black)
    vs = scene.view_settings
    keep = (vs.view_transform, vs.exposure, getattr(vs, "use_white_balance", False), scene.cycles.samples,
            scene.cycles.use_denoising, scene.render.filter_size)
    vs.view_transform, vs.exposure = "Standard", 0.0
    if hasattr(vs, "use_white_balance"):
        vs.use_white_balance = False
    scene.cycles.samples, scene.cycles.use_denoising, scene.render.filter_size = 1, False, 0.01
    hidden = [o for o in bpy.data.objects if o.type == "LIGHT" and not o.hide_render]
    for o in hidden:
        o.hide_render = True
    for v, c in cams:
        scene.render.resolution_x, scene.render.resolution_y = v["resolution"]
        render(c, src / f"id_{v['source_frame']}.png")
        out["views"].append(v["source_frame"])
    for o in hidden:
        o.hide_render = False
    for name, ms in saved.items():
        o = bpy.data.objects[name]
        o.data.materials.clear()
        for m in ms:
            o.data.materials.append(m)
    (vs.view_transform, vs.exposure, wb, scene.cycles.samples, scene.cycles.use_denoising,
     scene.render.filter_size) = keep
    if hasattr(vs, "use_white_balance"):
        vs.use_white_balance = wb
    scene.render.resolution_x, scene.render.resolution_y, scene.render.resolution_percentage = keep_res
    out["id_colours"] = {g: list(c) for g, c in colours.items()}
    (src / "id_colours.json").write_text(json.dumps(out, indent=1), encoding="utf-8")
    return out


def set_open(value: float) -> list[str]:
    """Drive every rigged joint to `value` (0 closed .. 1 fully open)."""
    names = []
    for obj in bpy.data.objects:
        if obj.get("ci_joint") and "open" in obj.keys():
            obj["open"] = value
            obj.update_tag()
            names.append(obj.name)
    bpy.context.view_layer.update()
    return names


def render_open(cams: dict, out_dir: Path) -> list[str]:
    """The same views with every door, drawer, lid and tray opened -- the
    structure read from the video, shown working. The saved .blend stays
    closed; this only changes the renders."""
    opened = set_open(0.85)
    paths = [render(c, out_dir / f"render_{vid}_open.png") for vid, c in cams.items()]
    set_open(0.0)
    print(f"[scene] opened {len(opened)} joints for the open renders", flush=True)
    return paths


def main() -> int:
    argv = sys.argv[sys.argv.index("--") + 1:] if "--" in sys.argv else []
    plan_path = Path(argv[0])
    samples = int(argv[argv.index("--samples") + 1]) if "--samples" in argv else 64
    plan = json.loads(plan_path.read_text(encoding="utf-8"))
    out_dir = plan_path.parent / "scene"
    out_dir.mkdir(parents=True, exist_ok=True)

    for obj in list(bpy.data.objects):
        bpy.data.objects.remove(obj, do_unlink=True)
    report = {"room": build_room(plan), "lights": build_lights(plan)}
    build_world(plan)
    report["objects"] = build_objects(plan)
    report["carved"] = carve_built_ins(plan)
    report["albedo_scaled"] = apply_albedo_scales(plan)
    report["wall_standoffs"] = settle_off_walls(plan)
    report["joint_limits"] = limit_joints_by_environment(plan)
    setup_render(plan, samples)
    cams = {v["id"]: camera(v, collection("CAMERAS")) for v in plan.get("views") or []}
    if "--calibrate" in argv and cams:
        cal = (plan.get("camera") or {}).get("calibration_view") or next(iter(cams))
        cam = cams.get(cal) or next(iter(cams.values()))
        # exposure first (so the masks see a sane image), then albedo, then
        # exposure again for the corrected materials
        report["calibration"] = calibrate(plan, cam, out_dir, tag="a")
        report["light_calibration"] = calibrate_light_spread(plan, cam, out_dir)
        report["albedo_calibration"] = calibrate_albedo(plan, cam, out_dir)
        report["calibration"] += calibrate(plan, cam, out_dir, rounds=5, tag="b")
    if plan.get("source_views"):
        plan["_video_cams"] = video_cameras(plan)
        report["video_cameras"] = len(plan["_video_cams"])
        first = plan["_video_cams"].get("F00") or next(iter(plan["_video_cams"].values()), None)
        if first is not None:
            bpy.context.scene.camera = first        # open the file, Numpad 0: model over the footage
    report["exposure"] = bpy.context.scene.view_settings.exposure
    report["device"] = plan.get("_device")
    blend = out_dir / "scene.blend"
    bpy.ops.wm.save_as_mainfile(filepath=str(blend))
    report["blend"] = str(blend)
    if "--percentage" in argv:
        bpy.context.scene.render.resolution_percentage = int(argv[argv.index("--percentage") + 1])
    if "--source-frames" in argv:
        plan["_source_frames"] = argv[argv.index("--source-frames") + 1].split(",")
    if ("--source-views" in argv or "--source-ids" in argv) and plan.get("source_views"):
        # a check, not a deliverable: ID masks from the video's own cameras
        report["source_views"] = render_source_views(plan, out_dir, ids_only="--source-ids" in argv)
    if "--no-render" not in argv:
        report["renders"] = [render(c, out_dir / f"render_{vid}.png") for vid, c in cams.items()]
        if "--open-renders" in argv:
            report["renders_open"] = render_open(cams, out_dir)
    (out_dir / "scene_build.json").write_text(json.dumps(report, indent=2, default=str),
                                              encoding="utf-8")
    print("SCENE_BUILD=" + json.dumps({k: report[k] for k in ("room", "lights", "exposure")}
                                      | {"objects": len(report["objects"])}, default=str))
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
