"""Turn derived joints into a Blender rig you can open (Blender side, bpy only).

For every joint from articulation.derive_all():

    ROOT_<oid>                      the object's root (moving it moves all)
      JOINT_<joint id>              an Empty at the derived hinge point
        door_left, door_left_handle the moving parts, re-parented, world pose kept

The Empty carries a custom property `open` in [0, 1] and a driver that maps it
onto the joint: rotation about the hinge axis for revolute / continuous
joints, translation along the slide axis for prismatic / press joints. The
driver is a "simple expression" (`o*-1.919862`), which Blender evaluates
without Python, so it works with auto-run scripts disabled -- checked on
Blender 5.2.4. A Limit constraint documents the range for anyone who removes
the driver and turns the Empty by hand.

A part with two motions (a mixer lever that lifts and swings) gets a CHAIN:
the second joint's Empty is parented under the first, so both work at once.

The joint record (type, axis, limits, origin, parts) is also stored as JSON
on the Empty and on the root (`ci_joints`), which is everything a URDF or
SceneCode articulation export needs.
"""
from __future__ import annotations

import json
import math

import bpy
from mathutils import Matrix, Vector


def _link_like(obj, ref):
    """Put `obj` in the same collections as `ref` (or the scene collection)."""
    cols = list(ref.users_collection) if ref is not None else []
    if not cols:
        cols = [bpy.context.scene.collection]
    for col in cols:
        if obj.name not in col.objects:
            col.objects.link(obj)


def _set_open_property(empty, joint: dict) -> None:
    empty["open"] = 0.0
    try:
        empty.id_properties_ui("open").update(
            min=0.0, max=1.0, soft_min=0.0, soft_max=1.0, step=5,
            description=f"{joint['type']} joint: 0 = closed, 1 = fully open "
                        f"({joint.get('function') or ', '.join(joint['parts'][:3])})")
    except Exception:  # noqa: BLE001  (older API: the property still works)
        pass


def _driver(empty, path: str, index: int, expression: str) -> None:
    fc = empty.driver_add(path, index)
    drv = fc.driver
    drv.type = "SCRIPTED"
    var = drv.variables.new()
    var.name = "o"
    var.type = "SINGLE_PROP"
    var.targets[0].id_type = "OBJECT"
    var.targets[0].id = empty
    var.targets[0].data_path = '["open"]'
    drv.expression = expression


def _limit(empty, joint: dict) -> None:
    i = joint["axis_index"]
    lo, hi = float(joint.get("lower") or 0.0), float(joint.get("upper") or 0.0)
    if joint["type"] in ("revolute", "continuous"):
        if joint["type"] == "continuous":
            return
        c = empty.constraints.new("LIMIT_ROTATION")
        c.owner_space = "LOCAL"
        setattr(c, f"use_limit_{'xyz'[i]}", True)
        setattr(c, f"min_{'xyz'[i]}", lo)
        setattr(c, f"max_{'xyz'[i]}", hi)
    else:
        c = empty.constraints.new("LIMIT_LOCATION")
        c.owner_space = "LOCAL"
        rest = empty.location[i]
        setattr(c, f"use_min_{'xyz'[i]}", True)
        setattr(c, f"use_max_{'xyz'[i]}", True)
        setattr(c, f"min_{'xyz'[i]}", rest + lo)
        setattr(c, f"max_{'xyz'[i]}", rest + hi)
    c.name = "ci_joint_range"


KINDS = {"revolute": "hinge: rotation about an axis (1 DOF)",
         "continuous": "swivel: unlimited rotation about an axis (1 DOF)",
         "prismatic": "slider: translation along an axis (1 DOF)",
         "press": "press: short translation along an axis (1 DOF)"}


def _one_dof(empty, j: dict, parts: list, i: int, value: float) -> None:
    """Make the Empty a real 1-DOF joint, not a pivot POINT: every transform
    channel except the joint's own is locked (drivers still drive it; the
    locks stop a hand-turn about the wrong axis), and a child Empty draws the
    axis LINE -- along the hinge edge for a hinge, along the travel for a slider."""
    rot = j["type"] in ("revolute", "continuous")
    empty.lock_location = [True, True, True] if rot else [k != i for k in range(3)]
    empty.lock_rotation = [k != i for k in range(3)] if rot else [True, True, True]
    empty.lock_scale = [True, True, True]
    empty.empty_display_type = "PLAIN_AXES"
    empty.empty_display_size = 0.03
    # extent of the moving parts along the axis, in the joint's frame
    inv = empty.matrix_world.inverted()
    vals = [(inv @ (p.matrix_world @ Vector(c)))[i] for p in parts for c in p.bound_box]
    lo, hi = (min(vals), max(vals)) if vals else (-0.05, 0.05)
    axis_vec = Vector([1.0 if k == i else 0.0 for k in range(3)])
    helper = bpy.data.objects.new(f"AXIS_{j['id']}", None)
    _link_like(helper, empty)
    helper.parent = empty
    helper.empty_display_type = "SINGLE_ARROW"
    if rot:                                   # the hinge line, through the pivot, as long as the part
        length = max(hi - lo, 0.08)
        helper.rotation_euler = Vector((0, 0, 1)).rotation_difference(axis_vec).to_euler()
        helper.location = axis_vec * (lo if hi - lo >= 0.08 else -0.04)
    else:                                     # the travel, pointing the way it opens
        length = max(abs(value), 0.02)
        helper.rotation_euler = Vector((0, 0, 1)).rotation_difference(axis_vec * (1 if value >= 0 else -1)).to_euler()
    helper.empty_display_size = length
    helper.hide_render = True
    helper["ci_axis_of"] = j["id"]
    empty["ci_joint_kind"] = KINDS.get(j["type"], j["type"])


def apply(root, objects: dict, joints: list[dict]) -> list[dict]:
    """Rig the joints. `objects`: part name -> Blender object (meshes).

    Joints whose parts were not found are skipped and reported. Returns the
    joint records as built.
    """
    built = []
    bpy.context.view_layer.update()
    for j in joints:
        parts = [objects[n] for n in j.get("parts") or [] if n in objects]
        if not parts or "origin" not in j:
            built.append({**{k: v for k, v in j.items() if k != "parts"},
                          "built": False, "reason": "moving parts not found"})
            continue
        # Chain onto an earlier joint that already carries these parts.
        current = parts[0].parent
        parent = current if (current is not None and current.get("ci_joint")) else root
        empty = bpy.data.objects.new(f"JOINT_{j['id']}", None)
        _link_like(empty, root if root is not None else parts[0])
        empty.empty_display_type = "ARROWS"
        empty.empty_display_size = 0.08
        origin_world = (root.matrix_world if root is not None else Matrix.Identity(4)) \
            @ Vector(j["origin"])
        empty.parent = parent
        empty.matrix_parent_inverse = Matrix.Identity(4)
        empty.matrix_world = Matrix.Translation(origin_world)
        bpy.context.view_layer.update()
        # a part already carried by an earlier joint (a flap on a lid, rigged
        # flap first) keeps that joint: the earlier joint's Empty is what moves
        # with this one, else the flap would be taken off its own hinge
        movers = []
        for part in parts:
            top = part
            while top.parent is not None and top.parent.get("ci_joint") and top.parent is not parent:
                top = top.parent
            if top not in movers and top is not empty:
                movers.append(top)
        for mover in movers:
            mw = mover.matrix_world.copy()
            mover.parent = empty
            mover.matrix_parent_inverse = Matrix.Identity(4)
            mover.matrix_world = mw
        for part in parts:
            part["ci_moves_with"] = empty.name
        bpy.context.view_layer.update()
        i = int(j["axis_index"])
        value = float(j["open_value"])
        if j["type"] in ("revolute", "continuous"):
            if j["type"] == "continuous":
                value = math.copysign(2 * math.pi, value or 1.0)
            _driver(empty, "rotation_euler", i, f"o*{value:.6f}")
        else:
            rest = float(empty.location[i])
            _driver(empty, "location", i, f"{rest:.6f}+o*{value:.6f}")
        _set_open_property(empty, j)
        _limit(empty, j)
        _one_dof(empty, j, parts, i, value)
        record = {k: v for k, v in j.items()}
        record.update({"built": True, "empty": empty.name, "kind": KINDS.get(j["type"], j["type"]),
                       "dof": 1, "parent": parent.name if parent is not None else None})
        empty["ci_joint"] = json.dumps(record, ensure_ascii=False, default=float)
        built.append(record)
    if root is not None:
        root["ci_joints"] = json.dumps(built, ensure_ascii=False, default=float)
    bpy.context.view_layer.update()
    return built


def rebind_drivers(objects) -> int:
    """After appending into another file, point each joint driver at its own
    Empty again (a self-reference must not keep pointing at the source)."""
    fixed = 0
    for obj in objects:
        ad = obj.animation_data
        if not ad or not obj.get("ci_joint"):
            continue
        for fc in ad.drivers:
            for var in fc.driver.variables:
                for tgt in var.targets:
                    if tgt.id_type == "OBJECT" and tgt.id is not obj:
                        tgt.id = obj
                        fixed += 1
    return fixed


def annotate(objects: dict, spec: dict) -> int:
    """Write what each part IS onto it: ci_part / ci_kind / ci_function ...

    Matching follows the naming contract (id, id_k, id_<piece>), so a key
    named touch_key_2_icon gets the touch_key annotation with instance 2.
    """
    import re
    done = 0
    parts = sorted(spec.get("parts") or [], key=lambda p: -len(p["id"]))
    for name, obj in objects.items():
        for p in parts:
            pid = p["id"]
            m = re.match(r"^" + re.escape(pid) + r"(?:_(\d+))?(?:_|$)", name)
            if not m:
                continue
            obj["ci_part"] = pid
            obj["ci_kind"] = p.get("kind", "")
            if m.group(1):
                obj["ci_instance"] = int(m.group(1))
            if p.get("function"):
                obj["ci_function"] = p["function"][:200]
            if p.get("interaction"):
                obj["ci_interaction"] = p["interaction"]
            labels = p.get("labels") or []
            if labels:
                k = int(m.group(1) or 1) - 1
                obj["ci_label"] = str(labels[k] if 0 <= k < len(labels) else labels[0])[:120]
            done += 1
            break
    return done
