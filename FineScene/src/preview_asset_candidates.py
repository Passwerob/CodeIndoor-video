"""Render component evidence before assembly, including TRELLIS GLBs.
Blender: --python preview_asset_candidates.py -- --asset component.glb --output DIR
Uniform preview scaling never edits the source or constitutes visual approval.
"""
import argparse
import hashlib
import json
import math
import sys
from pathlib import Path
import sys as _sys
_sys.path.insert(0, str(Path(__file__).resolve().parent))
import finescene_env as fenv
import bpy
from mathutils import Matrix, Vector

ROOT = fenv.STUDY_ASSETS
NAMES = ['ArmChair_01', 'GreenChair_01', 'mid_century_lounge_chair',
         'painted_wooden_chair_02', 'desk_lamp_arm_01']


def bounds(objects):
    bpy.context.view_layer.update()
    points = [o.matrix_world @ Vector(c) for o in objects for c in o.bound_box]
    return tuple(Vector(tuple(f(p[j] for p in points) for j in range(3)))
                 for f in (min, max))


def aim(obj, target):
    obj.rotation_euler = (Vector(target) - obj.location).to_track_quat('-Z', 'Y').to_euler()


def preview(source, output):
    bpy.ops.wm.read_factory_settings(use_empty=True)
    bpy.ops.import_scene.gltf(filepath=str(source))
    objects = [o for o in bpy.context.scene.objects if o.type == 'MESH']
    if not objects:
        raise ValueError(f'No mesh geometry: {source}')
    bpy.context.view_layer.update()
    matrices = [(o, o.matrix_world.copy()) for o in objects]
    for obj, matrix in matrices:
        obj.parent = None
        obj.matrix_world = matrix
    low, high = bounds(objects)
    dimensions = high - low
    if any(not math.isfinite(v) or v <= 0 for v in dimensions):
        raise ValueError(f'Invalid dimensions: {source}')
    scale = 1.1 / max(dimensions)
    bottom_center = Vector(((low.x + high.x) / 2, (low.y + high.y) / 2, low.z))
    transform = Matrix.Scale(scale, 4) @ Matrix.Translation(-bottom_center)
    for obj in objects:
        obj.matrix_world = transform @ obj.matrix_world
    placed_low, placed_high = bounds(objects)
    if abs(placed_low.z) > 1e-5:
        raise ValueError('Preview ground normalization failed')
    actual = placed_high - placed_low
    if any(abs(actual[j] / dimensions[j] - scale) > 1e-5 for j in range(3)):
        raise ValueError('Preview distorted component aspect ratio')
    scene = bpy.context.scene
    scene.render.engine = 'CYCLES'
    scene.cycles.samples = 24
    scene.cycles.use_denoising = True
    try:
        prefs = bpy.context.preferences.addons['cycles'].preferences
        prefs.compute_device_type = 'OPTIX'
        prefs.get_devices()
        for device in prefs.devices:
            device.use = device.type != 'CPU'
        if any(device.use for device in prefs.devices):
            scene.cycles.device = 'GPU'
    except (TypeError, RuntimeError):
        pass
    scene.render.resolution_x = 900
    scene.render.resolution_y = 900
    scene.render.resolution_percentage = 100
    scene.render.image_settings.file_format = 'PNG'
    scene.view_settings.view_transform = 'AgX'
    scene.world = bpy.data.worlds.new('ComponentWorld')
    scene.world.use_nodes = True
    scene.world.node_tree.nodes['Background'].inputs[0].default_value = (.22, .22, .22, 1)
    scene.world.node_tree.nodes['Background'].inputs[1].default_value = .4
    floor = bpy.data.materials.new('Preview floor')
    floor.diffuse_color = (.32, .32, .32, 1)
    bpy.ops.mesh.primitive_plane_add(size=10, location=(0, 0, -.005))
    bpy.context.object.data.materials.append(floor)
    target = (placed_low + placed_high) * .5
    for name, loc, power, size in [('key', (2, -3, 3), 400, 3),
                                  ('fill', (-2, -1, 2), 250, 2),
                                  ('rear', (0, 2, 2.5), 300, 2)]:
        light = bpy.data.lights.new(name, 'AREA')
        light.energy = power
        light.shape = 'DISK'
        light.size = size
        obj = bpy.data.objects.new(name, light)
        scene.collection.objects.link(obj)
        obj.location = loc
        aim(obj, target)
    data = bpy.data.cameras.new('ComponentCamera')
    camera = bpy.data.objects.new('ComponentCamera', data)
    scene.collection.objects.link(camera)
    scene.camera = camera
    data.type = 'ORTHO'
    data.ortho_scale = max(actual.length * 1.15, 1.55)
    images = []
    for name, offset in [('front', (2, -3, 1.6)), ('rear', (-2, 3, 1.5)),
                         ('side', (3, .2, .8))]:
        camera.location = target + Vector(offset)
        aim(camera, target)
        path = output / f'{source.stem}_{name}.png'
        scene.render.filepath = str(path)
        bpy.ops.render.render(write_still=True)
        images.append({'path': str(path), 'sha256': hashlib.sha256(path.read_bytes()).hexdigest()})
    return {'source': str(source), 'source_sha256': hashlib.sha256(source.read_bytes()).hexdigest(),
            'source_bounds': [list(low), list(high)],
            'preview_bounds': [list(placed_low), list(placed_high)],
            'preview_uniform_scale': scale, 'images': images,
            'status': 'evidence_ready', 'accepted': False,
            'note': 'Preview normalization only. Semantic completeness and scene fit require separate review.'}


def main():
    parser = argparse.ArgumentParser()
    parser.add_argument('--asset', type=Path, action='append')
    parser.add_argument('--output', type=Path, required=True)
    args = parser.parse_args(sys.argv[sys.argv.index('--') + 1:])
    output = args.output.resolve()
    output.mkdir(parents=True, exist_ok=False)
    sources = args.asset or [ROOT / name / (name + '_2k.gltf') for name in NAMES]
    records = []
    for i, source in enumerate(sources):
        folder = output / f'{i:02d}_{source.stem}'
        folder.mkdir()
        records.append(preview(source.resolve(), folder))
        (output / 'preview_manifest.json').write_text(json.dumps(records, indent=2), encoding='utf-8')
    print('COMPONENT_EVIDENCE_COMPLETE', flush=True)


if __name__ == '__main__':
    main()
