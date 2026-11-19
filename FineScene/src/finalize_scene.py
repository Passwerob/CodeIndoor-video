"""Audit final Blender geometry and render room views using its scene contract."""
import argparse
import json
from pathlib import Path
import sys
import math

import bpy
from mathutils import Vector


def main():
    parser = argparse.ArgumentParser()
    parser.add_argument("--output", required=True, type=Path)
    args = parser.parse_args(sys.argv[sys.argv.index("--") + 1:])
    out = args.output
    contract = json.loads((out / "resolved_scene_contract.json").read_text(encoding="utf-8"))
    export = out / "blender_export"
    bpy.ops.wm.open_mainfile(filepath=str(export / "scene.blend"))
    bpy.context.view_layer.update()
    known = {c["id"] for c in contract["components"]}
    bounds = {}
    counts = {}
    for obj in bpy.context.scene.objects:
        if obj.type != "MESH":
            continue
        component_id = None
        node = obj
        while node is not None and component_id is None:
            component_id = node.get("finescene_component_id")
            if not component_id:
                parts = str(node.get("adsl_path", node.name)).split("/")
                component_id = next((part for part in parts if part in known), None)
            node = node.parent
        if not component_id:
            continue
        corners = [obj.matrix_world @ Vector(c) for c in obj.bound_box]
        low = [min(p[a] for p in corners) for a in range(3)]
        high = [max(p[a] for p in corners) for a in range(3)]
        if component_id in bounds:
            low = [min(low[a], bounds[component_id][0][a]) for a in range(3)]
            high = [max(high[a], bounds[component_id][1][a]) for a in range(3)]
        bounds[component_id] = [low, high]
        counts[component_id] = counts.get(component_id, 0) + 1
    (out / "blender_world_bounds.json").write_text(json.dumps(bounds, indent=2), encoding="utf-8")
    (out / "blender_component_counts.json").write_text(json.dumps(counts, indent=2), encoding="utf-8")

    scene = bpy.context.scene
    room = contract["room"]
    width, depth, height = (room[a] for a in ("width", "depth", "height"))
    extent = max(width, depth)
    center = Vector((0, 0, height * .38))
    camera = scene.camera
    camera.data.type = "ORTHO"
    camera.data.ortho_scale = extent * 1.3
    camera.location = Vector((0, -depth * 1.7, height * 2.65))
    camera.rotation_euler = (center - camera.location).to_track_quat("-Z", "Y").to_euler()
    scene.render.resolution_x = scene.render.resolution_y = 1024
    scene.render.resolution_percentage = 100
    scene.view_settings.exposure = -.6
    for light in scene.objects:
        if light.type == "LIGHT" and light.name in {"Key", "Fill", "Rim"}:
            light.data.energy *= .35
    scene.cycles.samples = 32
    scene.render.filepath = str(export / "render" / "view_000.png")
    bpy.ops.render.render(write_still=True)
    front_location, front_rotation = camera.location.copy(), camera.rotation_euler.copy()
    camera.location = Vector((0, 0, height * 4))
    camera.rotation_euler = (0, 0, 0)
    camera.data.ortho_scale = extent * 1.15
    scene.render.filepath = str(export / "render" / "top.png")
    bpy.ops.render.render(write_still=True)
    render_names = ["view_000.png", "top.png"]
    camera.data.type = "PERSP"
    camera.data.lens = 20
    camera.data.clip_start = .02
    camera.location = Vector((-width*.35,-depth*.42,height*.68))
    target = Vector((width*.12,depth*.10,height*.38))
    camera.rotation_euler = (target-camera.location).to_track_quat("-Z","Y").to_euler()
    scene.render.filepath = str(export / "render" / "interior.png")
    bpy.ops.render.render(write_still=True)
    render_names.append("interior.png")
    for task in contract["furniture_tasks"]:
        if task["kind"] != "bookshelf":
            continue
        low, high = bounds[task["component_id"]]
        target = Vector(((low[0]+high[0])/2,(low[1]+high[1])/2,(low[2]+high[2])/2))
        angle = math.radians(float(task.get("rotation_deg",0)))
        front = Vector((math.sin(angle),-math.cos(angle),0))
        camera.location = target + front * 1.45
        camera.rotation_euler = (target-camera.location).to_track_quat("-Z","Y").to_euler()
        camera.data.type = "ORTHO"
        camera.data.ortho_scale = max(high[2]-low[2],float(task["parameters"].get("width",1.25))) * 1.12
        camera.data.clip_start = .02
        name = f"closeup_{task['component_id']}.png"
        scene.render.filepath = str(export / "render" / name)
        bpy.ops.render.render(write_still=True)
        render_names.append(name)
        # Oblique shelf detail reveals page blocks and cover thickness as well as spines.
        if not any(n.startswith("detail_") for n in render_names):
            side = Vector((math.cos(angle),math.sin(angle),0))
            target.z = high[2] - float(task["parameters"]["shelf_clearance"]) + .16
            camera.location = target + front*1.0 + side*.28 + Vector((0,0,.15))
            camera.rotation_euler = (target-camera.location).to_track_quat("-Z","Y").to_euler()
            camera.data.ortho_scale = float(task["parameters"].get("width",1.25)) * 1.04
            name = f"detail_{task['component_id']}.png"
            scene.render.filepath = str(export / "render" / name)
            bpy.ops.render.render(write_still=True)
            render_names.append(name)
    camera.location, camera.rotation_euler = front_location, front_rotation
    camera.data.type = "ORTHO"
    camera.data.ortho_scale = extent * 1.3
    scene.render.filepath = str(export / "render" / "view_000.png")
    bpy.ops.wm.save_as_mainfile(filepath=str(export / "scene.blend"))
    execution_path = export / "execution.json"
    execution = json.loads(execution_path.read_text(encoding="utf-8"))
    execution["render_paths"] = [str(export / "render" / name) for name in render_names]
    execution["verified_components"] = counts
    execution["bounds"] = [[min(v[0][a] for v in bounds.values()) for a in range(3)], [max(v[1][a] for v in bounds.values()) for a in range(3)]]
    execution_path.write_text(json.dumps(execution, indent=2), encoding="utf-8")


if __name__ == "__main__":
    main()
