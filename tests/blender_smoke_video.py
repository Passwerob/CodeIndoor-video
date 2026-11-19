"""Blender-side smoke test for the video pipeline (no FineScene, no network).

    blender -b --factory-startup --python tests/blender_smoke_video.py

Checks, inside the Blender you actually use:
  1. every parametric material class + modifier builds a node tree
     (tile grid, marble veins, terrazzo specks, brushed metal, glowing screen);
  2. a door + handle rigged by blender_articulation opens about its hinge when
     its `open` property is set, and a drawer slides;
  3. the room / lights / placeholder part of video_scene_builder runs on a tiny
     plan (no render).
Prints SMOKE=OK or raises.
"""
import json
import math
import sys
from pathlib import Path

import bpy
from mathutils import Vector

ADAPTER = Path(__file__).resolve().parents[1] / "adsl_adapter"
sys.path.insert(0, str(ADAPTER))

import articulation  # noqa: E402
import blender_articulation  # noqa: E402
import blender_materials  # noqa: E402
import video_scene_builder as vsb  # noqa: E402

for obj in list(bpy.data.objects):
    bpy.data.objects.remove(obj, do_unlink=True)

# 1. materials ------------------------------------------------------------
keys = ["paint:#F3F1EC", "wood:#B58A5A:0.4:grain=#7A5A3A", "fabric:#8FA08A:0.9",
        "velvet:#2F4A3C", "leather:#5A3A22", "metal:#B9BBBD:0.32:brushed", "glass:#FFFFFF",
        "ceramic:#DCD6CC", "plastic:#E8E8E8:0.4:transmission=0.6",
        "stone:#ECECE8:0.15:veins=#9A9A9A", "stone:#D9D9D6:0.45:speckle=#5A5A5A",
        "tile:#F4F4F2:0.12:grout=#1E1E1E:size=0.10:gap=0.004", "rubber:#151515",
        "screen:#0E0E12:0.06:emit=#3355FF:strength=3", "light:#FFFFFF:0.5:strength=10",
        "shade:#F2E3C4"]
cache = {}
for k in keys:
    mat = blender_materials.material_for_key(k, cache)
    assert mat is not None and mat.node_tree is not None, k
glow = blender_materials._find_bsdf(cache["screen:#0E0E12:0.06:emit=#3355FF:strength=3"].node_tree)
assert glow.inputs["Emission Strength"].default_value == 3.0

# 2. joints ---------------------------------------------------------------
col = bpy.context.scene.collection
root = bpy.data.objects.new("ROOT_test", None)
col.objects.link(root)
made = {}
for name, lo, hi in (("body", (-0.2, -0.18, 0.0), (0.2, 0.2, 1.0)),
                     ("door", (-0.19, -0.2, 0.05), (0.19, -0.18, 0.55)),
                     ("door_handle", (0.15, -0.22, 0.25), (0.17, -0.2, 0.45)),
                     ("drawer", (-0.19, -0.2, 0.6), (0.19, -0.18, 0.75))):
    o = vsb.box(name, lo, hi, col, "paint:#EEEEEE")
    o.parent = root
    made[name] = o
parts = [{"name": n, "min": [min((o.matrix_world @ Vector(c))[i] for c in o.bound_box) for i in range(3)],
          "max": [max((o.matrix_world @ Vector(c))[i] for c in o.bound_box) for i in range(3)]}
         for n, o in made.items()]
spec = {"parts": [{"id": "door", "kind": "door", "face": "front"},
                  {"id": "drawer", "kind": "drawer", "face": "front"}],
        "joints": [{"id": "hinge", "type": "revolute", "moving_parts": ["door"], "hinge_edge": "left",
                    "opens_toward": "front", "range": 100},
                   {"id": "slide", "type": "prismatic", "moving_parts": ["drawer"],
                    "opens_toward": "front", "range": 0.3}]}
joints = blender_articulation.apply(root, made, articulation.derive_all(spec, parts))
assert all(j["built"] for j in joints), joints
before = made["door_handle"].matrix_world.translation.copy()
drawer_before = made["drawer"].matrix_world.translation.copy()
for name in ("JOINT_hinge", "JOINT_slide"):
    e = bpy.data.objects[name]
    e["open"] = 1.0
    e.update_tag()
bpy.context.view_layer.update()
hinge = bpy.data.objects["JOINT_hinge"]
assert abs(math.degrees(hinge.rotation_euler.z) + 100) < 0.5, math.degrees(hinge.rotation_euler.z)
assert (made["door_handle"].matrix_world.translation - before).length > 0.2
assert made["door_handle"].matrix_world.translation.y < before.y      # swung out to the front
assert abs((made["drawer"].matrix_world.translation - drawer_before).y + 0.3) < 1e-3

# 3. room / lights / placeholders -----------------------------------------
plan = {"room": {"width": 3.0, "depth": 4.0, "height": 2.7,
                 "surfaces": {"floor": "stone:#D7D7D7:0.38:speckle=#8A8A86",
                              "walls": "paint:#EDEDED:0.75", "ceiling": "paint:#222224:0.75"},
                 "openings": [{"id": "w1", "kind": "window", "wall": "west", "offset_m": 2.0,
                               "width_m": 1.0, "height_m": 1.2, "sill_m": 0.9}],
                 "splashbacks": [{"wall": "east", "axis": "y", "from": -1.0, "to": 1.0, "z0": 0.9,
                                  "z1": 1.5, "material": "tile:#F4F4F2:0.12:grout=#1E1E1E"}]},
        "world": {"time_of_day": "night"},
        "lights": [{"id": "p1", "fixture": "pend", "kind": "pendant", "type": "SPOT", "on": True,
                    "location": [0, 0, 1.6], "energy_w": 40, "temperature_k": 4000,
                    "color_rgb": [1, 0.65, 0.38], "spot_size_deg": 75, "spot_blend": 0.35,
                    "shadow_soft_size": 0.04, "area_size": [0.1, 0.1],
                    "housing": {"shape": "cylinder", "diameter": 0.1, "height": 0.16,
                                "colour_hex": "#F2F2F2", "material": "metal", "top_z": 1.8,
                                "ceiling_z": 2.7, "cable": True},
                    "emitter_key": "light:#FFFFFF:0.5:strength=8"}],
        "objects": [{"id": "box1", "x": 1.0, "y": 0.0, "z": 0.0, "yaw_deg": -90,
                     "dims": [0.6, 0.4, 0.9], "mount": "floor", "blend": None,
                     "placeholder_material": "wood:#C1A17D:0.38"}]}
room = vsb.build_room(plan)
assert room == {"walls": 7, "openings": 1, "splashbacks": 1}, room   # 4 around the window + 3
assert vsb.build_lights(plan) == 1
light = bpy.data.objects["p1"].data
if hasattr(light, "use_temperature"):
    assert light.use_temperature and light.temperature == 4000
assert vsb.build_objects(plan)[0]["mode"] == "placeholder"
print("SMOKE=OK " + json.dumps({"materials": len(cache), "joints": len(joints),
                                "blender": bpy.app.version_string}))
