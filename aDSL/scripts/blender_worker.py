"""Run with Blender --background --factory-startup --python-exit-code 1 --python.

Use the repository's geometry exporter, with Blender's own Python and numpy.
"""
from __future__ import annotations

import argparse
import importlib
import importlib.util
import json
import math
from pathlib import Path
import runpy
import sys

import bpy
from mathutils import Matrix, Vector


ROOT = Path(__file__).resolve().parents[1]
# Editable-install finders belong to the host Python; expose only project code
# to Blender, keeping Blender's bundled packages first.
if "adsl" not in sys.modules:
    spec = importlib.util.spec_from_file_location(
        "adsl", ROOT / "adsl-core" / "adsl" / "__init__.py",
        submodule_search_locations=[str(ROOT / "adsl-core")])
    package = importlib.util.module_from_spec(spec)
    sys.modules["adsl"] = package
    spec.loader.exec_module(package)
sys.path.insert(0, str(ROOT / "adsl-core"))
sys.path.insert(0, str(ROOT))

from adsl.core import Asset
from adsl.core.joints import Joint

geometry = importlib.import_module("adsl.core.export.export_glb")


def asset_from_json(data):
    """Restore the serialized tree; joint geometry is already in link space."""
    asset = Asset(label=data.get("label", "Asset"))
    for primitive in data.get("primitives", []):
        record = dict(primitive)
        record.setdefault("color", (0.8, 0.8, 0.8))
        record.setdefault("alpha", None)
        asset.add_primitive(record)
    for name, child in data.get("children", {}).items():
        asset.attach_part(name, asset_from_json(child))
    for name, entry in data.get("joints", {}).items():
        spec = dict(entry["spec"])
        spec["name"] = name
        joint = Joint(**spec)
        child = asset_from_json(entry["child"])
        # attach_joint would rebase this already-rebased geometry a second time.
        asset._joints[name] = joint
        asset._joint_children[name] = child
        child._joint_parent = asset
        child._parent_joint = joint
    return asset


def serializable(value):
    if hasattr(value, "tolist"):
        return value.tolist()
    raise TypeError(f"Cannot serialize {type(value).__name__}")


def add_rgb_emission(strength):
    """Semantic opt-in for legacy RGB keys/LEDs; use named Blender sockets."""
    cache = {}
    count = 0
    for obj in bpy.context.scene.objects:
        if obj.type != "MESH":
            continue
        path = obj.parent.get("adsl_path", "").lower() if obj.parent else ""
        parts = path.split("/")
        emissive = any(p.startswith(("key_", "rgb_", "led_")) or p.endswith("_led") for p in parts)
        if not emissive or strength <= 0:
            continue
        for index, material in enumerate(obj.data.materials):
            key = material.name
            if key not in cache:
                copy = material.copy()
                copy.name = f"RGB_{material.name}"
                bsdf = copy.node_tree.nodes.get("Principled BSDF")
                color_socket = bsdf.inputs.get("Emission Color") or bsdf.inputs.get("Emission")
                strength_socket = bsdf.inputs.get("Emission Strength")
                if color_socket is None or strength_socket is None:
                    raise RuntimeError("Principled BSDF emission sockets unavailable")
                color_socket.default_value = bsdf.inputs["Base Color"].default_value
                strength_socket.default_value = strength
                cache[key] = copy
            obj.data.materials[index] = cache[key]
        count += 1
    return count


def scene_bounds(meshes):
    points = [obj.matrix_world @ Vector(corner) for obj in meshes for corner in obj.bound_box]
    if not points:
        raise ValueError("Scene contains no mesh geometry")
    low = Vector(tuple(min(p[i] for p in points) for i in range(3)))
    high = Vector(tuple(max(p[i] for p in points) for i in range(3)))
    if not all(math.isfinite(v) for v in (*low, *high)):
        raise ValueError("Scene bounds contain non-finite coordinates")
    return low, high


def external_component_report(meshes):
    """Expose imported high-detail components in the execution manifest."""
    report = []
    for obj in meshes:
        if obj.get("adsl_kind") != "external_mesh":
            continue
        points = [obj.matrix_world @ Vector(corner) for corner in obj.bound_box]
        low = Vector(tuple(min(point[i] for point in points) for i in range(3)))
        high = Vector(tuple(max(point[i] for point in points) for i in range(3)))
        report.append({
            "object": obj.name,
            "component_path": obj.get("adsl_path"),
            "source": obj.get("adsl_source"),
            "vertices": len(obj.data.vertices),
            "polygons": len(obj.data.polygons),
            "world_bounds": [list(low), list(high)],
        })
    return report


def configure_presentation(low, high, resolution):
    scene = bpy.context.scene
    scene.render.engine = "CYCLES"
    scene.cycles.device = "CPU"
    scene.cycles.samples = 24
    scene.cycles.use_denoising = True
    scene.render.resolution_x = resolution
    scene.render.resolution_y = resolution
    scene.render.resolution_percentage = 100
    scene.render.image_settings.file_format = "PNG"
    scene.world = bpy.data.worlds.new("LocalPreviewWorld")
    scene.world.use_nodes = True
    scene.world.node_tree.nodes["Background"].inputs[0].default_value = (0.12, 0.14, 0.18, 1)
    scene.world.node_tree.nodes["Background"].inputs[1].default_value = 0.5
    center = (low + high) / 2
    extent = max(high - low)
    if extent <= 0:
        raise ValueError("Scene has zero-size bounds")
    for name, direction, power in [
        ("Key", (1, -1.5, 2.5), 800),
        ("Fill", (-1.5, -0.5, 1), 450),
        ("Rim", (0.5, 2, 2), 650),
    ]:
        lamp = bpy.data.lights.new(name, "AREA")
        lamp.energy = power * extent**2
        lamp.shape = "DISK"
        lamp.size = extent * 1.5
        obj = bpy.data.objects.new(name, lamp)
        scene.collection.objects.link(obj)
        obj.location = center + Vector(direction) * extent
        obj.rotation_euler = (center - obj.location).to_track_quat("-Z", "Y").to_euler()
    camera_data = bpy.data.cameras.new("PreviewCamera")
    camera_data.type = "ORTHO"
    # Bounding-sphere diameter fits at every orbit angle.
    camera_data.ortho_scale = (high - low).length * 1.18
    camera_data.clip_end = max(1000, extent * 20)
    camera = bpy.data.objects.new("PreviewCamera", camera_data)
    scene.collection.objects.link(camera)
    scene.camera = camera
    return camera, center, extent


def main(argv=None):
    parser = argparse.ArgumentParser()
    source = parser.add_mutually_exclusive_group(required=True)
    source.add_argument("--input", type=Path, help="Serialized Asset JSON")
    source.add_argument("--source", type=Path, help="aDSL program assigning scene")
    parser.add_argument("--output", type=Path, required=True)
    parser.add_argument("--up-axis", choices=("y", "z"), default="z")
    parser.add_argument("--rgb-emission", type=float, default=0)
    parser.add_argument("--render", action="store_true")
    parser.add_argument("--render-view-count", type=int, default=1)
    parser.add_argument("--render-elevation", type=float, default=30)
    parser.add_argument("--resolution", type=int, default=640)
    parser.add_argument("--urdf", action="store_true")
    args = parser.parse_args(argv)
    if args.render_view_count < 1 or args.resolution < 64:
        parser.error("render-view-count must be positive and resolution >= 64")
    if args.up_axis != "z" and args.urdf:
        parser.error("URDF requires the source's native Z-up coordinate frame")
    output = args.output.resolve()
    if output.exists() and any(output.iterdir()):
        raise FileExistsError(f"Output must be new or empty: {output}")
    output.mkdir(parents=True, exist_ok=True)
    bpy.ops.wm.read_factory_settings(use_empty=True)
    if args.input:
        source_path = args.input.resolve()
        data = json.loads(source_path.read_text(encoding="utf-8-sig"))
    else:
        source_path = args.source.resolve()
        sys.path.insert(0, str(source_path.parent))
        namespace = runpy.run_path(str(source_path), run_name="__adsl_generated__")
        scene = namespace.get("scene")
        if not hasattr(scene, "to_dict"):
            raise TypeError("Source must assign an aDSL Asset to scene")
        data = scene.to_dict()
    asset = asset_from_json(data)
    (output / "asset.json").write_text(
        json.dumps(data, default=serializable, indent=2), encoding="utf-8")
    render_root = output / "render"
    render_root.mkdir()
    urdf = None
    if args.urdf:
        from adsl.core import export_urdf
        urdf = render_root / "scene.urdf"
        export_urdf(asset, filepath=urdf, mesh_backend="blender", visual_mesh_format="glb")
        # CSG link export may replace the current Blender scene.
        bpy.ops.wm.read_factory_settings(use_empty=True)
    meshes = geometry._build_shape(asset)
    if args.up_axis == "y":
        rotation = Matrix.Rotation(math.pi / 2, 4, "X")
        for obj in list(bpy.context.scene.objects):
            if obj.parent is None:
                obj.matrix_world = rotation @ obj.matrix_world
    bpy.context.view_layer.update()
    emissive_count = add_rgb_emission(args.rgb_emission)
    low, high = scene_bounds(meshes)
    glb = render_root / "scene.glb"
    bpy.ops.export_scene.gltf(
        filepath=str(glb), export_format="GLB", export_extras=True,
        export_apply=True, use_selection=False,
    )
    camera, center, extent = configure_presentation(low, high, args.resolution)
    renders = []
    for index in range(args.render_view_count if args.render else 1):
        azimuth = math.radians(-55 + 360 * index / args.render_view_count)
        elevation = math.radians(args.render_elevation)
        direction = Vector((math.cos(azimuth) * math.cos(elevation),
                            math.sin(azimuth) * math.cos(elevation), math.sin(elevation)))
        camera.location = center + direction * extent * 3
        camera.rotation_euler = (center - camera.location).to_track_quat("-Z", "Y").to_euler()
        if args.render:
            image = render_root / f"view_{index:03d}.png"
            bpy.context.scene.render.filepath = str(image)
            bpy.ops.render.render(write_still=True)
            renders.append(str(image))
    # Keep the saved project on the first orbit view.
    if renders:
        azimuth, elevation = math.radians(-55), math.radians(args.render_elevation)
        camera.location = center + Vector((math.cos(azimuth)*math.cos(elevation),
            math.sin(azimuth)*math.cos(elevation), math.sin(elevation))) * extent * 3
        camera.rotation_euler = (center-camera.location).to_track_quat("-Z", "Y").to_euler()
    blend = output / "scene.blend"
    bpy.ops.wm.save_as_mainfile(filepath=str(blend))
    report = {
        "result_path": str(output), "render_root": str(render_root),
        "glb_path": str(glb), "blend_path": str(blend),
        "urdf_path": str(urdf) if urdf else None,
        "render_paths": renders, "source_path": str(source_path),
        "blender_version": bpy.app.version_string, "source_up_axis": args.up_axis,
        "mesh_count": len(meshes), "emissive_mesh_count": emissive_count,
        "bounds": [list(low), list(high)],
        "external_components": external_component_report(meshes),
    }
    (output / "execution.json").write_text(json.dumps(report, indent=2), encoding="utf-8")
    print("ADSL_LOCAL_RESULT=" + json.dumps(report))
    return 0


if __name__ == "__main__":
    argv = sys.argv[sys.argv.index("--") + 1:] if "--" in sys.argv else []
    raise SystemExit(main(argv))
