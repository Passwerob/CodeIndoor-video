"""Replace an external-mesh placeholder with a refined Blender asset."""
from __future__ import annotations

import argparse
import json
from pathlib import Path
import sys

import bpy
from mathutils import Matrix, Vector


def root_of(obj):
    current = obj
    while current.parent is not None:
        current = current.parent
    return current


def world_bounds(meshes):
    points = [obj.matrix_world @ Vector(corner) for obj in meshes for corner in obj.bound_box]
    low = Vector((min(p[i] for p in points) for i in range(3)))
    high = Vector((max(p[i] for p in points) for i in range(3)))
    return low, high


def remove_placeholder(scene, component_id: str) -> None:
    doomed = []
    for obj in scene.objects:
        if component_id in str(obj.get("adsl_path", obj.name)).split("/") or obj.get("finescene_component_id") == component_id:
            doomed.append(obj)
    for obj in doomed:
        bpy.data.objects.remove(obj, do_unlink=True)


def append_blend_asset(source: Path, *, placement: tuple[float, float, float], rotation_z: float, component_id: str):
    before = set(bpy.data.objects)
    with bpy.data.libraries.load(str(source), link=False) as (data_from, data_to):
        data_to.objects = list(data_from.objects)
        data_to.materials = list(data_from.materials)
    loaded = [obj for obj in data_to.objects if obj is not None]
    for obj in loaded:
        if not obj.users_collection:
            bpy.context.scene.collection.objects.link(obj)
    roots = [obj for obj in loaded if obj.parent is None and obj.name == "Oakwarden_Medieval_Armchair"]
    if not roots:
        raise RuntimeError("The refined asset blend has no Oakwarden_Medieval_Armchair root")
    root = roots[0]
    root.name = f"{component_id}_Oakwarden"
    root.matrix_world = Matrix.Translation(Vector(placement))
    root.rotation_euler[2] = rotation_z * 3.141592653589793 / 180.0
    for obj in loaded:
        if obj is root or root_of(obj) is root:
            obj["finescene_component_id"] = component_id
            obj["finescene_refined_source"] = str(source)
    # Presentation floor, lights and camera from the source blend are not part
    # of the reusable furniture component.
    for obj in loaded:
        if root_of(obj) is not root:
            bpy.data.objects.remove(obj, do_unlink=True)
    bpy.context.view_layer.update()
    return root


def configure_render(scene, output: Path):
    meshes = [obj for obj in scene.objects if obj.type == "MESH"]
    if not meshes:
        raise RuntimeError("No mesh geometry remains after refined assembly")
    low, high = world_bounds(meshes)
    center = (low + high) * 0.5
    extent = max(float((high - low).length), 0.1)
    camera = scene.camera
    if camera is None:
        data = bpy.data.cameras.new("FineScene camera")
        camera = bpy.data.objects.new("FineScene camera", data)
        scene.collection.objects.link(camera)
        scene.camera = camera
    camera.data.type = "ORTHO"
    camera.data.ortho_scale = max(float(high[0] - low[0]), float(high[1] - low[1])) * 1.3
    camera.location = center + Vector((0, -extent * 1.5, extent * 1.05))
    camera.rotation_euler = (center - camera.location).to_track_quat("-Z", "Y").to_euler()
    scene.render.resolution_x = 1024
    scene.render.resolution_y = 1024
    scene.render.resolution_percentage = 100
    scene.render.image_settings.file_format = "PNG"
    render_path = output / "render" / "view_000.png"
    scene.render.filepath = str(render_path)
    bpy.ops.render.render(write_still=True)
    glb_path = output / "render" / "scene.glb"
    bpy.ops.object.select_all(action="DESELECT")
    for obj in meshes:
        obj.select_set(True)
    bpy.context.view_layer.objects.active = meshes[0]
    bpy.ops.export_scene.gltf(filepath=str(glb_path), export_format="GLB", export_apply=True, use_selection=True, export_extras=True)
    return low, high, render_path, glb_path


def main() -> None:
    parser = argparse.ArgumentParser()
    parser.add_argument("--source", type=Path, required=True)
    parser.add_argument("--output", type=Path, required=True)
    parser.add_argument("--component-id", default="chair_01")
    parser.add_argument("--x", type=float, required=True)
    parser.add_argument("--y", type=float, required=True)
    parser.add_argument("--z", type=float, required=True)
    parser.add_argument("--rotation-z", type=float, default=0.0)
    argv = sys.argv[sys.argv.index("--") + 1:] if "--" in sys.argv else sys.argv[1:]
    args = parser.parse_args(argv)
    output = args.output.resolve()
    scene_path = output / "scene.blend"
    bpy.ops.wm.open_mainfile(filepath=str(scene_path))
    remove_placeholder(bpy.context.scene, args.component_id)
    append_blend_asset(args.source.resolve(), placement=(args.x, args.y, args.z), rotation_z=args.rotation_z, component_id=args.component_id)
    low, high, render_path, glb_path = configure_render(bpy.context.scene, output)
    bpy.ops.wm.save_as_mainfile(filepath=str(scene_path))
    execution_path = output / "execution.json"
    execution = json.loads(execution_path.read_text(encoding="utf-8")) if execution_path.is_file() else {}
    execution.update({
        "blend_path": str(scene_path),
        "glb_path": str(glb_path),
        "render_paths": [str(render_path)],
        "bounds": [list(low), list(high)],
        "mesh_count": len([obj for obj in bpy.context.scene.objects if obj.type == "MESH"]),
        "refined_components": [{"component_id": args.component_id, "source": str(args.source.resolve()), "mode": "blend_append"}],
    })
    execution_path.write_text(json.dumps(execution, indent=2), encoding="utf-8")
    print(json.dumps(execution, ensure_ascii=False))


if __name__ == "__main__":
    main()
