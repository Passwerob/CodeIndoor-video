"""Export and round-trip QA the manual toilet reference model."""
from __future__ import annotations

import json
from pathlib import Path
import sys as _sys
_sys.path.insert(0, str(Path(__file__).resolve().parent))
import finescene_env as fenv

import bpy
from mathutils import Vector


RUN = fenv.RUNS / "trellis_toilet_20261020_01"
SOURCE_BLEND = RUN / "reference_scene.blend"
GLB = RUN / "manual_reference_only.glb"
PREVIEW = RUN / "manual_reference_glb_qa.png"
QA_BLEND = RUN / "manual_reference_glb_qa.blend"
CONTRACT = RUN / "manual_reference_contract.json"


def main() -> None:
    if not SOURCE_BLEND.is_file():
        raise FileNotFoundError(SOURCE_BLEND)
    bpy.ops.wm.open_mainfile(filepath=str(SOURCE_BLEND))
    scene = bpy.context.scene
    source_meshes = [obj for obj in scene.objects if obj.type == "MESH"]
    if not source_meshes:
        raise RuntimeError("The reference scene contains no mesh objects")

    bpy.ops.object.select_all(action="DESELECT")
    for obj in source_meshes:
        obj.select_set(True)
    bpy.context.view_layer.objects.active = source_meshes[0]
    result = bpy.ops.export_scene.gltf(
        filepath=str(GLB),
        export_format="GLB",
        export_apply=True,
        use_selection=True,
        export_extras=True,
    )
    if "FINISHED" not in result or not GLB.is_file() or GLB.stat().st_size == 0:
        raise RuntimeError(f"GLB export failed: {result}")

    # Replace the source meshes with the exported GLB, keeping the original
    # camera, lights and studio setup for a round-trip render check.
    for obj in list(source_meshes):
        bpy.data.objects.remove(obj, do_unlink=True)
    bpy.ops.import_scene.gltf(filepath=str(GLB))
    imported = [obj for obj in scene.objects if obj.type == "MESH"]
    if not imported:
        raise RuntimeError("The exported GLB re-imported without mesh objects")
    bpy.context.view_layer.update()
    points = [obj.matrix_world @ Vector(corner) for obj in imported for corner in obj.bound_box]
    low = [min(point[axis] for point in points) for axis in range(3)]
    high = [max(point[axis] for point in points) for axis in range(3)]
    if any(high[i] <= low[i] for i in range(3)):
        raise RuntimeError(f"Invalid re-imported GLB bounds: {low}, {high}")

    scene.render.engine = "CYCLES"
    scene.cycles.samples = 64
    scene.cycles.use_denoising = True
    scene.render.film_transparent = False
    scene.render.resolution_x = 1024
    scene.render.resolution_y = 1024
    scene.render.resolution_percentage = 100
    scene.render.image_settings.file_format = "PNG"
    scene.render.filepath = str(PREVIEW)
    bpy.ops.render.render(write_still=True)
    if not PREVIEW.is_file() or PREVIEW.stat().st_size == 0:
        raise RuntimeError("Round-trip render was not written")

    bpy.ops.wm.save_as_mainfile(filepath=str(QA_BLEND))
    material_names = sorted({slot.material.name for obj in imported for slot in obj.material_slots if slot.material})
    contract = {
        "schema": "finescene.reference_asset.v1",
        "component_id": "toilet_manual_reference_only",
        "asset_path": str(GLB),
        "source_blend": str(SOURCE_BLEND),
        "generator": "FineScene parametric Blender reference script",
        "source_mode": "manual",
        "purpose": "input/reference visualization only",
        "is_trellis_output": False,
        "trellis_acceptance": "not_applicable",
        "round_trip_preview": str(PREVIEW),
        "round_trip_blend": str(QA_BLEND),
        "mesh_objects": len(imported),
        "vertices": sum(len(obj.data.vertices) for obj in imported),
        "faces": sum(len(obj.data.polygons) for obj in imported),
        "materials": material_names,
        "bounds": [low, high],
        "visual_review": "Pending review of the round-trip preview.",
    }
    CONTRACT.write_text(json.dumps(contract, indent=2), encoding="utf-8")
    print(json.dumps(contract, indent=2), flush=True)


if __name__ == "__main__":
    main()
